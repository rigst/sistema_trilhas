"""Orquestração da geração do vídeo de um nível (sob demanda).

Encadeia as etapas — roteiro (IA, um por tópico) → slides (Chromium) → narração
(edge-tts) → mascote (Pillow) → montagem (ffmpeg) — e grava o MP4 em
MEDIA_ROOT/videos, atualizando o progresso.
"""

import os
import shutil
import tempfile
import uuid

from django.conf import settings
from django.utils import timezone

from ai import services
from . import video_avatar, video_montagem, video_slides, video_tts


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
    """Gera o vídeo do nível associado a ``video`` (VideoNivel).

    Percorre os subtópicos prontos na ordem: cada um abre um capítulo e contribui
    com suas seções. O roteiro é pedido à IA **por tópico** (e não de uma vez
    para o nível inteiro) para manter cada chamada pequena e longe do teto de
    tokens de saída.

    ``progresso`` é um callback opcional ``fn(pct: int, etapa: str)`` para
    reportar avanço. Retorna (url_local, duracao_seg). Levanta em qualquer falha
    (a task cuida do status de ERRO e do retry).
    """
    def _p(pct, etapa=''):
        if progresso:
            progresso(pct, etapa)

    from .models import Subtopico

    nivel = video.nivel
    trilha = nivel.trilha

    # O vídeo é do nível INTEIRO: qualquer tópico ainda sem conteúdo é gerado
    # aqui, antes de tudo, senão o vídeo sairia furado. É síncrono de propósito
    # — esta task já roda na fila de vídeo, com tempo de sobra.
    faltantes = [
        s for s in nivel.subtopicos.all().order_by('ordem')
        if s.status != Subtopico.Status.PRONTO or not s.conteudo_md
    ]
    for i, sub in enumerate(faltantes):
        _p(1 + int(19 * i / len(faltantes)),
           f'Gerando o conteúdo do tópico {sub.ordem} ({i + 1} de {len(faltantes)})')
        sub.conteudo_md = services.gerar_conteudo_subtopico(sub, profile)
        sub.status = Subtopico.Status.PRONTO
        sub.gerado_em = timezone.now()
        sub.erro = ''
        sub.save(update_fields=['conteudo_md', 'status', 'gerado_em', 'erro'])

    subs = [
        s for s in nivel.subtopicos.all().order_by('ordem')
        if s.status == Subtopico.Status.PRONTO and s.conteudo_md
    ]
    if not subs:
        raise RuntimeError('Nenhum tópico do nível tem conteúdo: nada para narrar.')

    _p(20, 'Escrevendo o roteiro')
    # Capa do nível.
    paginas = [{
        'tipo': 'capa',
        'kicker': trilha.titulo,
        'titulo': nivel.titulo,
        'sub': nivel.resumo or '',
        'emblema': trilha.emblema or '🎓',
    }]
    narracoes = [f'{nivel.titulo}. Vamos começar.']

    # Um capítulo por tópico: capa curta + as seções narradas do conteúdo.
    for i, sub in enumerate(subs):
        roteiro = services.gerar_roteiro_video(sub, profile)
        if not roteiro:
            continue
        paginas.append({
            'tipo': 'capa',
            'kicker': f'Tópico {i + 1} de {len(subs)}',
            'titulo': sub.titulo,
            'sub': sub.descricao_curta or '',
            'emblema': '📘',
        })
        narracoes.append(f'Tópico {i + 1}: {sub.titulo}.')
        for item in roteiro:
            paginas.append({'tipo': 'conteudo', 'md': item['md']})
            narracoes.append(item['narracao'])
        # Reporta o avanço tópico a tópico: são chamadas de IA, cada uma longa.
        _p(20 + int(25 * (i + 1) / len(subs)),
           f'Escrevendo o roteiro ({i + 1} de {len(subs)})')

    if len(paginas) == 1:
        raise RuntimeError('Não foi possível montar o roteiro do nível.')

    musica, credito_musica = _musica_para(trilha)
    paginas.append({
        'tipo': 'capa',
        'kicker': trilha.titulo,
        'titulo': 'Nível concluído',
        'sub': nivel.titulo,
        'emblema': '✓',
        'creditos': f'Música: {credito_musica}' if credito_musica else '',
    })
    narracoes.append('Você concluiu este nível. Continue na sua trilha de estudos.')

    trabalho = tempfile.mkdtemp(prefix=f'video_nivel_{nivel.pk}_')
    try:
        _p(45, f'Renderizando {len(paginas)} slides')
        slides = video_slides.render_slides(paginas, os.path.join(trabalho, 'slides'),
                                            mascote=video_avatar.ativo())
        _p(58, 'Narrando os slides')
        audios = video_tts.sintetizar_narracoes(narracoes, os.path.join(trabalho, 'audio'))
        _p(70, 'Animando o apresentador')
        # Mascote apresentador (opcional): a boca sai do volume destes MP3s e as
        # cores são as sorteadas para esta trilha.
        avatares = video_avatar.clipes(
            audios, os.path.join(trabalho, 'avatar'),
            paleta=video_avatar.paleta_da_trilha(trilha),
        )
        _p(78, 'Montando o vídeo')

        destino_dir = os.path.join(settings.MEDIA_ROOT, 'videos', str(nivel.pk))
        os.makedirs(destino_dir, exist_ok=True)
        nome = f'{uuid.uuid4().hex}.mp4'
        destino = os.path.join(destino_dir, nome)

        dur = video_montagem.montar(
            slides, audios, destino,
            work_dir=os.path.join(trabalho, 'montagem'),
            musica=musica,
            avatares=avatares,
        )
        _p(97, 'Finalizando')
    finally:
        shutil.rmtree(trabalho, ignore_errors=True)

    url = f'{settings.MEDIA_URL}videos/{nivel.pk}/{nome}'

    # Remove um vídeo anterior deste nível (regeração), mantendo só o atual.
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
    # Marca do conteúdo mais novo que entrou no vídeo: se algum tópico for
    # regerado depois disso, `desatualizado` passa a oferecer a regeração.
    gerados = [s.gerado_em for s in subs if s.gerado_em]
    video.fonte_gerado_em = max(gerados) if gerados else timezone.now()
    return url, video.duracao_seg
