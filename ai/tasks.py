"""Tasks Celery para a geração assíncrona de conteúdo e correção com IA."""

from celery import shared_task
from django.utils import timezone

from . import services

# A API da Anthropic pode falhar por transientes (overloaded, rate limit, rede).
# Todas as tasks de IA reexecutam com backoff; o status de ERRO só é persistido
# na última tentativa, para o polling do frontend não piscar erro no meio dos
# retries. soft_time_limit protege contra gerações presas.
TASK_KW = {
    "bind": True,
    "autoretry_for": (Exception,),
    "retry_backoff": 5,
    "retry_backoff_max": 120,
    "retry_jitter": True,
    "max_retries": 3,
    "soft_time_limit": 300,
    "time_limit": 360,
}


def _profile(user):
    return getattr(user, "profile", None)


def _ultima_tentativa(task):
    """True quando não haverá novo retry (evita gravar ERRO durante o backoff)."""
    return task.request.retries >= task.max_retries


def _pre_gerar_primeiro_topico(trilha):
    """Assim que a trilha é criada, já deixa pronto o 1º tópico do nível 1."""
    from trilhas.models import Subtopico

    nivel = trilha.niveis.order_by("ordem").first()
    if nivel is None:
        return
    sub = nivel.subtopicos.order_by("ordem").first()
    if sub is None or sub.status != Subtopico.Status.PENDENTE:
        return
    sub.status = Subtopico.Status.GERANDO
    sub.save(update_fields=["status"])
    task_gerar_subtopico.delay(sub.pk)


@shared_task(**TASK_KW)
def task_gerar_perguntas(self, trilha_id):
    from trilhas.models import Trilha

    try:
        trilha = Trilha.objects.select_related("user").get(pk=trilha_id)
    except Trilha.DoesNotExist:
        return "trilha inexistente"
    try:
        services.gerar_perguntas_direcionadoras(trilha, _profile(trilha.user))
        trilha.status = Trilha.Status.AGUARDANDO_RESPOSTAS
        trilha.erro = ""
        trilha.save(update_fields=["status", "erro", "atualizada_em"])
    except Exception as exc:
        if _ultima_tentativa(self):
            trilha.status = Trilha.Status.ERRO
            trilha.erro = str(exc)[:2000]
            trilha.save(update_fields=["status", "erro", "atualizada_em"])
        raise
    return f"perguntas geradas para trilha {trilha_id}"


@shared_task(**TASK_KW)
def task_gerar_sumario(self, trilha_id):
    from trilhas.models import Trilha

    try:
        trilha = Trilha.objects.select_related("user").get(pk=trilha_id)
    except Trilha.DoesNotExist:
        return "trilha inexistente"
    try:
        services.gerar_sumario(trilha, _profile(trilha.user))
        trilha.status = Trilha.Status.SUMARIO_GERADO
        trilha.erro = ""
        trilha.save(update_fields=["status", "erro", "atualizada_em"])
        _pre_gerar_primeiro_topico(trilha)
    except Exception as exc:
        if _ultima_tentativa(self):
            trilha.status = Trilha.Status.ERRO
            trilha.erro = str(exc)[:2000]
            trilha.save(update_fields=["status", "erro", "atualizada_em"])
        raise
    return f"sumário gerado para trilha {trilha_id}"


@shared_task(**TASK_KW)
def task_gerar_subtopico(self, subtopico_id):
    from trilhas.models import Subtopico

    try:
        sub = Subtopico.objects.select_related("nivel__trilha__user").get(pk=subtopico_id)
    except Subtopico.DoesNotExist:
        return "subtópico inexistente"
    # Evita retrabalho (ex.: pré-geração já disparada e concluída).
    if sub.status == Subtopico.Status.PRONTO and sub.conteudo_md:
        return f"subtópico {subtopico_id} já pronto"
    try:
        texto = services.gerar_conteudo_subtopico(sub, _profile(sub.nivel.trilha.user))
        sub.conteudo_md = texto
        sub.status = Subtopico.Status.PRONTO
        sub.gerado_em = timezone.now()
        sub.erro = ""
        sub.save(update_fields=["conteudo_md", "status", "gerado_em", "erro"])
    except Exception as exc:
        if _ultima_tentativa(self):
            sub.status = Subtopico.Status.ERRO
            sub.erro = str(exc)[:2000]
            sub.save(update_fields=["status", "erro"])
        raise
    return f"subtópico {subtopico_id} gerado"


