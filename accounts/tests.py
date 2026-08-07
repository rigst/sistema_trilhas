"""Testes do lembrete de ofensiva (streak) por e-mail."""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.tasks import enviar_lembretes_streak

User = get_user_model()


@override_settings(
    STREAK_REMINDERS_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class StreakReminderTests(TestCase):
    def _user(self, nome, email, *, estudo_offset_dias, streak):
        u = User.objects.create_user(nome, email=email, password="x")
        p = u.profile
        p.streak_dias = streak
        p.ultimo_estudo = timezone.localdate() - timezone.timedelta(days=estudo_offset_dias)
        p.save(update_fields=["streak_dias", "ultimo_estudo"])
        return u

    def test_avisa_quem_estudou_ontem(self):
        u = self._user("ana", "ana@example.com", estudo_offset_dias=1, streak=3)
        enviar_lembretes_streak()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ana@example.com", mail.outbox[0].to)
        u.profile.refresh_from_db()
        self.assertEqual(u.profile.lembrete_streak_em, timezone.localdate())

    def test_nao_avisa_quem_estudou_hoje(self):
        self._user("bob", "bob@example.com", estudo_offset_dias=0, streak=5)
        enviar_lembretes_streak()
        self.assertEqual(len(mail.outbox), 0)

    def test_nao_reenvia_no_mesmo_dia(self):
        self._user("cid", "cid@example.com", estudo_offset_dias=1, streak=2)
        enviar_lembretes_streak()
        enviar_lembretes_streak()  # segunda passada no mesmo dia
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(STREAK_REMINDERS_ENABLED=False)
    def test_desligado_nao_envia(self):
        self._user("dan", "dan@example.com", estudo_offset_dias=1, streak=2)
        enviar_lembretes_streak()
        self.assertEqual(len(mail.outbox), 0)
