"""Tasks Celery para a geração assíncrona de conteúdo e correção com IA."""

from celery import shared_task
from django.utils import timezone

from . import services


def _profile(user):
    return getattr(user, 'profile', None)


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
    except Exception as exc:  # noqa: BLE001
        trilha.status = Trilha.Status.ERRO
        trilha.erro = str(exc)[:2000]
        trilha.save(update_fields=['status', 'erro', 'atualizada_em'])
        raise
    return f'sumário gerado para trilha {trilha_id}'


@shared_task
def task_gerar_conteudo_nivel(nivel_id):
    from trilhas.models import Nivel

    try:
        nivel = Nivel.objects.select_related('trilha__user').get(pk=nivel_id)
    except Nivel.DoesNotExist:
        return 'nível inexistente'
    try:
        texto = services.gerar_conteudo_nivel(nivel, _profile(nivel.trilha.user))
        nivel.conteudo_md = texto
        nivel.status = Nivel.Status.CONTEUDO_PRONTO
        nivel.gerado_em = timezone.now()
        nivel.erro = ''
        nivel.save(update_fields=['conteudo_md', 'status', 'gerado_em', 'erro', 'atualizado_em'])
    except Exception as exc:  # noqa: BLE001
        nivel.status = Nivel.Status.ERRO
        nivel.erro = str(exc)[:2000]
        nivel.save(update_fields=['status', 'erro', 'atualizado_em'])
        raise
    return f'conteúdo gerado para nível {nivel_id}'


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
