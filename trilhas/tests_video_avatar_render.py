"""Testes da produção da arte e do clipe do mascote — sem Chromium nem ffmpeg.

A animação em si já é coberta em `tests_video.py`; aqui é a parte que sai do
Python para o mundo: o cache dos PNGs das camadas, a leitura do volume da
narração e a composição quadro a quadro que alimenta o ffmpeg pelo stdin.
"""

import os
import tempfile
from array import array
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from trilhas import video_avatar


def arte_falsa(destino, tam):
    """Gera os PNGs de todas as camadas, no tamanho que `_compor` espera."""
    from PIL import Image

    escala = tam / video_avatar.DISCO
    disco = (tam, tam)
    cabeca = (
        round(video_avatar.CAB_RECORTE[2] * escala),
        round(video_avatar.CAB_RECORTE[3] * escala),
    )
    mapa = {}
    for nome, tamanho in [
        ("fundo", disco),
        ("frente", disco),
        ("corpo", disco),
        *[
            (f"cabeca_b{b}_o{o}_s{s}", cabeca)
            for b in range(video_avatar.NIVEIS_BOCA)
            for o in video_avatar.OLHOS
            for s in video_avatar.CEJAS
        ],
    ]:
        caminho = os.path.join(destino, f"{nome}.png")
        Image.new("RGBA", tamanho, (10, 20, 30, 255)).save(caminho)
        mapa[nome] = caminho
    return mapa


class CamadasCacheTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _playwright(self):
        pagina = mock.MagicMock()

        def screenshot(path, **kwargs):
            open(path, "wb").close()

        pagina.screenshot.side_effect = screenshot
        navegador = mock.MagicMock()
        navegador.new_page.return_value = pagina
        p = mock.MagicMock()
        p.chromium.launch.return_value = navegador
        ctx = mock.MagicMock()
        ctx.__enter__.return_value = p
        return ctx, navegador, pagina

    def _dir(self, tam, paleta=None):
        """Caminho do cache — resolvido com o MEDIA_ROOT do teste."""
        with override_settings(MEDIA_ROOT=self.tmp.name):
            return video_avatar._dir_cache(tam, video_avatar.cores(paleta))

    def _camadas(self, tam=100):
        ctx, navegador, pagina = self._playwright()
        with (
            override_settings(MEDIA_ROOT=self.tmp.name),
            mock.patch("playwright.sync_api.sync_playwright", return_value=ctx),
        ):
            mapa = video_avatar.camadas(tam)
        return mapa, navegador, pagina

    def test_gera_um_png_por_camada(self):
        mapa, _navegador, pagina = self._camadas()

        esperadas = {nome for nome, _rec, _fn in video_avatar._camadas()}
        self.assertEqual(set(mapa), esperadas)
        self.assertEqual(pagina.screenshot.call_count, len(esperadas))
        self.assertTrue(all(os.path.exists(p) for p in mapa.values()))

    def test_alfa_e_preservado_na_captura(self):
        # Sem omit_background o mascote sairia num quadrado opaco.
        _mapa, _navegador, pagina = self._camadas()
        self.assertTrue(pagina.screenshot.call_args.kwargs["omit_background"])

    def test_segunda_chamada_nao_abre_o_chromium(self):
        self._camadas()
        _mapa, navegador, pagina = self._camadas()

        pagina.screenshot.assert_not_called()
        navegador.close.assert_not_called()

    def test_nada_parcial_fica_no_cache_se_a_captura_falhar(self):
        ctx, _navegador, pagina = self._playwright()
        pagina.screenshot.side_effect = RuntimeError("Chromium morreu")

        with (
            override_settings(MEDIA_ROOT=self.tmp.name),
            mock.patch("playwright.sync_api.sync_playwright", return_value=ctx),
        ):
            with self.assertRaises(RuntimeError):
                video_avatar.camadas(100)

        destino = self._dir(100)
        self.assertEqual([n for n in os.listdir(destino) if n.endswith(".png")], [])

    def test_tamanho_novo_descarta_o_cache_do_tamanho_antigo(self):
        self._camadas(tam=100)
        antigo = self._dir(100)
        self.assertTrue(os.path.isdir(antigo))

        self._camadas(tam=120)

        self.assertFalse(os.path.isdir(antigo))

    def test_paletas_diferentes_convivem_no_cache(self):
        # Apagar a paleta das outras trilhas faria a arte ser regerada a cada vídeo.
        ctx, _n, _p = self._playwright()
        with (
            override_settings(MEDIA_ROOT=self.tmp.name),
            mock.patch("playwright.sync_api.sync_playwright", return_value=ctx),
        ):
            video_avatar.camadas(100, paleta={"pele": "#ffcc00"})
            ctx2, _n2, _p2 = self._playwright()
            with mock.patch("playwright.sync_api.sync_playwright", return_value=ctx2):
                video_avatar.camadas(100, paleta={"pele": "#00ccff"})

        a = self._dir(100, {"pele": "#ffcc00"})
        b = self._dir(100, {"pele": "#00ccff"})

        self.assertNotEqual(a, b)
        self.assertTrue(os.path.isdir(a))
        self.assertTrue(os.path.isdir(b))


