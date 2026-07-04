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
        user=user, tema_livre='Tema', titulo='Trilha Teste',
        status=Trilha.Status.EM_ANDAMENTO, ativa=ativa, nota_minima_aprovacao=7.0,
    )
    n1 = Nivel.objects.create(trilha=trilha, ordem=1, titulo='Fundamentos',
                              faixa=Nivel.Faixa.INICIANTE, status=Nivel.Status.DISPONIVEL,
                              titulo_concedido='Iniciante em Tema')
    n2 = Nivel.objects.create(trilha=trilha, ordem=2, titulo='Próximo',
                              faixa=Nivel.Faixa.INTERMEDIARIO, status=Nivel.Status.BLOQUEADO)
    av = Avaliacao.objects.create(nivel=n1, tentativa=1, status=Avaliacao.Status.PRONTA)
    for i, escolha in enumerate(respostas, start=1):
        q = Questao.objects.create(
            avaliacao=av, ordem=i, tipo=Questao.Tipo.OBJETIVA,
            enunciado_md=f'Pergunta {i}',
            alternativas=[{'letra': 'A', 'texto': 'certa'}, {'letra': 'B', 'texto': 'errada'}],
            gabarito='A', peso=1.0,
        )
        Resposta.objects.create(questao=q, alternativa_escolhida=escolha)
    return trilha, n1, n2, av


class CorrecaoAprovacaoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', password='x')

    def test_aprovacao_concede_titulo_e_desbloqueia_proximo(self):
        trilha, n1, n2, av = montar_avaliacao(self.user, ['A'] * 10)
        corrigir_avaliacao(av, self.user.profile)
        av.refresh_from_db(); n1.refresh_from_db(); n2.refresh_from_db()

        self.assertEqual(av.nota_final, 10.0)
        self.assertTrue(av.aprovado)
        self.assertEqual(n1.status, Nivel.Status.APROVADO)
        self.assertEqual(n2.status, Nivel.Status.DISPONIVEL)  # próximo liberado
        self.assertTrue(Titulo.objects.filter(nivel=n1).exists())

    def test_reprovacao_nao_concede_titulo(self):
        # 5 certas, 5 erradas -> nota 5.0 < 7.0
        trilha, n1, n2, av = montar_avaliacao(self.user, ['A'] * 5 + ['B'] * 5)
        corrigir_avaliacao(av, self.user.profile)
        av.refresh_from_db(); n1.refresh_from_db(); n2.refresh_from_db()

        self.assertEqual(av.nota_final, 5.0)
        self.assertFalse(av.aprovado)
        self.assertNotEqual(n1.status, Nivel.Status.APROVADO)
        self.assertEqual(n2.status, Nivel.Status.BLOQUEADO)
        self.assertFalse(Titulo.objects.filter(nivel=n1).exists())

    def test_aprovacao_soma_xp(self):
        trilha, n1, n2, av = montar_avaliacao(self.user, ['A'] * 10)
        xp0 = self.user.profile.xp
        corrigir_avaliacao(av, self.user.profile)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.xp, xp0 + self.user.profile.XP_APROVACAO)

    def test_aprovacao_agenda_revisao_espacada(self):
        trilha, n1, n2, av = montar_avaliacao(self.user, ['A'] * 10)
        corrigir_avaliacao(av, self.user.profile)
        n1.refresh_from_db()
        # 1ª revisão agendada para amanhã
        self.assertEqual(n1.revisao_proxima, timezone.localdate() + timezone.timedelta(days=1))
        self.assertFalse(n1.revisao_devida)


class SM2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', password='x')
        self.trilha = Trilha.objects.create(user=self.user, tema_livre='T', titulo='T',
                                             status=Trilha.Status.EM_ANDAMENTO)
        self.n = Nivel.objects.create(trilha=self.trilha, ordem=1, titulo='N',
                                      faixa=Nivel.Faixa.INICIANTE, status=Nivel.Status.APROVADO)
        self.n.iniciar_revisao_espacada()

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
        self.n.registrar_revisao(5); self.n.registrar_revisao(5)
        self.n.registrar_revisao(1)  # falha -> volta a 1 dia
        self.assertEqual(self.n.revisao_intervalo, 1)
        self.assertEqual(self.n.revisao_repeticoes, 0)


class RevisaoAtivaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', password='x')
        self.client.force_login(self.user)

    def _aprovar_um_nivel(self, ativa=True):
        trilha, n1, n2, av = montar_avaliacao(self.user, ['A'] * 10, ativa=ativa)
        corrigir_avaliacao(av, self.user.profile)
        return trilha

    def test_revisar_exige_nivel_aprovado_ativo(self):
        # sem nível aprovado -> volta ao dashboard com aviso
        resp = self.client.post(reverse('avaliacoes:revisar'), follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse('dashboard'))

    @mock.patch('ai.tasks.task_gerar_revisao.delay')
    def test_desativar_trilha_remove_da_revisao(self, delay):
        trilha = self._aprovar_um_nivel(ativa=True)
        # com nível aprovado ativo, a revisão é permitida (cria e redireciona p/ ela)
        resp = self.client.post(reverse('avaliacoes:revisar'))
        self.assertIn('/revisao/', resp['Location'])
        # ao desativar, deixa de haver base para revisar
        Trilha.objects.filter(pk=trilha.pk).update(ativa=False)
        self.user.revisoes.all().delete()
        resp = self.client.post(reverse('avaliacoes:revisar'), follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse('dashboard'))
