from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.quota import bloqueio_ia
from ai import tasks as ai_tasks

from .mdrender import render_md, render_subtopico
from .models import (
    CardSalvo,
    Nivel,
    Percurso,
    SessaoSugestao,
    Subtopico,
    Trilha,
    TrilhaSugerida,
    VideoNivel,
)


def _pre_gerar_exercicios(nivel):
    """Garante que a lista de exercícios do nível já esteja sendo gerada."""
    from avaliacoes.models import ListaExercicios

    lista, created = ListaExercicios.objects.get_or_create(nivel=nivel)
    if created or lista.status == ListaExercicios.Status.ERRO:
        lista.status = ListaExercicios.Status.GERANDO
        lista.erro = ""
        lista.save(update_fields=["status", "erro"])
        ai_tasks.task_gerar_exercicios.delay(lista.pk)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def _questao_do_dia(nivel, hoje):
    """Uma questão objetiva do nível para revisar direto no dashboard —
    determinística por dia (a mesma pergunta o dia inteiro). Preferência:
    exercícios e questões de revisão (têm explicação) → questões de avaliação.
    Retorna None se o nível ainda não tem questão objetiva aproveitável."""
    from avaliacoes.models import Exercicio, Questao, QuestaoRevisao

    fontes = [
        Exercicio.objects.filter(lista__nivel=nivel),
        QuestaoRevisao.objects.filter(nivel=nivel),
        Questao.objects.filter(avaliacao__nivel=nivel),
    ]
    for qs in fontes:
        qs = list(qs.exclude(gabarito="").order_by("pk"))
        candidatas = [q for q in qs if q.alternativas]
        if not candidatas:
            continue
        q = candidatas[(hoje.toordinal() * 31 + nivel.pk) % len(candidatas)]
        return {
            "enunciado": render_md(q.enunciado_md),
            "alternativas": q.alternativas,
            "gabarito": (q.gabarito or "").strip().upper(),
            "explicacao": render_md(getattr(q, "explicacao_md", "") or ""),
        }
    return None


@login_required
def dashboard(request):
    trilhas = list(
        request.user.trilhas.prefetch_related("niveis__subtopicos", "titulos__nivel").all()
    )
    ativas = [t for t in trilhas if t.ativa]
    desativadas = [t for t in trilhas if not t.ativa]

    # Medalhas conquistadas (uma por trilha ativa que já tem título), da mais
    # recente para a mais antiga (por data de obtenção do título atual),
    # independentemente do grau, para a estante de conquistas.
    medalhas = []
    for t in ativas:
        m = t.medalha
        if not m:
            continue
        m["trilha"] = t
        m["pips"] = [i < m["estrelas"] for i in range(m["total"])]
        tit = t.titulo_atual
        m["obtido_em"] = tit.concedido_em if tit else None
        medalhas.append(m)
    medalhas.sort(
        key=lambda m: (m["obtido_em"] is not None, m["obtido_em"]),
        reverse=True,
    )

    # Trilhas ativas ordenadas globalmente por progresso decrescente.
    ativas_sorted = sorted(ativas, key=lambda t: -t.progresso_pct)

    pode_estudar = any(t.proximo_topico for t in ativas)

    # Destaque "continuar de onde parou": o próximo tópico da trilha mais recente.
    continuar = None
    for t in sorted(ativas, key=lambda t: t.atualizada_em, reverse=True):
        alvo = t.proximo_topico
        if alvo:
            continuar = {"trilha": t, "nivel": alvo[0], "sub": alvo[1]}
            break

    # Feed "para hoje": flashcards dos níveis com revisão espaçada devida.
    # Cada card traz uma QUESTÃO OBJETIVA do nível (revisão ativa responde-se
    # na hora); a autoavaliação lembrei/rever alimenta o SM-2. + quiz do dia.
    hoje = timezone.localdate()
    niveis_devidos = list(
        Nivel.objects.filter(
            trilha__user=request.user,
            trilha__ativa=True,
            status=Nivel.Status.APROVADO,
            revisao_proxima__lte=hoje,
        )
        .select_related("trilha")
        .prefetch_related("subtopicos")
        .order_by("revisao_proxima")[:3]
    )
    feed_revisao = [{"nivel": n, "questao": _questao_do_dia(n, hoje)} for n in niveis_devidos]
    quiz_feito_hoje = request.user.revisoes.filter(criada_em__date=hoje).exists()

    aprovados = Nivel.objects.filter(
        trilha__user=request.user, trilha__ativa=True, status=Nivel.Status.APROVADO
    )
    pode_revisar = aprovados.exists()
    revisoes_devidas = aprovados.filter(revisao_proxima__lte=timezone.localdate()).count()

    from accounts.models import Profile

    xp_tabela = [
        ("Ler um tópico", Profile.XP_TOPICO),
        ("Responder uma questão", Profile.XP_EXERCICIO),
        ("Enviar uma avaliação", Profile.XP_AVALIACAO),
        ("Ser aprovado num nível", Profile.XP_APROVACAO),
        ("Concluir uma trilha", Profile.XP_TRILHA_CONCLUIDA),
    ]

    return render(
        request,
        "trilhas/dashboard.html",
        {
            "trilhas": trilhas,
            "n_ativas": len(ativas),
            "desativadas": desativadas,
            "medalhas": medalhas,
            "ativas_sorted": ativas_sorted,
            "pode_estudar": pode_estudar,
            "continuar": continuar,
            "feed_revisao": feed_revisao,
            "quiz_feito_hoje": quiz_feito_hoje,
            "pode_revisar": pode_revisar,
            "revisoes_devidas": revisoes_devidas,
            "quota": _quota_ctx(request.user),
            "xp_tabela": xp_tabela,
            "diamante_xp_regra": Profile.XP_POR_DIAMANTE,
            "diamante_niveis_regra": Profile.DIAMANTE_A_CADA_N_NIVEIS,
        },
    )


