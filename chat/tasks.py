"""Manutenção das conversas do chat."""

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone


@shared_task
def purgar_conversas_antigas():
    """Apaga conversas paradas há mais que CHAT_RETENCAO_DIAS.

    A retenção é prometida na política de privacidade, então o expurgo é do
    sistema e não depende do aluno lembrar de limpar."""
    from .models import Conversa

    dias = getattr(settings, "CHAT_RETENCAO_DIAS", 90)
    corte = timezone.now() - timedelta(days=dias)
    total, _ = Conversa.objects.filter(atualizada_em__lt=corte).delete()
    return f"{total} registro(s) de conversa expurgado(s)."
