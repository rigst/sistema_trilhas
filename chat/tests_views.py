"""Endpoints do chat.

Como no resto do projeto, a IA é mockada no ponto de disparo
(`ai.tasks.task_responder_duvida.delay`): aqui o que importa é o contrato da
view, não o que o modelo responde.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from chat.models import Conversa, Mensagem
from trilhas.models import Nivel, Subtopico, Trilha

User = get_user_model()


def montar_subtopico(user, titulo="Índices no Postgres"):
    trilha = Trilha.objects.create(user=user, tema_livre="bancos", titulo="Bancos")
    nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1")
    return Subtopico.objects.create(
        nivel=nivel, ordem=1, titulo=titulo, conteudo_md="Um índice evita o seq scan."
    )


@mock.patch("ai.tasks.task_responder_duvida.delay")
class EnviarTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("aluno", password="x")
        self.client.force_login(self.user)
        self.url = reverse("chat:enviar")

    def test_grava_as_duas_falas_e_dispara_a_task(self, delay):
        sub = montar_subtopico(self.user)

        resp = self.client.post(
            self.url, {"pergunta": "Por que o índice ajuda?", "subtopico": sub.pk}
        )

        self.assertEqual(resp.status_code, 200)
        conversa = Conversa.objects.get(user=self.user, subtopico=sub)
        papeis = list(conversa.mensagens.values_list("papel", "status"))
        self.assertEqual(papeis, [("aluno", "pronta"), ("ia", "gerando")])
        resposta = conversa.mensagens.get(papel=Mensagem.Papel.IA)
        delay.assert_called_once_with(resposta.pk)
        self.assertEqual(resp.json()["resposta"]["id"], resposta.pk)

    def test_sem_subtopico_usa_a_conversa_geral(self, delay):
        self.client.post(self.url, {"pergunta": "Como funciona a revisão?"})

        conversa = Conversa.objects.get(user=self.user)
        self.assertIsNone(conversa.subtopico)

    def test_segunda_pergunta_continua_a_mesma_conversa(self, delay):
        sub = montar_subtopico(self.user)
        for texto in ("Primeira?", "Segunda?"):
            self.client.post(self.url, {"pergunta": texto, "subtopico": sub.pk})

        self.assertEqual(Conversa.objects.count(), 1)
        self.assertEqual(Mensagem.objects.count(), 4)

    def test_pergunta_vazia_e_recusada(self, delay):
        resp = self.client.post(self.url, {"pergunta": "   "})

        self.assertEqual(resp.status_code, 400)
        delay.assert_not_called()

    @override_settings(CHAT_MAX_CHARS_PERGUNTA=20)
    def test_pergunta_longa_demais_e_recusada(self, delay):
        resp = self.client.post(self.url, {"pergunta": "p" * 21})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("20", resp.json()["erro"])
        delay.assert_not_called()

    def test_sem_cota_devolve_429(self, delay):
        profile = self.user.profile
        profile.chat_quota_tokens_mes = 100
        profile.save(update_fields=["chat_quota_tokens_mes"])

        resp = self.client.post(self.url, {"pergunta": "Cabe?"})

        self.assertEqual(resp.status_code, 429)
        delay.assert_not_called()
        self.assertEqual(Mensagem.objects.count(), 0)

    @override_settings(CHAT_LIMITE_MENSAGENS=2)
    def test_rajada_devolve_429(self, delay):
        for _ in range(2):
            self.client.post(self.url, {"pergunta": "Vai?"})
        resp = self.client.post(self.url, {"pergunta": "E agora?"})

        self.assertEqual(resp.status_code, 429)
        self.assertEqual(delay.call_count, 2)

    def test_subtopico_de_outro_usuario_da_404(self, delay):
        alheio = montar_subtopico(User.objects.create_user("outro", password="x"))

        resp = self.client.post(self.url, {"pergunta": "Posso?", "subtopico": alheio.pk})

        self.assertEqual(resp.status_code, 404)
        delay.assert_not_called()

    @override_settings(CHAT_ENABLED=False)
    def test_desligado_responde_404(self, delay):
        resp = self.client.post(self.url, {"pergunta": "Tem alguém aí?"})

        self.assertEqual(resp.status_code, 404)
        delay.assert_not_called()

    def test_deslogado_vai_para_o_login(self, delay):
        self.client.logout()

        resp = self.client.post(self.url, {"pergunta": "E sem conta?"})

        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])


class StatusEHistoricoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("hist", password="x")
        self.client.force_login(self.user)
        self.conversa = Conversa.objects.create(user=self.user)

    def _mensagem(self, **kw):
        return Mensagem.objects.create(conversa=self.conversa, **kw)

    def test_resposta_pronta_vem_renderizada(self):
        msg = self._mensagem(
            papel=Mensagem.Papel.IA, texto="Use **índice**.", status=Mensagem.Status.PRONTA
        )

        dados = self.client.get(reverse("chat:mensagem_status", args=[msg.pk])).json()

        self.assertEqual(dados["status"], "pronta")
        self.assertIn("<strong>índice</strong>", dados["html"])

    def test_html_da_ia_e_sanitizado(self):
        msg = self._mensagem(
            papel=Mensagem.Papel.IA,
            texto="<script>alert(1)</script> ok",
            status=Mensagem.Status.PRONTA,
        )

        dados = self.client.get(reverse("chat:mensagem_status", args=[msg.pk])).json()

        self.assertNotIn("<script>", dados["html"])

    def test_enquanto_gera_devolve_o_parcial_do_cache(self):
        from chat.models import chave_parcial

        msg = self._mensagem(papel=Mensagem.Papel.IA, status=Mensagem.Status.GERANDO)
        cache.set(chave_parcial(msg.pk), "Um índice evi")

        dados = self.client.get(reverse("chat:mensagem_status", args=[msg.pk])).json()

        self.assertEqual(dados["parcial"], "Um índice evi")

    def test_erro_traz_recado_e_nao_a_causa_tecnica(self):
        msg = self._mensagem(
            papel=Mensagem.Papel.IA, status=Mensagem.Status.ERRO, erro="Connection reset by peer"
        )

        dados = self.client.get(reverse("chat:mensagem_status", args=[msg.pk])).json()

        self.assertNotIn("Connection reset", dados["erro"])

    def test_mensagem_de_outro_usuario_da_404(self):
        alheia = Mensagem.objects.create(
            conversa=Conversa.objects.create(user=User.objects.create_user("x2", password="x")),
            papel=Mensagem.Papel.IA,
        )

        resp = self.client.get(reverse("chat:mensagem_status", args=[alheia.pk]))

        self.assertEqual(resp.status_code, 404)

    def test_historico_devolve_a_conversa_da_pagina(self):
        self._mensagem(papel=Mensagem.Papel.ALUNO, texto="Oi")
        self._mensagem(papel=Mensagem.Papel.IA, texto="Olá")

        dados = self.client.get(reverse("chat:historico")).json()

        self.assertEqual([m["papel"] for m in dados["mensagens"]], ["aluno", "ia"])

    def test_historico_nao_cria_conversa(self):
        sub = montar_subtopico(self.user)

        self.client.get(reverse("chat:historico"), {"subtopico": sub.pk})

        self.assertFalse(Conversa.objects.filter(subtopico=sub).exists())

    def test_limpar_apaga_a_conversa(self):
        self._mensagem(papel=Mensagem.Papel.ALUNO, texto="Some daqui")

        resp = self.client.post(reverse("chat:limpar"))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Conversa.objects.count(), 0)
        self.assertEqual(Mensagem.objects.count(), 0)

    def test_limpar_sem_conversa_nao_quebra(self):
        Conversa.objects.all().delete()

        resp = self.client.post(reverse("chat:limpar"))

        self.assertEqual(resp.status_code, 200)
