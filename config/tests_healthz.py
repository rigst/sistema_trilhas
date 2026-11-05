"""Testes da sonda /healthz/ usada pelo smoke-test do CD."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse

URL = "/healthz/"


class HealthzTests(TestCase):
    def test_rota_na_raiz(self):
        self.assertEqual(reverse("healthz"), URL)

    def test_tudo_ok_responde_200(self):
        resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.content, b"ok\n")
        self.assertIn("no-cache", resposta["Cache-Control"])

    def test_banco_fora_responde_503(self):
        with (
            mock.patch("config.healthz.connection.cursor", side_effect=OperationalError("fora")),
            self.assertLogs("config.healthz", level="ERROR") as logs,
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(resposta.content, b"indisponivel\n")
        self.assertIn("banco", logs.output[0])

    def test_cache_fora_responde_503(self):
        with (
            mock.patch("config.healthz.cache.set", side_effect=ConnectionError("redis fora")),
            self.assertLogs("config.healthz", level="ERROR") as logs,
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)
        self.assertIn("cache", logs.output[0])

    def test_cache_que_nao_devolve_o_valor_responde_503(self):
        with (
            mock.patch("config.healthz.cache.get", return_value=None),
            self.assertLogs("config.healthz", level="ERROR"),
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL="redis://x:1/3")
    def test_broker_fora_responde_503(self):
        with (
            mock.patch(
                "config.celery.app.connection_for_write",
                side_effect=ConnectionError("broker fora"),
            ),
            self.assertLogs("config.healthz", level="ERROR") as logs,
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)
        self.assertIn("broker", logs.output[0])

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL="redis://x:1/3")
    def test_broker_no_ar_responde_200(self):
        with mock.patch("config.celery.app.connection_for_write") as conexao:
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 200)
        conexao.return_value.__enter__.return_value.ensure_connection.assert_called_once()

    def test_post_nao_e_aceito(self):
        self.assertEqual(self.client.post(URL).status_code, 405)

    def test_usuario_com_aceite_pendente_nao_e_redirecionado(self):
        # O CD chama sem sessão, mas a rota tem de ser livre também para quem
        # tem termos pendentes: um 302 aqui faria o smoke-test reprovar.
        usuario = get_user_model().objects.create_user("saude")
        self.client.force_login(usuario)
        with mock.patch("legal.middleware.precisa_reaceitar", return_value=True):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 200)
