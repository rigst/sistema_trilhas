from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ai import services as ai_services
from ai import tasks as ai_tasks
from trilhas.mdrender import render_md
from trilhas.models import Nivel

from .models import Avaliacao, Exercicio, ListaExercicios, Resposta

_md = render_md


@login_required
@require_POST
def avaliacao_iniciar(request, nivel_pk):
    nivel = get_object_or_404(Nivel, pk=nivel_pk, trilha__user=request.user)
    if nivel.status not in (Nivel.Status.CONTEUDO_PRONTO, Nivel.Status.APROVADO):
        messages.info(request, 'Estude o conteúdo do nível antes de fazer a avaliação.')
        return redirect('trilhas:nivel', pk=nivel.pk)

    ultima = nivel.avaliacoes.order_by('-criada_em').first()
    if ultima and ultima.status in (
        Avaliacao.Status.GERANDO, Avaliacao.Status.PRONTA, Avaliacao.Status.CORRIGINDO,
    ):
        return redirect('avaliacoes:detalhe', pk=ultima.pk)

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

    for questao in avaliacao.questoes.all():
        alt = (request.POST.get(f'alt_{questao.pk}') or '').strip()
        texto = (request.POST.get(f'texto_{questao.pk}') or '').strip()
        Resposta.objects.update_or_create(
            questao=questao,
            defaults={'alternativa_escolhida': alt, 'resposta_texto': texto},
        )

    avaliacao.status = Avaliacao.Status.CORRIGINDO
    avaliacao.save(update_fields=['status'])
    ai_tasks.task_corrigir_avaliacao.delay(avaliacao.pk)
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
        itens.append({
            'questao': q,
            'enunciado_html': _md(q.enunciado_md),
            'resposta': resp,
            'feedback_html': _md(resp.feedback_md) if resp else '',
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
    """Verifica um exercício e devolve o feedback (JSON). Prática — não afeta nota."""
    ex = get_object_or_404(
        Exercicio.objects.select_related('lista__nivel__trilha__user'),
        pk=pk, lista__nivel__trilha__user=request.user,
    )
    profile = getattr(request.user, 'profile', None)

    if ex.tipo == Exercicio.Tipo.OBJETIVA:
        escolhida = (request.POST.get('alternativa') or '').strip().upper()
        correta = (ex.gabarito or '').strip().upper()
        acertou = bool(escolhida) and escolhida == correta
        ex.alternativa_escolhida = escolhida
        ex.nota = 10.0 if acertou else 0.0
        ex.feedback_md = ex.explicacao_md
        ex.respondido_em = timezone.now()
        ex.save(update_fields=['alternativa_escolhida', 'nota', 'feedback_md', 'respondido_em'])
        return JsonResponse({
            'tipo': 'objetiva', 'correto': acertou, 'gabarito': correta,
            'feedback_html': _md(ex.explicacao_md),
        })

    # Dissertativa: feedback da IA (Sonnet), sem impacto em progressão.
    texto = (request.POST.get('resposta') or '').strip()
    try:
        nota, feedback = ai_services.verificar_exercicio_dissertativa(ex, texto, profile)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({'erro': str(exc)[:300]}, status=502)
    ex.resposta_texto = texto
    ex.nota = nota
    ex.feedback_md = feedback
    ex.respondido_em = timezone.now()
    ex.save(update_fields=['resposta_texto', 'nota', 'feedback_md', 'respondido_em'])
    return JsonResponse({
        'tipo': 'dissertativa', 'nota': nota,
        'feedback_html': _md(feedback),
        'gabarito_html': _md(ex.gabarito),
    })
