"""Testes das settings de produção.

Este é o módulo que endurece o deploy: se um cabeçalho de segurança cair ou uma
variável obrigatória deixar de ser exigida, o site vai ao ar frouxo sem que nada
mais acuse. O módulo é carregado isoladamente (importlib), sem trocar as
settings do processo de teste.
"""

import importlib
import sys
from unittest import mock

from django.test import SimpleTestCase

ENV_MINIMO = {
    "SECRET_KEY": "chave-de-teste-longa-o-suficiente-0a1b2c3d4e5f",
    "ALLOWED_HOSTS": "exemplo.com, www.exemplo.com",
    "DB_NAME": "trilhas",
    "DB_USER": "trilhas",
    "DB_PASSWORD": "segredo",
}


# Zeradas em toda carga para o teste não depender do .env da máquina.
ENV_NEUTRO = {"SENTRY_DSN": "", "SENTRY_ENVIRONMENT": "", "SENTRY_RELEASE": "", "STATIC_ROOT": ""}


def carregar(env=None, **extra):
    """Importa config.settings.production do zero com o ambiente dado.

    Reimportar é o único jeito de exercitar o módulo: as checagens de variável
    obrigatória acontecem no topo, na importação.
    """
    ambiente = {**ENV_NEUTRO, **(ENV_MINIMO if env is None else env), **extra}
    with mock.patch.dict("os.environ", ambiente, clear=False):
        sys.modules.pop("config.settings.production", None)
        try:
            return importlib.import_module("config.settings.production")
        finally:
            # Não deixa o módulo carregado com este ambiente para o teste seguinte.
            sys.modules.pop("config.settings.production", None)


class VariaveisObrigatoriasTests(SimpleTestCase):
    def test_carrega_com_o_ambiente_minimo(self):
        settings = carregar()
        self.assertFalse(settings.DEBUG)
        self.assertEqual(settings.ALLOWED_HOSTS, ["exemplo.com", "www.exemplo.com"])

    def test_cada_variavel_obrigatoria_falta_e_derruba_o_boot(self):
        for faltando in ENV_MINIMO:
            env = {k: v for k, v in ENV_MINIMO.items() if k != faltando}
            env[faltando] = ""
            with self.assertRaises(RuntimeError, msg=faltando) as ctx:
                carregar(env)
            self.assertIn(faltando, str(ctx.exception))

    def test_secret_key_de_desenvolvimento_e_recusada(self):
        with self.assertRaises(RuntimeError) as ctx:
            carregar(SECRET_KEY="django-insecure-abcdef")
        self.assertIn("django-insecure", str(ctx.exception))

    def test_hosts_vazios_na_lista_sao_descartados(self):
        settings = carregar(ALLOWED_HOSTS="exemplo.com, ,  , outro.com")
        self.assertEqual(settings.ALLOWED_HOSTS, ["exemplo.com", "outro.com"])


class SegurancaTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.settings = carregar()

    def test_https_obrigatorio_com_hsts_de_um_ano(self):
        self.assertTrue(self.settings.SECURE_SSL_REDIRECT)
        self.assertEqual(self.settings.SECURE_HSTS_SECONDS, 31536000)
        self.assertTrue(self.settings.SECURE_HSTS_INCLUDE_SUBDOMAINS)
        self.assertTrue(self.settings.SECURE_HSTS_PRELOAD)

    def test_cookies_so_trafegam_em_https(self):
        self.assertTrue(self.settings.SESSION_COOKIE_SECURE)
        self.assertTrue(self.settings.CSRF_COOKIE_SECURE)

    def test_nosniff_e_moldura_negada(self):
        self.assertTrue(self.settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(self.settings.X_FRAME_OPTIONS, "DENY")

    def test_proxy_reverso_informa_o_esquema(self):
        # Sem isto, atrás do nginx o Django acharia que todo request é http.
        self.assertEqual(self.settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))

    def test_origens_csrf_derivam_dos_hosts_permitidos_em_https(self):
        self.assertEqual(
            self.settings.CSRF_TRUSTED_ORIGINS,
            ["https://exemplo.com", "https://www.exemplo.com"],
        )


class InfraTests(SimpleTestCase):
    def test_banco_e_postgres_com_conexao_persistente(self):
        settings = carregar()
        db = settings.DATABASES["default"]
        self.assertEqual(db["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(db["NAME"], "trilhas")
        self.assertEqual(db["CONN_MAX_AGE"], 600)

    def test_host_e_porta_do_banco_tem_padrao(self):
        db = carregar().DATABASES["default"]
        self.assertTrue(db["HOST"])
        self.assertTrue(db["PORT"])

    def test_cache_e_sessao_usam_bancos_redis_distintos(self):
        # Um flush/estouro de um domínio não pode derrubar o outro.
        settings = carregar()
        self.assertNotEqual(
            settings.CACHES["default"]["LOCATION"], settings.CACHES["sessions"]["LOCATION"]
        )
        self.assertEqual(settings.SESSION_CACHE_ALIAS, "sessions")
        self.assertEqual(settings.SESSION_ENGINE, "django.contrib.sessions.backends.cache")

    def test_estaticos_usam_manifesto_com_hash(self):
        settings = carregar()
        self.assertEqual(
            settings.STORAGES["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
        )


class SentryTests(SimpleTestCase):
    def test_sem_dsn_o_sentry_nao_e_inicializado(self):
        with mock.patch("sentry_sdk.init") as init:
            settings = carregar(SENTRY_DSN="")
        init.assert_not_called()
        self.assertEqual(settings.SENTRY_DSN, "")

    def test_com_dsn_o_sentry_sobe_sem_pii(self):
        with mock.patch("sentry_sdk.init") as init:
            carregar(SENTRY_DSN="https://token@sentry.exemplo/1", SENTRY_ENVIRONMENT="staging")

        kwargs = init.call_args.kwargs
        self.assertEqual(kwargs["dsn"], "https://token@sentry.exemplo/1")
        self.assertEqual(kwargs["environment"], "staging")
        self.assertFalse(kwargs["send_default_pii"])

    def test_sem_o_pacote_instalado_o_app_ainda_sobe(self):
        with mock.patch.dict(sys.modules, {"sentry_sdk": None}):
            settings = carregar(SENTRY_DSN="https://token@sentry.exemplo/1")
        self.assertTrue(settings.SENTRY_DSN)  # carregou sem estourar ImportError
