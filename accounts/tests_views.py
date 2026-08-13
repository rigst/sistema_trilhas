"""Testes das views de conta, do middleware de visitante e do contexto global."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.quota import excedeu_limite
from accounts.services import criar_visitante
from legal.models import AceiteLegal, DocumentoLegal, OrigemAceite, TipoDocumento

User = get_user_model()


def documento_vigente(tipo=TipoDocumento.TERMOS, versao="1.0"):
    doc = DocumentoLegal.objects.create(
        tipo=tipo, versao=versao, titulo=f"Doc {tipo}", corpo_md="# T\n\ntexto", material=True
    )
    doc.publicar()
    return doc


class ExcedeuLimiteTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_conta_ate_o_limite_e_barra_depois(self):
        for _ in range(3):
            self.assertFalse(excedeu_limite("chave", limite=3, janela_s=60))
        self.assertTrue(excedeu_limite("chave", limite=3, janela_s=60))

    def test_chaves_diferentes_nao_se_misturam(self):
        excedeu_limite("a", limite=1, janela_s=60)
        self.assertTrue(excedeu_limite("a", limite=1, janela_s=60))
        self.assertFalse(excedeu_limite("b", limite=1, janela_s=60))


class CriarVisitanteTests(TestCase):
    @override_settings(QUOTA_TOKENS_VISITOR=1234, VISITOR_EXPIRY_HOURS=2)
    def test_visitante_nasce_com_quota_e_prazo_proprios(self):
        user, senha = criar_visitante()

        self.assertTrue(user.username.startswith("visitante_"))
        self.assertTrue(senha)
        self.assertTrue(user.profile.is_visitor)
        self.assertEqual(user.profile.quota_tokens_mes, 1234)
        self.assertLess(user.profile.expires_at, timezone.now() + timezone.timedelta(hours=3))

    def test_cada_visitante_tem_username_proprio(self):
        u1, _ = criar_visitante()
        u2, _ = criar_visitante()
        self.assertNotEqual(u1.username, u2.username)


class EntrarComoVisitanteTests(TestCase):
    def setUp(self):
        cache.clear()
        self.doc = documento_vigente()

    def test_aceite_registra_a_prova_e_autentica(self):
        resp = self.client.post(
            reverse("accounts:entrar_visitante"), {"aceite_legal": "on"}, follow=True
        )

        self.assertEqual(resp.redirect_chain[-1][0], reverse("dashboard"))
        user = User.objects.get(username__startswith="visitante_")
        aceite = AceiteLegal.objects.get(usuario=user)
        self.assertEqual(aceite.origem, OrigemAceite.VISITANTE)
        self.assertTrue(aceite.e_visitante)
        self.assertEqual(aceite.documento_sha256, self.doc.sha256)

    def test_sem_aceite_volta_para_a_tela_de_aceite_sem_criar_conta(self):
        # Volta para a própria tela de aceite: o checkbox não existe no login.
        resp = self.client.post(reverse("accounts:entrar_visitante"), {})

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "legal/aceite.html")
        self.assertFalse(User.objects.filter(username__startswith="visitante_").exists())

    def test_rajada_do_mesmo_ip_e_barrada(self):
        # Sem limite por IP, um script criaria visitantes em massa e queimaria
        # o crédito de API.
        for _ in range(5):
            self.client.post(reverse("accounts:entrar_visitante"), {"aceite_legal": "on"})
            self.client.logout()

        resp = self.client.post(
            reverse("accounts:entrar_visitante"), {"aceite_legal": "on"}, follow=True
        )

        self.assertEqual(resp.redirect_chain[-1][0], reverse("login"))
        self.assertEqual(User.objects.filter(username__startswith="visitante_").count(), 5)

    def test_get_nao_e_aceito(self):
        self.assertEqual(self.client.get(reverse("accounts:entrar_visitante")).status_code, 405)


class CadastroDesligadoTests(TestCase):
    """Com SIGNUP_ENABLED=False, o recurso existe mas é inacessível."""

    @override_settings(SIGNUP_ENABLED=False)
    def test_as_tres_views_respondem_404(self):
        urls = [
            reverse("accounts:cadastro"),
            reverse("accounts:cadastro_enviado"),
            reverse("accounts:confirmar_email", args=["abc", "def"]),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)


@override_settings(
    SIGNUP_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
)
class CadastroTests(TestCase):
    def setUp(self):
        cache.clear()
        documento_vigente()

    DADOS = {
        "username": "novo",
        "email": "Novo@Exemplo.com",
        "password1": "senha-bem-longa-123",
        "password2": "senha-bem-longa-123",
        "aceite_legal": "on",
    }

    def test_get_renderiza_o_formulario(self):
        resp = self.client.get(reverse("accounts:cadastro"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "registration/cadastro.html")

    def test_conta_nasce_inativa_e_recebe_o_e_mail(self):
        resp = self.client.post(reverse("accounts:cadastro"), self.DADOS)

        user = User.objects.get(username="novo")
        self.assertFalse(user.is_active)
        self.assertEqual(user.email, "novo@exemplo.com")  # normalizado
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("novo@exemplo.com", mail.outbox[0].to)
        self.assertRedirects(
            resp, reverse("accounts:cadastro_enviado"), fetch_redirect_response=False
        )

    def test_e_mail_repetido_e_recusado(self):
        User.objects.create_user("existente", email="novo@exemplo.com", password="x")
        resp = self.client.post(reverse("accounts:cadastro"), self.DADOS)

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="novo").exists())

    def test_usuario_ja_logado_nao_ve_o_formulario(self):
        self.client.force_login(User.objects.create_user("logado", password="x"))
        resp = self.client.get(reverse("accounts:cadastro"))
        # Quem já está autenticado sai do cadastro (aqui, para o interstitial de
        # aceite, que é o guarda que vem antes do painel).
        self.assertEqual(resp.status_code, 302)
        self.assertNotEqual(resp["Location"], reverse("accounts:cadastro"))

    def test_rajada_do_mesmo_ip_e_barrada(self):
        for i in range(5):
            self.client.post(
                reverse("accounts:cadastro"),
                {**self.DADOS, "username": f"u{i}", "email": f"u{i}@e.com"},
            )

        resp = self.client.post(reverse("accounts:cadastro"), self.DADOS, follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("login"))
        self.assertFalse(User.objects.filter(username="novo").exists())


@override_settings(SIGNUP_ENABLED=True)
class ConfirmarEmailTests(TestCase):
    def setUp(self):
        self.doc = documento_vigente()
        self.user = User.objects.create_user("pendente", email="p@e.com", password="x")
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

    def _url(self, user=None, token=None):
        user = user or self.user
        return reverse(
            "accounts:confirmar_email",
            args=[
                urlsafe_base64_encode(force_bytes(user.pk)),
                token or default_token_generator.make_token(user),
            ],
        )

    def test_link_valido_ativa_a_conta_e_registra_o_aceite(self):
        resp = self.client.get(self._url(), follow=True)

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(resp.redirect_chain[-1][0], reverse("dashboard"))
        aceite = AceiteLegal.objects.get(usuario=self.user)
        self.assertEqual(aceite.origem, OrigemAceite.CADASTRO)

    def test_token_invalido_nao_ativa(self):
        resp = self.client.get(self._url(token="token-falso"), follow=True)

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(resp.redirect_chain[-1][0], reverse("login"))

    def test_uid_inexistente_nao_ativa(self):
        fantasma = User(pk=999999)
        resp = self.client.get(self._url(user=fantasma, token="x"), follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse("login"))

    def test_uid_malformado_nao_ativa(self):
        url = reverse("accounts:confirmar_email", args=["!!!", "x"])
        resp = self.client.get(url, follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse("login"))

    def test_link_nao_reativa_conta_ja_ativa(self):
        # Reusar o link de confirmação não pode virar um login sem senha.
        self.user.is_active = True
        self.user.save(update_fields=["is_active"])

        resp = self.client.get(self._url(), follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("login"))
        self.assertFalse(AceiteLegal.objects.filter(usuario=self.user).exists())


class VisitorExpiryMiddlewareTests(TestCase):
    def setUp(self):
        self.user, _senha = criar_visitante()
        self.client.force_login(self.user)

    def test_visitante_ativo_tem_a_janela_renovada(self):
        antes = self.user.profile.expires_at
        self.user.profile.expires_at = timezone.now() + timezone.timedelta(hours=1)
        self.user.profile.save(update_fields=["expires_at"])

        self.client.get(reverse("dashboard"))

        self.user.profile.refresh_from_db()
        self.assertGreater(self.user.profile.expires_at, antes - timezone.timedelta(seconds=1))

    def test_visitante_expirado_e_deslogado(self):
        self.user.profile.expires_at = timezone.now() - timezone.timedelta(hours=1)
        self.user.profile.save(update_fields=["expires_at"])

        resp = self.client.get(reverse("dashboard"), follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_conta_normal_nao_expira(self):
        comum = User.objects.create_user("comum", password="x")
        self.client.force_login(comum)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)


class ProfileContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ctx", password="x")
        self.client.force_login(self.user)

    def test_anonimo_recebe_so_a_flag_de_cadastro(self):
        self.client.logout()
        resp = self.client.get(reverse("login"))
        self.assertNotIn("profile", resp.context)
        self.assertIn("signup_enabled", resp.context)

    def test_mapa_da_semana_tem_sete_dias_com_hoje_marcado(self):
        resp = self.client.get(reverse("dashboard"))
        dias = resp.context["dias_semana"]

        self.assertEqual(len(dias), 7)
        self.assertEqual(sum(1 for d in dias if d["hoje"]), 1)

    def test_semana_de_outra_referencia_e_zerada(self):
        p = self.user.profile
        p.dias_xp_semana = "1111111"
        p.semana_ref = "1999-W01"
        p.save(update_fields=["dias_xp_semana", "semana_ref"])

        resp = self.client.get(reverse("dashboard"))
        self.assertTrue(all(not d["ativo"] for d in resp.context["dias_semana"]))

    def test_xp_de_hoje_so_conta_com_a_referencia_do_dia(self):
        p = self.user.profile
        p.xp_hoje_acc = 40
        p.xp_hoje_ref = timezone.localdate() - timezone.timedelta(days=1)
        p.save(update_fields=["xp_hoje_acc", "xp_hoje_ref"])

        self.assertEqual(self.client.get(reverse("dashboard")).context["xp_hoje"], 0)

        p.xp_hoje_ref = timezone.localdate()
        p.save(update_fields=["xp_hoje_ref"])
        self.assertEqual(self.client.get(reverse("dashboard")).context["xp_hoje"], 40)

    def test_diamante_ganho_desde_a_ultima_pagina_dispara_a_animacao(self):
        self.client.get(reverse("dashboard"))  # grava o marcador na sessão

        p = self.user.profile
        p.diamantes += 2
        p.save(update_fields=["diamantes"])

        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.context["diamantes_ganhos"], 2)

        # Já mostrada, a animação não se repete na página seguinte.
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.context["diamantes_ganhos"], 0)

    def test_diamante_gasto_nao_dispara_animacao(self):
        self.client.get(reverse("dashboard"))
        self.user.profile.gastar_diamante()

        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.context["diamantes_ganhos"], 0)


class ProfileGamificacaoTests(TestCase):
    def setUp(self):
        self.profile = User.objects.create_user("gam", password="x").profile

    def test_diamante_por_marco_de_xp_e_creditado_uma_vez(self):
        diamantes0 = self.profile.diamantes
        self.profile.registrar_atividade(self.profile.XP_POR_DIAMANTE)
        creditados = self.profile.diamantes - diamantes0
        self.assertGreaterEqual(creditados, 1)
        self.assertEqual(self.profile.diamantes_xp_creditados, 1)

        # Idempotente: nova atividade sem cruzar outro marco não recredita.
        self.profile.registrar_atividade(1)
        self.assertEqual(self.profile.diamantes - diamantes0, creditados)
        self.assertEqual(self.profile.diamantes_xp_creditados, 1)

    def test_gastar_diamante_sem_saldo_falha(self):
        self.profile.diamantes = 0
        self.profile.save(update_fields=["diamantes"])
        self.assertFalse(self.profile.gastar_diamante())

    def test_gastar_e_creditar_sao_simetricos(self):
        diamantes0 = self.profile.diamantes
        self.assertTrue(self.profile.gastar_diamante())
        self.profile.creditar_diamante()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.diamantes, diamantes0)

    def test_streak_cresce_em_dias_seguidos_e_reseta_com_buraco(self):
        hoje = timezone.localdate()
        self.profile.ultimo_estudo = hoje - timezone.timedelta(days=1)
        self.profile.streak_dias = 4
        self.profile.save(update_fields=["ultimo_estudo", "streak_dias"])
        self.profile.registrar_atividade(1)
        self.assertEqual(self.profile.streak_dias, 5)

        self.profile.ultimo_estudo = hoje - timezone.timedelta(days=3)
        self.profile.save(update_fields=["ultimo_estudo"])
        self.profile.registrar_atividade(1)
        self.assertEqual(self.profile.streak_dias, 1)

    def test_quota_zera_na_virada_do_mes(self):
        self.profile.quota_tokens_mes = 1000
        self.profile.tokens_usados_mes = 900
        self.profile.quota_ref = timezone.localdate() - timezone.timedelta(days=45)
        self.profile.save(update_fields=["quota_tokens_mes", "tokens_usados_mes", "quota_ref"])

        self.assertEqual(self.profile.tokens_restantes, 1000)
        self.assertEqual(self.profile.quota_pct_usado, 0)

    def test_sem_quota_configurada_o_percentual_e_zero(self):
        self.profile.quota_tokens_mes = 0
        self.profile.save(update_fields=["quota_tokens_mes"])
        self.assertEqual(self.profile.quota_pct_usado, 0)


class EnviarConfirmacaoTests(TestCase):
    @override_settings(
        SIGNUP_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
    )
    def test_falha_no_envio_nao_derruba_o_cadastro(self):
        # send_mail é chamado com fail_silently=True: a conta é criada mesmo com
        # o SMTP fora, e o usuário pode pedir outro link depois.
        documento_vigente()
        with mock.patch("accounts.views.send_mail") as enviar:
            self.client.post(
                reverse("accounts:cadastro"),
                {
                    "username": "resiliente",
                    "email": "r@e.com",
                    "password1": "senha-bem-longa-123",
                    "password2": "senha-bem-longa-123",
                    "aceite_legal": "on",
                },
            )

        self.assertTrue(User.objects.filter(username="resiliente").exists())
        self.assertTrue(enviar.called)
