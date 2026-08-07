"""Testes do fluxo de avaliação objetiva e da revisão (sem tocar na IA)."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ai.services import corrigir_avaliacao
from avaliacoes.models import Avaliacao, Questao, Resposta, Titulo
from avaliacoes.spaced import _qualidade
from trilhas.models import Nivel, Trilha

User = get_user_model()


def montar_avaliacao(user, respostas, *, ativa=True):
    """Cria trilha (2 níveis) + avaliação objetiva do nível 1 já respondida.
    `respostas` é a lista de letras escolhidas; o gabarito é sempre 'A'."""
    trilha = Trilha.objects.create(
        user=user,
        tema_livre="Tema",
        titulo="Trilha Teste",
        status=Trilha.Status.EM_ANDAMENTO,
        ativa=ativa,
        nota_minima_aprovacao=7.0,
    )
    n1 = Nivel.objects.create(
        trilha=trilha,
        ordem=1,
        titulo="Fundamentos",
        faixa=Nivel.Faixa.INICIANTE,
        status=Nivel.Status.DISPONIVEL,
        titulo_concedido="Iniciante em Tema",
    )
    n2 = Nivel.objects.create(
        trilha=trilha,
        ordem=2,
        titulo="Próximo",
        faixa=Nivel.Faixa.INTERMEDIARIO,
        status=Nivel.Status.BLOQUEADO,
    )
    av = Avaliacao.objects.create(nivel=n1, tentativa=1, status=Avaliacao.Status.PRONTA)
    for i, escolha in enumerate(respostas, start=1):
        q = Questao.objects.create(
            avaliacao=av,
            ordem=i,
            tipo=Questao.Tipo.OBJETIVA,
            enunciado_md=f"Pergunta {i}",
            alternativas=[{"letra": "A", "texto": "certa"}, {"letra": "B", "texto": "errada"}],
            gabarito="A",
            peso=1.0,
        )
        Resposta.objects.create(questao=q, alternativa_escolhida=escolha)
    return trilha, n1, n2, av


class CorrecaoAprovacaoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password="x")

    def test_aprovacao_concede_titulo_e_desbloqueia_proximo(self):
        _trilha, n1, n2, av = montar_avaliacao(self.user, ["A"] * 10)
        corrigir_avaliacao(av, self.user.profile)
        av.refresh_from_db()
        n1.refresh_from_db()
        n2.refresh_from_db()

        self.assertEqual(av.nota_final, 10.0)
        self.assertTrue(av.aprovado)
        self.assertEqual(n1.status, Nivel.Status.APROVADO)
        self.assertEqual(n2.status, Nivel.Status.DISPONIVEL)  # próximo liberado
        self.assertTrue(Titulo.objects.filter(nivel=n1).exists())

    def test_reprovacao_nao_concede_titulo(self):
        # 5 certas, 5 erradas -> nota 5.0 < 7.0
        _trilha, n1, n2, av = montar_avaliacao(self.user, ["A"] * 5 + ["B"] * 5)
        corrigir_avaliacao(av, self.user.profile)
        av.refresh_from_db()
        n1.refresh_from_db()
        n2.refresh_from_db()

        self.assertEqual(av.nota_final, 5.0)
        self.assertFalse(av.aprovado)
        self.assertNotEqual(n1.status, Nivel.Status.APROVADO)
        self.assertEqual(n2.status, Nivel.Status.BLOQUEADO)
        self.assertFalse(Titulo.objects.filter(nivel=n1).exists())

    def test_aprovacao_soma_xp(self):
        _trilha, _n1, _n2, av = montar_avaliacao(self.user, ["A"] * 10)
        xp0 = self.user.profile.xp
        corrigir_avaliacao(av, self.user.profile)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.xp, xp0 + self.user.profile.XP_APROVACAO)

    def test_aprovacao_agenda_revisao_espacada(self):
        _trilha, n1, _n2, av = montar_avaliacao(self.user, ["A"] * 10)
        corrigir_avaliacao(av, self.user.profile)
        n1.refresh_from_db()
        # 1ª revisão agendada para amanhã
        self.assertEqual(n1.revisao_proxima, timezone.localdate() + timezone.timedelta(days=1))
        self.assertFalse(n1.revisao_devida)

    def test_reaprovar_nivel_nao_reganha_xp_nem_reseta_sm2(self):
        # Regressão: corrigir uma nova avaliação de um nível JÁ aprovado não
        # pode reganhar XP de aprovação nem zerar o progresso de revisão espaçada.
        _trilha, n1, _n2, av = montar_avaliacao(self.user, ["A"] * 10)
        corrigir_avaliacao(av, self.user.profile)
        self.user.profile.refresh_from_db()
        xp_apos_1a = self.user.profile.xp

        # Avança o SM-2 do nível (simula revisões feitas depois da aprovação).
        n1.refresh_from_db()
        n1.registrar_revisao(5)
        n1.registrar_revisao(5)
        intervalo_antes = n1.revisao_intervalo
        self.assertGreater(intervalo_antes, 1)

        # Segunda avaliação do mesmo nível, novamente com nota máxima.
        av2 = Avaliacao.objects.create(nivel=n1, tentativa=2, status=Avaliacao.Status.PRONTA)
        for q in av.questoes.all():
            nq = Questao.objects.create(
                avaliacao=av2,
                ordem=q.ordem,
                tipo=Questao.Tipo.OBJETIVA,
                enunciado_md=q.enunciado_md,
                alternativas=q.alternativas,
                gabarito="A",
                peso=1.0,
            )
            Resposta.objects.create(questao=nq, alternativa_escolhida="A")
        corrigir_avaliacao(av2, self.user.profile)

        self.user.profile.refresh_from_db()
        n1.refresh_from_db()
        self.assertEqual(self.user.profile.xp, xp_apos_1a)  # XP não dobrou
        self.assertEqual(n1.revisao_intervalo, intervalo_antes)  # SM-2 preservado


class SM2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password="x")
        self.trilha = Trilha.objects.create(
            user=self.user, tema_livre="T", titulo="T", status=Trilha.Status.EM_ANDAMENTO
        )
        self.n = Nivel.objects.create(
            trilha=self.trilha,
            ordem=1,
            titulo="N",
            faixa=Nivel.Faixa.INICIANTE,
            status=Nivel.Status.APROVADO,
        )
        self.n.iniciar_revisao_espacada()

    def test_flashcard_so_da_xp_quando_devido(self):
        # Regressão (farm de XP): revisao_rapida só concede XP quando o cartão
        # estava realmente devido. Após revisar, o SM-2 reagenda e repetir o
        # POST no mesmo dia não rende mais XP.
        self.client.force_login(self.user)
        Nivel.objects.filter(pk=self.n.pk).update(revisao_proxima=timezone.localdate())
        self.user.profile.refresh_from_db()
        xp0 = self.user.profile.xp

        url = reverse("avaliacoes:revisao_rapida", args=[self.n.pk])
        r1 = self.client.post(url, {"resposta": "lembrei"})
        self.assertEqual(r1.json()["xp_ganho"], self.user.profile.XP_EXERCICIO)

        # Segundo POST no mesmo dia: cartão já não está devido -> sem XP.
        r2 = self.client.post(url, {"resposta": "lembrei"})
        self.assertEqual(r2.json()["xp_ganho"], 0)

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.xp, xp0 + self.user.profile.XP_EXERCICIO)

    def test_qualidade_mapeia_percentual(self):
        self.assertEqual(_qualidade(1.0), 5)
        self.assertEqual(_qualidade(0.5), 3)
        self.assertEqual(_qualidade(0.0), 1)

    def test_boa_revisao_alonga_intervalo(self):
        self.n.registrar_revisao(5)  # rep 1 -> intervalo 1
        self.assertEqual(self.n.revisao_intervalo, 1)
        self.n.registrar_revisao(5)  # rep 2 -> intervalo 6
        self.assertEqual(self.n.revisao_intervalo, 6)
        self.n.registrar_revisao(5)  # rep 3 -> ~6*ef
        self.assertGreater(self.n.revisao_intervalo, 6)

    def test_revisao_ruim_reseta_para_um_dia(self):
        self.n.registrar_revisao(5)
        self.n.registrar_revisao(5)
        self.n.registrar_revisao(1)  # falha -> volta a 1 dia
        self.assertEqual(self.n.revisao_intervalo, 1)
        self.assertEqual(self.n.revisao_repeticoes, 0)


class RevisaoAtivaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password="x")
        self.client.force_login(self.user)

    def _aprovar_um_nivel(self, ativa=True):
        trilha, _n1, _n2, av = montar_avaliacao(self.user, ["A"] * 10, ativa=ativa)
        corrigir_avaliacao(av, self.user.profile)
        return trilha

    def test_revisar_exige_nivel_aprovado_ativo(self):
        # sem nível aprovado -> volta ao dashboard com aviso
        resp = self.client.post(reverse("avaliacoes:revisar"), follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse("dashboard"))

    @mock.patch("ai.tasks.task_gerar_revisao.delay")
    def test_desativar_trilha_remove_da_revisao(self, delay):
        trilha = self._aprovar_um_nivel(ativa=True)
        # com nível aprovado ativo, a revisão é permitida (cria e redireciona p/ ela)
        resp = self.client.post(reverse("avaliacoes:revisar"))
        self.assertIn("/revisao/", resp["Location"])
        # ao desativar, deixa de haver base para revisar
        Trilha.objects.filter(pk=trilha.pk).update(ativa=False)
        self.user.revisoes.all().delete()
        resp = self.client.post(reverse("avaliacoes:revisar"), follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse("dashboard"))


class TelaCorrecaoTests(TestCase):
    """A correção precisa ensinar: nas erradas, mostra os textos das alternativas."""

    def setUp(self):
        self.user = User.objects.create_user("u2", password="x")
        self.client.force_login(self.user)

    def _resultado(self, respostas):
        _trilha, _n1, _n2, av = montar_avaliacao(self.user, respostas)
        # Textos distinguíveis para conferir o que aparece na tela.
        for i, q in enumerate(av.questoes.all(), start=1):
            q.alternativas = [
                {"letra": "A", "texto": f"alternativa certa {i}"},
                {"letra": "B", "texto": f"alternativa errada {i}"},
            ]
            q.enunciado_md = f"Enunciado completo da pergunta {i}"
            q.save(update_fields=["alternativas", "enunciado_md"])
        corrigir_avaliacao(av, self.user.profile)
        return self.client.get(reverse("avaliacoes:resultado", args=[av.pk]))

    def test_questao_errada_mostra_escolhida_e_correta_com_texto(self):
        resp = self._resultado(["B"] + ["A"] * 9)
        conteudo = resp.content.decode()
        self.assertContains(resp, "Enunciado completo da pergunta 1")
        self.assertIn("alternativa errada 1", conteudo)  # o que ele marcou
        self.assertIn("alternativa certa 1", conteudo)  # o que era esperado
        self.assertIn("Resposta correta:", conteudo)

    def test_questao_certa_nao_repete_o_bloco_de_resposta_correta(self):
        resp = self._resultado(["A"] * 10)
        self.assertNotContains(resp, "Resposta correta:")

    def test_questao_em_branco_aparece_como_em_branco(self):
        resp = self._resultado([""] + ["A"] * 9)
        self.assertContains(resp, "em branco")
        self.assertIn("alternativa certa 1", resp.content.decode())
