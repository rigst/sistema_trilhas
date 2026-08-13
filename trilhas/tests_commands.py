"""Testes dos comandos de manutenção (backfill de capa, emblema, categoria e fotos).

São comandos que rodam à mão em produção e mexem no conteúdo já publicado, então
o que se verifica é o recorte do queryset (quem entra), o que é gravado e o
respeito ao --dry-run. As chamadas de IA e a Pexels são sempre mockadas.
"""

from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from trilhas.management.commands.fotos_conteudo import IMG_RE, _dividir_secoes
from trilhas.models import Nivel, Subtopico, Trilha

User = get_user_model()

FOTO_MD = (
    "![Um martelo](https://images.pexels.com/photos/7/x.jpg)\n*Um martelo · foto de Ana (Pexels)*"
)


def rodar(comando, *args, **kwargs):
    saida = StringIO()
    call_command(comando, *args, stdout=saida, stderr=saida, **kwargs)
    return saida.getvalue()


class CategorizarTrilhasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cat", password="x")
        self.sem_cat = Trilha.objects.create(user=self.user, tema_livre="t", titulo="Python")
        self.com_cat = Trilha.objects.create(
            user=self.user, tema_livre="t", titulo="Violão", categoria="Música"
        )
        # Sem título = sem sumário ainda: fica de fora em qualquer modo.
        self.rascunho = Trilha.objects.create(user=self.user, tema_livre="t", titulo="")

    def test_por_padrao_so_pega_as_sem_categoria(self):
        with mock.patch("ai.services.categorizar_trilhas") as categorizar:
            rodar("categorizar_trilhas")

        self.assertEqual([t.pk for t in categorizar.call_args.args[0]], [self.sem_cat.pk])

    def test_all_recategoriza_todas_as_com_sumario(self):
        with mock.patch("ai.services.categorizar_trilhas") as categorizar:
            rodar("categorizar_trilhas", "--all")

        enviadas = {t.pk for t in categorizar.call_args.args[0]}
        self.assertEqual(enviadas, {self.sem_cat.pk, self.com_cat.pk})

    def test_nada_a_fazer_nao_chama_a_ia(self):
        Trilha.objects.update(categoria="Já tem")
        with mock.patch("ai.services.categorizar_trilhas") as categorizar:
            saida = rodar("categorizar_trilhas")

        categorizar.assert_not_called()
        self.assertIn("Nada a categorizar.", saida)


class DefinirEmblemasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("emb", password="x")
        self.a = Trilha.objects.create(user=self.user, tema_livre="t", titulo="Direito Penal")
        self.b = Trilha.objects.create(user=self.user, tema_livre="t", titulo="Violão")
        self.ja_tem = Trilha.objects.create(
            user=self.user, tema_livre="t", titulo="Redes", emblema="🌐"
        )

    def _resposta(self, itens):
        return mock.patch(
            "trilhas.management.commands.definir_emblemas._gerar_json",
            return_value={"emblemas": itens},
        )

    def test_define_o_emblema_de_cada_trilha(self):
        with self._resposta(
            [{"id": self.a.pk, "emblema": "⚖️"}, {"id": self.b.pk, "emblema": "🎸"}]
        ):
            saida = rodar("definir_emblemas")

        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.emblema, "⚖️")
        self.assertEqual(self.b.emblema, "🎸")
        self.assertIn("2/2 emblemas definidos.", saida)

    def test_por_padrao_nao_mexe_em_quem_ja_tem(self):
        with self._resposta([{"id": self.ja_tem.pk, "emblema": "🔌"}]) as gerar:
            rodar("definir_emblemas")

        self.ja_tem.refresh_from_db()
        self.assertEqual(self.ja_tem.emblema, "🌐")
        self.assertNotIn(str(self.ja_tem.pk), gerar.call_args.args[1])

    def test_force_inclui_as_que_ja_tem(self):
        with self._resposta([{"id": self.ja_tem.pk, "emblema": "🔌"}]):
            rodar("definir_emblemas", "--force")

        self.ja_tem.refresh_from_db()
        self.assertEqual(self.ja_tem.emblema, "🔌")

    def test_emoji_repetido_e_descartado(self):
        # Duas trilhas com o mesmo emblema ficariam indistinguíveis no painel.
        with self._resposta([{"id": self.a.pk, "emblema": "⚖️"}, {"id": self.b.pk, "emblema": "⚖️"}]):
            saida = rodar("definir_emblemas")

        self.b.refresh_from_db()
        self.assertEqual(self.b.emblema, "")
        self.assertIn("1/2", saida)

    def test_ids_desconhecidos_e_emblemas_vazios_sao_ignorados(self):
        with self._resposta([{"id": 999999, "emblema": "🔥"}, {"id": self.a.pk, "emblema": "  "}]):
            saida = rodar("definir_emblemas")

        self.a.refresh_from_db()
        self.assertEqual(self.a.emblema, "")
        self.assertIn("0/2", saida)

    def test_falha_da_ia_e_reportada_sem_estourar(self):
        from ai.services import IAError

        with mock.patch(
            "trilhas.management.commands.definir_emblemas._gerar_json",
            side_effect=IAError("fora do ar"),
        ):
            saida = rodar("definir_emblemas")

        self.assertIn("Falha na IA: fora do ar", saida)
        self.a.refresh_from_db()
        self.assertEqual(self.a.emblema, "")

    def test_sem_trilha_alguma_nao_chama_a_ia(self):
        Trilha.objects.update(emblema="🎓")
        with mock.patch("trilhas.management.commands.definir_emblemas._gerar_json") as gerar:
            saida = rodar("definir_emblemas")

        gerar.assert_not_called()
        self.assertIn("Nenhuma trilha para definir.", saida)


class FetchCoversTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cover", password="x")
        self.sem_capa = Trilha.objects.create(user=self.user, tema_livre="t", titulo="Python")
        self.com_capa = Trilha.objects.create(
            user=self.user,
            tema_livre="t",
            titulo="Violão",
            cover_url="/media/covers/v.jpg",
            cover_pexels_id=7,
        )

    def _mocks(self, urls):
        return (
            mock.patch(
                "trilhas.management.commands.fetch_covers.buscar_capa", side_effect=list(urls)
            ),
            mock.patch(
                "trilhas.management.commands.fetch_covers.baixar_para_media",
                side_effect=lambda url: f"/media/covers/{url[-5:]}",
            ),
        )

    def test_baixa_a_capa_e_guarda_o_id_da_foto(self):
        buscar, baixar = self._mocks(["https://images.pexels.com/photos/42/x.jpg"])
        with buscar, baixar:
            saida = rodar("fetch_covers")

        self.sem_capa.refresh_from_db()
        self.assertEqual(self.sem_capa.cover_pexels_id, 42)
        self.assertTrue(self.sem_capa.cover_url.startswith("/media/covers/"))
        self.assertIn("1/1 capas atualizadas", saida)

    def test_por_padrao_pula_quem_ja_tem_capa(self):
        buscar, baixar = self._mocks(["https://images.pexels.com/photos/42/x.jpg"])
        with buscar as b, baixar:
            rodar("fetch_covers")

        self.assertEqual(b.call_count, 1)
        self.com_capa.refresh_from_db()
        self.assertEqual(self.com_capa.cover_pexels_id, 7)

    def test_force_refaz_todas(self):
        buscar, baixar = self._mocks(
            ["https://images.pexels.com/photos/1/x.jpg", "https://images.pexels.com/photos/2/x.jpg"]
        )
        with buscar as b, baixar:
            rodar("fetch_covers", "--force")

        self.assertEqual(b.call_count, 2)

    def test_ids_restringe_o_alvo(self):
        buscar, baixar = self._mocks(["https://images.pexels.com/photos/1/x.jpg"])
        with buscar as b, baixar:
            rodar("fetch_covers", "--force", "--ids", str(self.com_capa.pk))

        self.assertEqual(b.call_count, 1)
        self.assertEqual(b.call_args.args[0], "Violão")

    def test_capas_ja_usadas_nao_se_repetem(self):
        # Temas afins convergiriam na mesma foto e o painel ficaria com cards clonados.
        # O set de exclusão é mutado entre as chamadas, então guarda-se uma cópia.
        vistos = []

        def buscar(titulo, categoria, descricao, excluir_ids=None):
            vistos.append(set(excluir_ids or ()))
            return "https://images.pexels.com/photos/42/x.jpg"

        _b, baixar = self._mocks([])
        with (
            mock.patch("trilhas.management.commands.fetch_covers.buscar_capa", side_effect=buscar),
            baixar,
        ):
            rodar("fetch_covers")

        # A busca já sai excluindo a capa que a outra trilha do usuário usa.
        self.assertEqual(vistos, [{7}])

    def test_sem_resultado_avisa_e_nao_grava(self):
        buscar, baixar = self._mocks([""])
        with buscar, baixar:
            saida = rodar("fetch_covers")

        self.sem_capa.refresh_from_db()
        self.assertEqual(self.sem_capa.cover_url, "")
        self.assertIn("sem resultado", saida)
        self.assertIn("0/1", saida)