def _quota_ctx(user):
    """Uso da quota mensal de IA para a barra no painel (None se sem quota)."""
    profile = getattr(user, "profile", None)
    if profile is None or not profile.quota_tokens_mes:
        return None
    return {
        "pct": profile.quota_pct_usado,
        "restantes": profile.tokens_restantes,
        "total": profile.quota_tokens_mes,
        "is_visitor": profile.is_visitor,
    }


@login_required
@require_POST
def trilha_alternar_ativa(request, pk):
    """Ativa/desativa uma trilha. Desativadas somem da revisão e do mentor."""
    trilha = get_object_or_404(Trilha, pk=pk, user=request.user)
    trilha.ativa = not trilha.ativa
    trilha.save(update_fields=["ativa", "atualizada_em"])
    if trilha.ativa:
        messages.success(request, "Trilha reativada — de volta à revisão e ao mentor.")
    else:
        messages.info(
            request, "Trilha desativada — fora da revisão, do mentor e do “Estudar agora”."
        )
    return redirect("trilhas:detalhe", pk=pk)


@login_required
@require_POST
def trilha_nova_capa(request, pk):
    """Busca outra foto de capa na Pexels (excluindo a atual e as das demais
    trilhas do usuário, para a troca sempre render uma imagem diferente)."""
    from ai.services import baixar_para_media, buscar_capa, pexels_id_da_url

    trilha = get_object_or_404(Trilha, pk=pk, user=request.user)
    erro = bloqueio_ia(request.user, tokens_estimados=2000)
    if erro:
        messages.error(request, erro)
        return redirect("trilhas:detalhe", pk=pk)
    usadas = set(
        Trilha.objects.filter(user=request.user, cover_pexels_id__isnull=False).values_list(
            "cover_pexels_id", flat=True
        )
    )
    url = buscar_capa(
        trilha.titulo,
        trilha.categoria,
        trilha.descricao,
        profile=getattr(request.user, "profile", None),
        excluir_ids=usadas,
    )
    if url:
        trilha.cover_pexels_id = pexels_id_da_url(url)
        trilha.cover_url = baixar_para_media(url)
        trilha.save(update_fields=["cover_url", "cover_pexels_id", "atualizada_em"])
        messages.success(request, "Capa trocada — nova foto encontrada para o tema.")
    else:
        messages.info(
            request, "Não achei outra foto boa para este tema agora. Tente de novo em instantes."
        )
    return redirect("trilhas:detalhe", pk=pk)


