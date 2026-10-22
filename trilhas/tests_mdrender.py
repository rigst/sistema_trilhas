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


class QuebrasMolesNasCaixasTests(TestCase):
    """A IA quebra o corpo das caixas por largura, e o nl2br virava cada quebra
    num <br>: no celular a frase saía cortada no meio. Texto real de produção
    (subtópico 1501, "Renderização de listas e condicionais")."""

    QUIZ = (
        '!!! quiz "Por que não devemos usar o índice do array como key?"\n'
        "    Porque o índice muda de significado quando a ordem dos itens muda.\n"
        "    O React usa a key para associar estado e elementos DOM ao item\n"
        '    correto entre renderizações; com índice, itens podem "trocar"\n'
        "    de identidade e causar bugs visuais ou de estado (ex: um input\n"
        "    de texto mostrando o valor de outro item após reordenar a lista).\n"
    )

    def test_frase_quebrada_por_largura_vira_uma_linha_so(self):
        html = render_md(self.QUIZ)
        self.assertIn("ao item correto entre renderizações", html)
        self.assertIn('podem "trocar" de identidade', html)
        self.assertIn("um input de texto", html)

    def test_quebra_depois_de_frase_fechada_continua(self):
        # "...a ordem dos itens muda." encerra a frase: é a mesma linha lógica
        # do parágrafo, mas o <br> não corta nada no meio. O que não pode é
        # haver <br> no meio de uma frase.
        html = render_md(self.QUIZ)
        self.assertNotIn("ao item<br", html)
        self.assertNotIn('"trocar"<br', html)
        self.assertNotIn("input<br", html)

    def test_lista_dentro_da_caixa_nao_e_colada(self):
        md = '!!! dica "Passos"\n    Faça assim:\n\n    - primeiro\n    - segundo\n'
        html = render_md(md)
        self.assertIn("<li>primeiro</li>", html)
        self.assertIn("<li>segundo</li>", html)

    def test_item_de_lista_nunca_e_colado_na_frase(self):
        md = '!!! dica "Passos"\n    Siga a ordem\n    - primeiro\n    - segundo\n'
        html = render_md(md)
        self.assertNotIn("ordem - primeiro", html)
        self.assertNotIn("primeiro - segundo", html)

    def test_linha_terminada_em_dois_pontos_mantem_a_quebra(self):
        md = '!!! dica "Permissões"\n    Cada trio vira um número:\n    r=4, w=2, x=1.\n'
        html = render_md(md)
        self.assertNotIn("número: r=4", html)

    def test_paragrafos_separados_continuam_separados(self):
        md = '!!! conceito "T"\n    Primeiro parágrafo.\n\n    Segundo parágrafo.\n'
        html = render_md(md)
        self.assertEqual(html.count("<p>"), 2)

    def test_codigo_dentro_da_caixa_nao_e_colado(self):
        md = '!!! exemplo "Código"\n    Veja\n\n    ```python\n    a = 1\n    b = 2\n    ```\n'
        html = render_md(md)
        self.assertIn("codehilite", html)
        self.assertNotIn("a = 1 b = 2", html)

    def test_negrito_que_abre_topico_nao_e_colado(self):
        md = '!!! resumo "R"\n    Vantagem: rápido\n    **Desvantagem:** caro\n'
        html = render_md(md)
        self.assertNotIn("rápido <strong>", html)

    def test_fora_das_caixas_nada_muda(self):
        html = render_md("linha um\nlinha dois")
        self.assertIn("<br", html)


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