class DividirSecoesTests(TestCase):
    def test_separa_pelos_tracos_na_coluna_zero(self):
        self.assertEqual(len(_dividir_secoes("Um\n---\nDois\n---\nTrês")), 3)

    def test_traco_indentado_e_corpo_nao_separador(self):
        # Indentado costuma ser corpo de admonition, não fim de seção.
        self.assertEqual(len(_dividir_secoes("Um\n    ---\nainda a mesma seção")), 1)

    def test_traco_dentro_de_cerca_de_codigo_nao_separa(self):
        self.assertEqual(len(_dividir_secoes("Um\n```\n---\n```\nainda Um")), 1)

    def test_texto_sem_separador_e_uma_secao(self):
        self.assertEqual(_dividir_secoes("linha única"), [["linha única"]])


class ImgReTests(TestCase):
    def test_captura_alt_url_e_legenda(self):
        m = IMG_RE.search(FOTO_MD)
        self.assertEqual(m.group("alt"), "Um martelo")
        self.assertEqual(m.group("legenda"), "Um martelo · foto de Ana (Pexels)")

    def test_ignora_imagem_de_outro_dominio(self):
        self.assertIsNone(IMG_RE.search("![x](https://exemplo.com/foto.jpg)"))


class FotosConteudoInserirTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("fotos", password="x")
        trilha = Trilha.objects.create(user=self.user, tema_livre="t", titulo="Direito")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1")
        self.sub = Subtopico.objects.create(
            nivel=nivel,
            ordem=1,
            titulo="O martelo",
            status=Subtopico.Status.PRONTO,
            conteudo_md="# Primeira\ntexto um\n---\n# Segunda\ntexto dois",
        )

    def _mocks(self, fotos, *, inserido=None):
        return (
            mock.patch(
                "trilhas.management.commands.fotos_conteudo._gerar_json",
                return_value={"fotos": fotos},
            ),
            mock.patch(
                "trilhas.management.commands.fotos_conteudo.inserir_fotos_conteudo",
                side_effect=lambda texto, contexto="": (
                    inserido if inserido is not None else texto.replace("{{foto:", FOTO_MD + " {{")
                ),
            ),
        )

    def test_marcador_vira_foto_e_o_conteudo_e_salvo(self):
        gerar, inserir = self._mocks([{"secao": 1, "query": "gavel", "legenda": "Martelo"}])
        with gerar, inserir as insere:
            saida = rodar("fotos_conteudo")

        self.sub.refresh_from_db()
        self.assertIn("![", self.sub.conteudo_md)
        # O marcador é montado na seção escolhida, com query e legenda.
        texto_enviado = insere.call_args.args[0]
        self.assertIn("{{foto: gavel | Martelo}}", texto_enviado)
        self.assertIn("1 foto(s)", saida)

    def test_dry_run_nao_salva(self):
        gerar, inserir = self._mocks([{"secao": 1, "query": "gavel", "legenda": "Martelo"}])
        original = self.sub.conteudo_md
        with gerar, inserir:
            saida = rodar("fotos_conteudo", "--dry-run")

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.conteudo_md, original)
        self.assertIn("dry-run", saida)

    def test_nenhuma_foto_necessaria_nao_toca_no_texto(self):
        gerar, inserir = self._mocks([])
        original = self.sub.conteudo_md
        with gerar, inserir as insere:
            saida = rodar("fotos_conteudo")

        insere.assert_not_called()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.conteudo_md, original)
        self.assertIn("nenhuma foto necessária", saida)

    def test_secao_fora_da_faixa_ou_sem_query_e_ignorada(self):
        gerar, inserir = self._mocks(
            [{"secao": 99, "query": "x", "legenda": ""}, {"secao": 1, "query": "  ", "legenda": ""}]
        )
        with gerar, inserir as insere:
            rodar("fotos_conteudo")

        insere.assert_not_called()

    def test_foto_reprovada_na_auditoria_nao_e_salva(self):
        # A auditoria devolve o texto sem nenhuma imagem: não há o que gravar.
        gerar, inserir = self._mocks(
            [{"secao": 1, "query": "gavel", "legenda": "Martelo"}],
            inserido="# Primeira\ntexto um\n---\n# Segunda\ntexto dois",
        )
        with gerar, inserir:
            saida = rodar("fotos_conteudo")

        self.sub.refresh_from_db()
        self.assertNotIn("![", self.sub.conteudo_md)
        self.assertIn("nenhuma foto aprovada", saida)

    def test_subtopico_que_ja_tem_imagem_fica_de_fora(self):
        Subtopico.objects.filter(pk=self.sub.pk).update(conteudo_md=FOTO_MD)
        gerar, inserir = self._mocks([])
        with gerar as g, inserir:
            saida = rodar("fotos_conteudo")

        g.assert_not_called()
        self.assertIn("0 subtópico(s) sem imagens.", saida)

    def test_falha_da_ia_pula_o_subtopico(self):
        from ai.services import IAError

        with mock.patch(
            "trilhas.management.commands.fotos_conteudo._gerar_json",
            side_effect=IAError("estourou"),
        ):
            saida = rodar("fotos_conteudo")

        self.assertIn("estourou", saida)


class FotosConteudoAuditarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("audit", password="x")
        trilha = Trilha.objects.create(user=self.user, tema_livre="t", titulo="Direito")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1")
        self.sub = Subtopico.objects.create(
            nivel=nivel,
            ordem=1,
            titulo="O martelo",
            status=Subtopico.Status.PRONTO,
            conteudo_md=f"Antes do martelo\n\n{FOTO_MD}\n\nDepois",
        )

    def _resposta(self, remover):
        return mock.patch(
            "trilhas.management.commands.fotos_conteudo._gerar_json",
            return_value={"remover": remover},
        )

    def test_imagem_reprovada_e_removida_do_texto(self):
        with self._resposta([1]):
            saida = rodar("fotos_conteudo", "--auditar")

        self.sub.refresh_from_db()
        self.assertNotIn("![", self.sub.conteudo_md)
        self.assertIn("Antes do martelo", self.sub.conteudo_md)
        self.assertIn("Depois", self.sub.conteudo_md)
        self.assertIn("1 imagem(ns) removida(s)", saida)

    def test_nada_a_remover_mantem_o_texto(self):
        original = self.sub.conteudo_md
        with self._resposta([]):
            saida = rodar("fotos_conteudo", "--auditar")

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.conteudo_md, original)
        self.assertIn("todas ok", saida)

    def test_dry_run_nao_salva(self):
        original = self.sub.conteudo_md
        with self._resposta([1]):
            saida = rodar("fotos_conteudo", "--auditar", "--dry-run")

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.conteudo_md, original)
        self.assertIn("removeria 1", saida)

    def test_indice_fora_da_faixa_e_ignorado(self):
        original = self.sub.conteudo_md
        with self._resposta([99]):
            rodar("fotos_conteudo", "--auditar")

        self.sub.refresh_from_db()
        self.assertEqual(original.count("!["), self.sub.conteudo_md.count("!["))

    def test_falha_da_ia_pula_o_subtopico(self):
        from ai.services import IAError

        original = self.sub.conteudo_md
        with mock.patch(
            "trilhas.management.commands.fotos_conteudo._gerar_json",
            side_effect=IAError("estourou"),
        ):
            saida = rodar("fotos_conteudo", "--auditar")

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.conteudo_md, original)
        self.assertIn("estourou", saida)
