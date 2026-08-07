"""Montagem final do vídeo com ffmpeg (chamado via subprocess, sem moviepy).

Cada par (slide PNG, áudio MP3) vira um clipe 1280×720 com efeito Ken Burns e
duração igual à do áudio; os clipes são concatenados na ordem e, por fim, uma
faixa instrumental é mixada em volume baixo sob a narração. Saída: MP4
H.264/AAC 720p com faststart (streaming progressivo no player <video>).

Quando `video_avatar` entrega os clipes do mascote, cada quadro ainda recebe o
apresentador no canto inferior direito, por cima do Ken Burns (o mascote não
acompanha o zoom).
"""

import math
import os
import subprocess

from django.conf import settings

from . import video_utils

LARGURA, ALTURA, FPS = 1280, 720, 30
COR_FUNDO = "0x0C1020"  # var(--bg) do tema escuro

# Quadro de trabalho do Ken Burns: o slide entra aqui e o zoompan recorta daqui
# para 720p. É o tamanho em que os slides já são capturados (video_slides.ESCALA),
# então na prática o scale abaixo é passagem direta — nada é reamostrado à toa.
SUPER_L, SUPER_A = 1920, 1080

# Respiro do mascote até as bordas do quadro. A posição é fixa de propósito: o
# movimento acontece dentro do círculo (ver video_avatar), então o disco fica
# cravado no canto em vez de flutuar.
AVATAR_MARGEM = 34


def _bin(nome, setting):
    return getattr(settings, setting, nome) or nome


def _ffmpeg():
    return _bin("ffmpeg", "VIDEO_FFMPEG_BIN")


def _ffprobe():
    return _bin("ffprobe", "VIDEO_FFPROBE_BIN")


def _run(args):
    """Executa ffmpeg/ffprobe; levanta com o stderr em caso de falha."""
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        cauda = (proc.stderr or "").strip().splitlines()[-8:]
        raise RuntimeError(f"{args[0]} falhou: " + " | ".join(cauda))
    return proc


def duracao(path: str) -> float:
    """Duração de um arquivo de mídia em segundos (via ffprobe)."""
    proc = _run(
        [
            _ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ]
    )
    try:
        return float((proc.stdout or "0").strip())
    except ValueError:
        return 0.0


def _clipe(png: str, audio: str, destino: str, avatar: str | None = None):
    """Gera um clipe 1280×720 com Ken Burns, com a duração do áudio.

    ``avatar`` é o clipe com alfa do mascote para esta narração; sem ele o
    clipe sai só com o slide.
    """
    dur = max(0.8, duracao(audio))
    frames = max(1, math.ceil(dur * FPS))
    # Fit do slide no quadro de trabalho (nada é cortado) e zoom lento (Ken
    # Burns) já reduzindo para 720p. O recorte sai direto do PNG em tamanho
    # original: reduzir para 720p antes do zoom, como se fazia, custava um
    # reamostramento a mais e entregava um quadro ampliado a partir de menos
    # pixels do que o slide tinha.
    vf = (
        f"[0:v]scale={SUPER_L}:{SUPER_A}:force_original_aspect_ratio=decrease,"
        f"pad={SUPER_L}:{SUPER_A}:(ow-iw)/2:(oh-ih)/2:color={COR_FUNDO},setsar=1,"
        f"zoompan=z='min(zoom+0.0004,1.10)':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={LARGURA}x{ALTURA}:fps={FPS}[base]"
    )
    entradas = ["-loop", "1", "-i", png, "-i", audio]
    if avatar:
        # O alfa do WebM/VP9 vem num plano separado que só o decodificador
        # libvpx-vp9 lê; com o decodificador nativo o mascote sai num quadrado
        # preto opaco em vez de recortado no círculo.
        entradas += ["-c:v", "libvpx-vp9", "-i", avatar]
        vf += (
            ";[2:v]format=rgba,setpts=PTS-STARTPTS[mascote]"
            f";[base][mascote]overlay=x=W-w-{AVATAR_MARGEM}:y=H-h-{AVATAR_MARGEM}:"
            "eof_action=repeat,format=yuv420p[v]"
        )
    else:
        vf += ";[base]format=yuv420p[v]"
    _run(
        [
            _ffmpeg(),
            "-y",
            *entradas,
            "-filter_complex",
            vf,
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-t",
            f"{dur:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "44100",
            "-ac",
            "2",
            destino,
        ]
    )


def workers() -> int:
    """Quantos clipes codificar ao mesmo tempo.

    O x264 não satura sozinho os núcleos disponíveis no preset veryfast, então
    dois ffmpeg lado a lado rendem ~20% a mais que um de cada vez. O teto é o
    número de núcleos: acima disso eles só disputam CPU entre si (e a máquina
    ainda serve o site). VIDEO_WORKERS no settings/.env sobrepõe.
    """
    forcado = getattr(settings, "VIDEO_WORKERS", 0) or 0
    if forcado:
        return max(1, int(forcado))
    return max(1, min(2, os.cpu_count() or 1))


def _concat(clipes: list[str], destino: str, work_dir: str):
    lista = os.path.join(work_dir, "concat.txt")
    with open(lista, "w", encoding="utf-8") as fh:
        for c in clipes:
            fh.write(f"file '{c}'\n")
    _run(
        [
            _ffmpeg(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            lista,
            "-c",
            "copy",
            destino,
        ]
    )


def _com_musica(video: str, musica: str, destino: str):
    """Mixa a música de fundo (em loop, volume baixo) sob a narração."""
    fc = "[1:a]volume=0.08[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]"
    _run(
        [
            _ffmpeg(),
            "-y",
            "-i",
            video,
            "-stream_loop",
            "-1",
            "-i",
            musica,
            "-filter_complex",
            fc,
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            destino,
        ]
    )


def _faststart(video: str, destino: str):
    _run(
        [
            _ffmpeg(),
            "-y",
            "-i",
            video,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            destino,
        ]
    )


def montar(
    slides: list[str],
    audios: list[str],
    destino: str,
    work_dir: str,
    musica: str | None = None,
    avatares: list[str] | None = None,
    progresso=None,
) -> float:
    """Monta o vídeo final e devolve sua duração em segundos.

    ``slides`` e ``audios`` são listas paralelas (mesmo tamanho, mesma ordem).
    ``musica`` é opcional; se ausente ou inexistente, o vídeo sai só com narração.
    ``avatares`` (clipes do mascote, na mesma ordem) também é opcional.
    ``progresso`` é um callback opcional ``fn(prontos, total)``: os clipes são
    codificados em paralelo, mas esta é a etapa mais longa e vale mostrar avanço.
    """
    if not slides or len(slides) != len(audios):
        raise ValueError("slides e audios devem ter o mesmo tamanho e não vazios.")

    os.makedirs(work_dir, exist_ok=True)
    clipes = [os.path.join(work_dir, f"clipe_{i:03d}.mp4") for i in range(len(slides))]

    def _um(i: int):
        _clipe(
            slides[i],
            audios[i],
            clipes[i],
            avatar=avatares[i] if avatares and i < len(avatares) else None,
        )

    video_utils.em_paralelo(_um, list(range(len(clipes))), workers(), progresso)

    concat = os.path.join(work_dir, "sem_musica.mp4")
    _concat(clipes, concat, work_dir)

    if musica and os.path.exists(musica):
        _com_musica(concat, musica, destino)
    else:
        _faststart(concat, destino)

    return duracao(destino)
