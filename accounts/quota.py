"""Verificação de cota de IA antes de disparar gerações caras.

Só bloqueia as *entradas* iniciadas pelo usuário (criar trilha, pedir um novo
percurso do mentor, iniciar avaliação ou revisão). As gerações que acontecem no
meio de um fluxo já começado (subtópico durante a leitura, correção de uma
avaliação já respondida) não são bloqueadas para não deixar o usuário preso.
"""

MSG_SEM_QUOTA = (
    'Sua cota mensal de IA acabou. Ela é renovada no início do próximo mês.'
)


def sem_quota_ia(user):
    """True quando o usuário esgotou a cota mensal de tokens de IA."""
    profile = getattr(user, 'profile', None)
    if profile is None:
        return False
    return profile.tokens_restantes <= 0
