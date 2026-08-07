from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile

User = get_user_model()


@receiver(post_save, sender=User)
def criar_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(
            user=instance,
            quota_tokens_mes=getattr(settings, "QUOTA_TOKENS_DEFAULT", 3_000_000),
        )