class RmsPorQuadroTests(SimpleTestCase):
    def _pcm(self, amostras):
        return array("h", amostras).tobytes()

    def _rms(self, dados, returncode=0):
        proc = SimpleNamespace(returncode=returncode, stdout=dados, stderr=b"")
        with mock.patch("subprocess.run", return_value=proc):
            return video_avatar._rms_por_quadro("narracao.mp3")

    def test_silencio_da_rms_zero(self):
        valores = self._rms(self._pcm([0] * video_avatar.TAXA_PCM))
        self.assertTrue(all(v == 0.0 for v in valores))

    def test_uma_janela_por_fracao_de_segundo(self):
        # 1 s de áudio -> FPS_BOCA janelas.
        valores = self._rms(self._pcm([1000] * video_avatar.TAXA_PCM))
        self.assertEqual(len(valores), video_avatar.FPS_BOCA)

    def test_volume_maior_gera_rms_maior(self):
        baixo = self._rms(self._pcm([500] * video_avatar.TAXA_PCM))
        alto = self._rms(self._pcm([5000] * video_avatar.TAXA_PCM))
        self.assertLess(baixo[0], alto[0])

    def test_audio_vazio_devolve_lista_vazia(self):
        self.assertEqual(self._rms(b""), [])

    def test_bytes_impares_nao_quebram_a_conversao(self):
        # PCM truncado no meio de uma amostra: sobra é descartada.
        dados = self._pcm([1000] * 100) + b"\x01"
        self.assertTrue(self._rms(dados))

    def test_falha_do_ffmpeg_sobe(self):
        with self.assertRaises(RuntimeError):
            self._rms(b"", returncode=1)


