from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.quota import bloqueio_ia
from ai import tasks as ai_tasks
from trilhas.mdrender import render_md
from trilhas.models import Nivel, Trilha

from .models import (
    Avaliacao, Exercicio, ListaExercicios, QuestaoRevisao, Resposta, Revisao,
)

_md = render_md
_LETRAS_ALTERNATIVAS = frozenset({'A', 'B', 'C', 'D'})


@login_required
@require_POST
def avaliacao_iniciar(request, nivel_pk):
    nivel = get_object_or_404(Nivel, pk=nivel_pk, trilha__user=request.user)
    if nivel.status == Nivel.Status.BLOQUEADO:
        return redirect('trilhas:detalhe', pk=nivel.trilha_id)
    # Nível já aprovado não se reavalia: gastaria geração de IA à toa e abriria
    # brecha para reganhar XP de aprovação. Para revisar, use a revisão espaçada.
    if nivel.status == Nivel.Status.APROVADO:
        messages.info(request, 'Você já foi aprovado neste nível. Use a revisão para praticar.')
        return redirect('trilhas:nivel', pk=nivel.pk)
    # Pré-requisito: ler todos os tópicos. Os exercícios de prática são
    # opcionais — aquecem, mas não travam a avaliação.
    if not nivel.conteudo_lido:
        messages.info(request, 'Leia todos os tópicos do nível antes de fazer a avaliação.')
        return redirect('trilhas:nivel', pk=nivel.pk)

    ultima = nivel.avaliacoes.order_by('-criada_em').first()
    if ultima and ultima.status in (
        Avaliacao.Status.GERANDO, Avaliacao.Status.PRONTA, Avaliacao.Status.CORRIGINDO,
    ):
        return redirect('avaliacoes:detalhe', pk=ultima.pk)

    erro = bloqueio_ia(request.user, tokens_estimados=15000)
    if erro:
        messages.error(request, erro)
        return redirect('trilhas:nivel', pk=nivel.pk)
    tentativa = (ultima.tentativa + 1) if ultima else 1
    avaliacao = Avaliacao.objects.create(
        nivel=nivel, tentativa=tentativa, status=Avaliacao.Status.GERANDO,
    )
    ai_tasks.task_gerar_avaliacao.delay(avaliacao.pk)
    return redirect('avaliacoes:detalhe', pk=avaliacao.pk)


@login_required
def avaliacao_detalhe(request, pk):
    avaliacao = get_object_or_404(
        Avaliacao.objects.select_related('nivel__trilha')
        .prefetch_related('questoes'),
        pk=pk, nivel__trilha__user=request.user,
    )
    if avaliacao.status == Avaliacao.Status.CORRIGIDA:
        return redirect('avaliacoes:resultado', pk=avaliacao.pk)

    return render(request, 'avaliacoes/avaliacao.html', {
        'avaliacao': avaliacao,
        'nivel': avaliacao.nivel,
        'questoes': avaliacao.questoes.all(),
    })


@login_required
@require_POST
def avaliacao_submeter(request, pk):
    avaliacao = get_object_or_404(
        Avaliacao.objects.prefetch_related('questoes'),
        pk=pk, nivel__trilha__user=request.user,
    )
    if avaliacao.status != Avaliacao.Status.PRONTA:
        return redirect('avaliacoes:detalhe', pk=avaliacao.pk)

    respostas = []
    for questao in avaliacao.questoes.all():
        alt = (request.POST.get(f'alt_{questao.pk}') or '').strip()
        if alt not in _LETRAS_ALTERNATIVAS:
            messages.error(request, 'Escolha uma alternativa válida em cada questão.')
            return redirect('avaliacoes:detalhe', pk=avaliacao.pk)
        respostas.append((questao, alt))

    for questao, alt in respostas:
        Resposta.objects.update_or_create(
            questao=questao,
            defaults={'alternativa_escolhida': alt},
        )

    avaliacao.status = Avaliacao.Status.CORRIGINDO
    avaliacao.save(update_fields=['status'])
    ai_tasks.task_corrigir_avaliacao.delay(avaliacao.pk)

    profile = getattr(request.user, 'profile', None)
    if profile is not None:
        profile.registrar_atividade(profile.XP_AVALIACAO)

    return redirect('avaliacoes:detalhe', pk=avaliacao.pk)


