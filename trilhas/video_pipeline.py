"""Orquestração da geração do vídeo de um subtópico (sob demanda).

Encadeia as etapas — roteiro (IA) → slides (Chromium) → narração (edge-tts) →
montagem (ffmpeg) — e grava o MP4 em MEDIA_ROOT/videos, atualizando o progresso.
"""

import os
import shutil
import tempfile
import uuid

from django.conf import settings
from django.utils import timezone

from ai import services
from . import video_montagem, video_slides, video_tts


# Biblioteca de faixas de fundo (instrumentais, suaves), por clima → arquivo +
# crédito. Todas CC BY 4.0 de MusicLFiles (Wikimedia Commons); o crédito aparece
# no slide final e em static/audio/CREDITOS.md.
MUSICAS = {
    'corporativo': ('fundo_corporativo.ogg', 'Soft Corporate — MusicLFiles (CC BY 4.0)'),
    'ambiente':    ('fundo_ambiente.ogg',    'Placid Ambient — MusicLFiles (CC BY 4.0)'),
    'espiritual':  ('fundo_espiritual.ogg',  'Spiritual Ambient — MusicLFiles (CC BY 4.0)'),
    'ameno':       ('fundo_ameno.ogg',       'Warm Sunset — MusicLFiles (CC BY 4.0)'),
}

# Categoria da trilha (texto livre da IA) → clima da trilha sonora. Primeira
# palavra-chave que casar vence; sem match cai no clima 'ambiente' (neutro).
_CATEGORIA_CLIMA = [
    (('tecnolog', 'program', 'dados', 'comput', 'engenhar', 'rede', 'software', 'ti'), 'corporativo'),
    (('negóci', 'negoci', 'gest', 'econom', 'finan', 'marketing', 'administra'), 'corporativo'),
    (('matem', 'ciênc', 'cienc', 'físic', 'fisic', 'quím', 'quim', 'biolog', 'estat'), 'ambiente'),
    (('histó', 'hist', 'direit', 'filosof', 'saúde', 'saude', 'medic', 'psicolog', 'religi'), 'espiritual'),
    (('idiom', 'líng', 'ling', 'músic', 'music', 'arte', 'literat', 'design', 'redaç'), 'ameno'),
]


def _musica_para(trilha):
    """Escolhe a faixa de fundo pela categoria da trilha e devolve (caminho, credito).

    VIDEO_MUSICA_PATH no settings/.env, se definido e existente, sobrepõe a
    seleção (faixa fixa). Sem faixa disponível → (None, '') e vídeo só com narração.
    """
    override = getattr(settings, 'VIDEO_MUSICA_PATH', '') or ''
    if override and os.path.exists(override):
        return override, ''

    cat = (getattr(trilha, 'categoria', '') or '').lower()
    clima = 'ambiente'
    for chaves, c in _CATEGORIA_CLIMA:
        if any(k in cat for k in chaves):
            clima = c
            break
    nome, credito = MUSICAS[clima]
    caminho = os.path.join(settings.BASE_DIR, 'static', 'audio', nome)
    if not os.path.exists(caminho):
        return None, ''
    return caminho, credito


def gerar_video(video, profile=None, progresso=None):
    """Gera o vídeo do subtópico associado a ``video`` (VideoSubtopico).

    ``progresso`` é um callback opcional ``fn(pct: int)`` para reportar avanço.
    Retorna (url_local, duracao_seg). Levanta em qualquer falha (a task cuida do
    status de ERRO e do retry).
    """
    def _p(pct):
        if progresso:
            progresso(pct)

    sub = video.subtopico
    nivel = sub.nivel
    trilha = nivel.trilha

    _p(5)
    roteiro = services.gerar_roteiro_video(sub, profile)
    if not roteiro:
        raise RuntimeError('Conteúdo do tópico vazio: nada para narrar.')
    _p(20)

    # Páginas (slides) e narrações, em listas paralelas: intro + seções + outro.
    paginas = [{
        'tipo': 'capa',
        'kicker': trilha.titulo,
        'titulo': sub.titulo,
        'sub': sub.descricao_curta or '',
        'emblema': trilha.emblema or '🎓',
    }]
    narracoes = [f'{sub.titulo}. Vamos começar.']
    for item in roteiro:
        paginas.append({'tipo': 'conteudo', 'md': item['md']})
        narracoes.append(item['narracao'])
    musica, credito_musica = _musica_para(trilha)
    paginas.append({
        'tipo': 'capa',
        'kicker': trilha.titulo,
        'titulo': 'Tópico concluído',
        'sub': sub.titulo,
        'emblema': '✓',
        'creditos': f'Música: {credito_musica}' if credito_musica else '',
    })
    narracoes.append('Você concluiu este tópico. Continue na sua trilha de estudos.')

    trabalho = tempfile.mkdtemp(prefix=f'video_sub_{sub.pk}_')
    try:
        slides = video_slides.render_slides(paginas, os.path.join(trabalho, 'slides'))
        _p(50)
        audios = video_tts.sintetizar_narracoes(narracoes, os.path.join(trabalho, 'audio'))
        _p(72)

        destino_dir = os.path.join(settings.MEDIA_ROOT, 'videos', str(sub.pk))
        os.makedirs(destino_dir, exist_ok=True)
        nome = f'{uuid.uuid4().hex}.mp4'
        destino = os.path.join(destino_dir, nome)

        dur = video_montagem.montar(
            slides, audios, destino,
            work_dir=os.path.join(trabalho, 'montagem'),
            musica=musica,
        )
        _p(96)
    finally:
        shutil.rmtree(trabalho, ignore_errors=True)

    url = f'{settings.MEDIA_URL}videos/{sub.pk}/{nome}'

    # Remove um vídeo anterior deste subtópico (regeração), mantendo só o atual.
    if video.arquivo and video.arquivo != url:
        antigo = os.path.join(
            settings.MEDIA_ROOT, video.arquivo.replace(settings.MEDIA_URL, '', 1)
        )
        if os.path.exists(antigo) and os.path.abspath(antigo) != os.path.abspath(destino):
            try:
                os.remove(antigo)
            except OSError:
                pass

    video.arquivo = url
    video.duracao_seg = int(round(dur))
    video.fonte_gerado_em = sub.gerado_em or timezone.now()
    return url, video.duracao_seg