class ComporTests(SimpleTestCase):
    TAM = 80

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.arte = arte_falsa(self.tmp.name, self.TAM)
        self.destino = os.path.join(self.tmp.name, "mascote.webm")

    def _popen(self, returncode=0, stderr=b""):
        proc = mock.MagicMock()
        proc.stdin = mock.MagicMock()
        proc.stderr = mock.MagicMock()
        proc.stderr.read.return_value = stderr
        proc.wait.return_value = returncode
        return proc

    def _anim(self, n=3):
        return [
            video_avatar.Quadro(
                boca=i % video_avatar.NIVEIS_BOCA,
                olhos="aberto",
                cejas="neutro",
                corpo_dx=0.4 * i,
                corpo_dy=0.2 * i,
                cab_dx=0.3 * i,
                cab_dy=0.1 * i,
                cab_giro=0.5 * i,
            )
            for i in range(n)
        ]

    def _compor(self, anim, **kwargs):
        proc = self._popen(**kwargs)
        with mock.patch("subprocess.Popen", return_value=proc) as popen:
            video_avatar._compor(anim, self.arte, self.TAM, self.destino)
        return proc, popen

    def test_um_quadro_rgba_por_quadro_da_animacao(self):
        proc, _popen = self._compor(self._anim(3))

        self.assertEqual(proc.stdin.write.call_count, 3)
        esperado = self.TAM * self.TAM * 4  # RGBA
        for chamada in proc.stdin.write.call_args_list:
            self.assertEqual(len(chamada.args[0]), esperado)

    def test_ffmpeg_recebe_vp9_com_alfa(self):
        # Sem yuva420p o mascote perde o recorte circular na sobreposição.
        _proc, popen = self._compor(self._anim(1))
        args = popen.call_args.args[0]
        self.assertIn("libvpx-vp9", args)
        self.assertIn("yuva420p", args)
        self.assertEqual(args[args.index("-s") + 1], f"{self.TAM}x{self.TAM}")

    def test_stdin_e_fechado_ao_final(self):
        proc, _popen = self._compor(self._anim(2))
        proc.stdin.close.assert_called_once()

    def test_pipe_quebrado_nao_estoura(self):
        proc = self._popen()
        proc.stdin.write.side_effect = BrokenPipeError
        with mock.patch("subprocess.Popen", return_value=proc):
            video_avatar._compor(self._anim(2), self.arte, self.TAM, self.destino)
        # Chegou ao fim e ainda conferiu o resultado do ffmpeg.
        proc.wait.assert_called_once()

    def test_falha_do_ffmpeg_vira_erro_com_a_cauda_do_stderr(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._compor(self._anim(1), returncode=1, stderr=b"erro 1\nerro 2\nmorreu aqui")
        self.assertIn("morreu aqui", str(ctx.exception))

    def test_mascara_do_disco_e_circular(self):
        lado = self.TAM + 2 * round(video_avatar.MARGEM * self.TAM / video_avatar.DISCO)
        mascara = video_avatar._mascara_disco(lado, self.TAM, lado // 2 - self.TAM // 2)

        self.assertEqual(mascara.size, (lado, lado))
        self.assertEqual(mascara.getpixel((lado // 2, lado // 2)), 255)  # centro opaco
        self.assertEqual(mascara.getpixel((0, 0)), 0)  # canto transparente


class ClipesTests(SimpleTestCase):
    TAM = 80

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.arte = arte_falsa(self.tmp.name, self.TAM)

    @override_settings(VIDEO_AVATAR=True, VIDEO_AVATAR_TAMANHO=TAM)
    def test_um_clipe_por_narracao_na_ordem(self):
        saida = os.path.join(self.tmp.name, "clipes")
        with (
            mock.patch.object(video_avatar, "camadas", return_value=self.arte),
            mock.patch.object(video_avatar, "bocas_por_quadro", return_value=[0, 2, 3, 1] * 8),
            mock.patch.object(video_avatar, "_compor") as compor,
        ):
            caminhos = video_avatar.clipes(["a.mp3", "b.mp3"], saida)

        self.assertEqual(
            [os.path.basename(c) for c in caminhos], ["mascote_000.webm", "mascote_001.webm"]
        )
        self.assertEqual(compor.call_count, 2)

    @override_settings(VIDEO_AVATAR=True, VIDEO_AVATAR_TAMANHO=TAM)
    def test_narracao_muda_o_dado_e_nao_o_numero_de_clipes(self):
        # Áudio sem fala nenhuma ainda rende um clipe (com quadro parado).
        saida = os.path.join(self.tmp.name, "clipes")
        with (
            mock.patch.object(video_avatar, "camadas", return_value=self.arte),
            mock.patch.object(video_avatar, "bocas_por_quadro", return_value=[]),
            mock.patch.object(video_avatar, "_compor") as compor,
        ):
            caminhos = video_avatar.clipes(["mudo.mp3"], saida)

        self.assertEqual(len(caminhos), 1)
        anim = compor.call_args.args[0]
        self.assertEqual(len(anim), 1)

    @override_settings(VIDEO_AVATAR=True, VIDEO_AVATAR_TAMANHO=TAM)
    def test_falha_na_composicao_degrada_sem_derrubar_o_video(self):
        with (
            mock.patch.object(video_avatar, "camadas", return_value=self.arte),
            mock.patch.object(video_avatar, "bocas_por_quadro", return_value=[1, 2]),
            mock.patch.object(video_avatar, "_compor", side_effect=RuntimeError("ffmpeg fora")),
        ):
            with self.assertLogs("trilhas.video_avatar", level="WARNING"):
                self.assertIsNone(video_avatar.clipes(["a.mp3"], self.tmp.name))