@login_required
def avaliacao_resultado(request, pk):
    avaliacao = get_object_or_404(
        Avaliacao.objects.select_related('nivel__trilha')
        .prefetch_related('questoes__resposta'),
        pk=pk, nivel__trilha__user=request.user,
    )
    itens = []
    for q in avaliacao.questoes.all():
        resp = getattr(q, 'resposta', None)
        # Nas questões erradas o aluno precisa ver o que escolheu e qual era a
        # resposta certa, com o texto — a letra sozinha não ensina nada.
        alternativas = {a.get('letra'): a.get('texto', '') for a in q.alternativas}
        escolhida = resp.alternativa_escolhida if resp else ''
        errou = bool(resp and resp.nota is not None and resp.nota < 6)
        itens.append({
            'questao': q,
            'enunciado_html': _md(q.enunciado_md),
            'resposta': resp,
            'feedback_html': _md(resp.feedback_md) if resp else '',
            'errou': errou,
            'escolhida_letra': escolhida,
            'escolhida_texto': alternativas.get(escolhida, ''),
            'correta_letra': q.gabarito,
            'correta_texto': alternativas.get(q.gabarito, ''),
        })
    titulo = getattr(avaliacao.nivel, 'titulo_conquistado', None)
    return render(request, 'avaliacoes/resultado.html', {
        'avaliacao': avaliacao,
        'nivel': avaliacao.nivel,
        'trilha': avaliacao.nivel.trilha,
        'itens': itens,
        'titulo_conquistado': titulo,
    })


@login_required
def avaliacao_status(request, pk):
    avaliacao = get_object_or_404(Avaliacao, pk=pk, nivel__trilha__user=request.user)
    return JsonResponse({'status': avaliacao.status, 'erro': avaliacao.erro})


# ---------------------------------------------------------------------------
# Exercícios de prática (sem nota) — geração sob demanda + feedback imediato
# ---------------------------------------------------------------------------

@login_required
def exercicios(request, nivel_pk):
    nivel = get_object_or_404(
        Nivel.objects.select_related('trilha'), pk=nivel_pk, trilha__user=request.user
    )
    if nivel.status == Nivel.Status.BLOQUEADO:
        messages.info(request, 'Este nível ainda está bloqueado.')
        return redirect('trilhas:detalhe', pk=nivel.trilha_id)

    # Só libera os exercícios depois de ler todos os tópicos do nível.
    if not nivel.conteudo_lido:
        messages.info(request, 'Leia todos os tópicos do nível antes de praticar.')
        return redirect('trilhas:nivel', pk=nivel.pk)

    lista, created = ListaExercicios.objects.get_or_create(nivel=nivel)
    # Primeira visita (ou erro anterior): dispara a geração.
    if created or lista.status == ListaExercicios.Status.ERRO:
        lista.status = ListaExercicios.Status.GERANDO
        lista.erro = ''
        lista.save(update_fields=['status', 'erro'])
        ai_tasks.task_gerar_exercicios.delay(lista.pk)
        lista.refresh_from_db()

    itens = []
    for ex in lista.exercicios.all():
        itens.append({
            'ex': ex,
            'enunciado_html': _md(ex.enunciado_md),
            'explicacao_html': _md(ex.explicacao_md),
            'feedback_html': _md(ex.feedback_md) if ex.feedback_md else '',
        })
    return render(request, 'avaliacoes/exercicios.html', {
        'nivel': nivel, 'trilha': nivel.trilha, 'lista': lista, 'itens': itens,
    })


@login_required
def exercicios_status(request, pk):
    lista = get_object_or_404(ListaExercicios, pk=pk, nivel__trilha__user=request.user)
    return JsonResponse({'status': lista.status, 'erro': lista.erro})


