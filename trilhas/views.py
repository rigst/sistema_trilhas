from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from ai import tasks as ai_tasks

from .mdrender import render_md
from .models import Nivel, PerguntaDirecionadora, Subtopico, Trilha


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    trilhas = (
        request.user.trilhas
        .prefetch_related('niveis', 'titulos')
        .all()
    )
    return render(request, 'trilhas/dashboard.html', {'trilhas': trilhas})


# ---------------------------------------------------------------------------
# Criar trilha → dispara geração das perguntas
# ---------------------------------------------------------------------------

@login_required
def trilha_criar(request):
    if request.method == 'POST':
        tema = (request.POST.get('tema_livre') or '').strip()
        if not tema:
            messages.error(request, 'Descreva o que você quer aprender.')
            return render(request, 'trilhas/trilha_nova.html')
        trilha = Trilha.objects.create(
            user=request.user,
            tema_livre=tema,
            status=Trilha.Status.GERANDO_PERGUNTAS,
        )
        ai_tasks.task_gerar_perguntas.delay(trilha.pk)
        return redirect('trilhas:perguntas', pk=trilha.pk)
    return render(request, 'trilhas/trilha_nova.html')


# ---------------------------------------------------------------------------
# Perguntas direcionadoras → dispara geração do sumário
# ---------------------------------------------------------------------------

@login_required
def perguntas(request, pk):
    trilha = get_object_or_404(Trilha, pk=pk, user=request.user)

    if request.method == 'POST':
        if trilha.status != Trilha.Status.AGUARDANDO_RESPOSTAS:
            return redirect('trilhas:detalhe', pk=trilha.pk)
        for p in trilha.perguntas.all():
            p.resposta = (request.POST.get(f'pergunta_{p.pk}') or '').strip()
            p.save(update_fields=['resposta'])
        trilha.status = Trilha.Status.GERANDO_SUMARIO
        trilha.save(update_fields=['status', 'atualizada_em'])
        ai_tasks.task_gerar_sumario.delay(trilha.pk)
        return redirect('trilhas:detalhe', pk=trilha.pk)

    # Já passou da fase de perguntas → vai para o detalhe.
    if trilha.status in (
        Trilha.Status.GERANDO_SUMARIO, Trilha.Status.SUMARIO_GERADO,
        Trilha.Status.EM_ANDAMENTO, Trilha.Status.CONCLUIDA,
    ):
        return redirect('trilhas:detalhe', pk=trilha.pk)

    return render(request, 'trilhas/perguntas.html', {'trilha': trilha})


# ---------------------------------------------------------------------------
# Detalhe da trilha (mapa de níveis, progresso, títulos)
# ---------------------------------------------------------------------------

@login_required
def trilha_detalhe(request, pk):
    trilha = get_object_or_404(
        Trilha.objects.prefetch_related('niveis', 'titulos'), pk=pk, user=request.user
    )
    if trilha.status in (
        Trilha.Status.RASCUNHO, Trilha.Status.GERANDO_PERGUNTAS,
        Trilha.Status.AGUARDANDO_RESPOSTAS,
    ):
        return redirect('trilhas:perguntas', pk=trilha.pk)

    return render(request, 'trilhas/trilha_detalhe.html', {'trilha': trilha})


@login_required
@require_POST
def trilha_excluir(request, pk):
    trilha = get_object_or_404(Trilha, pk=pk, user=request.user)
    trilha.delete()
    messages.success(request, 'Trilha excluída.')
    return redirect('dashboard')


@login_required
def trilha_status(request, pk):
    trilha = get_object_or_404(Trilha, pk=pk, user=request.user)
    return JsonResponse({
        'status': trilha.status,
        'progresso_pct': trilha.progresso_pct,
        'erro': trilha.erro,
    })


# ---------------------------------------------------------------------------
# Nível — visão geral (índice dos tópicos + progresso de leitura)
# ---------------------------------------------------------------------------

@login_required
def nivel_detalhe(request, pk):
    nivel = get_object_or_404(
        Nivel.objects.select_related('trilha').prefetch_related('subtopicos'),
        pk=pk, trilha__user=request.user,
    )
    if nivel.status == Nivel.Status.BLOQUEADO:
        messages.info(request, 'Este nível ainda está bloqueado. Conclua os anteriores.')
        return redirect('trilhas:detalhe', pk=nivel.trilha_id)

    lista = getattr(nivel, 'lista_exercicios', None)
    return render(request, 'trilhas/nivel.html', {
        'nivel': nivel,
        'trilha': nivel.trilha,
        'subtopicos': nivel.subtopicos.all(),
        'primeiro_nao_lido': nivel.primeiro_nao_lido,
        'exercicios_concluidos': bool(lista and lista.concluida),
        'avaliacao': nivel.avaliacoes.order_by('-criada_em').first(),
    })


