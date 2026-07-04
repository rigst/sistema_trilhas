from celery import shared_task
from django.contrib.auth import get_user_model
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
    return f'{total} visitante(s) expirado(s) removido(s).'
