"""Síntese de voz (TTS) da narração do vídeo, via edge-tts (grátis, PT-BR).

Uma chamada por slide gera um MP3; a duração do slide no vídeo é a duração do
seu áudio (a montagem lê isso via ffprobe), garantindo narração e imagem em
sincronia. edge-tts usa as vozes neurais da Microsoft, sem chave nem custo.
"""

import asyncio
import os

from django.conf import settings

# Voz padrão PT-BR (configurável por VIDEO_TTS_VOICE no settings/.env).
VOZ_PADRAO = 'pt-BR-AntonioNeural'


def _voz():
    return getattr(settings, 'VIDEO_TTS_VOICE', VOZ_PADRAO) or VOZ_PADRAO


async def _sintetizar(texto: str, destino: str, voz: str):
    import edge_tts  # import tardio (dependência opcional da feature)
    comunicador = edge_tts.Communicate(texto, voz)
    await comunicador.save(destino)


def sintetizar_narracoes(narracoes: list[str], out_dir: str) -> list[str]:
    """Gera um MP3 por narração e devolve os caminhos, na ordem recebida.

    Narrações vazias recebem um silêncio curto para não quebrar a sequência de
    slides (a montagem ainda mostra o slide pela sua duração mínima).
    """
    os.makedirs(out_dir, exist_ok=True)
    voz = _voz()
    caminhos = []

    async def _tudo():
        for i, texto in enumerate(narracoes):
            destino = os.path.join(out_dir, f'audio_{i:03d}.mp3')
            fala = (texto or '').strip()
            if fala:
                await _sintetizar(fala, destino, voz)
            else:
                # Sem narração: um ponto curto gera ~0,5s de áudio (evita clipe vazio).
                await _sintetizar('.', destino, voz)
            caminhos.append(destino)

    asyncio.run(_tudo())
    return caminhos
