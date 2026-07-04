"""Testes dos fluxos críticos das trilhas (sem tocar na IA)."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ai.services import _catalogo_percurso
from avaliacoes.models import Titulo
from trilhas.models import Nivel, Subtopico, Trilha

User = get_user_model()


def criar_trilha(user, titulo='História do Rio Grande do Sul', *, ativa=True,
                 status=Trilha.Status.EM_ANDAMENTO, n_subs=3):
    trilha = Trilha.objects.create(
        user=user, tema_livre=titulo, titulo=titulo, status=status, ativa=ativa,
    )
    n1 = Nivel.objects.create(
        trilha=trilha, ordem=1, titulo='Fundamentos', faixa=Nivel.Faixa.INICIANTE,
        status=Nivel.Status.DISPONIVEL,
    )
    Nivel.objects.create(
        trilha=trilha, ordem=2, titulo='Avançado', faixa=Nivel.Faixa.INTERMEDIARIO,
        status=Nivel.Status.BLOQUEADO,
    )
    for i in range(1, n_subs + 1):
        Subtopico.objects.create(
            nivel=n1, ordem=i, titulo=f'Tópico {i}',
            status=Subtopico.Status.PRONTO, lido=False,
        )
    return trilha


class SiglaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('u', password='x')

    def _sigla(self, titulo):
        t = Trilha(user=self.user, tema_livre=titulo, titulo=titulo)
        return t.sigla

    def test_ignora_palavras_genericas_e_conectivos(self):
        self.assertEqual(self._sigla('Trilha de Bancos de Dados'), 'BD')
        self.assertEqual(self._sigla('História do Rio Grande do Sul'), 'HR')

    def test_pontuacao_nas_bordas_e_uma_palavra(self):
        self.assertEqual(self._sigla('Trilha de Estudos: Batuque e Nação'), 'BN')
        self.assertEqual(self._sigla('Python'), 'PY')

    def test_vazio(self):
        self.assertEqual(self._sigla(''), '★')


class DesbloqueioSequencialTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', password='x')
        self.trilha = criar_trilha(self.user)
        self.subs = list(self.trilha.niveis.get(ordem=1).subtopicos.order_by('ordem'))

    def test_apenas_o_primeiro_abre_no_inicio(self):
        self.assertTrue(self.subs[0].desbloqueado)
        self.assertFalse(self.subs[1].desbloqueado)
        self.assertFalse(self.subs[2].desbloqueado)

    def test_ler_um_libera_o_proximo(self):
        self.subs[0].lido = True
        self.subs[0].save(update_fields=['lido'])
        self.assertTrue(self.subs[1].desbloqueado)
        self.assertFalse(self.subs[2].desbloqueado)


class DashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', password='x')
        self.client.force_login(self.user)
        self.ativa = criar_trilha(self.user, 'Cálculo Diferencial')
        # dá um título à trilha ativa (para a estante de medalhas)
        n1 = self.ativa.niveis.get(ordem=1)
        Titulo.objects.create(trilha=self.ativa, nivel=n1, nome='Iniciante em Cálculo',
                              faixa=Nivel.Faixa.INICIANTE)
        self.inativa = criar_trilha(self.user, 'Trilha Arquivada', ativa=False)

    def test_render_e_pasta_desativadas(self):
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Desativadas')
        self.assertContains(resp, 'trilha-card--off')

    def test_medalha_ignora_trilha_desativada(self):
        # dá um título também à inativa; não deve entrar na estante de medalhas
        n1 = self.inativa.niveis.get(ordem=1)
        Titulo.objects.create(trilha=self.inativa, nivel=n1, nome='Iniciante Arquivado',
                              faixa=Nivel.Faixa.INICIANTE)
        resp = self.client.get(reverse('dashboard'))
        self.assertContains(resp, 'Iniciante em Cálculo')
        # a estante ("medal-shelf") deve conter exatamente 1 medalha (a da ativa)
        self.assertEqual(resp.content.decode().count('class="medal medal--'), 1)

    def test_queries_nao_escalam_com_numero_de_trilhas(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for i in range(8):
            criar_trilha(self.user, f'Extra {i}')
        self.client.get(reverse('dashboard'))  # aquece (prefetch etc.)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse('dashboard'))
        # com ~10 trilhas, um dashboard sem N+1 fica bem abaixo de 20 queries.
        self.assertLess(len(ctx.captured_queries), 20)


class QuotaGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', password='x')
        self.client.force_login(self.user)

    def test_criar_trilha_bloqueado_sem_quota(self):
        p = self.user.profile
        p.tokens_usados_mes = p.quota_tokens_mes
        p.save(update_fields=['tokens_usados_mes'])
        antes = Trilha.objects.count()
        resp = self.client.post(reverse('trilhas:criar'),
                                {'tema_livre': 'Algo novo'}, follow=True)
        self.assertContains(resp, 'cota mensal')
        self.assertEqual(Trilha.objects.count(), antes)  # nada foi criado

    @mock.patch('ai.tasks.task_gerar_perguntas.delay')
    def test_criar_trilha_ok_com_quota(self, delay):
        resp = self.client.post(reverse('trilhas:criar'),
                                {'tema_livre': 'Algo novo'})
        self.assertEqual(Trilha.objects.filter(user=self.user).count(), 1)
        self.assertEqual(resp.status_code, 302)
        delay.assert_called_once()  # a geração foi enfileirada


class AtivaFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', password='x')

    def test_catalogo_percurso_exclui_desativada(self):
        criar_trilha(self.user, 'Ativa X')
        catalogo, _ = _catalogo_percurso(self.user)
        self.assertTrue(any(k.startswith('aprender:') for k in catalogo))
        # desativa todas as trilhas
        Trilha.objects.filter(user=self.user).update(ativa=False)
        catalogo, _ = _catalogo_percurso(self.user)
        self.assertEqual(catalogo, {})

    def test_estudar_agora_pula_desativada(self):
        self.client.force_login(self.user)
        criar_trilha(self.user, 'Ativa Y')
        resp = self.client.get(reverse('trilhas:estudar_agora'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/topico/', resp['Location'])
        Trilha.objects.filter(user=self.user).update(ativa=False)
        resp = self.client.get(reverse('trilhas:estudar_agora'), follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], reverse('dashboard'))
