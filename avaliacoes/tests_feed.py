"""Testes do flashcard de revisão rápida (feed) e da revisão."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from avaliacoes.models import QuestaoRevisao, Revisao
from trilhas.models import Nivel, Trilha

User = get_user_model()


def nivel_aprovado(user, *, ativa=True, devida=True):
    trilha = Trilha.objects.create(
        user=user, tema_livre='Tema', titulo='Trilha Feed',
        status=Trilha.Status.EM_ANDAMENTO, ativa=ativa,
    )
    nivel = Nivel.objects.create(
        trilha=trilha, ordem=1, titulo='Fundamentos',
        faixa=Nivel.Faixa.INICIANTE, status=Nivel.Status.APROVADO,
    )
    nivel.iniciar_revisao_espacada()
    if devida:
        nivel.revisao_proxima = timezone.localdate()
        nivel.save(update_fields=['revisao_proxima'])
    return trilha, nivel


class RevisaoRapidaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('flash', password='x')

    def setUp(self):
        self.client.force_login(self.user)

    def _post(self, nivel, resposta):
        return self.client.post(
            reverse('avaliacoes:revisao_rapida', args=[nivel.pk]),
            {'resposta': resposta},
        )

    def test_lembrei_avanca_o_intervalo(self):
        _, nivel = nivel_aprovado(self.user)
        resp = self._post(nivel, 'lembrei')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        nivel.refresh_from_db()
        self.assertEqual(nivel.revisao_repeticoes, 1)
        self.assertGreater(nivel.revisao_proxima, timezone.localdate())
        self.assertEqual(data['proxima_em_dias'],
                         (nivel.revisao_proxima - timezone.localdate()).days)

    def test_rever_reseta_para_amanha(self):
        _, nivel = nivel_aprovado(self.user)
        nivel.revisao_repeticoes = 3
        nivel.revisao_intervalo = 15
        nivel.save(update_fields=['revisao_repeticoes', 'revisao_intervalo'])
        self._post(nivel, 'rever')
        nivel.refresh_from_db()
        self.assertEqual(nivel.revisao_repeticoes, 0)
        self.assertEqual(nivel.revisao_intervalo, 1)
        self.assertEqual(
            nivel.revisao_proxima, timezone.localdate() + timezone.timedelta(days=1)
        )

    def test_resposta_invalida_da_400(self):
        _, nivel = nivel_aprovado(self.user)
        self.assertEqual(self._post(nivel, 'talvez').status_code, 400)

    def test_nivel_de_outro_usuario_da_404(self):
        outro = User.objects.create_user('outro', password='x')
        _, nivel = nivel_aprovado(outro)
        self.assertEqual(self._post(nivel, 'lembrei').status_code, 404)

    def test_nivel_nao_aprovado_da_404(self):
        trilha, nivel = nivel_aprovado(self.user)
        nivel.status = Nivel.Status.DISPONIVEL
        nivel.save(update_fields=['status'])
        self.assertEqual(self._post(nivel, 'lembrei').status_code, 404)

    def test_dashboard_mostra_feed_e_quiz_do_dia(self):
        _, nivel = nivel_aprovado(self.user)
        resp = self.client.get(reverse('dashboard'))
        html = resp.content.decode()
        self.assertIn('Para hoje', html)
        self.assertIn('Revisão devida', html)
        self.assertIn('Quiz do dia', html)

    def test_quiz_do_dia_some_apos_revisao_de_hoje(self):
        _, nivel = nivel_aprovado(self.user, devida=False)
        Revisao.objects.create(user=self.user, status=Revisao.Status.PRONTA)
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('Quiz do dia', html)


class RevisaoRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('deck', password='x')
        _, nivel = nivel_aprovado(cls.user)
        cls.revisao = Revisao.objects.create(
            user=cls.user, status=Revisao.Status.PRONTA
        )
        for i in (1, 2):
            QuestaoRevisao.objects.create(
                revisao=cls.revisao, ordem=i, nivel=nivel,
                origem='Trilha Feed · Fundamentos',
                enunciado_md=f'Pergunta {i}?',
                alternativas=[{'letra': 'A', 'texto': 'sim'},
                              {'letra': 'B', 'texto': 'não'}],
                gabarito='A', explicacao_md='Porque sim.',
            )

    def test_revisao_renderiza_questoes_no_padrao_da_avaliacao(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('avaliacoes:revisao', args=[self.revisao.pk]))
        html = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn('q-block exercise', html)
        self.assertIn('q-enun q-enun--num', html)
        self.assertIn('Trilha Feed · Fundamentos', html)
        self.assertNotIn('story-bars', html)
        self.assertNotIn('TrilhasDeck', html)