@login_required
def estudar_agora(request):
    """Leva o usuário direto ao próximo tópico em andamento (trilha mais recente)."""
    candidatas = (
        request.user.trilhas.filter(ativa=True)
        .exclude(status=Trilha.Status.CONCLUIDA)
        .order_by("-atualizada_em")
    )
    for trilha in candidatas:
        alvo = trilha.proximo_topico
        if alvo:
            nivel, sub = alvo
            return redirect("trilhas:topico", nivel_pk=nivel.pk, ordem=sub.ordem)
    messages.info(request, "Você não tem tópicos em andamento. Comece uma nova trilha!")
    return redirect("dashboard")


# ---------------------------------------------------------------------------
# Mentor — percurso personalizado entre as trilhas
# ---------------------------------------------------------------------------


@login_required
def mentor(request):
    percurso = request.user.percursos.first()
    if percurso is None:
        erro = bloqueio_ia(request.user, tokens_estimados=8000)
        if erro:
            messages.error(request, erro)
            return redirect("dashboard")
        percurso = Percurso.objects.create(user=request.user, status=Percurso.Status.GERANDO)
        ai_tasks.task_gerar_percurso.delay(percurso.pk)
    return render(
        request,
        "trilhas/mentor.html",
        {
            "percurso": percurso,
            "passos": percurso.passos.select_related("nivel__trilha").all(),
        },
    )


@login_required
@require_POST
def mentor_atualizar(request):
    erro = bloqueio_ia(request.user, tokens_estimados=8000)
    if erro:
        messages.error(request, erro)
        return redirect("trilhas:mentor")
    percurso = Percurso.objects.create(user=request.user, status=Percurso.Status.GERANDO)
    ai_tasks.task_gerar_percurso.delay(percurso.pk)
    return redirect("trilhas:mentor")


@login_required
def percurso_status(request, pk):
    percurso = get_object_or_404(Percurso, pk=pk, user=request.user)
    return JsonResponse({"status": percurso.status, "erro": percurso.erro})


# ---------------------------------------------------------------------------
# Sugestões de novas trilhas — a IA propõe, o usuário aceita com um clique
# ---------------------------------------------------------------------------


def _trilhas_base_ctx(user):
    """Trilhas que podem servir de base para as sugestões (as com sumário)."""
    return (
        user.trilhas.filter(ativa=True)
        .exclude(
            status__in=[
                Trilha.Status.RASCUNHO,
                Trilha.Status.GERANDO_PERGUNTAS,
                Trilha.Status.AGUARDANDO_RESPOSTAS,
                Trilha.Status.GERANDO_SUMARIO,
                Trilha.Status.ERRO,
            ]
        )
        .order_by("-atualizada_em")
    )


@login_required
def sugestoes(request):
    """Página de Sugestões de trilhas: mostra a última rodada gerada, que fica
    salva aqui até o usuário pedir novas."""
    sessao = request.user.sessoes_sugestao.prefetch_related("sugestoes").first()
    if sessao is None:
        return redirect("trilhas:sugestoes_nova")
    return render(
        request,
        "trilhas/sugestoes.html",
        {
            "sessao": sessao,
            "sugestoes": sessao.sugestoes.all(),
        },
    )


@login_required
def sugestoes_nova(request):
    """Formulário para escolher quais trilhas a IA deve considerar e gerar
    uma nova rodada de sugestões."""
    candidatas = list(_trilhas_base_ctx(request.user))

    if request.method == "POST":
        if not candidatas:
            messages.info(request, "Crie ao menos uma trilha antes de pedir sugestões.")
            return redirect("dashboard")
        ids = {int(i) for i in request.POST.getlist("trilha") if i.isdigit()}
        escolhidas = [t for t in candidatas if t.pk in ids] or candidatas
        erro = bloqueio_ia(request.user, tokens_estimados=12000)
        if erro:
            messages.error(request, erro)
            return redirect("trilhas:sugestoes_nova")
        sessao = SessaoSugestao.objects.create(
            user=request.user, status=SessaoSugestao.Status.GERANDO
        )
        sessao.trilhas_base.set(escolhidas)
        ai_tasks.task_gerar_sugestoes.delay(sessao.pk)
        return redirect("trilhas:sugestoes")

    from itertools import groupby

    candidatas_ord = sorted(candidatas, key=lambda t: t.categoria_display)
    grupos_cat = [
        (cat, list(items))
        for cat, items in groupby(candidatas_ord, key=lambda t: t.categoria_display)
    ]
    return render(
        request, "trilhas/sugestoes_nova.html", {"candidatas": candidatas, "grupos_cat": grupos_cat}
    )


