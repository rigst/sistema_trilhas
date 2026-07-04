def profile_context(request):
    """Expõe o perfil e dados de quota para todos os templates."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    profile = getattr(user, 'profile', None)
    if profile is None:
        return {'profile': None}
    return {
        'profile': profile,
        'quota_restante': profile.tokens_restantes,
        'quota_total': profile.quota_tokens_mes,
    }
