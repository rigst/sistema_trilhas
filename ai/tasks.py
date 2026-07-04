"""Tasks Celery para a geração assíncrona de conteúdo e correção com IA."""

from celery import shared_task
from django.utils import timezone

from . import services


def _profile(user):
    return getattr(user, 'profile', None)


def _pre_gerar_primeiro_topico(trilha):
    """Assim que a trilha é criada, já deixa pronto o 1º tópico do nível 1."""
    from trilhas.models import Nivel, Subtopico

    nivel = trilha.niveis.order_by('ordem').first()
    if nivel is None:
        return
    sub = nivel.subtopicos.order_by('ordem').first()
    if sub is None or sub.status != Subtopico.Status.PENDENTE:
        return
    sub.status = Subtopico.Status.GERANDO
    sub.save(update_fields=['status'])
    task_gerar_subtopico.delay(sub.pk)


@shared_task
def task_gerar_perguntas(trilha_id):
    from trilhas.models import Trilha

    try:
        trilha = Trilha.objects.select_related('user').get(pk=trilha_id)
    except Trilha.DoesNotExist:
        return 'trilha inexistente'
    try:
        services.gerar_perguntas_direcionadoras(trilha, _profile(trilha.user))
        trilha.status = Trilha.Status.AGUARDANDO_RESPOSTAS
        trilha.erro = ''
        trilha.save(update_fields=['status', 'erro', 'atualizada_em'])
    except Exception as exc:  # noqa: BLE001
        trilha.status = Trilha.Status.ERRO
        trilha.erro = str(exc)[:2000]
        trilha.save(update_fields=['status', 'erro', 'atualizada_em'])
        raise
    return f'perguntas geradas para trilha {trilha_id}'


@shared_task
def task_gerar_sumario(trilha_id):
    from trilhas.models import Trilha

    try:
        trilha = Trilha.objects.select_related('user').get(pk=trilha_id)
    except Trilha.DoesNotExist:
        return 'trilha inexistente'
    try:
        services.gerar_sumario(trilha, _profile(trilha.user))
        trilha.status = Trilha.Status.SUMARIO_GERADO
        trilha.erro = ''
        trilha.save(update_fields=['status', 'erro', 'atualizada_em'])
        _pre_gerar_primeiro_topico(trilha)
    except Exception as exc:  # noqa: BLE001
        trilha.status = Trilha.Status.ERRO
        trilha.erro = str(exc)[:2000]
        trilha.save(update_fields=['status', 'erro', 'atualizada_em'])
        raise
    return f'sumário gerado para trilha {trilha_id}'


@shared_task
def task_gerar_subtopico(subtopico_id):
    from trilhas.models import Subtopico

    try:
        sub = Subtopico.objects.select_related('nivel__trilha__user').get(pk=subtopico_id)
    except Subtopico.DoesNotExist:
        return 'subtópico inexistente'
    # Evita retrabalho (ex.: pré-geração já disparada e concluída).
    if sub.status == Subtopico.Status.PRONTO and sub.conteudo_md:
        return f'subtópico {subtopico_id} já pronto'
    try:
        texto = services.gerar_conteudo_subtopico(sub, _profile(sub.nivel.trilha.user))
        sub.conteudo_md = texto
        sub.status = Subtopico.Status.PRONTO
        sub.gerado_em = timezone.now()
        sub.erro = ''
        sub.save(update_fields=['conteudo_md', 'status', 'gerado_em', 'erro'])
    except Exception as exc:  # noqa: BLE001
        sub.status = Subtopico.Status.ERRO
        sub.erro = str(exc)[:2000]
        sub.save(update_fields=['status', 'erro'])
        raise
    return f'subtópico {subtopico_id} gerado'


@shared_task
def task_gerar_avaliacao(avaliacao_id):
    from avaliacoes.models import Avaliacao

    try:
        avaliacao = Avaliacao.objects.select_related('nivel__trilha__user').get(pk=avaliacao_id)
    except Avaliacao.DoesNotExist:
        return 'avaliação inexistente'
    try:
        services.gerar_avaliacao(avaliacao, _profile(avaliacao.nivel.trilha.user))
        avaliacao.status = Avaliacao.Status.PRONTA
        avaliacao.erro = ''
        avaliacao.save(update_fields=['status', 'erro'])
    except Exception as exc:  # noqa: BLE001
        avaliacao.status = Avaliacao.Status.ERRO
        avaliacao.erro = str(exc)[:2000]
        avaliacao.save(update_fields=['status', 'erro'])
        raise
    return f'avaliação gerada {avaliacao_id}'


@shared_task
def task_gerar_exercicios(lista_id):
    from avaliacoes.models import ListaExercicios

    try:
        lista = ListaExercicios.objects.select_related('nivel__trilha__user').get(pk=lista_id)
    except ListaExercicios.DoesNotExist:
        return 'lista inexistente'
    try:
        services.gerar_exercicios(lista, _profile(lista.nivel.trilha.user))
        lista.status = ListaExercicios.Status.PRONTA
        lista.erro = ''
        lista.save(update_fields=['status', 'erro'])
    except Exception as exc:  # noqa: BLE001
        lista.status = ListaExercicios.Status.ERRO
        lista.erro = str(exc)[:2000]
        lista.save(update_fields=['status', 'erro'])
        raise
    return f'exercícios gerados {lista_id}'


@shared_task
def task_gerar_percurso(percurso_id):
    from trilhas.models import Percurso

    try:
        percurso = Percurso.objects.select_related('user').get(pk=percurso_id)
    except Percurso.DoesNotExist:
        return 'percurso inexistente'
    try:
        services.gerar_percurso(percurso, _profile(percurso.user))
        percurso.status = Percurso.Status.PRONTO
        percurso.erro = ''
        percurso.save(update_fields=['status', 'erro'])
    except Exception as exc:  # noqa: BLE001
        percurso.status = Percurso.Status.ERRO
        percurso.erro = str(exc)[:2000]
        percurso.save(update_fields=['status', 'erro'])
        raise
    return f'percurso gerado {percurso_id}'


@shared_task
def task_gerar_revisao(revisao_id):
    from avaliacoes.models import Revisao

    try:
        revisao = Revisao.objects.select_related('user').get(pk=revisao_id)
    except Revisao.DoesNotExist:
        return 'revisão inexistente'
    try:
        services.gerar_revisao(revisao, _profile(revisao.user))
        revisao.status = Revisao.Status.PRONTA
        revisao.erro = ''
        revisao.save(update_fields=['status', 'erro'])
    except Exception as exc:  # noqa: BLE001
        revisao.status = Revisao.Status.ERRO
        revisao.erro = str(exc)[:2000]
        revisao.save(update_fields=['status', 'erro'])
        raise
    return f'revisão gerada {revisao_id}'


@shared_task
def task_corrigir_avaliacao(avaliacao_id):
    from avaliacoes.models import Avaliacao

    try:
        avaliacao = Avaliacao.objects.select_related('nivel__trilha__user').get(pk=avaliacao_id)
    except Avaliacao.DoesNotExist:
        return 'avaliação inexistente'
    try:
        services.corrigir_avaliacao(avaliacao, _profile(avaliacao.nivel.trilha.user))
    except Exception as exc:  # noqa: BLE001
        avaliacao.status = Avaliacao.Status.ERRO
        avaliacao.erro = str(exc)[:2000]
        avaliacao.save(update_fields=['status', 'erro'])
        raise
    return f'avaliação corrigida {avaliacao_id}'
