"""Testes das views de trilhas: criação, leitura, mentor, sugestões e vídeo.

Todo disparo de IA é mockado em `ai.tasks.*.delay`; o que se verifica é o
contrato das views — permissões, guardas de estado, economia de diamantes e
o que vai para o template.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.quota import MSG_SEM_QUOTA
from avaliacoes.models import Exercicio, ListaExercicios, Questao, QuestaoRevisao, Revisao
from trilhas.models import (
    CardSalvo,
    Nivel,
    PassoPercurso,
    Percurso,
    PerguntaDirecionadora,
    SessaoSugestao,
    Subtopico,
    Trilha,
    TrilhaSugerida,
    VideoNivel,
)

User = get_user_model()


def criar_trilha(user, titulo="Trilha", *, status=Trilha.Status.EM_ANDAMENTO, ativa=True):
    return Trilha.objects.create(
        user=user, tema_livre=titulo, titulo=titulo, status=status, ativa=ativa
    )


def criar_nivel(trilha, *, ordem=1, status=Nivel.Status.DISPONIVEL, n_subs=2, subs_status=None):
    nivel = Nivel.objects.create(trilha=trilha, ordem=ordem, titulo=f"Nível {ordem}", status=status)
    for i in range(1, n_subs + 1):
        Subtopico.objects.create(
            nivel=nivel,
            ordem=i,
            titulo=f"Tópico {i}",
            status=subs_status or Subtopico.Status.PENDENTE,
            conteudo_md="# Conteúdo" if subs_status == Subtopico.Status.PRONTO else "",
        )
    return nivel


def mensagens(response):
    return [str(m) for m in response.context["messages"]]


class QuestaoDoDiaTests(TestCase):
    """A questão do feed é determinística por dia e prefere fontes com explicação."""

    def setUp(self):
        from trilhas.views import _questao_do_dia

        self._questao_do_dia = _questao_do_dia
        self.user = User.objects.create_user("qd", password="x")
        self.nivel = criar_nivel(criar_trilha(self.user), n_subs=0)
        self.hoje = timezone.localdate()

    def _exercicio(self, gabarito="A", alternativas=None):
        lista, _ = ListaExercicios.objects.get_or_create(nivel=self.nivel)
        return Exercicio.objects.create(
            lista=lista,
            ordem=Exercicio.objects.filter(lista=lista).count() + 1,
            tipo=Exercicio.Tipo.OBJETIVA,
            enunciado_md="Enunciado do **exercício**",
            alternativas=alternativas
            if alternativas is not None
            else [{"letra": "A", "texto": "certa"}],
            gabarito=gabarito,
            explicacao_md="Explicação do exercício",
        )

    def test_nivel_sem_questao_devolve_none(self):
        self.assertIsNone(self._questao_do_dia(self.nivel, self.hoje))

    def test_devolve_o_enunciado_renderizado_e_o_gabarito_normalizado(self):
        self._exercicio(gabarito=" a ")
        dados = self._questao_do_dia(self.nivel, self.hoje)
        self.assertIn("<strong>exercício</strong>", dados["enunciado"])
        self.assertEqual(dados["gabarito"], "A")
        self.assertIn("Explicação do exercício", dados["explicacao"])

    def test_a_escolha_e_estavel_no_mesmo_dia(self):
        for _ in range(5):
            self._exercicio()
        primeira = self._questao_do_dia(self.nivel, self.hoje)
        self.assertEqual(primeira, self._questao_do_dia(self.nivel, self.hoje))

    def test_ignora_questoes_sem_gabarito_ou_sem_alternativas(self):
        self._exercicio(gabarito="")
        self._exercicio(alternativas=[])
        self.assertIsNone(self._questao_do_dia(self.nivel, self.hoje))

    def test_cai_para_questoes_de_avaliacao_quando_nao_ha_pratica(self):
        from avaliacoes.models import Avaliacao

        avaliacao = Avaliacao.objects.create(nivel=self.nivel)
        Questao.objects.create(
            avaliacao=avaliacao,
            ordem=1,
            tipo=Questao.Tipo.OBJETIVA,
            enunciado_md="Da avaliação",
            alternativas=[{"letra": "A", "texto": "certa"}],
            gabarito="A",
        )
        dados = self._questao_do_dia(self.nivel, self.hoje)
        self.assertIn("Da avaliação", dados["enunciado"])
        self.assertEqual(dados["explicacao"], "")  # Questao não tem explicação

    def test_prefere_a_questao_de_revisao_a_da_avaliacao(self):
        revisao = Revisao.objects.create(user=self.user, status=Revisao.Status.PRONTA)
        QuestaoRevisao.objects.create(
            revisao=revisao,
            ordem=1,
            nivel=self.nivel,
            enunciado_md="Da revisão",
            alternativas=[{"letra": "A", "texto": "certa"}],
            gabarito="A",
            explicacao_md="Tem explicação",
        )
        dados = self._questao_do_dia(self.nivel, self.hoje)
        self.assertIn("Da revisão", dados["enunciado"])


class DashboardFeedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("feed", password="x")
        self.client.force_login(self.user)

    def test_feed_traz_os_niveis_com_revisao_devida(self):
        nivel = criar_nivel(criar_trilha(self.user), status=Nivel.Status.APROVADO, n_subs=0)
        Nivel.objects.filter(pk=nivel.pk).update(revisao_proxima=timezone.localdate())

        resp = self.client.get(reverse("dashboard"))
        self.assertEqual([f["nivel"].pk for f in resp.context["feed_revisao"]], [nivel.pk])
        self.assertEqual(resp.context["revisoes_devidas"], 1)
        self.assertTrue(resp.context["pode_revisar"])

    def test_revisao_futura_fica_fora_do_feed(self):
        nivel = criar_nivel(criar_trilha(self.user), status=Nivel.Status.APROVADO, n_subs=0)
        Nivel.objects.filter(pk=nivel.pk).update(
            revisao_proxima=timezone.localdate() + timezone.timedelta(days=3)
        )
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.context["feed_revisao"], [])
        self.assertEqual(resp.context["revisoes_devidas"], 0)

    def test_continuar_aponta_para_a_trilha_mais_recente(self):
        criar_nivel(criar_trilha(self.user, "Antiga"), subs_status=Subtopico.Status.PRONTO)
        recente = criar_trilha(self.user, "Recente")
        nivel = criar_nivel(recente, subs_status=Subtopico.Status.PRONTO)

        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.context["continuar"]["nivel"].pk, nivel.pk)
        self.assertTrue(resp.context["pode_estudar"])

    def test_quiz_do_dia_marcado_apos_criar_revisao_hoje(self):
        Revisao.objects.create(user=self.user, status=Revisao.Status.PRONTA)
        resp = self.client.get(reverse("dashboard"))
        self.assertTrue(resp.context["quiz_feito_hoje"])

    def test_quota_no_contexto(self):
        resp = self.client.get(reverse("dashboard"))
        quota = resp.context["quota"]
        self.assertEqual(quota["total"], self.user.profile.quota_tokens_mes)

        self.user.profile.quota_tokens_mes = 0
        self.user.profile.save(update_fields=["quota_tokens_mes"])
        resp = self.client.get(reverse("dashboard"))
        self.assertIsNone(resp.context["quota"])


class TrilhaGestaoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("gest", password="x")
        self.client.force_login(self.user)
        self.trilha = criar_trilha(self.user)

    def test_alternar_ativa_vai_e_volta(self):
        resp = self.client.post(
            reverse("trilhas:alternar_ativa", args=[self.trilha.pk]), follow=True
        )
        self.trilha.refresh_from_db()
        self.assertFalse(self.trilha.ativa)
        self.assertIn("Trilha desativada", " ".join(mensagens(resp)))

        resp = self.client.post(
            reverse("trilhas:alternar_ativa", args=[self.trilha.pk]), follow=True
        )
        self.trilha.refresh_from_db()
        self.assertTrue(self.trilha.ativa)
        self.assertIn("Trilha reativada", " ".join(mensagens(resp)))

    def test_renomear_grava_o_titulo(self):
        self.client.post(reverse("trilhas:renomear", args=[self.trilha.pk]), {"titulo": "  Novo  "})
        self.trilha.refresh_from_db()
        self.assertEqual(self.trilha.titulo, "Novo")

    def test_renomear_com_titulo_vazio_nao_muda_nada(self):
        self.client.post(reverse("trilhas:renomear", args=[self.trilha.pk]), {"titulo": "   "})
        self.trilha.refresh_from_db()
        self.assertEqual(self.trilha.titulo, "Trilha")

    def test_excluir_dentro_da_janela_devolve_o_diamante(self):
        Trilha.objects.filter(pk=self.trilha.pk).update(diamante_gasto=True)
        diamantes0 = self.user.profile.diamantes

        resp = self.client.post(reverse("trilhas:excluir", args=[self.trilha.pk]), follow=True)

        self.assertFalse(Trilha.objects.filter(pk=self.trilha.pk).exists())
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.diamantes, diamantes0 + 1)
        self.assertIn("diamante devolvido", " ".join(mensagens(resp)))

    def test_excluir_trilha_sem_diamante_gasto_nao_reembolsa(self):
        diamantes0 = self.user.profile.diamantes
        self.client.post(reverse("trilhas:excluir", args=[self.trilha.pk]))
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.diamantes, diamantes0)

    def test_excluir_fora_da_janela_nao_reembolsa(self):
        antiga = timezone.now() - timezone.timedelta(hours=Trilha.JANELA_REEMBOLSO_HORAS + 1)
        Trilha.objects.filter(pk=self.trilha.pk).update(diamante_gasto=True, criada_em=antiga)
        diamantes0 = self.user.profile.diamantes

        self.client.post(reverse("trilhas:excluir", args=[self.trilha.pk]))

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.diamantes, diamantes0)

    def test_status_devolve_json(self):
        resp = self.client.get(reverse("trilhas:status", args=[self.trilha.pk]))
        self.assertEqual(
            resp.json(),
            {"status": Trilha.Status.EM_ANDAMENTO, "progresso_pct": 0, "erro": ""},
        )

    def test_trilha_de_outro_usuario_da_404(self):
        outro = criar_trilha(User.objects.create_user("outro", password="x"))
        self.assertEqual(
            self.client.get(reverse("trilhas:status", args=[outro.pk])).status_code, 404
        )

    def test_detalhe_de_trilha_em_rascunho_volta_para_as_perguntas(self):
        Trilha.objects.filter(pk=self.trilha.pk).update(status=Trilha.Status.RASCUNHO)
        resp = self.client.get(reverse("trilhas:detalhe", args=[self.trilha.pk]))
        self.assertRedirects(
            resp,
            reverse("trilhas:perguntas", args=[self.trilha.pk]),
            fetch_redirect_response=False,
        )


class NovaCapaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("capa", password="x")
        self.client.force_login(self.user)
        self.trilha = criar_trilha(self.user)
        cache.clear()

    def test_troca_a_capa_quando_a_busca_encontra_foto(self):
        with (
            mock.patch("ai.services.buscar_capa", return_value="https://p/photos/42/x.jpg"),
            mock.patch("ai.services.baixar_para_media", return_value="/media/covers/x.jpg"),
        ):
            resp = self.client.post(
                reverse("trilhas:nova_capa", args=[self.trilha.pk]), follow=True
            )

        self.trilha.refresh_from_db()
        self.assertEqual(self.trilha.cover_url, "/media/covers/x.jpg")
        self.assertEqual(self.trilha.cover_pexels_id, 42)
        self.assertIn("Capa trocada", " ".join(mensagens(resp)))

    def test_exclui_as_capas_ja_usadas_pelo_usuario(self):
        outra = criar_trilha(self.user, "Outra")
        Trilha.objects.filter(pk=outra.pk).update(cover_pexels_id=7)

        with (
            mock.patch("ai.services.buscar_capa", return_value="") as buscar,
            mock.patch("ai.services.baixar_para_media"),
        ):
            self.client.post(reverse("trilhas:nova_capa", args=[self.trilha.pk]))

        self.assertEqual(buscar.call_args.kwargs["excluir_ids"], {7})

    def test_sem_foto_encontrada_avisa_e_mantem_a_capa(self):
        with mock.patch("ai.services.buscar_capa", return_value=""):
            resp = self.client.post(
                reverse("trilhas:nova_capa", args=[self.trilha.pk]), follow=True
            )

        self.trilha.refresh_from_db()
        self.assertEqual(self.trilha.cover_url, "")
        self.assertIn("Não achei outra foto boa", " ".join(mensagens(resp)))

    def test_sem_quota_nao_chama_a_busca(self):
        with (
            mock.patch("accounts.quota.sem_quota_ia", return_value=True),
            mock.patch("ai.services.buscar_capa") as buscar,
        ):
            resp = self.client.post(
                reverse("trilhas:nova_capa", args=[self.trilha.pk]), follow=True
            )

        buscar.assert_not_called()
        self.assertIn(MSG_SEM_QUOTA, mensagens(resp))


class TrilhaCriarTests(TestCase):
    TEMA_OK = "quero aprender python do zero para análise de dados"

    def setUp(self):
        self.user = User.objects.create_user("criar", password="x")
        self.client.force_login(self.user)
        cache.clear()

    def test_get_renderiza_o_formulario(self):
        self.assertEqual(self.client.get(reverse("trilhas:criar")).status_code, 200)

    def test_tema_vazio_e_recusado(self):
        resp = self.client.post(reverse("trilhas:criar"), {"tema_livre": "   "})
        self.assertIn("Descreva o que você quer aprender.", mensagens(resp))
        self.assertFalse(Trilha.objects.exists())

    @mock.patch("ai.tasks.task_gerar_perguntas.delay")
    def test_criacao_gasta_um_diamante(self, delay):
        diamantes0 = self.user.profile.diamantes
        self.client.post(reverse("trilhas:criar"), {"tema_livre": self.TEMA_OK})

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.diamantes, diamantes0 - 1)
        trilha = Trilha.objects.get()
        self.assertTrue(trilha.diamante_gasto)
        self.assertEqual(trilha.status, Trilha.Status.GERANDO_PERGUNTAS)
        delay.assert_called_once_with(trilha.pk)

    @mock.patch("ai.tasks.task_gerar_perguntas.delay")
    def test_sem_diamante_nao_cria(self, delay):
        self.user.profile.diamantes = 0
        self.user.profile.save(update_fields=["diamantes"])

        resp = self.client.post(reverse("trilhas:criar"), {"tema_livre": self.TEMA_OK}, follow=True)

        self.assertIn("sem diamantes", " ".join(mensagens(resp)))
        self.assertFalse(Trilha.objects.exists())
        delay.assert_not_called()


class PerguntasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("perg", password="x")
        self.client.force_login(self.user)
        self.trilha = criar_trilha(self.user, status=Trilha.Status.AGUARDANDO_RESPOSTAS)
        self.p1 = PerguntaDirecionadora.objects.create(
            trilha=self.trilha, ordem=1, pergunta="Seu nível?", opcoes=["A", "B"]
        )

    def test_get_renderiza_as_perguntas(self):
        resp = self.client.get(reverse("trilhas:perguntas", args=[self.trilha.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Seu nível?")

    def test_get_apos_a_fase_de_perguntas_vai_para_o_detalhe(self):
        Trilha.objects.filter(pk=self.trilha.pk).update(status=Trilha.Status.EM_ANDAMENTO)
        resp = self.client.get(reverse("trilhas:perguntas", args=[self.trilha.pk]))
        self.assertRedirects(
            resp, reverse("trilhas:detalhe", args=[self.trilha.pk]), fetch_redirect_response=False
        )

    @mock.patch("ai.tasks.task_gerar_sumario.delay")
    def test_post_grava_as_respostas_e_pede_o_sumario(self, delay):
        self.client.post(
            reverse("trilhas:perguntas", args=[self.trilha.pk]),
            {f"pergunta_{self.p1.pk}": "  Iniciante  "},
        )

        self.p1.refresh_from_db()
        self.trilha.refresh_from_db()
        self.assertEqual(self.p1.resposta, "Iniciante")
        self.assertEqual(self.trilha.status, Trilha.Status.GERANDO_SUMARIO)
        delay.assert_called_once_with(self.trilha.pk)

    @mock.patch("ai.tasks.task_gerar_sumario.delay")
    def test_post_fora_da_fase_nao_regera_o_sumario(self, delay):
        Trilha.objects.filter(pk=self.trilha.pk).update(status=Trilha.Status.EM_ANDAMENTO)
        self.client.post(reverse("trilhas:perguntas", args=[self.trilha.pk]), {})
        delay.assert_not_called()


class NivelDetalheTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("niv", password="x")
        self.client.force_login(self.user)
        self.trilha = criar_trilha(self.user)

    def test_nivel_bloqueado_volta_para_a_trilha(self):
        nivel = criar_nivel(self.trilha, status=Nivel.Status.BLOQUEADO)
        resp = self.client.get(reverse("trilhas:nivel", args=[nivel.pk]), follow=True)

        self.assertEqual(
            resp.redirect_chain[-1][0], reverse("trilhas:detalhe", args=[self.trilha.pk])
        )
        self.assertIn("Este nível ainda está bloqueado. Conclua os anteriores.", mensagens(resp))

    def test_video_so_pode_ser_gerado_com_algum_topico_pronto(self):
        nivel = criar_nivel(self.trilha)
        resp = self.client.get(reverse("trilhas:nivel", args=[nivel.pk]))
        self.assertFalse(resp.context["video_pronto_para_gerar"])

        nivel.subtopicos.update(status=Subtopico.Status.PRONTO, conteudo_md="# x")
        resp = self.client.get(reverse("trilhas:nivel", args=[nivel.pk]))
        self.assertTrue(resp.context["video_pronto_para_gerar"])

    def test_video_desligado_some_do_contexto(self):
        nivel = criar_nivel(self.trilha)
        VideoNivel.objects.create(nivel=nivel, status=VideoNivel.Status.PRONTO)
        with self.settings(VIDEO_ENABLED=False):
            resp = self.client.get(reverse("trilhas:nivel", args=[nivel.pk]))
        self.assertIsNone(resp.context["video"])
        self.assertFalse(resp.context["video_enabled"])


class TopicoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("top", password="x")
        self.client.force_login(self.user)
        self.trilha = criar_trilha(self.user, status=Trilha.Status.SUMARIO_GERADO)
        self.nivel = criar_nivel(self.trilha, n_subs=3)

    def _url(self, ordem):
        return reverse("trilhas:topico", args=[self.nivel.pk, ordem])

    @mock.patch("ai.tasks.task_gerar_subtopico.delay")
    def test_topico_pendente_dispara_a_geracao(self, delay):
        sub = self.nivel.subtopicos.get(ordem=1)
        self.client.get(self._url(1))

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subtopico.Status.GERANDO)
        delay.assert_called_once_with(sub.pk)

    @mock.patch("ai.tasks.task_gerar_subtopico.delay")
    def test_topico_pronto_marca_lido_da_xp_e_pre_gera_o_proximo(self, delay):
        sub1 = self.nivel.subtopicos.get(ordem=1)
        Subtopico.objects.filter(pk=sub1.pk).update(
            status=Subtopico.Status.PRONTO, conteudo_md="# Texto"
        )
        xp0 = self.user.profile.xp

        resp = self.client.get(self._url(1))

        sub1.refresh_from_db()
        self.assertTrue(sub1.lido)
        self.assertTrue(resp.context["ganhou_xp"])
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.xp, xp0 + self.user.profile.XP_TOPICO)
        # A trilha sai de "sumário gerado" na primeira leitura.
        self.trilha.refresh_from_db()
        self.assertEqual(self.trilha.status, Trilha.Status.EM_ANDAMENTO)
        # E o próximo tópico já entra em geração.
        delay.assert_called_once_with(self.nivel.subtopicos.get(ordem=2).pk)

    @mock.patch("ai.tasks.task_gerar_subtopico.delay")
    def test_reler_o_topico_nao_da_xp_de_novo(self, _delay):
        Subtopico.objects.filter(nivel=self.nivel).update(
            status=Subtopico.Status.PRONTO, conteudo_md="# Texto"
        )
        self.client.get(self._url(1))
        self.user.profile.refresh_from_db()
        xp_apos_1a = self.user.profile.xp

        resp = self.client.get(self._url(1))

        self.assertFalse(resp.context["ganhou_xp"])
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.xp, xp_apos_1a)

    @mock.patch("ai.tasks.task_gerar_exercicios.delay")
    @mock.patch("ai.tasks.task_gerar_subtopico.delay")
    def test_ultimo_topico_pre_gera_os_exercicios(self, _sub, exercicios):
        Subtopico.objects.filter(nivel=self.nivel).update(
            status=Subtopico.Status.PRONTO, conteudo_md="# Texto"
        )
        self.client.get(self._url(3))

        lista = ListaExercicios.objects.get(nivel=self.nivel)
        self.assertEqual(lista.status, ListaExercicios.Status.GERANDO)
        exercicios.assert_called_once_with(lista.pk)

    def test_navegacao_anterior_e_proximo(self):
        Subtopico.objects.filter(nivel=self.nivel).update(
            status=Subtopico.Status.PRONTO, conteudo_md="# Texto"
        )
        resp = self.client.get(self._url(2))
        self.assertEqual(resp.context["passo"], 2)
        self.assertEqual(resp.context["total"], 3)
        self.assertEqual(resp.context["anterior"].ordem, 1)
        self.assertEqual(resp.context["proximo"].ordem, 3)

    def test_ordem_inexistente_da_404(self):
        self.assertEqual(self.client.get(self._url(99)).status_code, 404)

    def test_nivel_sem_topicos_volta_para_a_trilha(self):
        vazio = criar_nivel(self.trilha, ordem=2, n_subs=0)
        resp = self.client.get(reverse("trilhas:topico", args=[vazio.pk, 1]))
        self.assertRedirects(
            resp, reverse("trilhas:detalhe", args=[self.trilha.pk]), fetch_redirect_response=False
        )

    def test_nivel_bloqueado_volta_para_a_trilha(self):
        bloqueado = criar_nivel(self.trilha, ordem=3, status=Nivel.Status.BLOQUEADO)
        resp = self.client.get(reverse("trilhas:topico", args=[bloqueado.pk, 1]), follow=True)
        self.assertIn("Este nível ainda está bloqueado.", mensagens(resp))

    def test_status_devolve_json(self):
        sub = self.nivel.subtopicos.get(ordem=1)
        resp = self.client.get(reverse("trilhas:topico_status", args=[sub.pk]))
        self.assertEqual(resp.json(), {"status": Subtopico.Status.PENDENTE, "erro": ""})


class MentorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ment", password="x")
        self.client.force_login(self.user)
        cache.clear()

    @mock.patch("ai.tasks.task_gerar_percurso.delay")
    def test_primeira_visita_cria_o_percurso(self, delay):
        resp = self.client.get(reverse("trilhas:mentor"))

        percurso = Percurso.objects.get()
        self.assertEqual(percurso.status, Percurso.Status.GERANDO)
        delay.assert_called_once_with(percurso.pk)
        self.assertEqual(resp.status_code, 200)

    @mock.patch("ai.tasks.task_gerar_percurso.delay")
    def test_percurso_existente_e_reaproveitado(self, delay):
        percurso = Percurso.objects.create(user=self.user, status=Percurso.Status.PRONTO)
        PassoPercurso.objects.create(
            percurso=percurso, ordem=1, tipo=PassoPercurso.Tipo.REVISAR_GLOBAL, titulo="Revisar"
        )

        resp = self.client.get(reverse("trilhas:mentor"))

        delay.assert_not_called()
        self.assertEqual(len(resp.context["passos"]), 1)

    @mock.patch("ai.tasks.task_gerar_percurso.delay")
    def test_sem_quota_nao_cria_percurso(self, delay):
        with mock.patch("accounts.quota.sem_quota_ia", return_value=True):
            resp = self.client.get(reverse("trilhas:mentor"), follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("dashboard"))
        self.assertFalse(Percurso.objects.exists())
        delay.assert_not_called()

    @mock.patch("ai.tasks.task_gerar_percurso.delay")
    def test_atualizar_cria_um_percurso_novo(self, delay):
        Percurso.objects.create(user=self.user, status=Percurso.Status.PRONTO)
        self.client.post(reverse("trilhas:mentor_atualizar"))

        self.assertEqual(Percurso.objects.count(), 2)
        delay.assert_called_once()

    @mock.patch("ai.tasks.task_gerar_percurso.delay")
    def test_atualizar_sem_quota_volta_para_o_mentor(self, delay):
        with mock.patch("accounts.quota.sem_quota_ia", return_value=True):
            resp = self.client.post(reverse("trilhas:mentor_atualizar"))

        self.assertRedirects(resp, reverse("trilhas:mentor"), fetch_redirect_response=False)
        self.assertFalse(Percurso.objects.exists())

    def test_status_devolve_json(self):
        percurso = Percurso.objects.create(user=self.user, status=Percurso.Status.ERRO, erro="ops")
        resp = self.client.get(reverse("trilhas:percurso_status", args=[percurso.pk]))
        self.assertEqual(resp.json(), {"status": Percurso.Status.ERRO, "erro": "ops"})


class SugestoesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("sug", password="x")
        self.client.force_login(self.user)
        self.trilha = criar_trilha(self.user, "Python")
        cache.clear()

    def test_sem_sessao_redireciona_para_a_nova(self):
        resp = self.client.get(reverse("trilhas:sugestoes"))
        self.assertRedirects(resp, reverse("trilhas:sugestoes_nova"), fetch_redirect_response=False)

    def test_pagina_mostra_a_ultima_rodada(self):
        sessao = SessaoSugestao.objects.create(user=self.user, status=SessaoSugestao.Status.PRONTO)
        TrilhaSugerida.objects.create(sessao=sessao, ordem=1, titulo="Django")

        resp = self.client.get(reverse("trilhas:sugestoes"))
        self.assertEqual(len(resp.context["sugestoes"]), 1)

    def test_form_agrupa_as_candidatas_por_categoria(self):
        Trilha.objects.filter(pk=self.trilha.pk).update(categoria="Programação")
        resp = self.client.get(reverse("trilhas:sugestoes_nova"))

        categorias = [cat for cat, _itens in resp.context["grupos_cat"]]
        self.assertEqual(categorias, ["Programação"])

    def test_trilha_em_rascunho_nao_pode_servir_de_base(self):
        Trilha.objects.filter(pk=self.trilha.pk).update(status=Trilha.Status.RASCUNHO)
        resp = self.client.get(reverse("trilhas:sugestoes_nova"))
        self.assertEqual(list(resp.context["candidatas"]), [])

    @mock.patch("ai.tasks.task_gerar_sugestoes.delay")
    def test_post_usa_as_trilhas_marcadas(self, delay):
        outra = criar_trilha(self.user, "Violão")
        self.client.post(reverse("trilhas:sugestoes_nova"), {"trilha": [str(outra.pk)]})

        sessao = SessaoSugestao.objects.get()
        self.assertEqual(list(sessao.trilhas_base.all()), [outra])
        delay.assert_called_once_with(sessao.pk)

    @mock.patch("ai.tasks.task_gerar_sugestoes.delay")
    def test_post_sem_marcar_nada_usa_todas(self, _delay):
        criar_trilha(self.user, "Violão")
        self.client.post(reverse("trilhas:sugestoes_nova"), {})

        sessao = SessaoSugestao.objects.get()
        self.assertEqual(sessao.trilhas_base.count(), 2)

    @mock.patch("ai.tasks.task_gerar_sugestoes.delay")
    def test_post_sem_trilha_alguma_avisa(self, delay):
        Trilha.objects.filter(pk=self.trilha.pk).delete()
        resp = self.client.post(reverse("trilhas:sugestoes_nova"), {}, follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("dashboard"))
        self.assertIn("Crie ao menos uma trilha antes de pedir sugestões.", mensagens(resp))
        delay.assert_not_called()

    @mock.patch("ai.tasks.task_gerar_sugestoes.delay")
    def test_post_sem_quota_nao_gera(self, delay):
        with mock.patch("accounts.quota.sem_quota_ia", return_value=True):
            resp = self.client.post(reverse("trilhas:sugestoes_nova"), {}, follow=True)

        self.assertIn(MSG_SEM_QUOTA, mensagens(resp))
        self.assertFalse(SessaoSugestao.objects.exists())

    def test_status_devolve_json(self):
        sessao = SessaoSugestao.objects.create(user=self.user, status=SessaoSugestao.Status.GERANDO)
        resp = self.client.get(reverse("trilhas:sugestoes_status", args=[sessao.pk]))
        self.assertEqual(resp.json(), {"status": SessaoSugestao.Status.GERANDO, "erro": ""})


class SugestaoAceitarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ace", password="x")
        self.client.force_login(self.user)
        self.sessao = SessaoSugestao.objects.create(
            user=self.user, status=SessaoSugestao.Status.PRONTO
        )
        self.sugestao = TrilhaSugerida.objects.create(
            sessao=self.sessao,
            ordem=1,
            titulo="Django na prática",
            enfoque="Web com Python",
            topicos=["ORM", "Views"],
        )
        cache.clear()

    @mock.patch("ai.tasks.task_gerar_perguntas.delay")
    def test_aceitar_cria_a_trilha_com_contexto_e_gasta_diamante(self, delay):
        diamantes0 = self.user.profile.diamantes
        resp = self.client.post(reverse("trilhas:sugestao_aceitar", args=[self.sugestao.pk]))

        trilha = Trilha.objects.get()
        self.assertEqual(trilha.titulo, "Django na prática")
        self.assertIn("ORM", trilha.tema_livre)  # o tema carrega os tópicos
        self.assertEqual(trilha.status, Trilha.Status.GERANDO_PERGUNTAS)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.diamantes, diamantes0 - 1)
        delay.assert_called_once_with(trilha.pk)
        self.assertRedirects(
            resp, reverse("trilhas:perguntas", args=[trilha.pk]), fetch_redirect_response=False
        )

    @mock.patch("ai.tasks.task_gerar_perguntas.delay")
    def test_aceitar_duas_vezes_nao_cria_outra_trilha(self, delay):
        self.client.post(reverse("trilhas:sugestao_aceitar", args=[self.sugestao.pk]))
        delay.reset_mock()

        self.client.post(reverse("trilhas:sugestao_aceitar", args=[self.sugestao.pk]))

        self.assertEqual(Trilha.objects.count(), 1)
        delay.assert_not_called()

    @mock.patch("ai.tasks.task_gerar_perguntas.delay")
    def test_sem_diamante_nao_aceita(self, delay):
        self.user.profile.diamantes = 0
        self.user.profile.save(update_fields=["diamantes"])

        resp = self.client.post(
            reverse("trilhas:sugestao_aceitar", args=[self.sugestao.pk]), follow=True
        )

        self.assertIn("sem diamantes", " ".join(mensagens(resp)))
        self.assertFalse(Trilha.objects.exists())
        delay.assert_not_called()

    @mock.patch("ai.tasks.task_gerar_perguntas.delay")
    def test_sem_quota_nao_aceita(self, delay):
        with mock.patch("accounts.quota.sem_quota_ia", return_value=True):
            resp = self.client.post(
                reverse("trilhas:sugestao_aceitar", args=[self.sugestao.pk]), follow=True
            )

        self.assertIn(MSG_SEM_QUOTA, mensagens(resp))
        self.assertFalse(Trilha.objects.exists())

    def test_sugestao_de_outro_usuario_da_404(self):
        outro = User.objects.create_user("terceiro", password="x")
        sessao = SessaoSugestao.objects.create(user=outro)
        alheia = TrilhaSugerida.objects.create(sessao=sessao, ordem=1, titulo="X")
        resp = self.client.post(reverse("trilhas:sugestao_aceitar", args=[alheia.pk]))
        self.assertEqual(resp.status_code, 404)


class VideoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("vid", password="x")
        self.client.force_login(self.user)
        self.trilha = criar_trilha(self.user)
        self.nivel = criar_nivel(self.trilha, subs_status=Subtopico.Status.PRONTO)
        cache.clear()

    @mock.patch("trilhas.tasks.task_gerar_video_nivel.delay")
    def test_enfileira_a_geracao(self, delay):
        self.client.post(reverse("trilhas:video_gerar", args=[self.nivel.pk]))

        video = VideoNivel.objects.get(nivel=self.nivel)
        self.assertEqual(video.status, VideoNivel.Status.GERANDO)
        self.assertEqual(video.etapa, "Na fila")
        self.assertIsNotNone(video.iniciado_em)
        delay.assert_called_once_with(video.pk)

    @mock.patch("trilhas.tasks.task_gerar_video_nivel.delay")
    def test_nao_reenfileira_o_que_ja_esta_gerando(self, delay):
        VideoNivel.objects.create(
            nivel=self.nivel, status=VideoNivel.Status.GERANDO, progresso_pct=40
        )
        self.client.post(reverse("trilhas:video_gerar", args=[self.nivel.pk]))

        delay.assert_not_called()
        self.assertEqual(VideoNivel.objects.get(nivel=self.nivel).progresso_pct, 40)

    @mock.patch("trilhas.tasks.task_gerar_video_nivel.delay")
    def test_nivel_sem_topicos_e_recusado(self, delay):
        vazio = criar_nivel(self.trilha, ordem=2, n_subs=0)
        resp = self.client.post(reverse("trilhas:video_gerar", args=[vazio.pk]), follow=True)

        self.assertIn("Este nível ainda não tem tópicos.", mensagens(resp))
        delay.assert_not_called()

    @mock.patch("trilhas.tasks.task_gerar_video_nivel.delay")
    def test_sem_quota_nao_enfileira(self, delay):
        with mock.patch("accounts.quota.sem_quota_ia", return_value=True):
            resp = self.client.post(
                reverse("trilhas:video_gerar", args=[self.nivel.pk]), follow=True
            )

        self.assertIn(MSG_SEM_QUOTA, mensagens(resp))
        self.assertFalse(VideoNivel.objects.exists())
        delay.assert_not_called()

    def test_status_devolve_o_progresso(self):
        VideoNivel.objects.create(
            nivel=self.nivel,
            status=VideoNivel.Status.PRONTO,
            progresso_pct=100,
            arquivo="/media/videos/1/x.mp4",
        )
        dados = self.client.get(reverse("trilhas:video_status", args=[self.nivel.pk])).json()

        self.assertEqual(dados["status"], VideoNivel.Status.PRONTO)
        self.assertEqual(dados["url"], "/media/videos/1/x.mp4")
        self.assertIsNone(dados["restante_seg"])  # só estima enquanto gera

    def test_status_sem_video_da_404(self):
        resp = self.client.get(reverse("trilhas:video_status", args=[self.nivel.pk]))
        self.assertEqual(resp.status_code, 404)


class CardsSalvosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("card", password="x")
        self.client.force_login(self.user)
        self.trilha = criar_trilha(self.user)
        self.nivel = criar_nivel(self.trilha, subs_status=Subtopico.Status.PRONTO)
        self.sub = self.nivel.subtopicos.get(ordem=1)

    def _toggle(self, **extra):
        dados = {"subtopico": self.sub.pk, "indice": "0", "html": "<p>trecho</p>"}
        dados.update(extra)
        return self.client.post(reverse("trilhas:salvo_toggle"), dados)

    def test_salva_e_remove_o_card(self):
        self.assertTrue(self._toggle().json()["salvo"])
        self.assertEqual(CardSalvo.objects.count(), 1)

        self.assertFalse(self._toggle().json()["salvo"])
        self.assertEqual(CardSalvo.objects.count(), 0)

    def test_html_e_sanitizado_antes_de_persistir(self):
        # O endpoint aceita qualquer POST: script enviado pelo cliente não pode
        # ser guardado como veio.
        self._toggle(html='<p>ok</p><script>alert("xss")</script>')
        salvo = CardSalvo.objects.get()
        self.assertIn("<p>ok</p>", salvo.html)
        self.assertNotIn("<script>", salvo.html)

    def test_indice_invalido_da_400(self):
        resp = self._toggle(indice="abc")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["erro"], "Índice inválido.")

    def test_card_vazio_da_400(self):
        resp = self._toggle(html="   ")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["erro"], "Card vazio.")

    def test_subtopico_de_outro_usuario_da_404(self):
        outro = User.objects.create_user("dono", password="x")
        alheio = criar_nivel(criar_trilha(outro)).subtopicos.first()
        resp = self.client.post(
            reverse("trilhas:salvo_toggle"),
            {"subtopico": alheio.pk, "indice": "0", "html": "<p>x</p>"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_pagina_agrupa_os_cards_por_trilha(self):
        self._toggle()
        resp = self.client.get(reverse("trilhas:salvos"))

        self.assertEqual(resp.context["total"], 1)
        grupos = list(resp.context["grupos"])
        self.assertEqual(grupos[0][0], self.trilha)


class CertificadoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cert", password="x", first_name="Ana")
        self.client.force_login(self.user)
        self.trilha = criar_trilha(self.user)

    def test_trilha_incompleta_nao_emite(self):
        criar_nivel(self.trilha, n_subs=0)
        resp = self.client.get(reverse("trilhas:certificado", args=[self.trilha.pk]), follow=True)

        self.assertEqual(
            resp.redirect_chain[-1][0], reverse("trilhas:detalhe", args=[self.trilha.pk])
        )
        self.assertIn("Conclua todos os níveis para emitir o certificado.", mensagens(resp))

    def test_trilha_concluida_gera_o_pdf(self):
        criar_nivel(self.trilha, status=Nivel.Status.APROVADO, n_subs=0)
        pdf_falso = b"%PDF-1.7 fake"
        html_mock = mock.Mock()
        html_mock.return_value.write_pdf.return_value = pdf_falso

        with mock.patch.dict("sys.modules", {"weasyprint": mock.Mock(HTML=html_mock)}):
            resp = self.client.get(reverse("trilhas:certificado", args=[self.trilha.pk]))

        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn(f"certificado-{self.trilha.pk}.pdf", resp["Content-Disposition"])
        self.assertEqual(resp.content, pdf_falso)

    def test_sem_weasyprint_cai_na_versao_web(self):
        criar_nivel(self.trilha, status=Nivel.Status.APROVADO, n_subs=0)
        with mock.patch.dict("sys.modules", {"weasyprint": None}):
            resp = self.client.get(reverse("trilhas:certificado", args=[self.trilha.pk]))

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp["Content-Type"])
        self.assertIn("Ana", resp.content.decode())