@login_required
def sugestoes_status(request, pk):
    sessao = get_object_or_404(SessaoSugestao, pk=pk, user=request.user)
    return JsonResponse({"status": sessao.status, "erro": sessao.erro})


@login_required
@require_POST
def sugestao_aceitar(request, pk):
    """Aceita uma sugestão: cria a trilha (com contexto rico) e segue o fluxo
    normal de geração de perguntas."""
    sugestao = get_object_or_404(TrilhaSugerida, pk=pk, sessao__user=request.user)
    if sugestao.trilha_id:
        return redirect("trilhas:perguntas", pk=sugestao.trilha_id)

    erro = bloqueio_ia(request.user, tokens_estimados=30000)
    if erro:
        messages.error(request, erro)
        return redirect("trilhas:sugestoes")

    # Criar uma trilha a partir de uma sugestão também custa 1 diamante.
    profile = getattr(request.user, "profile", None)
    if profile is None or not profile.gastar_diamante():
        messages.error(
            request,
            "Você está sem diamantes. Ganhe mais estudando: a cada "
            "10 níveis de XP (≈ concluir uma trilha) você recebe um novo diamante.",
        )
        return redirect("trilhas:sugestoes")

    trilha = Trilha.objects.create(
        user=request.user,
        tema_livre=sugestao.como_tema(),
        titulo=sugestao.titulo,
        status=Trilha.Status.GERANDO_PERGUNTAS,
        diamante_gasto=True,
    )
    sugestao.trilha = trilha
    sugestao.save(update_fields=["trilha"])
    ai_tasks.task_gerar_perguntas.delay(trilha.pk)
    return redirect("trilhas:perguntas", pk=trilha.pk)


# ---------------------------------------------------------------------------
# Criar trilha → dispara geração das perguntas
# ---------------------------------------------------------------------------

# Mínimo de palavras no tema: força uma frase com conceito/foco (não só
# "aprender python"), o que dá contexto melhor para a IA montar o plano.
MIN_PALAVRAS_TEMA = 5


@login_required
def trilha_criar(request):
    if request.method == "POST":
        tema = (request.POST.get("tema_livre") or "").strip()
        if not tema:
            messages.error(request, "Descreva o que você quer aprender.")
            return render(request, "trilhas/trilha_nova.html", {"tema_livre": tema})
        if len(tema.split()) < MIN_PALAVRAS_TEMA:
            messages.error(
                request,
                f"Descreva com mais detalhe: use pelo menos {MIN_PALAVRAS_TEMA} "
                "palavras com o conceito geral e o foco (ex.: para que você quer "
                "aprender, seu nível atual ou os assuntos de interesse).",
            )
            return render(request, "trilhas/trilha_nova.html", {"tema_livre": tema})
        erro = bloqueio_ia(request.user, tokens_estimados=30000)
        if erro:
            messages.error(request, erro)
            return redirect("dashboard")
        # Criar uma trilha custa 1 diamante. Débito atômico antes de criar.
        profile = getattr(request.user, "profile", None)
        if profile is None or not profile.gastar_diamante():
            messages.error(
                request,
                "Você está sem diamantes. Ganhe mais estudando: a cada "
                "10 níveis de XP (≈ concluir uma trilha) você recebe um novo diamante.",
            )
            return redirect("dashboard")
        trilha = Trilha.objects.create(
            user=request.user,
            tema_livre=tema,
            status=Trilha.Status.GERANDO_PERGUNTAS,
            diamante_gasto=True,
        )
        ai_tasks.task_gerar_perguntas.delay(trilha.pk)
        return redirect("trilhas:perguntas", pk=trilha.pk)
    return render(request, "trilhas/trilha_nova.html")


# ---------------------------------------------------------------------------
# Perguntas direcionadoras → dispara geração do sumário
# ---------------------------------------------------------------------------


