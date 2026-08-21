"""Testes da camada de serviços da IA — sem tocar a API da Anthropic.

O corte é sempre o mais externo possível: `_gerar_json` (chamada estruturada),
`client.messages.stream` (conteúdo) e `urllib.request.urlopen` (Pexels). O que
se testa aqui é o que o projeto faz com a resposta — validação, persistência,
débito de quota e fallbacks —, não o modelo.
"""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from ai import services
from ai.services import IAError

User = get_user_model()


def bloco_texto(texto):
    return SimpleNamespace(type="text", text=texto)


def resposta_fake(texto, *, stop_reason="end_turn", usage=None):
    return SimpleNamespace(
        content=[bloco_texto(texto)],
        stop_reason=stop_reason,
        usage=usage,
    )


def usage_fake(entrada=0, saida=0, cache_read=0, cache_write=0):
    return SimpleNamespace(
        input_tokens=entrada,
        output_tokens=saida,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )


def foto(id_, alt="", *, large="https://img/l.jpg", large2x="https://img/l2x.jpg", autor=""):
    return {
        "id": id_,
        "alt": alt,
        "photographer": autor,
        "src": {"large": large, "large2x": large2x, "original": "https://img/o.jpg"},
    }


class InfraTests(SimpleTestCase):
    def test_texto_concatena_so_os_blocos_de_texto(self):
        content = [
            bloco_texto("a"),
            SimpleNamespace(type="thinking", text="ignorar"),
            bloco_texto("b"),
        ]
        self.assertEqual(services._texto(content), "ab")

    def test_system_cache_marca_o_prompt_como_cacheavel(self):
        bloco = services._system_cache("regras")[0]
        self.assertEqual(bloco["text"], "regras")
        self.assertEqual(bloco["cache_control"], {"type": "ephemeral"})

    @override_settings(ANTHROPIC_API_KEY="")
    def test_client_sem_chave_levanta_iaerror(self):
        with self.assertRaises(IAError):
            services.get_client()

    @override_settings(AI_PRICES={"modelo-x": (2.0, 10.0)})
    def test_precos_usa_a_tabela_por_modelo(self):
        self.assertEqual(services._precos("modelo-x"), (2.0, 10.0))

    @override_settings(AI_PRICES={}, AI_PRICE_INPUT_PER_MTOK=5.0, AI_PRICE_OUTPUT_PER_MTOK=25.0)
    def test_precos_cai_no_padrao_para_modelo_desconhecido(self):
        self.assertEqual(services._precos("modelo-novo"), (5.0, 25.0))

    @override_settings(AI_PRICES={"m": (3.0, 15.0)})
    def test_custo_usd_soma_entrada_e_saida(self):
        # 1M de entrada a 3 + 1M de saída a 15.
        self.assertEqual(services.custo_usd("m", 1_000_000, 1_000_000), Decimal("18"))

    def test_pexels_id_da_url(self):
        self.assertEqual(
            services.pexels_id_da_url("https://images.pexels.com/photos/12345/pexels-photo.jpg"),
            12345,
        )
        self.assertIsNone(services.pexels_id_da_url("https://exemplo.com/foto.jpg"))
        self.assertIsNone(services.pexels_id_da_url(""))

    def test_foto_mais_aderente_prefere_o_alt_que_bate_com_a_query(self):
        photos = [foto(1, "a cat sleeping"), foto(2, "courtroom gavel justice")]
        melhor = services._foto_mais_aderente(photos, "courtroom gavel")
        self.assertEqual(melhor["id"], 2)

    def test_foto_mais_aderente_respeita_a_exclusao(self):
        photos = [foto(1, "courtroom gavel"), foto(2, "sem relação")]
        melhor = services._foto_mais_aderente(photos, "courtroom gavel", excluir={1})
        self.assertEqual(melhor["id"], 2)

    def test_foto_mais_aderente_sem_candidatos(self):
        self.assertIsNone(services._foto_mais_aderente([], "x"))
        self.assertIsNone(services._foto_mais_aderente([foto(1)], "x", excluir={1}))


class DebitarTests(TestCase):
    def setUp(self):
        self.profile = User.objects.create_user("debito", password="x").profile
        self.profile.quota_tokens_mes = 1_000_000
        self.profile.save(update_fields=["quota_tokens_mes"])

    @override_settings(AI_PRICES={"m": (3.0, 15.0)})
    def test_todos_os_tokens_de_entrada_contam_para_a_quota(self):
        # Cache (leitura e escrita) também consome quota mensal.
        services._debitar(self.profile, usage_fake(100, 50, cache_read=200, cache_write=40), "m")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.tokens_usados_mes, 100 + 50 + 200 + 40)
        self.assertGreater(self.profile.custo_acumulado, 0)

    def test_sem_profile_ou_sem_usage_nao_quebra(self):
        services._debitar(None, usage_fake(10, 10), "m")
        services._debitar(self.profile, None, "m")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.tokens_usados_mes, 0)


