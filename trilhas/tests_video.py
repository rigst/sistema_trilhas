"""Testes do vídeo do nível e do mascote apresentador (sem ffmpeg/Chromium)."""

import threading
import time
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from trilhas import video_avatar, video_pipeline, video_utils
from trilhas.models import Nivel, Subtopico, Trilha, VideoNivel

User = get_user_model()


def _fala(padrao, repeticoes=1):
    """Sequência de níveis de boca, no ritmo de análise (FPS_BOCA)."""
    return list(padrao) * repeticoes


class BocasPorQuadroTests(SimpleTestCase):
    """A abertura da boca sai do volume da narração, quadro a quadro."""

    def _niveis(self, rms):
        with mock.patch.object(video_avatar, '_rms_por_quadro', return_value=rms):
            return video_avatar.bocas_por_quadro('narracao.mp3')

    def test_silencio_mantem_a_boca_fechada(self):
        self.assertEqual(self._niveis([0.0] * 5), [0] * 5)

    def test_audio_vazio_nao_gera_animacao(self):
        self.assertEqual(self._niveis([]), [])

    def test_volume_alto_abre_mais_que_volume_baixo(self):
        niveis = self._niveis([500.0] * 10 + [10000.0] * 10)
        self.assertLess(niveis[5], niveis[-1])
        self.assertEqual(niveis[-1], len(video_avatar.LIMIARES))

    def test_referencia_e_relativa_ao_proprio_audio(self):
        """Dobrar o volume do arquivo inteiro não muda a animação."""
        base = [800.0, 4000.0, 12000.0, 300.0] * 6
        self.assertEqual(self._niveis(base), self._niveis([v * 2 for v in base]))

    def test_um_nivel_por_janela(self):
        rms = [float(i * 137 % 9000) for i in range(50)]
        niveis = self._niveis(rms)
        self.assertEqual(len(niveis), len(rms))
        self.assertTrue(all(0 <= n < video_avatar.NIVEIS_BOCA for n in niveis))


class SuavidadeTests(SimpleTestCase):
    """O movimento é contínuo: nada de saltar de uma pose para outra.

    Este é o teste que segura a regressão principal — todo deslocamento é
    calculado por quadro, com as curvas saindo e chegando em zero.
    """

    # Limites por quadro a 30 fps. O pico é a soma das derivadas das curvas que
    # podem coincidir num quadro (deriva + respiração + ênfase + aceno) — bem
    # abaixo de um salto de pose, que moveria vários pixels de uma vez.
    LIMITE_PX = 1.1
    LIMITE_GRAUS = 1.1

    def _anim(self):
        # Fala longa, com pausas, para acionar aceno, ênfase e gestos.
        niveis = _fala([2, 3, 1, 2, 3, 2, 1, 2] + [0] * 14, 8)
        return video_avatar.animacao(niveis, fps=30)

    def test_cabeca_e_corpo_andam_em_passos_pequenos(self):
        anim = self._anim()
        self.assertGreater(len(anim), 200)
        for campo, limite in (('cab_dx', self.LIMITE_PX), ('cab_dy', self.LIMITE_PX),
                              ('corpo_dx', self.LIMITE_PX), ('corpo_dy', self.LIMITE_PX),
                              ('cab_giro', self.LIMITE_GRAUS)):
            saltos = [abs(getattr(b, campo) - getattr(a, campo))
                      for a, b in zip(anim, anim[1:])]
            self.assertLessEqual(
                max(saltos), limite,
                f'{campo} saltou {max(saltos):.2f} entre quadros vizinhos',
            )

    def test_curvas_saem_e_chegam_em_zero(self):
        self.assertAlmostEqual(video_avatar._pulso(0.0), 0.0, places=6)
        self.assertAlmostEqual(video_avatar._pulso(1.0), 0.0, places=6)
        self.assertAlmostEqual(video_avatar._suave(0.0), 0.0, places=6)
        self.assertAlmostEqual(video_avatar._suave(1.0), 1.0, places=6)


