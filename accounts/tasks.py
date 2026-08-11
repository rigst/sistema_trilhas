from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

User = get_user_model()


@shared_task
def cleanup_expired_visitors():
    """Remove visitantes expirados e todos os seus dados (cascade)."""
    agora = timezone.now()
    expirados = User.objects.filter(
        profile__is_visitor=True,
        profile__expires_at__lt=agora,
    )
    total = expirados.count()
    expirados.delete()
    return f"{total} visitante(s) expirado(s) removido(s)."


@shared_task
def enviar_lembretes_streak():
    """Avisa quem estudou ontem e ainda não hoje que a ofensiva está em risco.

    No-op enquanto STREAK_REMINDERS_ENABLED não for ligado (precisa de SMTP)."""
    if not getattr(settings, "STREAK_REMINDERS_ENABLED", False):
        return "lembretes de ofensiva desativados"

    from accounts.models import Profile

    hoje = timezone.localdate()
    ontem = hoje - timedelta(days=1)
    alvos = (
        Profile.objects.filter(ultimo_estudo=ontem, streak_dias__gte=1, is_visitor=False)
        .exclude(user__email="")
        .exclude(lembrete_streak_em=hoje)
        .select_related("user")
    )
    site = getattr(settings, "SITE_URL", "")
    enviados = 0
    for p in alvos:
        ok = send_mail(
            subject=f"🔥 Sua ofensiva de {p.streak_dias} dia(s) está em risco",
            message=(
                f"Olá! Você estudou ontem e está com uma sequência de "
                f"{p.streak_dias} dia(s). Faça um tópico hoje para não perdê-la.\n\n"
                f"{site}"
            ),
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[p.user.email],
            fail_silently=True,
        )
        if ok:
            p.lembrete_streak_em = hoje
            p.save(update_fields=["lembrete_streak_em", "atualizado_em"])
            enviados += 1
    return f"{enviados} lembrete(s) de ofensiva enviado(s)."