class GerarJsonTests(TestCase):
    def setUp(self):
        self.profile = User.objects.create_user("json", password="x").profile
        self.profile.quota_tokens_mes = 1_000_000
        self.profile.save(update_fields=["quota_tokens_mes"])

    def _chamar(self, resp):
        client = mock.Mock()
        client.messages.create.return_value = resp
        with mock.patch.object(services, "get_client", return_value=client):
            return services._gerar_json("sys", "user", {}, self.profile, "m", "low"), client

    def test_devolve_o_json_e_debita_a_quota(self):
        data, client = self._chamar(
            resposta_fake('{"ok": true}', usage=usage_fake(entrada=30, saida=12))
        )
        self.assertEqual(data, {"ok": True})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.tokens_usados_mes, 42)

        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "m")
        self.assertEqual(kwargs["output_config"]["effort"], "low")

    def test_resposta_truncada_vira_iaerror(self):
        with self.assertRaises(IAError) as ctx:
            self._chamar(resposta_fake("{", stop_reason="max_tokens", usage=usage_fake()))
        self.assertIn("truncada", str(ctx.exception))

    def test_json_invalido_vira_iaerror(self):
        with self.assertRaises(IAError) as ctx:
            self._chamar(resposta_fake("não é json", usage=usage_fake()))
        self.assertIn("não é JSON válido", str(ctx.exception))