class AnimacaoTests(SimpleTestCase):
    """Estrutura e duração da animação."""

    def test_um_quadro_por_quadro_de_video(self):
        niveis = _fala([2] * video_avatar.FPS_BOCA, 4)  # 4 segundos de fala
        anim = video_avatar.animacao(niveis, fps=30)
        self.assertEqual(len(anim), 4 * 30)

    def test_narracao_vazia_nao_gera_quadros(self):
        self.assertEqual(video_avatar.animacao([], fps=30), [])

    def test_estados_de_arte_sempre_existem_no_cache(self):
        anim = video_avatar.animacao(_fala([0, 2, 3, 1] + [0] * 12, 10), fps=30)
        for q in anim:
            self.assertIn(q.olhos, video_avatar.OLHOS)
            self.assertIn(q.cejas, video_avatar.CEJAS)
            self.assertIn(q.boca, range(video_avatar.NIVEIS_BOCA))

    def test_o_disco_nao_tem_como_se_mexer(self):
        """Só o personagem tem deslocamento; fundo e aro não são parametrizados."""
        moveis = {'corpo_dx', 'corpo_dy', 'cab_dx', 'cab_dy', 'cab_giro'}
        self.assertEqual(set(video_avatar.Quadro._fields) - moveis,
                         {'boca', 'olhos', 'cejas'})

    def test_respiracao_continua_no_silencio(self):
        """Mesmo sem fala o mascote não congela."""
        anim = video_avatar.animacao([0] * 60, fps=30)
        self.assertGreater(len({round(q.corpo_dy, 2) for q in anim}), 10)


class EventosTests(SimpleTestCase):
    """Reações tiradas do desenho da narração."""

    def _tipos(self, niveis, inicio=0):
        return [t for t, _, _ in video_avatar.eventos(niveis, inicio)]

    def test_pausa_longa_vira_aceno_e_piscada(self):
        vazio = round(video_avatar.PAUSA_MINIMA * video_avatar.FPS_BOCA) + 2
        tipos = self._tipos([2] * 10 + [0] * vazio + [2] * 10)
        self.assertIn('aceno', tipos)
        self.assertIn('piscada', tipos)

    def test_pausa_curta_nao_dispara_aceno(self):
        curto = round(video_avatar.PAUSA_MINIMA * video_avatar.FPS_BOCA) - 2
        self.assertNotIn('aceno', self._tipos([2] * 10 + [0] * curto + [2] * 10))

    def test_silaba_forte_dispara_enfase(self):
        niveis = [1] * 5 + [video_avatar.NIVEIS_BOCA - 1] + [1] * 20
        self.assertIn('enfase', self._tipos(niveis))

    def test_enfase_respeita_o_intervalo_minimo(self):
        """Narração inteira no volume máximo não vira ênfase contínua."""
        duracao = 10.0
        niveis = [video_avatar.NIVEIS_BOCA - 1] * int(duracao * video_avatar.FPS_BOCA)
        enfases = self._tipos(niveis).count('enfase')
        self.assertLessEqual(enfases, duracao / video_avatar.ENFASE_INTERVALO + 1)

    def test_rodizio_de_gestos_muda_com_o_slide(self):
        """Cada narração começa em um ponto diferente do roteiro de gestos.

        Sem isso todo slide faria o mesmo gesto, porque cada um tem poucas pausas.
        """
        vazio = round(video_avatar.PAUSA_MINIMA * video_avatar.FPS_BOCA) + 2
        niveis = [2] * 10 + [0] * vazio + [2] * 10
        vistos = {tuple(sorted(set(self._tipos(niveis, i))))
                  for i in range(len(video_avatar.ROTEIRO_PAUSAS))}
        self.assertGreater(len(vistos), 1)

    def test_piscadas_de_repouso_ao_longo_da_fala(self):
        niveis = [2] * (video_avatar.FPS_BOCA * 20)  # 20 s sem pausa nenhuma
        self.assertGreaterEqual(self._tipos(niveis).count('piscada'), 3)