@shared_task(**TASK_KW)
def task_gerar_avaliacao(self, avaliacao_id):
    from avaliacoes.models import Avaliacao

    try:
        avaliacao = Avaliacao.objects.select_related("nivel__trilha__user").get(pk=avaliacao_id)
    except Avaliacao.DoesNotExist:
        return "avaliação inexistente"
    try:
        services.gerar_avaliacao(avaliacao, _profile(avaliacao.nivel.trilha.user))
        avaliacao.status = Avaliacao.Status.PRONTA
        avaliacao.erro = ""
        avaliacao.save(update_fields=["status", "erro"])
    except Exception as exc:
        if _ultima_tentativa(self):
            avaliacao.status = Avaliacao.Status.ERRO
            avaliacao.erro = str(exc)[:2000]
            avaliacao.save(update_fields=["status", "erro"])
        raise
    return f"avaliação gerada {avaliacao_id}"


@shared_task(**TASK_KW)
def task_gerar_exercicios(self, lista_id):
    from avaliacoes.models import ListaExercicios

    try:
        lista = ListaExercicios.objects.select_related("nivel__trilha__user").get(pk=lista_id)
    except ListaExercicios.DoesNotExist:
        return "lista inexistente"
    try:
        services.gerar_exercicios(lista, _profile(lista.nivel.trilha.user))
        lista.status = ListaExercicios.Status.PRONTA
        lista.erro = ""
        lista.save(update_fields=["status", "erro"])
    except Exception as exc:
        if _ultima_tentativa(self):
            lista.status = ListaExercicios.Status.ERRO
            lista.erro = str(exc)[:2000]
            lista.save(update_fields=["status", "erro"])
        raise
    return f"exercícios gerados {lista_id}"


@shared_task(**TASK_KW)
def task_gerar_percurso(self, percurso_id):
    from trilhas.models import Percurso

    try:
        percurso = Percurso.objects.select_related("user").get(pk=percurso_id)
    except Percurso.DoesNotExist:
        return "percurso inexistente"
    try:
        services.gerar_percurso(percurso, _profile(percurso.user))
        percurso.status = Percurso.Status.PRONTO
        percurso.erro = ""
        percurso.save(update_fields=["status", "erro"])
    except Exception as exc:
        if _ultima_tentativa(self):
            percurso.status = Percurso.Status.ERRO
            percurso.erro = str(exc)[:2000]
            percurso.save(update_fields=["status", "erro"])
        raise
    return f"percurso gerado {percurso_id}"


@shared_task(**TASK_KW)
def task_gerar_sugestoes(self, sessao_id):
    from trilhas.models import SessaoSugestao

    try:
        sessao = SessaoSugestao.objects.select_related("user").get(pk=sessao_id)
    except SessaoSugestao.DoesNotExist:
        return "sessão inexistente"
    try:
        services.gerar_sugestoes(sessao, _profile(sessao.user))
        sessao.status = SessaoSugestao.Status.PRONTO
        sessao.erro = ""
        sessao.save(update_fields=["status", "erro"])
    except Exception as exc:
        if _ultima_tentativa(self):
            sessao.status = SessaoSugestao.Status.ERRO
            sessao.erro = str(exc)[:2000]
            sessao.save(update_fields=["status", "erro"])
        raise
    return f"sugestões geradas {sessao_id}"


