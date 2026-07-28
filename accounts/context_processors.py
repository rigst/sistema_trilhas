from django.conf import settings
from django.utils import timezone


def profile_context(request):
    """Expõe o perfil e dados de quota para todos os templates."""
    ctx = {'signup_enabled': getattr(settings, 'SIGNUP_ENABLED', False)}
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return ctx
    profile = getattr(user, 'profile', None)
    if profile is None:
        ctx['profile'] = None
        return ctx

    hoje = timezone.localdate()
    xp_hoje = profile.xp_hoje_acc if profile.xp_hoje_ref == hoje else 0

    # Mapa semanal: 7 dias Seg–Dom com estado de cada um
    iso = hoje.isocalendar()
    semana_atual = f"{iso[0]}-W{iso[1]:02d}"
    semana = (profile.dias_xp_semana or '0000000').ljust(7, '0')[:7]
    if profile.semana_ref != semana_atual:
        semana = '0000000'
    nomes = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    hoje_idx = hoje.weekday()
    dias_semana = [
        {'nome': nomes[i], 'ativo': semana[i] == '1', 'hoje': i == hoje_idx}
        for i in range(7)
    ]

    # Detecta ganho de diamante desde a última página vista (independe de ONDE
    # foi ganho) comparando com um marcador na sessão → dispara a animação.
    diamantes_ganhos = 0
    vistos = request.session.get('diamantes_vistos')
    if vistos is None:
        request.session['diamantes_vistos'] = profile.diamantes
    else:
        if profile.diamantes > vistos:
            diamantes_ganhos = profile.diamantes - vistos
        if profile.diamantes != vistos:
            request.session['diamantes_vistos'] = profile.diamantes

    ctx.update({
        'profile': profile,
        'quota_restante': profile.tokens_restantes,
        'quota_total': profile.quota_tokens_mes,
        'xp': profile.xp,
        'xp_no_nivel': profile.xp_no_nivel,
        'nivel_xp': profile.nivel_xp,
        'diamantes': profile.diamantes,
        'xp_para_proximo_diamante': profile.xp_para_proximo_diamante,
        'diamantes_ganhos': diamantes_ganhos,
        'streak_dias': profile.streak_dias,
        'xp_hoje': xp_hoje,
        'dias_semana': dias_semana,
    })
    return ctx