class ArteTests(SimpleTestCase):
    """Camadas, paleta e cache."""

    def test_uma_camada_por_estado_de_arte(self):
        nomes = [nome for nome, _, _ in video_avatar._camadas()]
        self.assertEqual(len(nomes), len(set(nomes)))
        cabecas = video_avatar.NIVEIS_BOCA * len(video_avatar.OLHOS) \
            * len(video_avatar.CEJAS)
        self.assertEqual(len(nomes), 3 + cabecas)

    def test_pivo_de_rotacao_cai_no_centro_do_recorte(self):
        """O Pillow gira em torno do centro do recorte: a cabeça no pescoço."""
        x, y, larg, alt = video_avatar.CAB_RECORTE
        self.assertEqual((x + larg / 2, y + alt / 2), (100, 148))

    def test_boca_fechada_nao_desenha_dentes(self):
        c = video_avatar.cores()
        self.assertNotIn(c['dentes'], video_avatar._boca_svg(0, c))
        self.assertIn(c['dentes'], video_avatar._boca_svg(3, c))

    def test_sobrancelhas_sobem_por_estado(self):
        c = video_avatar.cores()
        alturas = [video_avatar._sobrancelhas_svg(e, c) for e in video_avatar.CEJAS]
        self.assertEqual(len(set(alturas)), len(video_avatar.CEJAS))

    def test_olhos_tem_meio_fechado_entre_aberto_e_fechado(self):
        c = video_avatar.cores()
        estados = [video_avatar._olhos_svg(e, c) for e in video_avatar.OLHOS]
        self.assertEqual(len(set(estados)), len(video_avatar.OLHOS))

    @override_settings(VIDEO_AVATAR_PALETA={'camisa_a': '#FF0000'})
    def test_paleta_pode_ser_sobrescrita_pelo_settings(self):
        c = video_avatar.cores()
        self.assertEqual(c['camisa_a'], '#FF0000')
        self.assertEqual(c['pele'], video_avatar.PALETA['pele'])
        self.assertIn('#FF0000', video_avatar._defs(c))

    def test_cada_trilha_recebe_sempre_a_mesma_paleta(self):
        class FakeTrilha:
            def __init__(self, pk):
                self.pk = pk

        for pk in (1, 7, 42, 1234):
            escolhas = {id(video_avatar.paleta_da_trilha(FakeTrilha(pk)))
                        for _ in range(5)}
            self.assertEqual(len(escolhas), 1, 'a paleta da trilha oscilou')

    def test_trilhas_diferentes_usam_paletas_diferentes(self):
        class FakeTrilha:
            def __init__(self, pk):
                self.pk = pk

        vistas = {video_avatar._dir_cache(
            200, video_avatar.cores(video_avatar.paleta_da_trilha(FakeTrilha(pk))))
            for pk in range(1, 40)}
        self.assertGreater(len(vistas), 1)
        self.assertLessEqual(len(vistas), len(video_avatar.PALETAS))

    def test_toda_paleta_alternativa_usa_chaves_conhecidas(self):
        """Uma chave com typo passaria despercebida e não trocaria cor nenhuma."""
        for i, variante in enumerate(video_avatar.PALETAS):
            desconhecidas = set(variante) - set(video_avatar.PALETA)
            self.assertEqual(desconhecidas, set(), f'paleta {i}: {desconhecidas}')

    def test_trocar_a_paleta_invalida_o_cache(self):
        padrao = video_avatar._dir_cache(200, video_avatar.PALETA)
        outra = dict(video_avatar.PALETA, camisa_a='#FF0000')
        self.assertNotEqual(video_avatar._dir_cache(200, outra), padrao)

    def test_cache_separa_versao_da_arte_e_tamanho(self):
        c = video_avatar.PALETA
        self.assertNotEqual(video_avatar._dir_cache(240, c),
                            video_avatar._dir_cache(200, c))
        self.assertIn(f'v{video_avatar.VERSAO_ARTE}_240',
                      video_avatar._dir_cache(240, c))