@login_required
def perguntas(request, pk):
    trilha = get_object_or_404(Trilha, pk=pk, user=request.user)

    if request.method == "POST":
        if trilha.status != Trilha.Status.AGUARDANDO_RESPOSTAS:
            return redirect("trilhas:detalhe", pk=trilha.pk)
        for p in trilha.perguntas.all():
            p.resposta = (request.POST.get(f"pergunta_{p.pk}") or "").strip()
            p.save(update_fields=["resposta"])
        trilha.status = Trilha.Status.GERANDO_SUMARIO
        trilha.save(update_fields=["status", "atualizada_em"])
        ai_tasks.task_gerar_sumario.delay(trilha.pk)
        return redirect("trilhas:detalhe", pk=trilha.pk)

    # Já passou da fase de perguntas → vai para o detalhe.
    if trilha.status in (
        Trilha.Status.GERANDO_SUMARIO,
        Trilha.Status.SUMARIO_GERADO,
        Trilha.Status.EM_ANDAMENTO,
        Trilha.Status.CONCLUIDA,
    ):
        return redirect("trilhas:detalhe", pk=trilha.pk)

    return render(request, "trilhas/perguntas.html", {"trilha": trilha})


# ---------------------------------------------------------------------------
# Detalhe da trilha (mapa de níveis, progresso, títulos)
# ---------------------------------------------------------------------------


@login_required
def trilha_detalhe(request, pk):
    trilha = get_object_or_404(
        Trilha.objects.prefetch_related("niveis", "titulos__nivel"),
        pk=pk,
        user=request.user,
    )
    if trilha.status in (
        Trilha.Status.RASCUNHO,
        Trilha.Status.GERANDO_PERGUNTAS,
        Trilha.Status.AGUARDANDO_RESPOSTAS,
    ):
        return redirect("trilhas:perguntas", pk=trilha.pk)

    return render(
        request,
        "trilhas/trilha_detalhe.html",
        {
            "trilha": trilha,
            "certificado_enabled": getattr(settings, "CERTIFICADO_ENABLED", True),
        },
    )


@login_required
@require_POST
def trilha_renomear(request, pk):
    trilha = get_object_or_404(Trilha, pk=pk, user=request.user)
    titulo = request.POST.get("titulo", "").strip()
    if titulo:
        trilha.titulo = titulo
        trilha.save(update_fields=["titulo", "atualizada_em"])
    return redirect("trilhas:detalhe", pk=pk)


@login_required
@require_POST
def trilha_excluir(request, pk):
    trilha = get_object_or_404(Trilha, pk=pk, user=request.user)
    # Excluir dentro da janela (48h) devolve o diamante gasto na criação.
    reembolsa = trilha.reembolsavel
    profile = getattr(request.user, "profile", None)
    # Delete condicional (filtra por pk+user): num duplo-POST concorrente só um
    # request remove a linha de fato, então o diamante é devolvido uma única vez.
    deletados, _ = Trilha.objects.filter(pk=trilha.pk, user=request.user).delete()
    if deletados and reembolsa and profile is not None:
        profile.creditar_diamante()
        messages.success(request, "Trilha excluída — diamante devolvido.")
    else:
        messages.success(request, "Trilha excluída.")
    return redirect("dashboard")


@login_required
def trilha_status(request, pk):
    trilha = get_object_or_404(Trilha, pk=pk, user=request.user)
    return JsonResponse(
        {
            "status": trilha.status,
            "progresso_pct": trilha.progresso_pct,
            "erro": trilha.erro,
        }
    )


# ---------------------------------------------------------------------------
# Nível — visão geral (índice dos tópicos + progresso de leitura)
# ---------------------------------------------------------------------------


@login_required
def nivel_detalhe(request, pk):
    nivel = get_object_or_404(
        Nivel.objects.select_related("trilha").prefetch_related("subtopicos"),
        pk=pk,
        trilha__user=request.user,
    )
    if nivel.status == Nivel.Status.BLOQUEADO:
        messages.info(request, "Este nível ainda está bloqueado. Conclua os anteriores.")
        return redirect("trilhas:detalhe", pk=nivel.trilha_id)

    lista = getattr(nivel, "lista_exercicios", None)
    video_on = getattr(settings, "VIDEO_ENABLED", True)
    subs = list(nivel.subtopicos.all())
    return render(
        request,
        "trilhas/nivel.html",
        {
            "nivel": nivel,
            "trilha": nivel.trilha,
            "subtopicos": subs,
            "primeiro_nao_lido": nivel.primeiro_nao_lido,
            "exercicios_concluidos": bool(lista and lista.concluida),
            "avaliacao": nivel.avaliacoes.order_by("-criada_em").first(),
            "video_enabled": video_on,
            "video": getattr(nivel, "video", None) if video_on else None,
            # O vídeo cobre o nível inteiro: só faz sentido com algum tópico pronto.
            "video_pronto_para_gerar": any(
                s.status == Subtopico.Status.PRONTO and s.conteudo_md for s in subs
            ),
        },
    )


