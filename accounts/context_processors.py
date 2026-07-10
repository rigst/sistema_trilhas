from django.conf import settings


def profile_context(request):
    """Expõe o perfil e dados de quota para todos os templates."""
    # Flag do auto-cadastro precisa estar disponível mesmo deslogado (tela de login).
    ctx = {'signup_enabled': getattr(settings, 'SIGNUP_ENABLED', False)}
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return ctx
    profile = getattr(user, 'profile', None)
    if profile is None:
        ctx['profile'] = None
        return ctx
    ctx.update({
        'profile': profile,
        'quota_restante': profile.tokens_restantes,
        'quota_total': profile.quota_tokens_mes,
        'xp': profile.xp,
        'xp_no_nivel': profile.xp_no_nivel,
        'nivel_xp': profile.nivel_xp,
        'streak_dias': profile.streak_dias,
    })
    return ctx