@shared_task(**TASK_KW)
def task_gerar_pergunta_retrieval(self, pergunta_id):
    from avaliacoes.models import PerguntaRetrieval

    try:
        pergunta = PerguntaRetrieval.objects.select_related(
            "subtopico__nivel__trilha__user"
        ).get(pk=pergunta_id)
    except PerguntaRetrieval.DoesNotExist:
        return "pergunta inexistente"
    try:
        services.gerar_pergunta_retrieval(
            pergunta, _profile(pergunta.subtopico.nivel.trilha.user)
        )
    except Exception as exc:
        if _ultima_tentativa(self):
            pergunta.status = PerguntaRetrieval.Status.ERRO
            pergunta.erro = str(exc)[:2000]
            pergunta.save(update_fields=["status", "erro"])
        raise
    return f"pergunta retrieval gerada {pergunta_id}"


@shared_task(**TASK_KW)
def task_gerar_revisao(self, revisao_id, trilha_id=None):
    from avaliacoes.models import Revisao

    try:
        revisao = Revisao.objects.select_related("user").get(pk=revisao_id)
    except Revisao.DoesNotExist:
        return "revisão inexistente"
    try:
        services.gerar_revisao(revisao, _profile(revisao.user), trilha_id=trilha_id)
        revisao.status = Revisao.Status.PRONTA
        revisao.erro = ""
        revisao.save(update_fields=["status", "erro"])
    except Exception as exc:
        if _ultima_tentativa(self):
            revisao.status = Revisao.Status.ERRO
            revisao.erro = str(exc)[:2000]
            revisao.save(update_fields=["status", "erro"])
        raise
    return f"revisão gerada {revisao_id}"


@shared_task(**TASK_KW)
def task_corrigir_avaliacao(self, avaliacao_id):
    from avaliacoes.models import Avaliacao

    try:
        avaliacao = Avaliacao.objects.select_related("nivel__trilha__user").get(pk=avaliacao_id)
    except Avaliacao.DoesNotExist:
        return "avaliação inexistente"
    try:
        services.corrigir_avaliacao(avaliacao, _profile(avaliacao.nivel.trilha.user))
    except Exception as exc:
        if _ultima_tentativa(self):
            avaliacao.status = Avaliacao.Status.ERRO
            avaliacao.erro = str(exc)[:2000]
            avaliacao.save(update_fields=["status", "erro"])
        raise
    return f"avaliação corrigida {avaliacao_id}"


# Dúvida no chat é interativa: o aluno está com o painel aberto olhando. Esperar
# três backoffs (até 2 min) é pior que falhar rápido e deixá-lo perguntar de
# novo — por isso esta task destoa do TASK_KW no número de tentativas.
TASK_KW_CHAT = {**TASK_KW, "max_retries": 1, "soft_time_limit": 120, "time_limit": 150}


@shared_task(**TASK_KW_CHAT)
def task_responder_duvida(self, mensagem_id):
    from django.core.cache import cache

    from chat.models import Mensagem, chave_parcial

    try:
        mensagem = Mensagem.objects.select_related("conversa__user__profile").get(pk=mensagem_id)
    except Mensagem.DoesNotExist:
        return "mensagem inexistente"
    if mensagem.status != Mensagem.Status.GERANDO:
        return f"mensagem {mensagem_id} já respondida"

    try:
        texto = services.responder_duvida(mensagem, _profile(mensagem.conversa.user))
        recusada = services.fora_de_escopo(texto)
        mensagem.texto = services.limpar_marcador_escopo(texto) if recusada else texto
        mensagem.status = Mensagem.Status.RECUSADA if recusada else Mensagem.Status.PRONTA
        mensagem.erro = ""
        mensagem.save(update_fields=["texto", "status", "erro", "tokens_entrada", "tokens_saida"])
        mensagem.conversa.save(update_fields=["atualizada_em"])
    except Exception as exc:
        if _ultima_tentativa(self):
            mensagem.status = Mensagem.Status.ERRO
            # Mensagem da exceção, nunca o texto do aluno: `erro` fica no banco.
            mensagem.erro = str(exc)[:2000]
            mensagem.save(update_fields=["status", "erro"])
        raise
    finally:
        cache.delete(chave_parcial(mensagem.pk))
    return f"dúvida {mensagem_id} respondida"
