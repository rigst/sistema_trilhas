"""Permissions-Policy e CSP (config/security_headers.py)."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from trilhas.tests import criar_trilha

User = get_user_model()

POLITICA_FROTA = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
CSP = "Content-Security-Policy"
PP = "Permissions-Policy"


class PermissionsPolicyTests(TestCase):
    def test_rota_publica_tem_o_cabecalho(self):
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp[PP], POLITICA_FROTA)

    def test_healthz_tem_o_cabecalho(self):
        resp = self.client.get("/healthz/")
        self.assertEqual(resp[PP], POLITICA_FROTA)

    def test_admin_tem_o_cabecalho(self):
        admin = User.objects.create_superuser("chefe", password="x")
        self.client.force_login(admin)
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp[PP], POLITICA_FROTA)

    def test_redirect_do_login_obrigatorio_tambem_tem(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp[PP], POLITICA_FROTA)

    @override_settings(PERMISSIONS_POLICY="")
    def test_valor_vazio_desliga(self):
        self.assertNotIn(PP, self.client.get(reverse("login")))


class CSPTests(TestCase):
    def test_publico_estrito_com_nonce(self):
        resp = self.client.get(reverse("login"))
        csp = resp[CSP]
        self.assertIn("script-src 'self' 'nonce-", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        for diretiva in (
            "frame-ancestors 'none'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "media-src 'self'",
        ):
            self.assertIn(diretiva, csp)

    def test_nonce_do_cabecalho_e_o_dos_scripts_inline(self):
        resp = self.client.get(reverse("login"))
        nonce = resp[CSP].split("'nonce-", 1)[1].split("'", 1)[0]
        html = resp.content.decode()
        self.assertIn(f'<script nonce="{nonce}">', html)
        self.assertNotIn("<script>", html)

    def test_nonce_muda_a_cada_requisicao(self):
        a = self.client.get(reverse("login"))[CSP]
        b = self.client.get(reverse("login"))[CSP]
        self.assertNotEqual(a, b)

    def test_admin_libera_unsafe_eval_so_no_prefixo(self):
        admin = User.objects.create_superuser("chefe", password="x")
        self.client.force_login(admin)
        self.assertIn("'unsafe-eval'", self.client.get("/admin/")[CSP])
        self.assertNotIn("'unsafe-eval'", self.client.get(reverse("dashboard"))[CSP])

    @override_settings(ENABLE_CSP=False)
    def test_desligavel(self):
        self.assertNotIn(CSP, self.client.get(reverse("login")))

    def test_templates_sem_handler_inline(self):
        """Atributo de evento (onclick=, onsubmit=) não aceita nonce: a CSP o
        bloqueia em silêncio e o botão para de funcionar."""
        user = User.objects.create_user("aluna", password="x")
        trilha = criar_trilha(user)
        self.client.force_login(user)
        for url in (
            reverse("dashboard"),
            reverse("trilhas:detalhe", args=[trilha.pk]),
            reverse("offline"),
        ):
            html = self.client.get(url).content.decode()
            for evento in ("onclick=", "onsubmit=", "onchange="):
                self.assertNotIn(evento, html, url)
