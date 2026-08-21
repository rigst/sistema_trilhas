"""Modelos, cota separada e expurgo do chat de dúvidas."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.quota import MSG_MUITAS_PERGUNTAS, MSG_SEM_QUOTA_CHAT, bloqueio_chat, bloqueio_ia
from chat.models import Conversa, Mensagem
from chat.tasks import purgar_conversas_antigas

User = get_user_model()


class BaldeDeTokensTests(TestCase):
    def setUp(self):
        self.profile = User.objects.create_user("balde", password="x").profile
        self.profile.quota_tokens_mes = 1_000
        self.profile.chat_quota_tokens_mes = 500
        self.profile.save(update_fields=["quota_tokens_mes", "chat_quota_tokens_mes"])

    def test_uso_do_chat_nao_toca_a_quota_geral(self):
        self.profile.registrar_uso(100, 20, 0, balde="chat")

        self.assertEqual(self.profile.chat_tokens_usados_mes, 120)
        self.assertEqual(self.profile.tokens_usados_mes, 0)
        self.assertEqual(self.profile.chat_tokens_restantes, 380)
        self.assertEqual(self.profile.tokens_restantes, 1_000)

    def test_uso_geral_nao_toca_o_balde_do_chat(self):
        self.profile.registrar_uso(100, 20, 0)

        self.assertEqual(self.profile.tokens_usados_mes, 120)
        self.assertEqual(self.profile.chat_tokens_usados_mes, 0)

    def test_custo_soma_nos_dois_baldes(self):
        self.profile.registrar_uso(10, 10, 3, balde="chat")
        self.profile.registrar_uso(10, 10, 4)

        self.assertEqual(int(self.profile.custo_acumulado), 7)

    def test_virada_do_mes_zera_os_dois_contadores(self):
        self.profile.registrar_uso(50, 0, 0)
        self.profile.registrar_uso(50, 0, 0, balde="chat")
        antigo = timezone.localdate().replace(day=1) - timedelta(days=40)
        type(self.profile).objects.filter(pk=self.profile.pk).update(quota_ref=antigo)
        self.profile.refresh_from_db()

        self.assertEqual(self.profile.tokens_restantes, 1_000)
        self.assertEqual(self.profile.chat_tokens_restantes, 500)

    def test_pct_usado_do_chat(self):
        self.profile.registrar_uso(250, 0, 0, balde="chat")
        self.assertEqual(self.profile.chat_quota_pct_usado, 50)


class BloqueioChatTests(TestCase):
    def setUp(self):
        cache.clear()  # o throttle vive no cache: limpar isola os testes
        self.user = User.objects.create_user("bloq", password="x")
        self.profile = self.user.profile
        self.profile.quota_tokens_mes = 1_000_000
        self.profile.chat_quota_tokens_mes = 10_000
        self.profile.save(update_fields=["quota_tokens_mes", "chat_quota_tokens_mes"])

    def test_libera_com_saldo(self):
        self.assertIsNone(bloqueio_chat(self.user, tokens_estimados=3_000))

    def test_barra_sem_saldo_no_balde_do_chat(self):
        self.profile.registrar_uso(10_000, 0, 0, balde="chat")
        self.assertEqual(bloqueio_chat(self.user, tokens_estimados=3_000), MSG_SEM_QUOTA_CHAT)

    def test_balde_do_chat_vazio_nao_barra_as_geracoes(self):
        self.profile.registrar_uso(10_000, 0, 0, balde="chat")
        self.assertIsNone(bloqueio_ia(self.user, tokens_estimados=30_000))

    @override_settings(CHAT_LIMITE_MENSAGENS=3, CHAT_JANELA_S=600)
    def test_rajada_de_perguntas_e_barrada(self):
        for _ in range(3):
            self.assertIsNone(bloqueio_chat(self.user))
        self.assertEqual(bloqueio_chat(self.user), MSG_MUITAS_PERGUNTAS)

    @override_settings(CHAT_LIMITE_MENSAGENS=1)
    def test_rajada_do_chat_nao_gasta_a_das_geracoes(self):
        bloqueio_chat(self.user)
        bloqueio_chat(self.user)  # já estourou a do chat
        self.assertIsNone(bloqueio_ia(self.user))


class ConversaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("conv", password="x")

    def test_uma_conversa_geral_por_aluno(self):
        Conversa.objects.create(user=self.user)
        _conversa, criada = Conversa.objects.get_or_create(user=self.user, trilha=None)

        self.assertFalse(criada)
        self.assertEqual(Conversa.objects.filter(user=self.user).count(), 1)

    def test_rotulo_diz_de_qual_trilha_e_a_conversa(self):
        from trilhas.models import Trilha

        trilha = Trilha.objects.create(user=self.user, tema_livre="t", titulo="Bancos")

        self.assertIn("geral", str(Conversa.objects.create(user=self.user)))
        self.assertIn("Bancos", str(Conversa.objects.create(user=self.user, trilha=trilha)))

    def test_contexto_mostra_o_ultimo_topico_perguntado(self):
        from trilhas.models import Nivel, Subtopico, Trilha

        trilha = Trilha.objects.create(user=self.user, tema_livre="t", titulo="Bancos")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1")
        sub = Subtopico.objects.create(nivel=nivel, ordem=1, titulo="Índices")
        conversa = Conversa.objects.create(user=self.user, trilha=trilha)
        Mensagem.objects.create(conversa=conversa, papel=Mensagem.Papel.ALUNO, texto="a")
        Mensagem.objects.create(
            conversa=conversa, subtopico=sub, papel=Mensagem.Papel.ALUNO, texto="b"
        )

        self.assertEqual(conversa.contexto, "Índices")

    def test_rotulo_da_mensagem_mostra_o_comeco_do_texto(self):
        conversa = Conversa.objects.create(user=self.user)
        mensagem = Mensagem.objects.create(
            conversa=conversa, papel=Mensagem.Papel.ALUNO, texto="Por que o índice ajuda?"
        )

        self.assertEqual(str(mensagem), "Aluno: Por que o índice ajuda?")

    def test_apagar_o_aluno_leva_a_conversa(self):
        conversa = Conversa.objects.create(user=self.user)
        Mensagem.objects.create(conversa=conversa, papel=Mensagem.Papel.ALUNO, texto="oi")
        self.user.delete()

        self.assertEqual(Conversa.objects.count(), 0)
        self.assertEqual(Mensagem.objects.count(), 0)


@override_settings(CHAT_RETENCAO_DIAS=90)
class PurgaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("purga", password="x")

    def _conversa(self, dias):
        conversa = Conversa.objects.create(user=self.user)
        Mensagem.objects.create(conversa=conversa, papel=Mensagem.Papel.ALUNO, texto="oi")
        # auto_now impede atribuir direto: só um update chega ao campo.
        Conversa.objects.filter(pk=conversa.pk).update(
            atualizada_em=timezone.now() - timedelta(days=dias)
        )
        return conversa

    def test_apaga_so_o_que_passou_da_retencao(self):
        velha = self._conversa(120)
        self.user2 = User.objects.create_user("purga2", password="x")
        nova = Conversa.objects.create(user=self.user2)

        purgar_conversas_antigas()

        self.assertFalse(Conversa.objects.filter(pk=velha.pk).exists())
        self.assertTrue(Conversa.objects.filter(pk=nova.pk).exists())

    def test_mensagens_vao_junto(self):
        self._conversa(120)
        purgar_conversas_antigas()
        self.assertEqual(Mensagem.objects.count(), 0)

    def test_nada_a_purgar_nao_quebra(self):
        Conversa.objects.create(user=self.user)
        self.assertIn("0 registro", purgar_conversas_antigas())