@login_required
@require_POST
def exercicio_verificar(request, pk):
    """Verifica um exercício objetivo e devolve o feedback (JSON). Prática."""
    ex = get_object_or_404(
        Exercicio.objects.select_related('lista__nivel__trilha__user'),
        pk=pk, lista__nivel__trilha__user=request.user,
    )

    # Uma vez respondido, a resposta é definitiva — não pode ser alterada.
    if ex.respondido_em is not None:
        return JsonResponse({
            'ja_respondido': True,
            'correto': (ex.nota or 0) >= 10,
            'gabarito': (ex.gabarito or '').strip().upper(),
            'feedback_html': _md(ex.feedback_md) if ex.feedback_md else '',
            'concluida': ex.lista.concluida,
        }, status=409)

    escolhida = (request.POST.get('alternativa') or '').strip().upper()
    if escolhida not in _LETRAS_ALTERNATIVAS:
        return JsonResponse({'erro': 'Alternativa inválida.'}, status=400)
    correta = (ex.gabarito or '').strip().upper()
    acertou = bool(escolhida) and escolhida == correta
    ex.alternativa_escolhida = escolhida
    ex.nota = 10.0 if acertou else 0.0
    ex.feedback_md = ex.explicacao_md
    ex.respondido_em = timezone.now()
    ex.save(update_fields=['alternativa_escolhida', 'nota', 'feedback_md', 'respondido_em'])

    xp_ganho = 0
    profile = getattr(request.user, 'profile', None)
    if profile is not None:
        xp_ganho = profile.XP_EXERCICIO
        profile.registrar_atividade(xp_ganho)

    return JsonResponse({
        'correto': acertou, 'gabarito': correta,
        'feedback_html': _md(ex.explicacao_md),
        'concluida': ex.lista.concluida,
        'xp_ganho': xp_ganho,
    })


# ---------------------------------------------------------------------------
# Flashcard de revisão rápida (feed do dashboard) — autoavaliação direto no SM-2
# ---------------------------------------------------------------------------

# Qualidade SM-2 por autoavaliação: lembrar bem avança o intervalo; pedir
# revisão reseta (q < 3) e reagenda para amanhã.
QUALIDADE_FLASHCARD = {'lembrei': 4, 'rever': 2}


@login_required
@require_POST
def revisao_rapida(request, nivel_pk):
    nivel = get_object_or_404(
        Nivel, pk=nivel_pk, trilha__user=request.user,
        trilha__ativa=True, status=Nivel.Status.APROVADO,
    )
    qualidade = QUALIDADE_FLASHCARD.get(request.POST.get('resposta'))
    if qualidade is None:
        return JsonResponse({'erro': 'Resposta inválida.'}, status=400)

    # XP só quando a revisão estava REALMENTE devida: assim que se revisa, o
    # SM-2 reagenda para o futuro e o cartão sai do "devido", então repetir o
    # POST não rende mais XP (fecha o farm de XP/diamante deste endpoint).
    devida = nivel.revisao_devida
    nivel.registrar_revisao(qualidade)

    profile = getattr(request.user, 'profile', None)
    xp_ganho = 0
    if profile is not None and devida:
        xp_ganho = profile.XP_EXERCICIO
        profile.registrar_atividade(xp_ganho)

    dias = max(1, (nivel.revisao_proxima - timezone.localdate()).days)
    return JsonResponse({
        'ok': True,
        'proxima_em_dias': dias,
        'xp_ganho': xp_ganho,
        'streak': profile.streak_dias if profile else None,
    })


# ---------------------------------------------------------------------------
# Revisão espaçada — quiz misto sobre os níveis já concluídos (várias trilhas)
# ---------------------------------------------------------------------------

