from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ai import tasks as ai_tasks

from .mdrender import render_md
from .models import Nivel, PerguntaDirecionadora, Trilha


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
# Nível — leitura do conteúdo (geração sob demanda)
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

    # Primeira visita: dispara a geração do conteúdo sob demanda.
    if nivel.status == Nivel.Status.DISPONIVEL:
        nivel.status = Nivel.Status.CONTEUDO_GERANDO
        nivel.save(update_fields=['status', 'atualizado_em'])
        ai_tasks.task_gerar_conteudo_nivel.delay(nivel.pk)
        nivel.refresh_from_db()

    avaliacao = nivel.avaliacoes.order_by('-criada_em').first()
    contexto = {
        'nivel': nivel,
        'trilha': nivel.trilha,
        'conteudo_html': render_md(nivel.conteudo_md) if nivel.conteudo_md else '',
        'avaliacao': avaliacao,
    }
    return render(request, 'trilhas/nivel.html', contexto)


@login_required
def nivel_status(request, pk):
    nivel = get_object_or_404(Nivel, pk=pk, trilha__user=request.user)
    return JsonResponse({'status': nivel.status, 'erro': nivel.erro})
