"""Síntese de voz (TTS) da narração do vídeo, via edge-tts (grátis, PT-BR).

Uma chamada por slide gera um MP3; a duração do slide no vídeo é a duração do
seu áudio (a montagem lê isso via ffprobe), garantindo narração e imagem em
sincronia. edge-tts usa as vozes neurais da Microsoft, sem chave nem custo.

As sínteses saem em paralelo: cada uma é um WebSocket para o serviço da
Microsoft e o tempo é quase todo espera de rede, então rodar uma de cada vez
desperdiçava minutos num vídeo com dezenas de slides.
"""

import asyncio
import os

from django.conf import settings

# Voz padrão PT-BR (configurável por VIDEO_TTS_VOICE no settings/.env).
VOZ_PADRAO = "pt-BR-AntonioNeural"

# Sínteses simultâneas. 6 é conservador para não levar throttling do serviço
# (que é gratuito e sem contrato) e já derruba o tempo da etapa em ~6×.
CONCORRENCIA = 6


def _voz():
    return getattr(settings, "VIDEO_TTS_VOICE", VOZ_PADRAO) or VOZ_PADRAO


async def _sintetizar(texto: str, destino: str, voz: str):
    import edge_tts  # import tardio (dependência opcional da feature)

    # Uma retentativa: com várias conexões simultâneas uma queda esporádica do
    # serviço é normal, e perder o vídeo inteiro por causa de um slide não é.
    for tentativa in range(2):
        try:
            comunicador = edge_tts.Communicate(texto, voz)
            await comunicador.save(destino)
            return
        except Exception:
            if tentativa:
                raise
            await asyncio.sleep(1.5)


def sintetizar_narracoes(narracoes: list[str], out_dir: str) -> list[str]:
    """Gera um MP3 por narração e devolve os caminhos, na ordem recebida.

    As sínteses rodam em paralelo (até CONCORRENCIA ao mesmo tempo); a lista de
    volta continua na ordem das narrações, não na de término.

    Narrações vazias recebem um silêncio curto para não quebrar a sequência de
    slides (a montagem ainda mostra o slide pela sua duração mínima).
    """
    os.makedirs(out_dir, exist_ok=True)
    voz = _voz()
    caminhos = [os.path.join(out_dir, f"audio_{i:03d}.mp3") for i in range(len(narracoes))]

    async def _tudo():
        limite = asyncio.Semaphore(CONCORRENCIA)

        async def _um(i: int, texto: str):
            async with limite:
                # Sem narração: um ponto curto gera ~0,5s de áudio (evita clipe vazio).
                await _sintetizar((texto or "").strip() or ".", caminhos[i], voz)

        await asyncio.gather(*(_um(i, t) for i, t in enumerate(narracoes)))

    asyncio.run(_tudo())
    return caminhos