class ClipesTests(SimpleTestCase):
    """Contrato de degradação: o vídeo sai mesmo sem o mascote."""

    @override_settings(VIDEO_AVATAR=False)
    def test_mascote_desligado_nao_gera_clipes(self):
        self.assertIsNone(video_avatar.clipes(['a.mp3'], '/tmp/ignorado'))

    def test_sem_narracoes_nao_gera_clipes(self):
        self.assertIsNone(video_avatar.clipes([], '/tmp/ignorado'))

    @override_settings(VIDEO_AVATAR=True)
    def test_falha_na_arte_degrada_sem_derrubar_o_video(self):
        with mock.patch.object(video_avatar, 'camadas', side_effect=OSError('sem disco')):
            with self.assertLogs('trilhas.video_avatar', level='WARNING'):
                self.assertIsNone(video_avatar.clipes(['a.mp3'], '/tmp/ignorado'))


class EmParaleloTests(SimpleTestCase):
    """As etapas caras rodam em threads, mas o vídeo depende da ordem."""

    def test_resultados_voltam_na_ordem_de_entrada(self):
        # O primeiro item é o mais lento de propósito: quem termina antes não
        # pode furar a fila, senão slides e narrações sairiam trocados.
        def _lento(n):
            time.sleep(0.05 if n == 0 else 0)
            return n * 10

        self.assertEqual(
            video_utils.em_paralelo(_lento, [0, 1, 2, 3], 4), [0, 10, 20, 30])

    def test_progresso_conta_na_ordem_de_termino(self):
        vistos = []
        video_utils.em_paralelo(
            lambda n: n, [1, 2, 3], 3,
            progresso=lambda pronto, total: vistos.append((pronto, total)))
        self.assertEqual(vistos, [(1, 3), (2, 3), (3, 3)])

    def test_falha_de_um_trabalho_sobe(self):
        def _explode(n):
            if n == 2:
                raise RuntimeError('ffmpeg falhou')
            return n

        with self.assertRaises(RuntimeError):
            video_utils.em_paralelo(_explode, [1, 2, 3], 2)

    def test_um_trabalho_so_nao_abre_thread(self):
        atual = threading.current_thread()
        onde = video_utils.em_paralelo(
            lambda _: threading.current_thread(), ['x'], 4)
        self.assertEqual(onde, [atual])


