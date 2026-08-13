"""Testes das etapas do pipeline de vídeo — sem Chromium, edge-tts nem ffmpeg.

Cada etapa é verificada pelo que ela ENTREGA para a etapa seguinte: o fatiamento
das seções, o HTML do slide, os caminhos dos MP3, os argumentos passados ao
ffmpeg e a escolha da trilha sonora. Os processos externos são todos mockados.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from trilhas import tasks, video_montagem, video_pipeline, video_slides, video_tts
from trilhas.models import Nivel, Trilha, VideoNivel
from trilhas.video_utils import fatiar_secoes

User = get_user_model()


# ---------------------------------------------------------------------------
# Fatiamento das seções (a fronteira entre o conteúdo e os slides)
# ---------------------------------------------------------------------------


class FatiarSecoesTests(SimpleTestCase):
    def test_divide_pelos_separadores(self):
        self.assertEqual(fatiar_secoes("Um\n\n---\n\nDois\n\n---\n\nTrês"), ["Um", "Dois", "Três"])

    def test_texto_sem_separador_vira_secao_unica(self):
        self.assertEqual(fatiar_secoes("# Título\ntexto"), ["# Título\ntexto"])

    def test_vazio_devolve_lista_vazia(self):
        self.assertEqual(fatiar_secoes(""), [])
        self.assertEqual(fatiar_secoes("   \n\n  "), [])

    def test_secoes_vazias_sao_descartadas(self):
        self.assertEqual(fatiar_secoes("Um\n\n---\n\n---\n\nDois"), ["Um", "Dois"])

    def test_traco_dentro_de_cerca_de_codigo_nao_separa(self):
        # Regressão: um `---` dentro de ``` é conteúdo (YAML, por exemplo),
        # não separador de slide.
        md = "Antes\n\n```yaml\n---\nchave: valor\n```\n\n---\n\nDepois"
        secoes = fatiar_secoes(md)
        self.assertEqual(len(secoes), 2)
        self.assertIn("chave: valor", secoes[0])

    def test_cerca_com_til_tambem_protege(self):
        md = "Antes\n\n~~~\n---\n~~~\n\n---\n\nDepois"
        self.assertEqual(len(fatiar_secoes(md)), 2)


# ---------------------------------------------------------------------------
# Slides (Chromium mockado)
# ---------------------------------------------------------------------------


class SlideHtmlTests(SimpleTestCase):
    def test_documento_e_autossuficiente(self):
        doc = video_slides._documento("<p>oi</p>")
        self.assertTrue(doc.startswith("<!doctype html>"))
        # Todo CSS entra por file://, sem depender de rede na hora da captura.
        self.assertIn('href="file://', doc)
        self.assertNotIn("http://", doc)

    def test_capa_escapa_o_texto_do_usuario(self):
        doc = video_slides._slide_capa("Trilha", "<script>alert(1)</script>", sub="&", emblema="🎓")
        self.assertIn("&lt;script&gt;", doc)
        self.assertNotIn("<script>alert", doc)
        self.assertIn("🎓", doc)

    def test_capa_omite_as_partes_vazias(self):
        doc = video_slides._slide_capa("", "Só o título")
        self.assertNotIn("cover-kicker", doc)
        self.assertNotIn("cover-sub", doc)
        self.assertNotIn("cover-cred", doc)

    def test_conteudo_renderiza_o_markdown(self):
        doc = video_slides._slide_conteudo("# Cabeçalho\n\ntexto **forte**")
        self.assertIn("<h1", doc)
        self.assertIn("<strong>forte</strong>", doc)

    def test_slide_com_mascote_reserva_o_canto(self):
        self.assertIn("vslide--mascote", video_slides._slide_conteudo("texto", mascote=True))
        self.assertNotIn("vslide--mascote", video_slides._slide_conteudo("texto"))


class RenderSlidesTests(SimpleTestCase):
    def _playwright(self):
        pagina = mock.MagicMock()
        navegador = mock.MagicMock()
        navegador.new_page.return_value = pagina
        p = mock.MagicMock()
        p.chromium.launch.return_value = navegador
        ctx = mock.MagicMock()
        ctx.__enter__.return_value = p
        return ctx, navegador, pagina

    def _render(self, paginas, **kwargs):
        ctx, navegador, pagina = self._playwright()
        with tempfile.TemporaryDirectory() as out:
            with mock.patch("playwright.sync_api.sync_playwright", return_value=ctx):
                caminhos = video_slides.render_slides(paginas, out, **kwargs)
            htmls = sorted(f for f in os.listdir(out) if f.endswith(".html"))
        return caminhos, navegador, pagina, htmls

    def test_um_png_por_pagina_na_ordem(self):
        caminhos, navegador, pagina, htmls = self._render(
            [{"tipo": "capa", "titulo": "A"}, {"tipo": "conteudo", "md": "texto"}]
        )

        self.assertEqual(
            [os.path.basename(c) for c in caminhos], ["slide_000.png", "slide_001.png"]
        )
        self.assertEqual(htmls, ["slide_000.html", "slide_001.html"])
        self.assertEqual(pagina.screenshot.call_count, 2)
        navegador.close.assert_called_once()

    def test_viewport_16_9_com_escala(self):
        _caminhos, navegador, _pagina, _htmls = self._render([{"tipo": "conteudo", "md": "x"}])
        kwargs = navegador.new_page.call_args.kwargs
        self.assertEqual(
            kwargs["viewport"], {"width": video_slides.LARGURA, "height": video_slides.ALTURA}
        )
        self.assertEqual(kwargs["device_scale_factor"], video_slides.ESCALA)

    def test_mermaid_so_roda_no_slide_que_tem_diagrama(self):
        # Carregar a biblioteca em todo slide custa ~0,5s por slide à toa.
        _c, _n, pagina, _h = self._render([{"tipo": "conteudo", "md": "sem diagrama"}])
        pagina.add_script_tag.assert_not_called()

        _c, _n, pagina, _h = self._render(
            [{"tipo": "conteudo", "md": "```mermaid\ngraph TD;A-->B;\n```"}]
        )
        pagina.add_script_tag.assert_called_once()
        pagina.evaluate.assert_called_once()

    def test_lista_vazia_nao_captura_nada(self):
        caminhos, _n, pagina, _h = self._render([])
        self.assertEqual(caminhos, [])
        pagina.screenshot.assert_not_called()


# ---------------------------------------------------------------------------
# Narração (edge-tts mockado)
# ---------------------------------------------------------------------------


class TtsTests(SimpleTestCase):
    def _edge_tts_falso(self, falhas=0):
        """Módulo edge_tts falso; `falhas` = quantas vezes o save quebra antes de ir."""
        chamadas = []
        estado = {"restantes": falhas}

        class Communicate:
            def __init__(self, texto, voz):
                self.texto, self.voz = texto, voz

            async def save(self, destino):
                chamadas.append((self.texto, self.voz, destino))
                if estado["restantes"] > 0:
                    estado["restantes"] -= 1
                    raise RuntimeError("serviço fora do ar")

        return SimpleNamespace(Communicate=Communicate), chamadas

    def _sintetizar(self, narracoes, modulo):
        with tempfile.TemporaryDirectory() as out:
            with mock.patch.dict("sys.modules", {"edge_tts": modulo}):
                return video_tts.sintetizar_narracoes(narracoes, out)

    def test_um_mp3_por_narracao_na_ordem(self):
        modulo, chamadas = self._edge_tts_falso()
        caminhos = self._sintetizar(["um", "dois", "três"], modulo)

        self.assertEqual(
            [os.path.basename(c) for c in caminhos],
            ["audio_000.mp3", "audio_001.mp3", "audio_002.mp3"],
        )
        # As sínteses saem em paralelo, então só o conjunto é determinístico.
        self.assertEqual(sorted(t for t, _v, _d in chamadas), ["dois", "três", "um"])

    def test_narracao_vazia_vira_um_ponto(self):
        # Um clipe sem áudio quebraria a sequência de slides na montagem.
        modulo, chamadas = self._edge_tts_falso()
        self._sintetizar(["  "], modulo)
        self.assertEqual(chamadas[0][0], ".")

    @override_settings(VIDEO_TTS_VOICE="pt-BR-FranciscaNeural")
    def test_voz_configuravel(self):
        modulo, chamadas = self._edge_tts_falso()
        self._sintetizar(["oi"], modulo)
        self.assertEqual(chamadas[0][1], "pt-BR-FranciscaNeural")

    @override_settings(VIDEO_TTS_VOICE="")
    def test_voz_vazia_cai_no_padrao(self):
        modulo, chamadas = self._edge_tts_falso()
        self._sintetizar(["oi"], modulo)
        self.assertEqual(chamadas[0][1], video_tts.VOZ_PADRAO)

    def test_uma_falha_esporadica_e_retentada(self):
        # Perder o vídeo inteiro por uma queda de um slide não é aceitável.
        modulo, chamadas = self._edge_tts_falso(falhas=1)
        with mock.patch("asyncio.sleep", new=self._sleep_falso()):
            self._sintetizar(["oi"], modulo)
        self.assertEqual(len(chamadas), 2)

    def test_falha_persistente_sobe(self):
        modulo, _chamadas = self._edge_tts_falso(falhas=2)
        with mock.patch("asyncio.sleep", new=self._sleep_falso()):
            with self.assertRaises(RuntimeError):
                self._sintetizar(["oi"], modulo)

    @staticmethod
    def _sleep_falso():
        async def _dorme(_s):
            return None

        return _dorme

    def test_lista_vazia_nao_sintetiza_nada(self):
        modulo, chamadas = self._edge_tts_falso()
        self.assertEqual(self._sintetizar([], modulo), [])
        self.assertEqual(chamadas, [])


# ---------------------------------------------------------------------------
# Montagem (ffmpeg mockado)
# ---------------------------------------------------------------------------


class FfmpegRunTests(SimpleTestCase):
    def test_falha_do_ffmpeg_vira_erro_com_a_cauda_do_stderr(self):
        proc = SimpleNamespace(returncode=1, stderr="linha 1\nlinha 2\nfalhou feio", stdout="")
        with mock.patch("subprocess.run", return_value=proc):
            with self.assertRaises(RuntimeError) as ctx:
                video_montagem._run(["ffmpeg", "-y"])
        self.assertIn("falhou feio", str(ctx.exception))

    def test_duracao_le_a_saida_do_ffprobe(self):
        proc = SimpleNamespace(returncode=0, stdout=" 12.5 \n", stderr="")
        with mock.patch("subprocess.run", return_value=proc):
            self.assertEqual(video_montagem.duracao("a.mp3"), 12.5)

    def test_duracao_ilegivel_vira_zero(self):
        proc = SimpleNamespace(returncode=0, stdout="N/A", stderr="")
        with mock.patch("subprocess.run", return_value=proc):
            self.assertEqual(video_montagem.duracao("a.mp3"), 0.0)

    @override_settings(VIDEO_FFMPEG_BIN="/opt/bin/ffmpeg")
    def test_binario_configuravel(self):
        self.assertEqual(video_montagem._ffmpeg(), "/opt/bin/ffmpeg")

    @override_settings(VIDEO_WORKERS=5)
    def test_workers_forcado_pelo_settings(self):
        self.assertEqual(video_montagem.workers(), 5)

    @override_settings(VIDEO_WORKERS=0)
    def test_workers_padrao_limitado_a_dois(self):
        self.assertLessEqual(video_montagem.workers(), 2)
        self.assertGreaterEqual(video_montagem.workers(), 1)


class ClipeTests(SimpleTestCase):
    def _args_do_clipe(self, avatar=None):
        with (
            mock.patch.object(video_montagem, "duracao", return_value=3.0),
            mock.patch.object(video_montagem, "_run") as run,
        ):
            video_montagem._clipe("s.png", "a.mp3", "c.mp4", avatar=avatar)
        return run.call_args.args[0]

    def test_clipe_dura_o_tempo_do_audio(self):
        args = self._args_do_clipe()
        self.assertIn("-t", args)
        self.assertEqual(args[args.index("-t") + 1], "3.000")

    def test_sem_mascote_nao_ha_overlay(self):
        args = " ".join(self._args_do_clipe())
        self.assertIn("zoompan", args)  # Ken Burns
        self.assertNotIn("overlay", args)

    def test_com_mascote_usa_libvpx_para_ler_o_alfa(self):
        # Com o decodificador nativo o mascote sairia num quadrado preto opaco.
        args = self._args_do_clipe(avatar="m.webm")
        self.assertIn("libvpx-vp9", args)
        self.assertIn("overlay", " ".join(args))

    def test_audio_curtissimo_respeita_a_duracao_minima(self):
        with (
            mock.patch.object(video_montagem, "duracao", return_value=0.1),
            mock.patch.object(video_montagem, "_run") as run,
        ):
            video_montagem._clipe("s.png", "a.mp3", "c.mp4")
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-t") + 1], "0.800")


class MontarTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _montar(self, *, slides=None, audios=None, musica=None, avatares=None, progresso=None):
        slides = slides if slides is not None else ["s0.png", "s1.png"]
        audios = audios if audios is not None else ["a0.mp3", "a1.mp3"]
        with (
            mock.patch.object(video_montagem, "_clipe") as clipe,
            mock.patch.object(video_montagem, "_concat") as concat,
            mock.patch.object(video_montagem, "_com_musica") as com_musica,
            mock.patch.object(video_montagem, "_faststart") as faststart,
            mock.patch.object(video_montagem, "duracao", return_value=42.0),
        ):
            dur = video_montagem.montar(
                slides,
                audios,
                os.path.join(self.tmp.name, "final.mp4"),
                work_dir=os.path.join(self.tmp.name, "work"),
                musica=musica,
                avatares=avatares,
                progresso=progresso,
            )
        return dur, SimpleNamespace(
            clipe=clipe, concat=concat, com_musica=com_musica, faststart=faststart
        )

    def test_um_clipe_por_slide_e_a_duracao_final(self):
        dur, m = self._montar()
        self.assertEqual(dur, 42.0)
        self.assertEqual(m.clipe.call_count, 2)
        m.concat.assert_called_once()

    def test_sem_musica_apenas_faststart(self):
        _dur, m = self._montar()
        m.faststart.assert_called_once()
        m.com_musica.assert_not_called()

    def test_com_musica_existente_faz_a_mixagem(self):
        faixa = os.path.join(self.tmp.name, "fundo.ogg")
        open(faixa, "wb").close()
        _dur, m = self._montar(musica=faixa)
        m.com_musica.assert_called_once()
        m.faststart.assert_not_called()

    def test_musica_inexistente_cai_no_video_so_com_narracao(self):
        _dur, m = self._montar(musica="/caminho/que/nao/existe.ogg")
        m.faststart.assert_called_once()
        m.com_musica.assert_not_called()

    def test_avatar_e_repassado_por_indice(self):
        _dur, m = self._montar(avatares=["av0.webm"])
        # O primeiro clipe recebe o mascote; o segundo, que não tem, sai sem.
        avatares = [c.kwargs["avatar"] for c in m.clipe.call_args_list]
        self.assertEqual(avatares, ["av0.webm", None])

    def test_progresso_reporta_todos_os_clipes(self):
        vistos = []
        self._montar(progresso=lambda pronto, total: vistos.append((pronto, total)))
        self.assertEqual(sorted(vistos), [(1, 2), (2, 2)])

    def test_listas_desalinhadas_sao_recusadas(self):
        with self.assertRaises(ValueError):
            self._montar(slides=["s0.png"], audios=["a0.mp3", "a1.mp3"])

    def test_sem_slides_e_recusado(self):
        with self.assertRaises(ValueError):
            self._montar(slides=[], audios=[])


# ---------------------------------------------------------------------------
# Trilha sonora por categoria
# ---------------------------------------------------------------------------


class MusicaTests(SimpleTestCase):
    def _clima(self, categoria):
        """Clima escolhido para a categoria (identificado pelo nome do arquivo)."""
        trilha = SimpleNamespace(categoria=categoria)
        with mock.patch("os.path.exists", return_value=True):
            caminho, credito = video_pipeline._musica_para(trilha)
        nome = os.path.basename(caminho)
        return next(c for c, (arq, _cred) in video_pipeline.MUSICAS.items() if arq == nome), credito

    def test_categorias_mapeiam_para_climas_distintos(self):
        self.assertEqual(self._clima("Programação")[0], "corporativo")
        self.assertEqual(self._clima("Matemática")[0], "ambiente")
        self.assertEqual(self._clima("Direito")[0], "espiritual")
        self.assertEqual(self._clima("Música")[0], "ameno")

    def test_chave_curta_nao_casa_no_meio_de_outra_palavra(self):
        # Regressão: como substring, o "ti" de TI casava dentro de "matemática",
        # "estatística" e "linguística", jogando-as na trilha corporativa.
        self.assertEqual(self._clima("Estatística")[0], "ambiente")
        self.assertEqual(self._clima("Linguística")[0], "ameno")
        self.assertEqual(self._clima("TI")[0], "corporativo")

    def test_categoria_desconhecida_cai_no_clima_neutro(self):
        self.assertEqual(self._clima("Jardinagem")[0], "ambiente")
        self.assertEqual(self._clima("")[0], "ambiente")

    def test_faixa_escolhida_vem_com_credito(self):
        _clima, credito = self._clima("Programação")
        self.assertIn("CC BY 4.0", credito)

    def test_faixa_ausente_no_disco_deixa_o_video_so_com_narracao(self):
        with mock.patch("os.path.exists", return_value=False):
            caminho, credito = video_pipeline._musica_para(SimpleNamespace(categoria="Música"))
        self.assertIsNone(caminho)
        self.assertEqual(credito, "")

    def test_override_do_settings_vence(self):
        with tempfile.NamedTemporaryFile(suffix=".ogg") as fixa:
            with override_settings(VIDEO_MUSICA_PATH=fixa.name):
                caminho, credito = video_pipeline._musica_para(
                    SimpleNamespace(categoria="Programação")
                )
        self.assertEqual(caminho, fixa.name)
        self.assertEqual(credito, "")


# ---------------------------------------------------------------------------
# Task do vídeo
# ---------------------------------------------------------------------------


class TaskVideoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("vid", password="x")
        trilha = Trilha.objects.create(user=self.user, tema_livre="tema", titulo="T")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1")
        self.video = VideoNivel.objects.create(nivel=nivel, status=VideoNivel.Status.GERANDO)

    def _rodar(self, video_id=None):
        return tasks.task_gerar_video_nivel.apply(
            args=[video_id if video_id is not None else self.video.pk], throw=False
        )

    def test_video_inexistente_encerra_sem_erro(self):
        self.assertEqual(self._rodar(999999).result, "vídeo inexistente")

    def test_sucesso_marca_pronto_em_cem_por_cento(self):
        def gerar(video, _profile, progresso=None):
            video.arquivo = "/media/videos/1/x.mp4"
            video.duracao_seg = 90
            return video.arquivo, 90

        with mock.patch.object(video_pipeline, "gerar_video", side_effect=gerar):
            self._rodar()

        self.video.refresh_from_db()
        self.assertEqual(self.video.status, VideoNivel.Status.PRONTO)
        self.assertEqual(self.video.progresso_pct, 100)
        self.assertEqual(self.video.etapa, "")
        self.assertEqual(self.video.arquivo, "/media/videos/1/x.mp4")

    def test_callback_de_progresso_grava_pct_e_etapa(self):
        def gerar(_video, _profile, progresso=None):
            progresso(30, "Narrando os slides")
            progresso(55)  # sem etapa: mantém a anterior
            return "", 0

        with mock.patch.object(video_pipeline, "gerar_video", side_effect=gerar):
            with mock.patch.object(VideoNivel, "save"):  # congela o estado do progresso
                self._rodar()

        self.video.refresh_from_db()
        self.assertEqual(self.video.progresso_pct, 55)
        self.assertEqual(self.video.etapa, "Narrando os slides")

    def test_falha_marca_erro_e_limpa_a_etapa(self):
        # Sem retry: regerar do zero é caro, o usuário reenvia.
        with mock.patch.object(
            video_pipeline, "gerar_video", side_effect=RuntimeError("ffmpeg falhou")
        ):
            self._rodar()

        self.video.refresh_from_db()
        self.assertEqual(self.video.status, VideoNivel.Status.ERRO)
        self.assertIn("ffmpeg falhou", self.video.erro)
        self.assertEqual(self.video.etapa, "")

    def test_mensagem_de_erro_e_truncada(self):
        with mock.patch.object(video_pipeline, "gerar_video", side_effect=RuntimeError("x" * 5000)):
            self._rodar()

        self.video.refresh_from_db()
        self.assertEqual(len(self.video.erro), 2000)
