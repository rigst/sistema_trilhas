"""Testes do leitor em cards (stories) e do hero Continuar do dashboard."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

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
        self.assertIn('inner.className = "sc-in markdown-body"', html)
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


class AdmonitionTests(TestCase):
    def _render(self, md):
        from trilhas.mdrender import render_md
        return render_md(md)

    def test_indentacao_de_8_espacos_vira_admonition(self):
        # Caso real de produção: corpo com 8 espaços virava bloco de código.
        html = self._render(
            '!!! conceito "SSH"\n'
            '        SSH é um túnel blindado.\n'
            '        Sem ele, nada de VPS.\n'
        )
        self.assertIn('admonition conceito', html)
        self.assertIn('<p>SSH é um túnel blindado.', html)
        self.assertNotIn('<code>SSH', html)

    def test_corpo_sem_indentacao_e_absorvido(self):
        html = self._render(
            '!!! dica "Atalho"\nUse Ctrl+R para buscar no histórico.\n\nParágrafo fora.'
        )
        self.assertIn('admonition dica', html)
        self.assertIn('<p>Use Ctrl+R', html)
        # O parágrafo após a linha vazia fica FORA da caixa.
        self.assertIn('<p>Parágrafo fora.</p>', html)

    def test_indentacao_correta_permanece_intacta(self):
        html = self._render('!!! resumo "Fim"\n    Ponto A e ponto B.\n')
        self.assertIn('admonition resumo', html)
        self.assertIn('<p>Ponto A e ponto B.</p>', html)

    def test_exclamacoes_em_code_fence_nao_sao_tocadas(self):
        html = self._render('```bash\necho "!!! nao e admonition"\n```\n')
        self.assertNotIn('class="admonition', html)
        self.assertIn('codehilite', html)


class MermaidTests(TestCase):
    def test_cerca_mermaid_vira_div_nao_bloco_de_codigo(self):
        from trilhas.mdrender import render_md
        html = render_md('Antes.\n\n```mermaid\nflowchart TD\n  A --> B\n```\n\nDepois.')
        self.assertIn('class="mermaid"', html)
        self.assertIn('flowchart TD', html)
        # Não pode ter virado bloco de código destacado.
        trecho = html.split('mermaid')[1][:200]
        self.assertNotIn('<pre', trecho)

    def test_quebra_literal_dentro_do_mermaid_vira_br(self):
        from trilhas.mdrender import render_md
        html = render_md('```mermaid\nflowchart TD\n  A["Linha 1\\nLinha 2"]\n```')
        self.assertIn('class="mermaid"', html)
        self.assertIn('&lt;br/&gt;', html)
        self.assertNotIn(r'\\n', html)

    def test_codigo_normal_continua_destacado(self):
        from trilhas.mdrender import render_md
        html = render_md('```python\nx = 1\n```')
        self.assertIn('codehilite', html)
        self.assertNotIn('class="mermaid"', html)


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