@override_settings(VIDEO_ENABLED=True)
class VideoNivelTests(TestCase):
    """O vídeo passou a ser do nível inteiro, não de um tópico."""

    def setUp(self):
        self.user = User.objects.create_user('v', password='x')
        self.client.force_login(self.user)
        self.trilha = Trilha.objects.create(
            user=self.user, tema_livre='Tema', titulo='Trilha',
            status=Trilha.Status.EM_ANDAMENTO, ativa=True,
        )
        self.nivel = Nivel.objects.create(
            trilha=self.trilha, ordem=1, titulo='Fundamentos',
            faixa=Nivel.Faixa.INICIANTE, status=Nivel.Status.DISPONIVEL,
        )

    def _subtopico(self, ordem, *, conteudo='## Seção\n\ntexto'):
        return Subtopico.objects.create(
            nivel=self.nivel, ordem=ordem, titulo=f'Tópico {ordem}',
            status=Subtopico.Status.PRONTO, conteudo_md=conteudo,
            gerado_em=timezone.now(),
        )

    def test_nivel_sem_topico_nenhum_nao_oferece_geracao(self):
        resp = self.client.get(reverse('trilhas:nivel', args=[self.nivel.pk]))
        self.assertFalse(resp.context['video_pronto_para_gerar'])

    def test_nivel_com_conteudo_oferece_geracao(self):
        self._subtopico(1)
        resp = self.client.get(reverse('trilhas:nivel', args=[self.nivel.pk]))
        self.assertTrue(resp.context['video_pronto_para_gerar'])
        self.assertContains(resp, 'Gerar vídeo do nível')

    def test_gerar_enfileira_uma_vez_e_e_idempotente(self):
        self._subtopico(1)
        url = reverse('trilhas:video_gerar', args=[self.nivel.pk])
        with mock.patch('trilhas.tasks.task_gerar_video_nivel.delay') as task:
            self.client.post(url)
            video = VideoNivel.objects.get(nivel=self.nivel)
            self.assertEqual(video.status, VideoNivel.Status.GERANDO)
            self.assertEqual(task.call_count, 1)
            # Já gerando: não reenfileira.
            self.client.post(url)
            self.assertEqual(task.call_count, 1)

    def test_video_de_outro_usuario_nao_e_acessivel(self):
        self._subtopico(1)
        VideoNivel.objects.create(nivel=self.nivel)
        outro = User.objects.create_user('outro', password='x')
        self.client.force_login(outro)
        resp = self.client.get(reverse('trilhas:video_status', args=[self.nivel.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_desatualizado_quando_qualquer_topico_muda(self):
        s1 = self._subtopico(1)
        self._subtopico(2)
        video = VideoNivel.objects.create(
            nivel=self.nivel, status=VideoNivel.Status.PRONTO,
            arquivo='/media/videos/1/a.mp4', fonte_gerado_em=timezone.now(),
        )
        self.assertFalse(video.desatualizado)
        s1.gerado_em = timezone.now() + timedelta(minutes=1)
        s1.save(update_fields=['gerado_em'])
        self.assertTrue(video.desatualizado)

    def test_pipeline_monta_capa_capitulo_e_fecho_de_cada_topico(self):
        self._subtopico(1)
        self._subtopico(2)
        video = VideoNivel.objects.create(nivel=self.nivel)
        roteiro = [{'md': '## Seção', 'narracao': 'narração da seção'}]
        capturado = {}

        def _slides(paginas, out_dir, mascote=False):
            capturado['paginas'] = paginas
            return [f'{out_dir}/{i}.png' for i in range(len(paginas))]

        with mock.patch('ai.services.gerar_roteiro_video', return_value=roteiro), \
             mock.patch('trilhas.video_slides.render_slides', side_effect=_slides), \
             mock.patch('trilhas.video_tts.sintetizar_narracoes',
                        side_effect=lambda n, d: [f'{d}/{i}.mp3' for i in range(len(n))]), \
             mock.patch('trilhas.video_avatar.clipes', return_value=None), \
             mock.patch('trilhas.video_montagem.montar', return_value=12.0):
            video_pipeline.gerar_video(video)

        tipos = [p['tipo'] for p in capturado['paginas']]
        titulos = [p.get('titulo') for p in capturado['paginas'] if p['tipo'] == 'capa']
        # capa do nível + (capítulo + seção) × 2 tópicos + fecho
        self.assertEqual(tipos, ['capa', 'capa', 'conteudo', 'capa', 'conteudo', 'capa'])
        self.assertEqual(titulos[0], 'Fundamentos')
        self.assertEqual(titulos[1:3], ['Tópico 1', 'Tópico 2'])
        self.assertEqual(titulos[-1], 'Nível concluído')

    def test_pipeline_sem_topico_pronto_levanta(self):
        video = VideoNivel.objects.create(nivel=self.nivel)
        with self.assertRaises(RuntimeError):
            video_pipeline.gerar_video(video)


@override_settings(VIDEO_ENABLED=True)
class VideoConteudoFaltanteTests(TestCase):
    """Gerar o vídeo do nível escreve antes o que faltar de conteúdo."""

    def setUp(self):
        self.user = User.objects.create_user('c', password='x')
        self.trilha = Trilha.objects.create(
            user=self.user, tema_livre='Tema', titulo='Trilha',
            status=Trilha.Status.EM_ANDAMENTO, ativa=True,
        )
        self.nivel = Nivel.objects.create(
            trilha=self.trilha, ordem=1, titulo='Fundamentos',
            faixa=Nivel.Faixa.INICIANTE, status=Nivel.Status.DISPONIVEL,
        )
        self.pronto = Subtopico.objects.create(
            nivel=self.nivel, ordem=1, titulo='Pronto',
            status=Subtopico.Status.PRONTO, conteudo_md='## Já escrito',
            gerado_em=timezone.now(),
        )
        self.faltante = Subtopico.objects.create(
            nivel=self.nivel, ordem=2, titulo='Faltante',
            status=Subtopico.Status.PENDENTE,
        )

    def _gerar(self, video):
        roteiro = [{'md': '## Seção', 'narracao': 'narração'}]
        with mock.patch('ai.services.gerar_conteudo_subtopico',
                        return_value='## Escrito agora') as escreve, \
             mock.patch('ai.services.gerar_roteiro_video', return_value=roteiro), \
             mock.patch('trilhas.video_slides.render_slides',
                        side_effect=lambda p, d, mascote=False: [f'{d}/{i}.png'
                                                                 for i in range(len(p))]), \
             mock.patch('trilhas.video_tts.sintetizar_narracoes',
                        side_effect=lambda n, d: [f'{d}/{i}.mp3' for i in range(len(n))]), \
             mock.patch('trilhas.video_avatar.clipes', return_value=None), \
             mock.patch('trilhas.video_montagem.montar', return_value=30.0):
            video_pipeline.gerar_video(video, None)
        return escreve

    def test_topico_sem_conteudo_e_escrito_antes_do_roteiro(self):
        video = VideoNivel.objects.create(nivel=self.nivel)
        escreve = self._gerar(video)

        self.assertEqual(escreve.call_count, 1)
        self.assertEqual(escreve.call_args[0][0].pk, self.faltante.pk)
        self.faltante.refresh_from_db()
        self.assertEqual(self.faltante.status, Subtopico.Status.PRONTO)
        self.assertEqual(self.faltante.conteudo_md, '## Escrito agora')
        self.assertIsNotNone(self.faltante.gerado_em)

    def test_topico_ja_pronto_nao_e_reescrito(self):
        self.faltante.delete()
        video = VideoNivel.objects.create(nivel=self.nivel)
        escreve = self._gerar(video)
        self.assertEqual(escreve.call_count, 0)
        self.pronto.refresh_from_db()
        self.assertEqual(self.pronto.conteudo_md, '## Já escrito')

    def test_todos_os_topicos_entram_no_video(self):
        video = VideoNivel.objects.create(nivel=self.nivel)
        capturado = {}

        def _slides(paginas, out_dir, mascote=False):
            capturado['paginas'] = paginas
            return [f'{out_dir}/{i}.png' for i in range(len(paginas))]

        roteiro = [{'md': '## Seção', 'narracao': 'narração'}]
        with mock.patch('ai.services.gerar_conteudo_subtopico', return_value='## Novo'), \
             mock.patch('ai.services.gerar_roteiro_video', return_value=roteiro), \
             mock.patch('trilhas.video_slides.render_slides', side_effect=_slides), \
             mock.patch('trilhas.video_tts.sintetizar_narracoes',
                        side_effect=lambda n, d: [f'{d}/{i}.mp3' for i in range(len(n))]), \
             mock.patch('trilhas.video_avatar.clipes', return_value=None), \
             mock.patch('trilhas.video_montagem.montar', return_value=30.0):
            video_pipeline.gerar_video(video, None)

        titulos = [p.get('titulo') for p in capturado['paginas'] if p['tipo'] == 'capa']
        self.assertIn('Pronto', titulos)
        self.assertIn('Faltante', titulos)

    def test_view_aceita_gerar_com_topicos_ainda_sem_conteudo(self):
        self.client.force_login(self.user)
        with mock.patch('trilhas.tasks.task_gerar_video_nivel.delay') as task:
            resp = self.client.post(
                reverse('trilhas:video_gerar', args=[self.nivel.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(task.call_count, 1)
        video = VideoNivel.objects.get(nivel=self.nivel)
        self.assertEqual(video.status, VideoNivel.Status.GERANDO)
        self.assertIsNotNone(video.iniciado_em)

    def test_nivel_sem_topicos_e_recusado(self):
        self.pronto.delete()
        self.faltante.delete()
        self.client.force_login(self.user)
        with mock.patch('trilhas.tasks.task_gerar_video_nivel.delay') as task:
            self.client.post(reverse('trilhas:video_gerar', args=[self.nivel.pk]))
        self.assertEqual(task.call_count, 0)


class EstimativaTests(TestCase):
    """Barra de progresso: tempo decorrido e estimativa do que falta."""

    def setUp(self):
        user = User.objects.create_user('e', password='x')
        trilha = Trilha.objects.create(
            user=user, tema_livre='T', titulo='T',
            status=Trilha.Status.EM_ANDAMENTO, ativa=True,
        )
        self.nivel = Nivel.objects.create(
            trilha=trilha, ordem=1, titulo='N', faixa=Nivel.Faixa.INICIANTE,
            status=Nivel.Status.DISPONIVEL,
        )

    def _video(self, **kw):
        return VideoNivel.objects.create(nivel=self.nivel, **kw)

    def test_sem_inicio_nao_ha_decorrido_nem_estimativa(self):
        video = self._video(status=VideoNivel.Status.GERANDO, progresso_pct=50)
        self.assertEqual(video.decorrido_seg, 0)
        self.assertIsNone(video.restante_seg)

    def test_estimativa_extrapola_o_ritmo_ate_aqui(self):
        # 60 s para chegar a 25% → faltam ~180 s.
        video = self._video(
            status=VideoNivel.Status.GERANDO, progresso_pct=25,
            iniciado_em=timezone.now() - timedelta(seconds=60),
        )
        self.assertAlmostEqual(video.decorrido_seg, 60, delta=2)
        self.assertAlmostEqual(video.restante_seg, 180, delta=10)

    def test_progresso_baixo_demais_nao_arrisca_estimativa(self):
        """No comecinho o percentual é ruído e a conta daria minutos absurdos."""
        video = self._video(
            status=VideoNivel.Status.GERANDO, progresso_pct=1,
            iniciado_em=timezone.now() - timedelta(seconds=30),
        )
        self.assertIsNone(video.restante_seg)

    def test_video_pronto_nao_estima_nada(self):
        video = self._video(
            status=VideoNivel.Status.PRONTO, progresso_pct=100,
            iniciado_em=timezone.now() - timedelta(seconds=300),
        )
        self.assertIsNone(video.restante_seg)

    def test_status_devolve_etapa_e_tempos(self):
        self.client.force_login(self.nivel.trilha.user)
        self._video(
            status=VideoNivel.Status.GERANDO, progresso_pct=40,
            etapa='Narrando os slides',
            iniciado_em=timezone.now() - timedelta(seconds=100),
        )
        d = self.client.get(
            reverse('trilhas:video_status', args=[self.nivel.pk])).json()
        self.assertEqual(d['etapa'], 'Narrando os slides')
        self.assertEqual(d['progresso_pct'], 40)
        self.assertGreaterEqual(d['decorrido_seg'], 99)
        self.assertAlmostEqual(d['restante_seg'], 150, delta=15)
