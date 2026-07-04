import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class QuotaExcedida(Exception):
    """Levantada quando o usuário não tem quota de IA suficiente."""


def criar_visitante():
    """Cria um usuário visitante temporário com quota reduzida."""
    sufixo = secrets.token_hex(4)
    username = f'visitante_{sufixo}'
    senha = secrets.token_urlsafe(16)
    user = User.objects.create_user(username=username, password=senha)
    user.first_name = 'Visitante'
    user.save(update_fields=['first_name'])

    profile = user.profile
    profile.is_visitor = True
    profile.quota_tokens_mes = getattr(settings, 'QUOTA_TOKENS_VISITOR', 300_000)
    horas = getattr(settings, 'VISITOR_EXPIRY_HOURS', 48)
    profile.expires_at = timezone.now() + timezone.timedelta(hours=horas)
    profile.save(update_fields=['is_visitor', 'quota_tokens_mes', 'expires_at'])
    return user, senha