# ---------------------------------------------------------------------------
# Tópico — uma página por subtópico (geração sob demanda + pré-geração)
# ---------------------------------------------------------------------------


@login_required
def topico(request, nivel_pk, ordem):
    nivel = get_object_or_404(
        Nivel.objects.select_related("trilha"), pk=nivel_pk, trilha__user=request.user
    )
    if nivel.status == Nivel.Status.BLOQUEADO:
        messages.info(request, "Este nível ainda está bloqueado.")
        return redirect("trilhas:detalhe", pk=nivel.trilha_id)

    subs = list(nivel.subtopicos.all())
    if not subs:
        return redirect("trilhas:detalhe", pk=nivel.trilha_id)
    atual = next((s for s in subs if s.ordem == ordem), None)
    if atual is None:
        raise Http404("Tópico não encontrado.")
    idx = subs.index(atual)

    # Geração sob demanda deste tópico.
    if atual.status == Subtopico.Status.PENDENTE:
        atual.status = Subtopico.Status.GERANDO
        atual.save(update_fields=["status"])
        ai_tasks.task_gerar_subtopico.delay(atual.pk)
        atual.refresh_from_db()

    # Pronto: marca como lido (XP na 1ª vez) e pré-gera o próximo em background.
    ganhou_xp = False
    if atual.status == Subtopico.Status.PRONTO:
        if not atual.lido:
            ganhou_xp = True
            atual.lido = True
            atual.save(update_fields=["lido"])
            profile = getattr(request.user, "profile", None)
            if profile is not None:
                profile.registrar_atividade(profile.XP_TOPICO)
            if nivel.trilha.status == Trilha.Status.SUMARIO_GERADO:
                nivel.trilha.status = Trilha.Status.EM_ANDAMENTO
                nivel.trilha.save(update_fields=["status", "atualizada_em"])
        proximo_sub = subs[idx + 1] if idx + 1 < len(subs) else None
        if proximo_sub and proximo_sub.status == Subtopico.Status.PENDENTE:
            proximo_sub.status = Subtopico.Status.GERANDO
            proximo_sub.save(update_fields=["status"])
            ai_tasks.task_gerar_subtopico.delay(proximo_sub.pk)

        # No último tópico, já começa a preparar os exercícios em background.
        if atual.eh_ultimo:
            _pre_gerar_exercicios(nivel)

    return render(
        request,
        "trilhas/topico.html",
        {
            "nivel": nivel,
            "trilha": nivel.trilha,
            "subtopico": atual,
            "subtopicos": subs,
            "conteudo_html": render_subtopico(atual),
            "passo": idx + 1,
            "total": len(subs),
            "anterior": subs[idx - 1] if idx > 0 else None,
            "proximo": subs[idx + 1] if idx + 1 < len(subs) else None,
            "ganhou_xp": ganhou_xp,
            "xp_topico": getattr(getattr(request.user, "profile", None), "XP_TOPICO", 10),
            "salvos_indices": list(
                CardSalvo.objects.filter(user=request.user, subtopico=atual).values_list(
                    "indice", flat=True
                )
            ),
        },
    )


@login_required
def topico_status(request, pk):
    sub = get_object_or_404(Subtopico, pk=pk, nivel__trilha__user=request.user)
    return JsonResponse({"status": sub.status, "erro": sub.erro})


# ---------------------------------------------------------------------------
# Vídeo do tópico — slideshow narrado gerado sob demanda
# ---------------------------------------------------------------------------