@login_required
@require_POST
def revisar_iniciar(request):
    tem_aprovados = Nivel.objects.filter(
        trilha__user=request.user, trilha__ativa=True, status=Nivel.Status.APROVADO
    ).exists()
    if not tem_aprovados:
        messages.info(request, 'Conclua ao menos um nível para liberar a revisão.')
        return redirect('dashboard')

    # "Nova revisão" (botão explícito) força um deck novo; a entrada normal
    # (ex.: quiz do dashboard) retoma uma revisão em andamento.
    forcar_nova = request.POST.get('nova') == '1'
    ultima = request.user.revisoes.first()
    # Nunca duplica uma geração em curso (evita disparar a IA duas vezes).
    if ultima and ultima.status == Revisao.Status.GERANDO:
        return redirect('avaliacoes:revisao', pk=ultima.pk)
    # Revisão pronta mas não concluída: retoma, a menos que se peça uma nova.
    if ultima and ultima.status == Revisao.Status.PRONTA and not ultima.concluida:
        if not forcar_nova:
            return redirect('avaliacoes:revisao', pk=ultima.pk)
        # Ao pedir uma nova, descarta a incompleta para não acumular órfãs.
        ultima.delete()

    erro = bloqueio_ia(request.user, tokens_estimados=15000)
    if erro:
        messages.error(request, erro)
        return redirect('dashboard')
    revisao = Revisao.objects.create(user=request.user, status=Revisao.Status.GERANDO)
    ai_tasks.task_gerar_revisao.delay(revisao.pk)
    return redirect('avaliacoes:revisao', pk=revisao.pk)


@login_required
@require_POST
def revisar_trilha_iniciar(request, trilha_pk):
    trilha = get_object_or_404(Trilha, pk=trilha_pk, user=request.user)
    tem_aprovados = Nivel.objects.filter(
        trilha=trilha, status=Nivel.Status.APROVADO
    ).exists()
    if not tem_aprovados:
        messages.info(request, 'Conclua ao menos um nível desta trilha para revisar.')
        return redirect('trilhas:detalhe', pk=trilha_pk)
    erro = bloqueio_ia(request.user, tokens_estimados=15000)
    if erro:
        messages.error(request, erro)
        return redirect('trilhas:detalhe', pk=trilha_pk)
    revisao = Revisao.objects.create(user=request.user, status=Revisao.Status.GERANDO)
    ai_tasks.task_gerar_revisao.delay(revisao.pk, trilha_id=trilha.pk)
    return redirect('avaliacoes:revisao', pk=revisao.pk)


@login_required
def revisao_detalhe(request, pk):
    revisao = get_object_or_404(
        Revisao.objects.prefetch_related('questoes'), pk=pk, user=request.user
    )
    itens = []
    for q in revisao.questoes.all():
        itens.append({
            'q': q,
            'enunciado_html': _md(q.enunciado_md),
            'explicacao_html': _md(q.explicacao_md),
        })
    return render(request, 'avaliacoes/revisao.html', {
        'revisao': revisao, 'itens': itens,
    })


@login_required
def revisao_status(request, pk):
    revisao = get_object_or_404(Revisao, pk=pk, user=request.user)
    return JsonResponse({'status': revisao.status, 'erro': revisao.erro})


@login_required
@require_POST
def questao_revisao_verificar(request, pk):
    q = get_object_or_404(QuestaoRevisao, pk=pk, revisao__user=request.user)
    if q.respondido_em is not None:
        return JsonResponse({
            'ja_respondido': True,
            'correto': (q.nota or 0) >= 10,
            'gabarito': (q.gabarito or '').strip().upper(),
            'feedback_html': _md(q.explicacao_md),
            'concluida': q.revisao.concluida,
        }, status=409)

    escolhida = (request.POST.get('alternativa') or '').strip().upper()
    if escolhida not in _LETRAS_ALTERNATIVAS:
        return JsonResponse({'erro': 'Alternativa inválida.'}, status=400)
    correta = (q.gabarito or '').strip().upper()
    acertou = bool(escolhida) and escolhida == correta
    q.alternativa_escolhida = escolhida
    q.nota = 10.0 if acertou else 0.0
    q.respondido_em = timezone.now()
    q.save(update_fields=['alternativa_escolhida', 'nota', 'respondido_em'])

    xp_ganho = 0
    profile = getattr(request.user, 'profile', None)
    if profile is not None:
        xp_ganho = profile.XP_EXERCICIO
        profile.registrar_atividade(xp_ganho)

    # Ao concluir a revisão, reagenda a repetição espaçada de cada nível.
    concluida = q.revisao.concluida
    if concluida:
        from .spaced import aplicar_sm2
        aplicar_sm2(q.revisao)

    return JsonResponse({
        'correto': acertou, 'gabarito': correta,
        'feedback_html': _md(q.explicacao_md),
        'concluida': concluida,
        'xp_ganho': xp_ganho,
    })
