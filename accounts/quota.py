"""Verificação de cota de IA antes de disparar gerações caras.

Só bloqueia as *entradas* iniciadas pelo usuário (criar trilha, pedir um novo
percurso do mentor, iniciar avaliação ou revisão). As gerações que acontecem no
meio de um fluxo já começado (subtópico durante a leitura, correção de uma
avaliação já respondida) não são bloqueadas para não deixar o usuário preso.
"""

from django.conf import settings
from django.core.cache import cache

MSG_SEM_QUOTA = "Sua cota mensal de IA acabou. Ela é renovada no início do próximo mês."
MSG_MUITAS_GERACOES = (
    "Você disparou muitas gerações de IA em pouco tempo. Aguarde alguns minutos e tente de novo."
)

# Rajada máxima de gerações de IA iniciadas pelo usuário (janela deslizante
# aproximada via contador no cache). A cota mensal segue sendo o teto real;
# isto só impede consumo em rajada (abuso/script).
LIMITE_GERACOES = 10
JANELA_GERACOES_S = 10 * 60

MSG_SEM_QUOTA_CHAT = (
    "Sua cota mensal de perguntas ao chat acabou. Ela é renovada no início do próximo mês."
)
MSG_MUITAS_PERGUNTAS = "Você fez muitas perguntas em pouco tempo. Aguarde alguns minutos."


def excedeu_limite(chave, limite, janela_s):
    """Contador simples no cache: True quando a chave passou do limite."""
    key = f"throttle:{chave}"
    try:
        atual = cache.incr(key)
    except ValueError:
        cache.add(key, 1, janela_s)
        atual = 1
    return atual > limite


def sem_quota_ia(user, tokens_estimados=0):
    """True quando o usuário esgotou a cota mensal de tokens de IA (ou não
    tem saldo para a geração estimada)."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return not profile.tem_quota(tokens_estimados)


def rajada_ia_excedida(user):
    """True quando o usuário estourou o limite de gerações em rajada."""
    return excedeu_limite(f"ia:{user.pk}", LIMITE_GERACOES, JANELA_GERACOES_S)


def sem_quota_chat(user, tokens_estimados=0):
    """True quando o balde mensal do chat acabou."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return not profile.tem_quota_chat(tokens_estimados)


def bloqueio_chat(user, tokens_estimados=0):
    """Mensagem de erro se a pergunta deve ser barrada, senão None.

    Chave de rajada própria (`chat:<pk>`): perguntar no widget não pode gastar o
    orçamento de rajada de quem está gerando trilhas, nem o contrário.
    """
    if sem_quota_chat(user, tokens_estimados):
        return MSG_SEM_QUOTA_CHAT
    limite = getattr(settings, "CHAT_LIMITE_MENSAGENS", 20)
    janela = getattr(settings, "CHAT_JANELA_S", 600)
    if excedeu_limite(f"chat:{user.pk}", limite, janela):
        return MSG_MUITAS_PERGUNTAS
    return None


def bloqueio_ia(user, tokens_estimados=0):
    """Mensagem de erro se a geração deve ser bloqueada, senão None.

    Combina a cota mensal (com estimativa opcional da geração) e o limite
    de rajada — uso: `erro = bloqueio_ia(request.user)`.
    """
    if sem_quota_ia(user, tokens_estimados):
        return MSG_SEM_QUOTA
    if rajada_ia_excedida(user):
        return MSG_MUITAS_GERACOES
    return None
