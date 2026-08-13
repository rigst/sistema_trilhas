"""Testes das views de avaliação, exercícios de prática e revisão.

Toda a IA é mockada no ponto de disparo (`ai.tasks.*.delay`): o que estas
suítes checam é o contrato das views — quem pode entrar, o que é gravado e o
que sai no JSON —, não a geração em si.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.quota import LIMITE_GERACOES, MSG_MUITAS_GERACOES, MSG_SEM_QUOTA
from avaliacoes.models import (
    Avaliacao,
    Exercicio,
    ListaExercicios,
    Questao,
    QuestaoRevisao,
    Resposta,
    Revisao,
)
from avaliacoes.spaced import aplicar_sm2
from trilhas.models import Nivel, Subtopico, Trilha

User = get_user_model()


def montar_nivel(user, *, status=Nivel.Status.DISPONIVEL, lido=True, ativa=True):
    """Trilha com um nível e dois subtópicos (lidos ou não)."""
    trilha = Trilha.objects.create(
        user=user,
        tema_livre="Tema",
        titulo="Trilha",
        status=Trilha.Status.EM_ANDAMENTO,
        ativa=ativa,
        nota_minima_aprovacao=7.0,
    )
    nivel = Nivel.objects.create(
        trilha=trilha,
        ordem=1,
        titulo="Fundamentos",
        faixa=Nivel.Faixa.INICIANTE,
        status=status,
    )
    for i in (1, 2):
        Subtopico.objects.create(
            nivel=nivel,
            ordem=i,
            titulo=f"Tópico {i}",
            status=Subtopico.Status.PRONTO,
            lido=lido,
        )
    return trilha, nivel


def mensagens(response):
    return [str(m) for m in response.context["messages"]]


@mock.patch("ai.tasks.task_gerar_avaliacao.delay")
class AvaliacaoIniciarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("aval", password="x")
        self.client.force_login(self.user)
        cache.clear()

    def test_cria_avaliacao_e_dispara_geracao(self, delay):
        _trilha, nivel = montar_nivel(self.user)
        resp = self.client.post(reverse("avaliacoes:iniciar", args=[nivel.pk]))

        avaliacao = Avaliacao.objects.get(nivel=nivel)
        self.assertEqual(avaliacao.status, Avaliacao.Status.GERANDO)
        self.assertEqual(avaliacao.tentativa, 1)
        self.assertRedirects(
            resp, reverse("avaliacoes:detalhe", args=[avaliacao.pk]), fetch_redirect_response=False
        )
        delay.assert_called_once_with(avaliacao.pk)

    def test_nivel_bloqueado_volta_para_a_trilha(self, delay):
        trilha, nivel = montar_nivel(self.user, status=Nivel.Status.BLOQUEADO)
        resp = self.client.post(reverse("avaliacoes:iniciar", args=[nivel.pk]))

        self.assertRedirects(
            resp, reverse("trilhas:detalhe", args=[trilha.pk]), fetch_redirect_response=False
        )
        self.assertFalse(Avaliacao.objects.exists())
        delay.assert_not_called()

    def test_nivel_aprovado_nao_se_reavalia(self, delay):
        # Regressão: reavaliar um nível aprovado gastaria IA à toa e abriria
        # brecha para reganhar XP de aprovação.
        _trilha, nivel = montar_nivel(self.user, status=Nivel.Status.APROVADO)
        resp = self.client.post(reverse("avaliacoes:iniciar", args=[nivel.pk]), follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("trilhas:nivel", args=[nivel.pk]))
        self.assertIn(
            "Você já foi aprovado neste nível. Use a revisão para praticar.", mensagens(resp)
        )
        self.assertFalse(Avaliacao.objects.exists())
        delay.assert_not_called()

    def test_exige_ter_lido_todos_os_topicos(self, delay):
        _trilha, nivel = montar_nivel(self.user, lido=False)
        resp = self.client.post(reverse("avaliacoes:iniciar", args=[nivel.pk]), follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("trilhas:nivel", args=[nivel.pk]))
        self.assertIn("Leia todos os tópicos do nível antes de fazer a avaliação.", mensagens(resp))
        delay.assert_not_called()

    def test_avaliacao_em_andamento_e_retomada_sem_gerar_outra(self, delay):
        _trilha, nivel = montar_nivel(self.user)
        em_curso = Avaliacao.objects.create(
            nivel=nivel, tentativa=1, status=Avaliacao.Status.PRONTA
        )
        resp = self.client.post(reverse("avaliacoes:iniciar", args=[nivel.pk]))

        self.assertRedirects(
            resp, reverse("avaliacoes:detalhe", args=[em_curso.pk]), fetch_redirect_response=False
        )
        self.assertEqual(Avaliacao.objects.count(), 1)
        delay.assert_not_called()

    def test_nova_tentativa_incrementa_o_contador(self, delay):
        _trilha, nivel = montar_nivel(self.user)
        Avaliacao.objects.create(nivel=nivel, tentativa=1, status=Avaliacao.Status.CORRIGIDA)
        self.client.post(reverse("avaliacoes:iniciar", args=[nivel.pk]))

        nova = Avaliacao.objects.exclude(tentativa=1).get()
        self.assertEqual(nova.tentativa, 2)

    def test_sem_quota_mensal_nao_gera(self, delay):
        _trilha, nivel = montar_nivel(self.user)
        with mock.patch("accounts.quota.sem_quota_ia", return_value=True):
            resp = self.client.post(reverse("avaliacoes:iniciar", args=[nivel.pk]), follow=True)

        self.assertIn(MSG_SEM_QUOTA, mensagens(resp))
        self.assertFalse(Avaliacao.objects.exists())
        delay.assert_not_called()

    def test_rajada_de_geracoes_e_barrada(self, delay):
        _trilha, nivel = montar_nivel(self.user)
        url = reverse("avaliacoes:iniciar", args=[nivel.pk])
        # Cada POST consome uma geração; a partir do limite, o throttle barra.
        for _ in range(LIMITE_GERACOES):
            self.client.post(url)
            Avaliacao.objects.update(status=Avaliacao.Status.CORRIGIDA)

        resp = self.client.post(url, follow=True)
        self.assertIn(MSG_MUITAS_GERACOES, mensagens(resp))
        self.assertEqual(Avaliacao.objects.count(), LIMITE_GERACOES)

    def test_nivel_de_outro_usuario_da_404(self, delay):
        outro = User.objects.create_user("outro", password="x")
        _trilha, nivel = montar_nivel(outro)
        resp = self.client.post(reverse("avaliacoes:iniciar", args=[nivel.pk]))
        self.assertEqual(resp.status_code, 404)


class AvaliacaoFluxoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("fluxo", password="x")
        self.client.force_login(self.user)
        _trilha, self.nivel = montar_nivel(self.user)
        self.avaliacao = Avaliacao.objects.create(
            nivel=self.nivel, tentativa=1, status=Avaliacao.Status.PRONTA
        )
        for i in range(1, 4):
            Questao.objects.create(
                avaliacao=self.avaliacao,
                ordem=i,
                tipo=Questao.Tipo.OBJETIVA,
                enunciado_md=f"Pergunta {i}",
                alternativas=[
                    {"letra": "A", "texto": "certa"},
                    {"letra": "B", "texto": "errada"},
                ],
                gabarito="A",
            )

    def _payload(self, letras):
        questoes = list(self.avaliacao.questoes.all())
        return {f"alt_{q.pk}": letra for q, letra in zip(questoes, letras, strict=True)}

    def test_detalhe_lista_as_questoes(self):
        resp = self.client.get(reverse("avaliacoes:detalhe", args=[self.avaliacao.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["questoes"]), 3)
        self.assertContains(resp, "Pergunta 1")

    def test_detalhe_de_avaliacao_corrigida_vai_para_o_resultado(self):
        Avaliacao.objects.filter(pk=self.avaliacao.pk).update(status=Avaliacao.Status.CORRIGIDA)
        resp = self.client.get(reverse("avaliacoes:detalhe", args=[self.avaliacao.pk]))
        self.assertRedirects(
            resp,
            reverse("avaliacoes:resultado", args=[self.avaliacao.pk]),
            fetch_redirect_response=False,
        )

    @mock.patch("ai.tasks.task_corrigir_avaliacao.delay")
    def test_submeter_grava_respostas_e_manda_corrigir(self, delay):
        xp0 = self.user.profile.xp
        resp = self.client.post(
            reverse("avaliacoes:submeter", args=[self.avaliacao.pk]), self._payload("ABA")
        )

        self.avaliacao.refresh_from_db()
        self.assertEqual(self.avaliacao.status, Avaliacao.Status.CORRIGINDO)
        self.assertEqual(
            list(
                Resposta.objects.order_by("questao__ordem").values_list(
                    "alternativa_escolhida", flat=True
                )
            ),
            ["A", "B", "A"],
        )
        delay.assert_called_once_with(self.avaliacao.pk)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.xp, xp0 + self.user.profile.XP_AVALIACAO)
        self.assertRedirects(
            resp,
            reverse("avaliacoes:detalhe", args=[self.avaliacao.pk]),
            fetch_redirect_response=False,
        )

    @mock.patch("ai.tasks.task_corrigir_avaliacao.delay")
    def test_submeter_com_questao_em_branco_nao_grava_nada(self, delay):
        payload = self._payload("ABA")
        payload[next(iter(payload))] = ""
        resp = self.client.post(
            reverse("avaliacoes:submeter", args=[self.avaliacao.pk]), payload, follow=True
        )

        self.assertIn("Escolha uma alternativa válida em cada questão.", mensagens(resp))
        # Nada é gravado: a submissão é tudo-ou-nada.
        self.assertFalse(Resposta.objects.exists())
        self.avaliacao.refresh_from_db()
        self.assertEqual(self.avaliacao.status, Avaliacao.Status.PRONTA)
        delay.assert_not_called()

    @mock.patch("ai.tasks.task_corrigir_avaliacao.delay")
    def test_submeter_com_letra_fora_do_conjunto_e_recusado(self, delay):
        resp = self.client.post(
            reverse("avaliacoes:submeter", args=[self.avaliacao.pk]),
            self._payload("AZA"),
            follow=True,
        )
        self.assertIn("Escolha uma alternativa válida em cada questão.", mensagens(resp))
        self.assertFalse(Resposta.objects.exists())
        delay.assert_not_called()

    @mock.patch("ai.tasks.task_corrigir_avaliacao.delay")
    def test_submeter_duas_vezes_nao_recorrige(self, delay):
        self.client.post(
            reverse("avaliacoes:submeter", args=[self.avaliacao.pk]), self._payload("AAA")
        )
        self.client.post(
            reverse("avaliacoes:submeter", args=[self.avaliacao.pk]), self._payload("BBB")
        )
        # A segunda submissão cai no guarda de status (já está CORRIGINDO).
        delay.assert_called_once()
        self.assertEqual(
            set(Resposta.objects.values_list("alternativa_escolhida", flat=True)), {"A"}
        )

    def test_status_devolve_json(self):
        resp = self.client.get(reverse("avaliacoes:status", args=[self.avaliacao.pk]))
        self.assertEqual(resp.json(), {"status": Avaliacao.Status.PRONTA, "erro": ""})

    def test_status_de_avaliacao_alheia_da_404(self):
        outro = User.objects.create_user("intruso", password="x")
        self.client.force_login(outro)
        resp = self.client.get(reverse("avaliacoes:status", args=[self.avaliacao.pk]))
        self.assertEqual(resp.status_code, 404)


@mock.patch("ai.tasks.task_gerar_exercicios.delay")
class ExerciciosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pratica", password="x")
        self.client.force_login(self.user)

    def test_primeira_visita_cria_a_lista_e_dispara_geracao(self, delay):
        _trilha, nivel = montar_nivel(self.user)
        resp = self.client.get(reverse("avaliacoes:exercicios", args=[nivel.pk]))

        lista = ListaExercicios.objects.get(nivel=nivel)
        self.assertEqual(lista.status, ListaExercicios.Status.GERANDO)
        delay.assert_called_once_with(lista.pk)
        self.assertEqual(resp.status_code, 200)

    def test_lista_pronta_nao_regenera(self, delay):
        _trilha, nivel = montar_nivel(self.user)
        ListaExercicios.objects.create(nivel=nivel, status=ListaExercicios.Status.PRONTA)
        self.client.get(reverse("avaliacoes:exercicios", args=[nivel.pk]))
        delay.assert_not_called()

    def test_lista_com_erro_e_regenerada(self, delay):
        _trilha, nivel = montar_nivel(self.user)
        lista = ListaExercicios.objects.create(
            nivel=nivel, status=ListaExercicios.Status.ERRO, erro="estourou"
        )
        self.client.get(reverse("avaliacoes:exercicios", args=[nivel.pk]))

        lista.refresh_from_db()
        self.assertEqual(lista.status, ListaExercicios.Status.GERANDO)
        self.assertEqual(lista.erro, "")
        delay.assert_called_once_with(lista.pk)

    def test_nivel_bloqueado_volta_para_a_trilha(self, delay):
        trilha, nivel = montar_nivel(self.user, status=Nivel.Status.BLOQUEADO)
        resp = self.client.get(reverse("avaliacoes:exercicios", args=[nivel.pk]), follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("trilhas:detalhe", args=[trilha.pk]))
        self.assertIn("Este nível ainda está bloqueado.", mensagens(resp))
        self.assertFalse(ListaExercicios.objects.exists())

    def test_exige_ter_lido_os_topicos(self, delay):
        _trilha, nivel = montar_nivel(self.user, lido=False)
        resp = self.client.get(reverse("avaliacoes:exercicios", args=[nivel.pk]), follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("trilhas:nivel", args=[nivel.pk]))
        self.assertIn("Leia todos os tópicos do nível antes de praticar.", mensagens(resp))

    def test_status_devolve_json(self, delay):
        _trilha, nivel = montar_nivel(self.user)
        lista = ListaExercicios.objects.create(
            nivel=nivel, status=ListaExercicios.Status.ERRO, erro="falhou"
        )
        resp = self.client.get(reverse("avaliacoes:exercicios_status", args=[lista.pk]))
        self.assertEqual(resp.json(), {"status": ListaExercicios.Status.ERRO, "erro": "falhou"})


class ExercicioVerificarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("verif", password="x")
        self.client.force_login(self.user)
        _trilha, self.nivel = montar_nivel(self.user)
        self.lista = ListaExercicios.objects.create(
            nivel=self.nivel, status=ListaExercicios.Status.PRONTA
        )
        self.ex = Exercicio.objects.create(
            lista=self.lista,
            ordem=1,
            tipo=Exercicio.Tipo.OBJETIVA,
            enunciado_md="2 + 2?",
            alternativas=[{"letra": "A", "texto": "4"}, {"letra": "B", "texto": "5"}],
            gabarito="A",
            explicacao_md="Porque **4**.",
        )

    def _post(self, alternativa):
        return self.client.post(
            reverse("avaliacoes:exercicio_verificar", args=[self.ex.pk]),
            {"alternativa": alternativa},
        )

    def test_acerto_grava_nota_e_da_xp(self):
        xp0 = self.user.profile.xp
        dados = self._post("A").json()

        self.assertTrue(dados["correto"])
        self.assertEqual(dados["gabarito"], "A")
        self.assertEqual(dados["xp_ganho"], self.user.profile.XP_EXERCICIO)
        self.assertTrue(dados["concluida"])  # único exercício da lista
        self.assertIn("<strong>4</strong>", dados["feedback_html"])

        self.ex.refresh_from_db()
        self.assertEqual(self.ex.nota, 10.0)
        self.assertIsNotNone(self.ex.respondido_em)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.xp, xp0 + self.user.profile.XP_EXERCICIO)

    def test_erro_grava_nota_zero(self):
        dados = self._post("B").json()
        self.assertFalse(dados["correto"])
        self.ex.refresh_from_db()
        self.assertEqual(self.ex.nota, 0.0)

    def test_gabarito_em_minuscula_ainda_bate(self):
        Exercicio.objects.filter(pk=self.ex.pk).update(gabarito=" a ")
        self.assertTrue(self._post("a").json()["correto"])

    def test_alternativa_invalida_da_400(self):
        resp = self._post("Z")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["erro"], "Alternativa inválida.")
        self.ex.refresh_from_db()
        self.assertIsNone(self.ex.respondido_em)

    def test_alternativa_ausente_da_400(self):
        resp = self.client.post(reverse("avaliacoes:exercicio_verificar", args=[self.ex.pk]))
        self.assertEqual(resp.status_code, 400)

    def test_resposta_e_definitiva(self):
        # Regressão (farm de XP): reenviar não muda a nota nem paga de novo.
        self._post("B")
        self.user.profile.refresh_from_db()
        xp_apos_1a = self.user.profile.xp

        resp = self._post("A")
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.json()["ja_respondido"])
        self.assertFalse(resp.json()["correto"])

        self.ex.refresh_from_db()
        self.assertEqual(self.ex.alternativa_escolhida, "B")
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.xp, xp_apos_1a)

    def test_exercicio_de_outro_usuario_da_404(self):
        outro = User.objects.create_user("alheio", password="x")
        self.client.force_login(outro)
        self.assertEqual(self._post("A").status_code, 404)


class RevisaoViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rev", password="x")
        self.client.force_login(self.user)
        cache.clear()

    def _trilha_com_aprovado(self, *, ativa=True):
        trilha, nivel = montar_nivel(self.user, status=Nivel.Status.APROVADO, ativa=ativa)
        return trilha, nivel

    @mock.patch("ai.tasks.task_gerar_revisao.delay")
    def test_revisao_em_geracao_nao_dispara_outra(self, delay):
        self._trilha_com_aprovado()
        em_curso = Revisao.objects.create(user=self.user, status=Revisao.Status.GERANDO)
        resp = self.client.post(reverse("avaliacoes:revisar"))

        self.assertRedirects(
            resp, reverse("avaliacoes:revisao", args=[em_curso.pk]), fetch_redirect_response=False
        )
        delay.assert_not_called()
        self.assertEqual(Revisao.objects.count(), 1)

    @mock.patch("ai.tasks.task_gerar_revisao.delay")
    def test_revisao_pronta_incompleta_e_retomada(self, delay):
        self._trilha_com_aprovado()
        pendente = Revisao.objects.create(user=self.user, status=Revisao.Status.PRONTA)
        QuestaoRevisao.objects.create(revisao=pendente, ordem=1, enunciado_md="q", gabarito="A")

        resp = self.client.post(reverse("avaliacoes:revisar"))
        self.assertRedirects(
            resp, reverse("avaliacoes:revisao", args=[pendente.pk]), fetch_redirect_response=False
        )
        delay.assert_not_called()

    @mock.patch("ai.tasks.task_gerar_revisao.delay")
    def test_nova_revisao_descarta_a_incompleta(self, delay):
        self._trilha_com_aprovado()
        pendente = Revisao.objects.create(user=self.user, status=Revisao.Status.PRONTA)
        QuestaoRevisao.objects.create(revisao=pendente, ordem=1, enunciado_md="q", gabarito="A")

        self.client.post(reverse("avaliacoes:revisar"), {"nova": "1"})

        self.assertFalse(Revisao.objects.filter(pk=pendente.pk).exists())
        nova = Revisao.objects.get()
        self.assertEqual(nova.status, Revisao.Status.GERANDO)
        delay.assert_called_once_with(nova.pk)

    @mock.patch("ai.tasks.task_gerar_revisao.delay")
    def test_revisao_sem_quota_nao_gera(self, delay):
        self._trilha_com_aprovado()
        with mock.patch("accounts.quota.sem_quota_ia", return_value=True):
            resp = self.client.post(reverse("avaliacoes:revisar"), follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("dashboard"))
        self.assertIn(MSG_SEM_QUOTA, mensagens(resp))
        self.assertFalse(Revisao.objects.exists())
        delay.assert_not_called()

    @mock.patch("ai.tasks.task_gerar_revisao.delay")
    def test_revisar_trilha_passa_o_id_da_trilha(self, delay):
        trilha, _nivel = self._trilha_com_aprovado()
        resp = self.client.post(reverse("avaliacoes:revisar_trilha", args=[trilha.pk]))

        revisao = Revisao.objects.get()
        delay.assert_called_once_with(revisao.pk, trilha_id=trilha.pk)
        self.assertRedirects(
            resp, reverse("avaliacoes:revisao", args=[revisao.pk]), fetch_redirect_response=False
        )

    @mock.patch("ai.tasks.task_gerar_revisao.delay")
    def test_revisar_trilha_exige_nivel_aprovado(self, delay):
        trilha, _nivel = montar_nivel(self.user, status=Nivel.Status.DISPONIVEL)
        resp = self.client.post(reverse("avaliacoes:revisar_trilha", args=[trilha.pk]), follow=True)

        self.assertEqual(resp.redirect_chain[-1][0], reverse("trilhas:detalhe", args=[trilha.pk]))
        self.assertIn("Conclua ao menos um nível desta trilha para revisar.", mensagens(resp))
        delay.assert_not_called()

    @mock.patch("ai.tasks.task_gerar_revisao.delay")
    def test_revisar_trilha_sem_quota_nao_gera(self, delay):
        trilha, _nivel = self._trilha_com_aprovado()
        with mock.patch("accounts.quota.sem_quota_ia", return_value=True):
            resp = self.client.post(
                reverse("avaliacoes:revisar_trilha", args=[trilha.pk]), follow=True
            )

        self.assertIn(MSG_SEM_QUOTA, mensagens(resp))
        self.assertFalse(Revisao.objects.exists())

    def test_revisar_trilha_de_outro_usuario_da_404(self):
        outro = User.objects.create_user("dono", password="x")
        trilha, _nivel = montar_nivel(outro, status=Nivel.Status.APROVADO)
        resp = self.client.post(reverse("avaliacoes:revisar_trilha", args=[trilha.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_detalhe_renderiza_o_markdown_das_questoes(self):
        revisao = Revisao.objects.create(user=self.user, status=Revisao.Status.PRONTA)
        QuestaoRevisao.objects.create(
            revisao=revisao,
            ordem=1,
            enunciado_md="Qual é o **maior**?",
            alternativas=[{"letra": "A", "texto": "esse"}],
            gabarito="A",
            explicacao_md="Porque _sim_.",
        )
        resp = self.client.get(reverse("avaliacoes:revisao", args=[revisao.pk]))

        self.assertEqual(resp.status_code, 200)
        item = resp.context["itens"][0]
        self.assertIn("<strong>maior</strong>", item["enunciado_html"])
        self.assertIn("<em>sim</em>", item["explicacao_html"])

    def test_status_devolve_json(self):
        revisao = Revisao.objects.create(user=self.user, status=Revisao.Status.ERRO, erro="ops")
        resp = self.client.get(reverse("avaliacoes:revisao_status", args=[revisao.pk]))
        self.assertEqual(resp.json(), {"status": Revisao.Status.ERRO, "erro": "ops"})

    def test_status_de_revisao_alheia_da_404(self):
        outro = User.objects.create_user("terceiro", password="x")
        revisao = Revisao.objects.create(user=outro, status=Revisao.Status.PRONTA)
        resp = self.client.get(reverse("avaliacoes:revisao_status", args=[revisao.pk]))
        self.assertEqual(resp.status_code, 404)


class QuestaoRevisaoVerificarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("qrev", password="x")
        self.client.force_login(self.user)
        _trilha, self.nivel = montar_nivel(self.user, status=Nivel.Status.APROVADO)
        self.nivel.iniciar_revisao_espacada()
        self.revisao = Revisao.objects.create(user=self.user, status=Revisao.Status.PRONTA)
        self.q1, self.q2 = (
            QuestaoRevisao.objects.create(
                revisao=self.revisao,
                ordem=i,
                nivel=self.nivel,
                enunciado_md=f"Pergunta {i}",
                alternativas=[{"letra": "A", "texto": "certa"}, {"letra": "B", "texto": "errada"}],
                gabarito="A",
                explicacao_md=f"Explicação {i}",
            )
            for i in (1, 2)
        )

    def _post(self, questao, alternativa):
        return self.client.post(
            reverse("avaliacoes:revisao_verificar", args=[questao.pk]),
            {"alternativa": alternativa},
        )

    def test_acerto_da_xp_e_marca_a_nota(self):
        xp0 = self.user.profile.xp
        dados = self._post(self.q1, "A").json()

        self.assertTrue(dados["correto"])
        self.assertFalse(dados["concluida"])  # ainda falta a q2
        self.assertEqual(dados["xp_ganho"], self.user.profile.XP_EXERCICIO)
        self.q1.refresh_from_db()
        self.assertEqual(self.q1.nota, 10.0)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.xp, xp0 + self.user.profile.XP_EXERCICIO)

    def test_alternativa_invalida_da_400(self):
        resp = self._post(self.q1, "X")
        self.assertEqual(resp.status_code, 400)
        self.q1.refresh_from_db()
        self.assertIsNone(self.q1.respondido_em)

    def test_resposta_e_definitiva(self):
        self._post(self.q1, "B")
        resp = self._post(self.q1, "A")
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.json()["ja_respondido"])
        self.q1.refresh_from_db()
        self.assertEqual(self.q1.alternativa_escolhida, "B")

    def test_ultima_questao_conclui_e_reagenda_o_sm2(self):
        self.assertEqual(self.nivel.revisao_repeticoes, 0)
        self._post(self.q1, "A")
        # Enquanto falta questão, a revisão não está concluída e o SM-2 não roda.
        self.nivel.refresh_from_db()
        self.assertIsNone(self.nivel.revisao_ultima)

        dados = self._post(self.q2, "A").json()
        self.assertTrue(dados["concluida"])

        # 100% de acertos -> qualidade 5 -> repetição contabilizada e reagendada.
        self.nivel.refresh_from_db()
        self.assertEqual(self.nivel.revisao_repeticoes, 1)
        self.assertEqual(self.nivel.revisao_ultima, timezone.localdate())
        self.assertEqual(
            self.nivel.revisao_proxima,
            timezone.localdate() + timezone.timedelta(days=self.nivel.revisao_intervalo),
        )

    def test_questao_de_outro_usuario_da_404(self):
        outro = User.objects.create_user("estranho", password="x")
        self.client.force_login(outro)
        self.assertEqual(self._post(self.q1, "A").status_code, 404)


class AplicarSM2Tests(TestCase):
    """`aplicar_sm2` roda ao concluir a revisão e reagenda cada nível avaliado."""

    def setUp(self):
        self.user = User.objects.create_user("sm2", password="x")
        _trilha, self.nivel = montar_nivel(self.user, status=Nivel.Status.APROVADO)
        self.nivel.iniciar_revisao_espacada()
        self.revisao = Revisao.objects.create(user=self.user, status=Revisao.Status.PRONTA)

    def _questao(self, *, nota, respondida=True, nivel=True):
        return QuestaoRevisao.objects.create(
            revisao=self.revisao,
            ordem=QuestaoRevisao.objects.filter(revisao=self.revisao).count() + 1,
            nivel=self.nivel if nivel else None,
            enunciado_md="q",
            gabarito="A",
            nota=nota,
            respondido_em=timezone.now() if respondida else None,
        )

    def test_desempenho_alto_alonga_o_intervalo(self):
        for _ in range(4):
            self._questao(nota=10.0)
        aplicar_sm2(self.revisao)

        self.nivel.refresh_from_db()
        self.assertEqual(self.nivel.revisao_repeticoes, 1)
        self.assertEqual(self.nivel.revisao_ultima, timezone.localdate())

    def test_desempenho_ruim_reseta_o_intervalo(self):
        self.nivel.registrar_revisao(5)
        self.nivel.registrar_revisao(5)
        self.assertGreater(self.nivel.revisao_intervalo, 1)

        for _ in range(4):
            self._questao(nota=0.0)  # 0% -> qualidade 1 -> reset
        aplicar_sm2(self.revisao)

        self.nivel.refresh_from_db()
        self.assertEqual(self.nivel.revisao_intervalo, 1)
        self.assertEqual(self.nivel.revisao_repeticoes, 0)

    def test_questoes_sem_nivel_ou_nao_respondidas_sao_ignoradas(self):
        self._questao(nota=10.0, nivel=False)  # sem nível de origem
        self._questao(nota=10.0, respondida=False)  # ainda em branco
        aplicar_sm2(self.revisao)

        self.nivel.refresh_from_db()
        self.assertIsNone(self.nivel.revisao_ultima)  # nada foi reagendado

    def test_nivel_nao_aprovado_nao_e_reagendado(self):
        Nivel.objects.filter(pk=self.nivel.pk).update(status=Nivel.Status.DISPONIVEL)
        self.nivel.refresh_from_db()
        self._questao(nota=10.0)
        aplicar_sm2(self.revisao)

        self.nivel.refresh_from_db()
        self.assertIsNone(self.nivel.revisao_ultima)