# ---------------------------------------------------------------------------
# Tópico — uma página por subtópico (geração sob demanda + pré-geração)
# ---------------------------------------------------------------------------

@login_required
def topico(request, nivel_pk, ordem):
    nivel = get_object_or_404(
        Nivel.objects.select_related('trilha'), pk=nivel_pk, trilha__user=request.user
    )
    if nivel.status == Nivel.Status.BLOQUEADO:
        messages.info(request, 'Este nível ainda está bloqueado.')
        return redirect('trilhas:detalhe', pk=nivel.trilha_id)

    subs = list(nivel.subtopicos.all())
    if not subs:
        return redirect('trilhas:detalhe', pk=nivel.trilha_id)
    atual = next((s for s in subs if s.ordem == ordem), None)
    if atual is None:
        raise Http404('Tópico não encontrado.')
    idx = subs.index(atual)

    # Geração sob demanda deste tópico.
    if atual.status == Subtopico.Status.PENDENTE:
        atual.status = Subtopico.Status.GERANDO
        atual.save(update_fields=['status'])
        ai_tasks.task_gerar_subtopico.delay(atual.pk)
        atual.refresh_from_db()

    # Pronto: marca como lido (XP na 1ª vez) e pré-gera o próximo em background.
    if atual.status == Subtopico.Status.PRONTO:
        if not atual.lido:
            atual.lido = True
            atual.save(update_fields=['lido'])
            profile = getattr(request.user, 'profile', None)
            if profile is not None:
                profile.registrar_atividade(profile.XP_TOPICO)
            if nivel.trilha.status == Trilha.Status.SUMARIO_GERADO:
                nivel.trilha.status = Trilha.Status.EM_ANDAMENTO
                nivel.trilha.save(update_fields=['status', 'atualizada_em'])
        proximo_sub = subs[idx + 1] if idx + 1 < len(subs) else None
        if proximo_sub and proximo_sub.status == Subtopico.Status.PENDENTE:
            proximo_sub.status = Subtopico.Status.GERANDO
            proximo_sub.save(update_fields=['status'])
            ai_tasks.task_gerar_subtopico.delay(proximo_sub.pk)

    return render(request, 'trilhas/topico.html', {
        'nivel': nivel,
        'trilha': nivel.trilha,
        'subtopico': atual,
        'subtopicos': subs,
        'conteudo_html': render_md(atual.conteudo_md) if atual.conteudo_md else '',
        'passo': idx + 1,
        'total': len(subs),
        'anterior': subs[idx - 1] if idx > 0 else None,
        'proximo': subs[idx + 1] if idx + 1 < len(subs) else None,
    })


@login_required
def topico_status(request, pk):
    sub = get_object_or_404(Subtopico, pk=pk, nivel__trilha__user=request.user)
    return JsonResponse({'status': sub.status, 'erro': sub.erro})


# ---------------------------------------------------------------------------
# Certificado (PDF) ao concluir a trilha
# ---------------------------------------------------------------------------

@login_required
def certificado(request, pk):
    trilha = get_object_or_404(
        Trilha.objects.prefetch_related('titulos'), pk=pk, user=request.user
    )
    if not trilha.concluida:
        messages.info(request, 'Conclua todos os níveis para emitir o certificado.')
        return redirect('trilhas:detalhe', pk=trilha.pk)

    html = render_to_string('trilhas/certificado.html', {
        'trilha': trilha,
        'aluno': request.user.get_full_name() or request.user.get_short_name() or request.user.username,
        'titulos': trilha.titulos.all(),
        'data': timezone.localdate(),
    }, request=request)

    try:
        from weasyprint import HTML
    except Exception:  # noqa: BLE001
        messages.info(request, 'PDF indisponível neste servidor (weasyprint). Exibindo versão web.')
        return HttpResponse(html)

    pdf = HTML(string=html, base_url=request.build_absolute_uri('/')).write_pdf()
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="certificado-{trilha.pk}.pdf"'
    return resp