@override_settings(PEXELS_API_KEY="chave-de-teste")
class PexelsTests(SimpleTestCase):
    def _urlopen(self, payload):
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps(payload).encode()
        resp.__enter__.return_value = resp
        return resp

    def test_busca_devolve_a_lista_de_fotos(self):
        with mock.patch(
            "urllib.request.urlopen", return_value=self._urlopen({"photos": [foto(1)]})
        ):
            self.assertEqual(len(services._pexels_search("gato")), 1)

    def test_falha_de_rede_devolve_lista_vazia(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            self.assertEqual(services._pexels_search("gato"), [])

    @override_settings(PEXELS_API_KEY="")
    def test_sem_chave_nao_chama_a_api(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertEqual(services._pexels_search("gato"), [])
        urlopen.assert_not_called()

    def test_query_vazia_nao_chama_a_api(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertEqual(services._pexels_search(""), [])
        urlopen.assert_not_called()


class BaixarParaMediaTests(SimpleTestCase):
    def _resposta(self, ctype, dados=b"binario"):
        resp = mock.MagicMock()
        resp.headers.get.return_value = ctype
        resp.read.return_value = dados
        resp.__enter__.return_value = resp
        return resp

    def test_baixa_para_media_e_devolve_url_local(self):
        with (
            mock.patch("urllib.request.urlopen", return_value=self._resposta("image/jpeg")),
            mock.patch("os.makedirs"),
            mock.patch("builtins.open", mock.mock_open()) as arquivo,
        ):
            url = services.baixar_para_media("https://img/foto.jpg")

        self.assertTrue(url.endswith(".jpg"))
        self.assertIn("covers/", url)
        arquivo().write.assert_called_once_with(b"binario")

    def test_conteudo_que_nao_e_imagem_mantem_a_url_original(self):
        original = "https://img/foto.jpg"
        with mock.patch("urllib.request.urlopen", return_value=self._resposta("text/html")):
            self.assertEqual(services.baixar_para_media(original), original)

    def test_falha_de_download_mantem_a_url_original(self):
        # Nunca quebra a capa: em qualquer erro devolve o hotlink.
        original = "https://img/foto.jpg"
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            self.assertEqual(services.baixar_para_media(original), original)

    def test_url_nao_http_passa_direto(self):
        self.assertEqual(services.baixar_para_media("/media/covers/x.jpg"), "/media/covers/x.jpg")
        self.assertEqual(services.baixar_para_media(""), "")


class QueryCapaTests(SimpleTestCase):
    def test_usa_o_termo_devolvido_pela_ia(self):
        with mock.patch.object(services, "_gerar_json", return_value={"query": "courtroom gavel"}):
            self.assertEqual(services._query_capa("Direito Penal"), "courtroom gavel")

    def test_falha_da_ia_cai_na_heuristica_do_titulo(self):
        # Só palavras com mais de 3 letras entram no termo de busca.
        with mock.patch.object(services, "_gerar_json", side_effect=IAError("fora do ar")):
            self.assertEqual(
                services._query_capa("Direito Penal e as leis", "Justiça"),
                "Direito Penal leis Justiça",
            )

    def test_query_vazia_da_ia_cai_na_heuristica(self):
        with mock.patch.object(services, "_gerar_json", return_value={"query": "  "}):
            self.assertEqual(services._query_capa("Fotografia Urbana"), "Fotografia Urbana")


class BuscarCapaTests(SimpleTestCase):
    def test_ia_escolhe_a_foto_pelo_indice(self):
        photos = [foto(1, "genérica"), foto(2, "courtroom", large2x="https://img/certa.jpg")]
        with (
            mock.patch.object(services, "_query_capa", return_value="courtroom gavel"),
            mock.patch.object(services, "_pexels_search", return_value=photos),
            mock.patch.object(services, "_gerar_json", return_value={"indice": 2}),
        ):
            self.assertEqual(services.buscar_capa("Direito"), "https://img/certa.jpg")

    def test_indice_fora_da_faixa_cai_na_heuristica(self):
        photos = [foto(1, "sem relação"), foto(2, "courtroom gavel", large2x="https://img/h.jpg")]
        with (
            mock.patch.object(services, "_query_capa", return_value="courtroom gavel"),
            mock.patch.object(services, "_pexels_search", return_value=photos),
            mock.patch.object(services, "_gerar_json", return_value={"indice": 99}),
        ):
            self.assertEqual(services.buscar_capa("Direito"), "https://img/h.jpg")

    def test_falha_da_ia_cai_na_heuristica(self):
        photos = [foto(1, "courtroom gavel", large2x="https://img/h.jpg")]
        with (
            mock.patch.object(services, "_query_capa", return_value="courtroom gavel"),
            mock.patch.object(services, "_pexels_search", return_value=photos),
            mock.patch.object(services, "_gerar_json", side_effect=IAError("boom")),
        ):
            self.assertEqual(services.buscar_capa("Direito"), "https://img/h.jpg")

    def test_sem_fotos_devolve_vazio(self):
        with (
            mock.patch.object(services, "_query_capa", return_value="x"),
            mock.patch.object(services, "_pexels_search", return_value=[]),
        ):
            self.assertEqual(services.buscar_capa("Direito"), "")

    def test_excluir_ids_evita_repetir_a_capa_de_outra_trilha(self):
        with (
            mock.patch.object(services, "_query_capa", return_value="x"),
            mock.patch.object(services, "_pexels_search", return_value=[foto(1), foto(2)]),
            mock.patch.object(services, "_gerar_json", return_value={"indice": 1}),
        ):
            url = services.buscar_capa("Direito", excluir_ids={1})
        # Sobrou só a foto 2, que passa a ser o índice 1 da lista filtrada.
        self.assertEqual(url, "https://img/l2x.jpg")


class InserirFotosConteudoTests(SimpleTestCase):
    def test_texto_sem_marcador_passa_intacto(self):
        with mock.patch.object(services, "_pexels_search") as busca:
            self.assertEqual(services.inserir_fotos_conteudo("texto puro"), "texto puro")
        busca.assert_not_called()

    def test_marcador_aprovado_vira_imagem_com_credito(self):
        with (
            mock.patch.object(
                services, "_pexels_search", return_value=[foto(7, "a gavel", autor="Ana")]
            ),
            mock.patch.object(services, "_auditar_fotos", return_value={1}),
        ):
            saida = services.inserir_fotos_conteudo("Antes {{foto: gavel | Um martelo}} depois")

        self.assertIn("![Um martelo](https://img/l.jpg)", saida)
        self.assertIn("*Um martelo · foto de Ana (Pexels)*", saida)

    def test_foto_reprovada_pela_auditoria_e_removida(self):
        # Imagem errada é pior que nenhuma: o marcador some do texto.
        with (
            mock.patch.object(services, "_pexels_search", return_value=[foto(7, "outra coisa")]),
            mock.patch.object(services, "_auditar_fotos", return_value=set()),
        ):
            saida = services.inserir_fotos_conteudo("Antes {{foto: gavel | Martelo}} depois")

        self.assertEqual(saida, "Antes  depois")

    def test_marcador_sem_foto_encontrada_e_removido(self):
        with (
            mock.patch.object(services, "_pexels_search", return_value=[]),
            mock.patch.object(services, "_auditar_fotos", return_value=set()),
        ):
            self.assertEqual(services.inserir_fotos_conteudo("a {{foto: nada}} b"), "a  b")

    def test_fotos_nao_se_repetem_no_mesmo_texto(self):
        buscas = []

        def busca(query, per_page=6):
            buscas.append(query)
            return [foto(1, "gavel"), foto(2, "court")]

        with (
            mock.patch.object(services, "_pexels_search", side_effect=busca),
            mock.patch.object(services, "_auditar_fotos", return_value={1, 2}),
        ):
            saida = services.inserir_fotos_conteudo("{{foto: gavel}} e {{foto: gavel}}")

        # A segunda ocorrência não pode reusar a mesma foto da primeira.
        self.assertEqual(len(buscas), 2)
        self.assertEqual(saida.count("!["), 2)


class AuditarFotosTests(SimpleTestCase):
    def _itens(self, n=2):
        return [
            {"query": f"q{i}", "legenda": f"l{i}", "alt": f"a{i}", "foto": foto(i)}
            for i in range(1, n + 1)
        ]

    def test_devolve_os_indices_aprovados(self):
        with mock.patch.object(services, "_gerar_json", return_value={"aprovadas": [2]}):
            self.assertEqual(services._auditar_fotos(self._itens()), {2})

    def test_lista_vazia_nao_chama_a_ia(self):
        with mock.patch.object(services, "_gerar_json") as gerar:
            self.assertEqual(services._auditar_fotos([]), set())
        gerar.assert_not_called()

    def test_falha_da_ia_aprova_tudo_para_nao_travar_a_geracao(self):
        with mock.patch.object(services, "_gerar_json", side_effect=IAError("boom")):
            self.assertEqual(services._auditar_fotos(self._itens(3)), {1, 2, 3})


class GerarPerguntasTests(TestCase):
    def setUp(self):
        from trilhas.models import Trilha

        self.user = User.objects.create_user("perg", password="x")
        self.trilha = Trilha.objects.create(user=self.user, tema_livre="Quero aprender violão")

    @mock.patch.object(services, "_gerar_json")
    def test_persiste_as_perguntas_numeradas(self, gerar_json):
        gerar_json.return_value = {
            "perguntas": [
                {"pergunta": "Seu nível?", "tipo": "escolha_unica", "opcoes": ["A", "B"]},
                {"ordem": 2, "pergunta": "Seu objetivo?", "tipo": "escolha_unica", "opcoes": ["X"]},
            ]
        }
        services.gerar_perguntas_direcionadoras(self.trilha)

        perguntas = list(self.trilha.perguntas.order_by("ordem"))
        self.assertEqual([p.ordem for p in perguntas], [1, 2])
        self.assertEqual(perguntas[0].opcoes, ["A", "B"])

    @mock.patch.object(services, "_gerar_json")
    def test_regerar_substitui_as_perguntas_anteriores(self, gerar_json):
        from trilhas.models import PerguntaDirecionadora

        PerguntaDirecionadora.objects.create(trilha=self.trilha, ordem=1, pergunta="antiga")
        gerar_json.return_value = {"perguntas": [{"pergunta": "nova", "opcoes": []}]}

        services.gerar_perguntas_direcionadoras(self.trilha)

        self.assertEqual(list(self.trilha.perguntas.values_list("pergunta", flat=True)), ["nova"])


class GerarSumarioTests(TestCase):
    def setUp(self):
        from trilhas.models import PerguntaDirecionadora, Trilha

        self.user = User.objects.create_user("sum", password="x")
        self.trilha = Trilha.objects.create(user=self.user, tema_livre="Python do zero")
        PerguntaDirecionadora.objects.create(
            trilha=self.trilha, ordem=1, pergunta="Seu nível?", resposta="Iniciante"
        )
        PerguntaDirecionadora.objects.create(trilha=self.trilha, ordem=2, pergunta="Objetivo?")

    def _payload(self, n_niveis=3):
        return {
            "titulo": "Trilha de Python do Zero",
            "descricao": "Do básico ao avançado.",
            "emblema": "🐍",
            "categoria": "Programação",
            "objetivos": ["Escrever scripts"],
            "niveis": [
                {
                    "titulo": f"Nível {i}",
                    "resumo": f"resumo {i}",
                    "faixa": "iniciante",
                    "titulo_concedido": f"Título {i}",
                    "subtopicos": [{"titulo": f"Sub {i}.1", "descricao_curta": "d"}],
                }
                for i in range(1, n_niveis + 1)
            ],
        }

    def _gerar(self, payload):
        with (
            mock.patch.object(services, "_gerar_json", return_value=payload),
            mock.patch.object(services, "buscar_capa", return_value="https://img/photos/99/x.jpg"),
            mock.patch.object(services, "baixar_para_media", return_value="/media/covers/x.jpg"),
        ):
            services.gerar_sumario(self.trilha)
        self.trilha.refresh_from_db()

    def test_limpa_o_prefixo_do_titulo_e_guarda_a_capa(self):
        self._gerar(self._payload())
        self.assertEqual(self.trilha.titulo, "Python do Zero")
        self.assertEqual(self.trilha.categoria, "Programação")
        self.assertEqual(self.trilha.cover_url, "/media/covers/x.jpg")
        self.assertEqual(self.trilha.cover_pexels_id, 99)

    def test_primeiro_nivel_disponivel_e_demais_bloqueados(self):
        from trilhas.models import Nivel

        self._gerar(self._payload())
        status = list(self.trilha.niveis.order_by("ordem").values_list("status", flat=True))
        self.assertEqual(
            status,
            [Nivel.Status.DISPONIVEL, Nivel.Status.BLOQUEADO, Nivel.Status.BLOQUEADO],
        )

    def test_ultimo_nivel_e_sempre_mestre(self):
        # A faixa do último nível é imposta pelo projeto, não pela IA.
        self._gerar(self._payload(n_niveis=4))
        faixas = list(self.trilha.niveis.order_by("ordem").values_list("faixa", flat=True))
        self.assertEqual(faixas, ["iniciante", "iniciante", "iniciante", "mestre"])

    def test_subtopicos_sao_criados_por_nivel(self):
        from trilhas.models import Subtopico

        self._gerar(self._payload(n_niveis=2))
        self.assertEqual(Subtopico.objects.filter(nivel__trilha=self.trilha).count(), 2)

    def test_regerar_substitui_os_niveis_anteriores(self):
        self._gerar(self._payload(n_niveis=3))
        self._gerar(self._payload(n_niveis=2))
        self.assertEqual(self.trilha.niveis.count(), 2)

    def test_emblema_e_categoria_sao_truncados(self):
        payload = self._payload(n_niveis=1)
        payload["emblema"] = "🐍" * 20
        payload["categoria"] = "C" * 200
        self._gerar(payload)
        self.assertLessEqual(len(self.trilha.emblema), 8)
        self.assertEqual(len(self.trilha.categoria), 60)


class GerarConteudoSubtopicoTests(TestCase):
    def setUp(self):
        from trilhas.models import Nivel, Subtopico, Trilha

        self.user = User.objects.create_user("cont", password="x")
        self.profile = self.user.profile
        self.profile.quota_tokens_mes = 1_000_000
        self.profile.save(update_fields=["quota_tokens_mes"])
        trilha = Trilha.objects.create(user=self.user, tema_livre="tema", titulo="Trilha")
        self.nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1", resumo="r")
        self.sub1 = Subtopico.objects.create(nivel=self.nivel, ordem=1, titulo="Primeiro")
        self.sub2 = Subtopico.objects.create(nivel=self.nivel, ordem=2, titulo="Último")

    def _client(self, texto):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.get_final_message.return_value = resposta_fake(texto, usage=usage_fake(100, 200))
        client = mock.Mock()
        client.messages.stream.return_value = stream
        return client

    def _gerar(self, sub, texto="Conteúdo gerado"):
        client = self._client(texto)
        with (
            mock.patch.object(services, "get_client", return_value=client),
            mock.patch.object(services, "inserir_fotos_conteudo", side_effect=lambda t, **kw: t),
        ):
            return services.gerar_conteudo_subtopico(sub, self.profile), client

    def test_devolve_o_texto_e_debita_a_quota(self):
        texto, _client = self._gerar(self.sub1)
        self.assertEqual(texto, "Conteúdo gerado")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.tokens_usados_mes, 300)

    def test_ultimo_subtopico_pede_bibliografia(self):
        _texto, client = self._gerar(self.sub2)
        user_msg = client.messages.stream.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Bibliografia e referências", user_msg)

    def test_subtopico_do_meio_nao_pede_bibliografia(self):
        _texto, client = self._gerar(self.sub1)
        user_msg = client.messages.stream.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("Bibliografia e referências", user_msg)

    def test_fotos_sao_inseridas_no_texto_final(self):
        client = self._client("antes {{foto: gato}} depois")
        with (
            mock.patch.object(services, "get_client", return_value=client),
            mock.patch.object(
                services, "inserir_fotos_conteudo", return_value="COM FOTO"
            ) as insere,
        ):
            texto = services.gerar_conteudo_subtopico(self.sub1, self.profile)

        self.assertEqual(texto, "COM FOTO")
        insere.assert_called_once()


class GerarRoteiroVideoTests(TestCase):
    def setUp(self):
        from trilhas.models import Nivel, Subtopico, Trilha

        user = User.objects.create_user("rot", password="x")
        trilha = Trilha.objects.create(user=user, tema_livre="tema", titulo="Trilha")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1")
        self.sub = Subtopico.objects.create(
            nivel=nivel,
            ordem=1,
            titulo="Tópico",
            descricao_curta="Descrição curta",
            conteudo_md="# Uma\ntexto\n\n---\n\n# Duas\ntexto",
        )

    def test_alinha_a_narracao_pela_ordem(self):
        with mock.patch.object(
            services,
            "_gerar_json",
            return_value={
                "slides": [
                    {"ordem": 2, "narracao": "fala da segunda"},
                    {"ordem": 1, "narracao": "fala da primeira"},
                ]
            },
        ):
            roteiro = services.gerar_roteiro_video(self.sub)

        self.assertEqual([s["narracao"] for s in roteiro], ["fala da primeira", "fala da segunda"])

    def test_secao_sem_narracao_cai_no_fallback(self):
        with mock.patch.object(
            services, "_gerar_json", return_value={"slides": [{"ordem": 1, "narracao": "só a 1ª"}]}
        ):
            roteiro = services.gerar_roteiro_video(self.sub)

        self.assertEqual(roteiro[1]["narracao"], "Descrição curta")

    def test_ordem_invalida_e_ignorada(self):
        with mock.patch.object(
            services,
            "_gerar_json",
            return_value={"slides": [{"ordem": "x", "narracao": "n"}, {"narracao": "m"}]},
        ):
            roteiro = services.gerar_roteiro_video(self.sub)

        self.assertEqual([s["narracao"] for s in roteiro], ["Descrição curta"] * 2)

    def test_conteudo_vazio_devolve_roteiro_vazio(self):
        self.sub.conteudo_md = ""
        with mock.patch.object(services, "_gerar_json") as gerar:
            self.assertEqual(services.gerar_roteiro_video(self.sub), [])
        gerar.assert_not_called()


class CategorizarTrilhasTests(TestCase):
    def setUp(self):
        from trilhas.models import Trilha

        user = User.objects.create_user("cat", password="x")
        self.t1 = Trilha.objects.create(user=user, tema_livre="python", titulo="Python")
        self.t2 = Trilha.objects.create(user=user, tema_livre="violão", titulo="Violão")

    def test_salva_a_categoria_de_cada_trilha(self):
        with mock.patch.object(
            services,
            "_gerar_json",
            return_value={
                "categorias": [
                    {"id": self.t1.pk, "categoria": "Programação"},
                    {"id": self.t2.pk, "categoria": "Música"},
                ]
            },
        ):
            resultado = services.categorizar_trilhas([self.t1, self.t2])

        self.t1.refresh_from_db()
        self.t2.refresh_from_db()
        self.assertEqual(self.t1.categoria, "Programação")
        self.assertEqual(self.t2.categoria, "Música")
        self.assertEqual(resultado, {self.t1.pk: "Programação", self.t2.pk: "Música"})

    def test_ignora_ids_desconhecidos_e_categorias_vazias(self):
        with mock.patch.object(
            services,
            "_gerar_json",
            return_value={
                "categorias": [
                    {"id": 999999, "categoria": "Fantasma"},
                    {"id": self.t1.pk, "categoria": "  "},
                ]
            },
        ):
            self.assertEqual(services.categorizar_trilhas([self.t1]), {})

    def test_lista_vazia_nao_chama_a_ia(self):
        with mock.patch.object(services, "_gerar_json") as gerar:
            self.assertEqual(services.categorizar_trilhas([]), {})
        gerar.assert_not_called()


class PercursoTests(TestCase):
    def setUp(self):
        from trilhas.models import Nivel, Percurso, Subtopico, Trilha

        self.user = User.objects.create_user("mentor", password="x")
        self.trilha = Trilha.objects.create(
            user=self.user,
            tema_livre="tema",
            titulo="Trilha A",
            categoria="Programação",
            status=Trilha.Status.EM_ANDAMENTO,
        )
        self.n1 = Nivel.objects.create(
            trilha=self.trilha, ordem=1, titulo="Nível 1", status=Nivel.Status.DISPONIVEL
        )
        Subtopico.objects.create(nivel=self.n1, ordem=1, titulo="Sub 1")
        self.percurso = Percurso.objects.create(user=self.user)

    def test_catalogo_oferece_aprender_no_proximo_topico(self):
        catalogo, texto = services._catalogo_percurso(self.user)
        ref = f"aprender:{self.n1.pk}:1"
        self.assertIn(ref, catalogo)
        self.assertEqual(catalogo[ref]["tipo"], "aprender")
        self.assertIn("[APRENDER]", texto)

    def test_catalogo_oferece_avaliar_quando_lido_e_praticado(self):
        from avaliacoes.models import ListaExercicios

        self.n1.subtopicos.update(lido=True)
        lista = ListaExercicios.objects.create(nivel=self.n1, status=ListaExercicios.Status.PRONTA)
        with mock.patch.object(type(lista), "concluida", property(lambda self: True)):
            catalogo, texto = services._catalogo_percurso(self.user)

        self.assertIn(f"avaliar:{self.n1.pk}", catalogo)
        self.assertIn("[AVALIAR]", texto)

    def test_catalogo_oferece_revisar_e_revisar_global_com_nivel_aprovado(self):
        from trilhas.models import Nivel

        Nivel.objects.filter(pk=self.n1.pk).update(status=Nivel.Status.APROVADO)
        catalogo, texto = services._catalogo_percurso(self.user)

        self.assertIn(f"revisar:{self.n1.pk}", catalogo)
        self.assertIn("revisar_global", catalogo)
        self.assertIn("[REVISAR]", texto)

    def test_catalogo_ignora_trilhas_em_rascunho(self):
        from trilhas.models import Trilha

        Trilha.objects.filter(pk=self.trilha.pk).update(status=Trilha.Status.RASCUNHO)
        catalogo, _texto = services._catalogo_percurso(self.user)
        self.assertEqual(catalogo, {})

    def test_gerar_percurso_sem_catalogo_levanta_iaerror(self):
        with mock.patch.object(services, "_catalogo_percurso", return_value=({}, "")):
            with self.assertRaises(IAError):
                services.gerar_percurso(self.percurso)

    def test_gerar_percurso_persiste_os_passos_renumerados(self):
        ref = f"aprender:{self.n1.pk}:1"
        with mock.patch.object(
            services,
            "_gerar_json",
            return_value={
                "resumo": "Vamos lá!",
                "passos": [
                    {"ref": "inexistente", "titulo": "ignorado", "motivo": "m"},
                    {"ref": ref, "titulo": "Continuar o nível 1", "motivo": "parado há dias"},
                ],
            },
        ):
            services.gerar_percurso(self.percurso)

        self.percurso.refresh_from_db()
        passos = list(self.percurso.passos.all())
        self.assertEqual(self.percurso.resumo, "Vamos lá!")
        # Passo com ref inválido é descartado, e a numeração fica sem buracos.
        self.assertEqual(len(passos), 1)
        self.assertEqual(passos[0].ordem, 1)
        self.assertEqual(passos[0].tipo, "aprender")
        self.assertEqual(passos[0].nivel_id, self.n1.pk)


class SugestoesTests(TestCase):
    def setUp(self):
        from trilhas.models import SessaoSugestao, Trilha

        self.user = User.objects.create_user("sug", password="x")
        self.trilha = Trilha.objects.create(
            user=self.user,
            tema_livre="tema",
            titulo="Python",
            categoria="Programação",
            descricao="Trilha de Python",
            objetivos=["Automatizar tarefas"],
        )
        self.sessao = SessaoSugestao.objects.create(user=self.user)
        self.sessao.trilhas_base.add(self.trilha)

    def test_catalogo_resume_titulo_area_e_objetivos(self):
        texto = services._catalogo_sugestoes([self.trilha])
        self.assertIn('"Python" (área: Programação)', texto)
        self.assertIn("Automatizar tarefas", texto)

    def test_catalogo_inclui_o_titulo_ja_conquistado(self):
        from avaliacoes.models import Titulo
        from trilhas.models import Nivel

        nivel = Nivel.objects.create(
            trilha=self.trilha, ordem=1, titulo="N1", status=Nivel.Status.APROVADO
        )
        Titulo.objects.create(
            trilha=self.trilha, nivel=nivel, nome="Iniciante em Python", faixa="iniciante"
        )
        self.assertIn("Iniciante em Python", services._catalogo_sugestoes([self.trilha]))

    def test_sem_trilha_base_levanta_iaerror(self):
        self.sessao.trilhas_base.clear()
        with self.assertRaises(IAError):
            services.gerar_sugestoes(self.sessao)

    def test_persiste_as_sugestoes_com_tipo_normalizado(self):
        with mock.patch.object(
            services,
            "_gerar_json",
            return_value={
                "sugestoes": [
                    {
                        "titulo": "Django",
                        "tipo": "aprofundar",
                        "enfoque": "web",
                        "topicos": ["ORM"],
                    },
                    {"titulo": "Go", "tipo": "tipo-inventado", "enfoque": "", "topicos": []},
                    {"titulo": "  ", "tipo": "direcao"},  # sem título: descartada
                ]
            },
        ):
            services.gerar_sugestoes(self.sessao)

        sugestoes = list(self.sessao.sugestoes.order_by("ordem"))
        self.assertEqual([s.titulo for s in sugestoes], ["Django", "Go"])
        self.assertEqual([s.tipo for s in sugestoes], ["aprofundar", "direcao"])
        self.assertEqual(sugestoes[0].topicos, ["ORM"])

    def test_nenhuma_sugestao_valida_levanta_iaerror(self):
        with mock.patch.object(
            services, "_gerar_json", return_value={"sugestoes": [{"titulo": "   "}]}
        ):
            with self.assertRaises(IAError):
                services.gerar_sugestoes(self.sessao)


class GerarRevisaoTests(TestCase):
    def setUp(self):
        from avaliacoes.models import Revisao
        from trilhas.models import Nivel, Subtopico, Trilha

        self.user = User.objects.create_user("rev", password="x")
        self.trilha = Trilha.objects.create(user=self.user, tema_livre="tema", titulo="Trilha")
        self.nivel = Nivel.objects.create(
            trilha=self.trilha, ordem=1, titulo="N1", status=Nivel.Status.APROVADO
        )
        Subtopico.objects.create(nivel=self.nivel, ordem=1, titulo="Sub 1")
        self.revisao = Revisao.objects.create(user=self.user)

    def _questao(self, i, origem=1):
        return {
            "ordem": i,
            "origem": origem,
            "enunciado": f"Pergunta {i}",
            "alternativas": [
                {"letra": letra, "texto": f"Alternativa {letra}"} for letra in ("A", "B", "C", "D")
            ],
            "gabarito": "A",
            "explicacao": "Porque sim.",
        }

    def test_sem_nivel_aprovado_levanta_iaerror(self):
        from trilhas.models import Nivel

        Nivel.objects.filter(pk=self.nivel.pk).update(status=Nivel.Status.DISPONIVEL)
        with self.assertRaises(IAError):
            services.gerar_revisao(self.revisao)

    def test_revisao_de_trilha_pede_dez_questoes(self):
        with mock.patch.object(
            services,
            "_gerar_json",
            return_value={"questoes": [self._questao(i) for i in range(1, 11)]},
        ):
            services.gerar_revisao(self.revisao, trilha_id=self.trilha.pk)

        self.assertEqual(self.revisao.questoes.count(), 10)
        questao = self.revisao.questoes.first()
        self.assertEqual(questao.nivel_id, self.nivel.pk)
        self.assertEqual(questao.origem, "Trilha · N1")

    def test_conjunto_incompleto_dispara_nova_tentativa(self):
        incompleto = {"questoes": [self._questao(i) for i in range(1, 4)]}
        completo = {"questoes": [self._questao(i) for i in range(1, 11)]}
        with mock.patch.object(
            services, "_gerar_json", side_effect=[incompleto, completo]
        ) as gerar_json:
            services.gerar_revisao(self.revisao, trilha_id=self.trilha.pk)

        self.assertEqual(gerar_json.call_count, 2)
        self.assertEqual(self.revisao.questoes.count(), 10)

    def test_falha_nas_duas_tentativas_nao_publica(self):
        with mock.patch.object(
            services, "_gerar_json", return_value={"questoes": [self._questao(1)]}
        ) as gerar_json:
            with self.assertRaises(IAError):
                services.gerar_revisao(self.revisao, trilha_id=self.trilha.pk)

        self.assertEqual(gerar_json.call_count, 2)
        self.assertEqual(self.revisao.questoes.count(), 0)

    def test_origem_fora_da_faixa_cai_no_primeiro_nivel(self):
        questoes = [self._questao(i, origem=99) for i in range(1, 11)]
        with mock.patch.object(services, "_gerar_json", return_value={"questoes": questoes}):
            services.gerar_revisao(self.revisao, trilha_id=self.trilha.pk)

        self.assertTrue(all(q.nivel_id == self.nivel.pk for q in self.revisao.questoes.all()))

    def test_revisao_global_dimensiona_pelo_numero_de_niveis(self):
        # 1 nível aprovado -> piso de 6 questões na revisão global.
        with mock.patch.object(
            services,
            "_gerar_json",
            return_value={"questoes": [self._questao(i) for i in range(1, 7)]},
        ):
            services.gerar_revisao(self.revisao)

        self.assertEqual(self.revisao.questoes.count(), 6)


class ResponderDuvidaTests(TestCase):
    def setUp(self):
        from chat.models import Conversa
        from trilhas.models import Nivel, Subtopico, Trilha

        self.user = User.objects.create_user("duvida", password="x")
        self.profile = self.user.profile
        self.profile.quota_tokens_mes = 1_000_000
        self.profile.chat_quota_tokens_mes = 100_000
        self.profile.save(update_fields=["quota_tokens_mes", "chat_quota_tokens_mes"])

        self.trilha = Trilha.objects.create(
            user=self.user, tema_livre="bancos", titulo="Bancos de dados"
        )
        nivel = Nivel.objects.create(trilha=self.trilha, ordem=1, titulo="N1")
        self.sub = Subtopico.objects.create(
            nivel=nivel, ordem=1, titulo="Índices", conteudo_md="Um índice evita o seq scan."
        )
        self.conversa = Conversa.objects.create(user=self.user, subtopico=self.sub)

    def _mensagens(self, pergunta="Por que o índice ajuda?"):
        from chat.models import Mensagem

        Mensagem.objects.create(conversa=self.conversa, papel=Mensagem.Papel.ALUNO, texto=pergunta)
        return Mensagem.objects.create(
            conversa=self.conversa, papel=Mensagem.Papel.IA, status=Mensagem.Status.GERANDO
        )

    def _responder(self, texto="Porque ele evita varrer a tabela.", pedacos=None, msg=None):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.text_stream = iter(pedacos if pedacos is not None else [texto])
        stream.get_final_message.return_value = resposta_fake(
            texto, usage=usage_fake(entrada=800, saida=120)
        )
        client = mock.Mock()
        client.messages.stream.return_value = stream
        with mock.patch.object(services, "get_client", return_value=client):
            return services.responder_duvida(msg or self._mensagens(), self.profile), client

    def test_devolve_o_texto_e_debita_no_balde_do_chat(self):
        texto, _client = self._responder()

        self.assertEqual(texto, "Porque ele evita varrer a tabela.")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.chat_tokens_usados_mes, 920)
        self.assertEqual(self.profile.tokens_usados_mes, 0)

    def test_o_material_da_pagina_vai_no_system(self):
        _texto, client = self._responder()

        system = client.messages.stream.call_args.kwargs["system"][0]["text"]
        self.assertIn("Um índice evita o seq scan.", system)
        self.assertIn("Bancos de dados", system)  # a lista de assuntos permitidos

    @override_settings(CHAT_CONTEXTO_MAX_CHARS=10)
    def test_material_longo_e_truncado(self):
        self.sub.conteudo_md = "x" * 5000
        self.sub.save(update_fields=["conteudo_md"])

        _texto, client = self._responder()

        system = client.messages.stream.call_args.kwargs["system"][0]["text"]
        self.assertNotIn("x" * 11, system)

    def test_a_pergunta_vai_delimitada(self):
        _texto, client = self._responder()

        turnos = client.messages.stream.call_args.kwargs["messages"]
        self.assertEqual(turnos[-1]["role"], "user")
        self.assertIn('"""Por que o índice ajuda?"""', turnos[-1]["content"])

    @override_settings(CHAT_HISTORICO_TURNOS=2)
    def test_historico_e_cortado_no_limite(self):
        from chat.models import Mensagem

        for i in range(6):
            Mensagem.objects.create(
                conversa=self.conversa,
                papel=Mensagem.Papel.ALUNO if i % 2 == 0 else Mensagem.Papel.IA,
                texto=f"fala {i}",
            )
        _texto, client = self._responder()

        turnos = client.messages.stream.call_args.kwargs["messages"]
        self.assertLessEqual(len(turnos), 2)

    def test_conversa_nunca_comeca_pelo_assistente(self):
        from chat.models import Mensagem

        Mensagem.objects.create(conversa=self.conversa, papel=Mensagem.Papel.IA, texto="Olá!")
        _texto, client = self._responder()

        turnos = client.messages.stream.call_args.kwargs["messages"]
        self.assertEqual(turnos[0]["role"], "user")

    def test_fala_em_geracao_nao_entra_no_historico(self):
        pendente = self._mensagens()
        _texto, client = self._responder(msg=pendente)

        turnos = client.messages.stream.call_args.kwargs["messages"]
        self.assertTrue(all(t["content"] for t in turnos))
        self.assertEqual(len(turnos), 1)

    def test_sem_pergunta_levanta_iaerror(self):
        from chat.models import Mensagem

        sozinha = Mensagem.objects.create(
            conversa=self.conversa, papel=Mensagem.Papel.IA, status=Mensagem.Status.GERANDO
        )
        with self.assertRaises(IAError):
            services.responder_duvida(sozinha, self.profile)

    def test_texto_parcial_e_publicado_no_cache(self):
        from django.core.cache import cache

        from chat.models import chave_parcial

        msg = self._mensagens()
        pedacos = [f"p{i} " for i in range(25)]
        self._responder(texto="".join(pedacos), pedacos=pedacos, msg=msg)

        # O flush acontece a cada 20 pedaços: no 20º o cache já tem texto.
        self.assertIn("p0", cache.get(chave_parcial(msg.pk)) or "")

    def test_marcador_de_escopo_e_reconhecido_e_removido(self):
        self.assertTrue(services.fora_de_escopo("[FORA_DE_ESCOPO]\nIsso está fora."))
        self.assertFalse(services.fora_de_escopo("Porque o índice ajuda."))
        self.assertEqual(
            services.limpar_marcador_escopo("[FORA_DE_ESCOPO]\nIsso está fora."),
            "Isso está fora.",
        )