@login_required
@require_POST
def video_gerar(request, pk):
    """Enfileira a geração do vídeo narrado com todo o conteúdo deste nível."""
    from . import tasks as trilhas_tasks

    nivel = get_object_or_404(
        Nivel.objects.select_related("trilha"), pk=pk, trilha__user=request.user
    )
    total = nivel.subtopicos.count()
    if not total:
        messages.error(request, "Este nível ainda não tem tópicos.")
        return redirect("trilhas:nivel", pk=nivel.pk)

    # A geração cobre o nível inteiro: o que faltar de conteúdo é escrito antes
    # do roteiro (ver video_pipeline), então a estimativa de tokens conta TODOS
    # os tópicos, não só os que já estão prontos.
    faltantes = (
        nivel.subtopicos.exclude(status=Subtopico.Status.PRONTO).count()
        + nivel.subtopicos.filter(conteudo_md="").count()
    )
    erro = bloqueio_ia(request.user, tokens_estimados=15000 * total + 20000 * faltantes)
    if erro:
        messages.error(request, erro)
        return redirect("trilhas:nivel", pk=nivel.pk)

    video, _ = VideoNivel.objects.get_or_create(nivel=nivel)
    # Idempotente: se já está gerando, não reenfileira.
    if video.status != VideoNivel.Status.GERANDO:
        video.status = VideoNivel.Status.GERANDO
        video.progresso_pct = 0
        video.etapa = "Na fila"
        video.iniciado_em = timezone.now()
        video.erro = ""
        video.save(
            update_fields=[
                "status",
                "progresso_pct",
                "etapa",
                "iniciado_em",
                "erro",
                "atualizado_em",
            ]
        )
        trilhas_tasks.task_gerar_video_nivel.delay(video.pk)

    return redirect("trilhas:nivel", pk=nivel.pk)


@login_required
def video_status(request, pk):
    video = get_object_or_404(
        VideoNivel,
        nivel_id=pk,
        nivel__trilha__user=request.user,
    )
    return JsonResponse(
        {
            "status": video.status,
            "progresso_pct": video.progresso_pct,
            "etapa": video.etapa,
            "decorrido_seg": video.decorrido_seg,
            "restante_seg": video.restante_seg,
            "url": video.arquivo,
            "erro": video.erro,
        }
    )


# ---------------------------------------------------------------------------
# Cards salvos — biblioteca pessoal de destaques (como salvar um post)
# ---------------------------------------------------------------------------


@login_required
def salvos(request):
    cards = list(request.user.cards_salvos.select_related("subtopico__nivel__trilha"))
    # Agrupa por trilha (mais recente primeiro dentro do grupo, já pela ordering).
    from collections import OrderedDict

    grupos = OrderedDict()
    for c in cards:
        grupos.setdefault(c.subtopico.nivel.trilha, []).append(c)
    return render(
        request,
        "trilhas/salvos.html",
        {
            "grupos": grupos.items(),
            "total": len(cards),
        },
    )


@login_required
@require_POST
def salvo_toggle(request):
    """Salva/remove um card de leitura (JSON). O HTML vem do próprio render."""
    sub = get_object_or_404(
        Subtopico,
        pk=request.POST.get("subtopico"),
        nivel__trilha__user=request.user,
    )
    try:
        indice = int(request.POST.get("indice", ""))
    except ValueError:
        return JsonResponse({"erro": "Índice inválido."}, status=400)

    existente = CardSalvo.objects.filter(user=request.user, subtopico=sub, indice=indice).first()
    if existente:
        existente.delete()
        return JsonResponse({"salvo": False})

    # O cliente envia o innerHTML do card, mas o endpoint aceita qualquer POST:
    # sanitiza com a mesma allowlist do renderizador antes de persistir.
    from .mdrender import _sanitizar

    html = _sanitizar((request.POST.get("html") or "").strip())
    if not html:
        return JsonResponse({"erro": "Card vazio."}, status=400)
    CardSalvo.objects.create(
        user=request.user,
        subtopico=sub,
        indice=indice,
        html=html[:20000],
    )
    return JsonResponse({"salvo": True})


# ---------------------------------------------------------------------------
# Certificado (PDF) ao concluir a trilha
# ---------------------------------------------------------------------------


@login_required
def certificado(request, pk):
    trilha = get_object_or_404(Trilha.objects.prefetch_related("titulos"), pk=pk, user=request.user)
    if not trilha.concluida:
        messages.info(request, "Conclua todos os níveis para emitir o certificado.")
        return redirect("trilhas:detalhe", pk=trilha.pk)

    html = render_to_string(
        "trilhas/certificado.html",
        {
            "trilha": trilha,
            "aluno": request.user.get_full_name()
            or request.user.get_short_name()
            or request.user.username,
            "titulos": trilha.titulos.all(),
            "data": timezone.localdate(),
        },
        request=request,
    )

    try:
        from weasyprint import HTML
    except Exception:
        messages.info(request, "PDF indisponível neste servidor (weasyprint). Exibindo versão web.")
        return HttpResponse(html)

    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="certificado-{trilha.pk}.pdf"'
    return resp
