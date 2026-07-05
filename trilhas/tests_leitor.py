"""Testes do leitor em cards (stories) e do hero Continuar do dashboard."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from trilhas.models import Subtopico
from trilhas.tests import criar_trilha

User = get_user_model()

MD = (
    "Primeiro parágrafo de introdução ao assunto.\n\n"
    "---\n\n"
    "### Uma seção\n\nTexto da seção com detalhes.\n\n"
    "---\n\n"
    "!!! resumo \"Resumo\"\n    Pontos-chave do tópico.\n"
)


class TopicoRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('leitor', password='x')
        cls.trilha = criar_trilha(cls.user)
        nivel = cls.trilha.niveis.first()
        cls.sub = nivel.subtopicos.first()
        cls.sub.conteudo_md = MD
        cls.sub.save(update_fields=['conteudo_md'])

    def setUp(self):
        self.client.force_login(self.user)

    def test_topico_pronto_renderiza_leitor(self):
        nivel = self.sub.nivel
        resp = self.client.get(
            reverse('trilhas:topico', args=[nivel.pk, self.sub.ordem])
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('story-bars', html)
        self.assertIn('story-final', html)
        self.assertIn('mode-toggle', html)
        self.assertIn('+10 XP', html)          # primeira leitura ganha XP
        self.assertIn('is-reader', html)       # corpo em modo leitura
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.lido)

    def test_releitura_nao_mostra_xp(self):
        nivel = self.sub.nivel
        url = reverse('trilhas:topico', args=[nivel.pk, self.sub.ordem])
        self.client.get(url)
        resp = self.client.get(url)
        self.assertNotIn('+10 XP', resp.content.decode())

    def test_dashboard_mostra_continuar(self):
        resp = self.client.get(reverse('dashboard'))
        html = resp.content.decode()
        self.assertIn('continue-hero', html)
        self.assertIn('Continuar de onde parou', html)
        self.assertIn('app-tabbar', html)


class SalvosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('colecionador', password='x')
        cls.trilha = criar_trilha(cls.user)
        cls.sub = cls.trilha.niveis.first().subtopicos.first()

    def setUp(self):
        self.client.force_login(self.user)

    def _toggle(self, **extra):
        dados = {'subtopico': self.sub.pk, 'indice': 2,
                 'html': '<p>Um destaque</p>'}
        dados.update(extra)
        return self.client.post(reverse('trilhas:salvo_toggle'), dados)

    def test_salvar_e_remover(self):
        resp = self._toggle()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['salvo'])
        self.assertEqual(self.user.cards_salvos.count(), 1)
        # Segundo toggle no mesmo card remove.
        resp = self._toggle()
        self.assertFalse(resp.json()['salvo'])
        self.assertEqual(self.user.cards_salvos.count(), 0)

    def test_salvar_sem_html_da_400(self):
        self.assertEqual(self._toggle(html='').status_code, 400)

    def test_indice_invalido_da_400(self):
        self.assertEqual(self._toggle(indice='x').status_code, 400)

    def test_subtopico_de_outro_usuario_da_404(self):
        outro = User.objects.create_user('outro2', password='x')
        sub = criar_trilha(outro).niveis.first().subtopicos.first()
        self.assertEqual(self._toggle(subtopico=sub.pk).status_code, 404)

    def test_pagina_de_salvos_lista_e_agrupa(self):
        self._toggle()
        resp = self.client.get(reverse('trilhas:salvos'))
        html = resp.content.decode()
        self.assertIn('Um destaque', html)
        self.assertIn(self.trilha.titulo, html)
        self.assertIn('Abrir tópico', html)

    def test_pagina_vazia_convida_a_estudar(self):
        resp = self.client.get(reverse('trilhas:salvos'))
        self.assertIn('Nada salvo por enquanto', resp.content.decode())


class PWATests(TestCase):
    def test_service_worker_servido_da_raiz(self):
        resp = self.client.get('/sw.js')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/javascript')
        self.assertIn(b'trilhas-static', resp.content)

    def test_base_referencia_manifest(self):
        User.objects.create_user('pwa', password='x')
        self.client.login(username='pwa', password='x')
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('manifest.webmanifest', html)
        self.assertIn('serviceWorker', html)
