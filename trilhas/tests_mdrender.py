"""Testes do renderizador de Markdown — sanitização, callouts, Mermaid e cache.

O texto vem da IA a partir de tema livre do usuário, então a allowlist do nh3 é
a fronteira de segurança da leitura: HTML cru nunca pode chegar ativo ao DOM.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from trilhas.mdrender import render_md, render_subtopico
from trilhas.models import Nivel, Subtopico, Trilha
from trilhas.templatetags.md import md_inline

User = get_user_model()


class SanitizacaoTests(TestCase):
    def test_script_e_removido(self):
        html = render_md('texto\n\n<script>alert("xss")</script>')
        self.assertNotIn("<script", html)
        self.assertIn("texto", html)

    def test_atributo_de_evento_e_removido(self):
        html = render_md('<p onclick="roubar()">clique</p>')
        self.assertNotIn("onclick", html)
        self.assertIn("clique", html)

    def test_link_javascript_e_removido(self):
        html = render_md("[clique](javascript:alert(1))")
        self.assertNotIn("javascript:", html)

    def test_link_externo_ganha_rel_seguro(self):
        html = render_md("[site](https://exemplo.com)")
        self.assertIn('rel="noopener noreferrer"', html)

    def test_imagem_https_e_preservada(self):
        html = render_md("![alt](https://exemplo.com/f.jpg)")
        self.assertIn("<img", html)
        self.assertIn("https://exemplo.com/f.jpg", html)

    def test_texto_vazio_devolve_string_vazia(self):
        self.assertEqual(render_md(""), "")
        self.assertEqual(render_md(None), "")


class MermaidTests(TestCase):
    def test_cerca_mermaid_vira_div_e_nao_bloco_de_codigo(self):
        html = render_md("```mermaid\ngraph TD;\n  A-->B;\n```")
        self.assertIn('<div class="mermaid">', html)
        self.assertIn("graph TD;", html)

    def test_quebra_literal_vira_br_dentro_do_diagrama(self):
        # A IA escreve "\n" literal nos rótulos; o Mermaid mostraria "\n" na tela.
        # O <br/> sai escapado no HTML: o Mermaid lê o textContent da div.
        html = render_md(r"```mermaid" + "\n" + r"graph TD; A[um\ndois];" + "\n```")
        self.assertIn("&lt;br/&gt;", html)
        self.assertNotIn(r"\n", html)

    def test_quebra_literal_em_python_nao_e_tocada(self):
        html = render_md('```python\nprint("a\\nb")\n```')
        self.assertNotIn("br/", html)


class AdmonitionTests(TestCase):
    def test_corpo_com_quatro_espacos(self):
        html = render_md('!!! nota "Atenção"\n    Corpo da caixa.')
        self.assertIn("admonition", html)
        self.assertIn("Corpo da caixa.", html)

    def test_corpo_com_oito_espacos_nao_vira_bloco_de_codigo(self):
        # Regressão: com 8 espaços o parser tratava o corpo como código.
        html = render_md('!!! nota "Atenção"\n        Corpo da caixa.')
        self.assertIn("admonition", html)
        self.assertNotIn("<pre", html)

    def test_corpo_sem_indentacao_e_adotado(self):
        html = render_md('!!! nota "Atenção"\nCorpo colado na margem.')
        self.assertIn("admonition", html)
        self.assertIn("Corpo colado na margem.", html)

    def test_marcador_dentro_de_cerca_de_codigo_nao_vira_caixa(self):
        html = render_md('```\n!!! nota "Isto é exemplo"\n```')
        self.assertNotIn("admonition", html)

    def test_codigo_dentro_da_caixa_e_destacado(self):
        md = '!!! nota "Exemplo"\n    Veja:\n\n    ```python\n    x = 1\n    ```\n'
        html = render_md(md)
        self.assertIn("admonition", html)
        self.assertIn("codehilite", html)

    def test_caixa_seguida_de_outro_bloco_nao_engole_o_bloco(self):
        html = render_md('!!! nota "T"\nCorpo\n\n## Depois')
        self.assertIn("<h2", html)


class MdInlineTests(TestCase):
    def test_paragrafo_unico_sai_sem_p(self):
        self.assertEqual(md_inline("apenas **texto**"), "apenas <strong>texto</strong>")

    def test_conteudo_com_bloco_mantem_a_forma_de_bloco(self):
        html = md_inline("- um\n- dois")
        self.assertIn("<ul>", html)

    def test_vazio_devolve_string_vazia(self):
        self.assertEqual(md_inline(""), "")


class RenderSubtopicoCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        user = User.objects.create_user("md", password="x")
        trilha = Trilha.objects.create(user=user, tema_livre="t", titulo="T")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1")
        self.sub = Subtopico.objects.create(
            nivel=nivel,
            ordem=1,
            titulo="Sub",
            conteudo_md="# Original",
            gerado_em=timezone.now(),
        )

    def test_conteudo_vazio_devolve_string_vazia(self):
        self.sub.conteudo_md = ""
        self.assertEqual(render_subtopico(self.sub), "")

    def test_segunda_chamada_vem_do_cache(self):
        primeiro = render_subtopico(self.sub)
        # Muda o texto sem mexer no carimbo: a chave é a mesma, o cache responde.
        Subtopico.objects.filter(pk=self.sub.pk).update(conteudo_md="# Trocado")
        self.sub.refresh_from_db()
        self.assertEqual(render_subtopico(self.sub), primeiro)

    def test_regerar_invalida_o_cache(self):
        render_subtopico(self.sub)
        self.sub.conteudo_md = "# Regerado"
        self.sub.gerado_em = timezone.now() + timezone.timedelta(seconds=1)
        self.sub.save(update_fields=["conteudo_md", "gerado_em"])

        self.assertIn("Regerado", render_subtopico(self.sub))

    def test_subtopico_sem_carimbo_ainda_renderiza(self):
        self.sub.gerado_em = None
        self.sub.save(update_fields=["gerado_em"])
        self.assertIn("Original", render_subtopico(self.sub))
