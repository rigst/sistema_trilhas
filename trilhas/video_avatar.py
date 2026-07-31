"""Mascote apresentador no canto do vídeo (círculo com boca sincronizada).

A arte é um SVG na paleta dos slides — volume dado por gradientes, sombras
borradas e luz de borda — capturado pelo Chromium em **camadas** separadas:
fundo, corpo e cabeça (esta em cada combinação de boca × olhos × sobrancelhas).
São poucos PNGs, gerados uma vez e guardados em cache.

A animação não escolhe poses prontas: cada quadro é composto pelo Pillow
aplicando *transformações contínuas* nas camadas — a cabeça desliza e gira
frações de grau, o corpo respira. É o que evita o salto de uma pose para outra.
Por cima disso entram os eventos tirados da própria narração:

* **boca** — pelo volume (RMS) da narração, o único elemento que troca em
  degraus (é assim que se anima fala);
* **piscada** — periódica e no começo das pausas, com meio-fechado nas pontas;
* **ênfase** — nas sílabas fortes: cabeça sobe e sobrancelhas erguem, em rampa;
* **reações de pausa** — aceno, inclinação de cabeça ou encolher de ombros, em
  rodízio entre as frases.

Tudo sai do MP3 que o edge-tts já gerou: sem IA, GPU ou serviço externo. Cada
trilha recebe uma das PALETAS, sorteada pelo id — sempre a mesma para a mesma
trilha.

O disco (fundo, vinheta e aro) não se move: quem anda é o personagem dentro
dele, então o círculo fica cravado no canto do vídeo.

Saída: um .webm com canal alfa por slide (VP9), que a montagem
sobrepõe ao quadro com o ffmpeg. É decoração — qualquer falha aqui devolve None
e o vídeo sai sem o mascote, em vez de quebrar a geração inteira.
"""

import hashlib
import logging
import math
import os
import shutil
import subprocess
import tempfile
from array import array
from collections import namedtuple

from django.conf import settings

from . import video_montagem, video_utils

logger = logging.getLogger(__name__)

# Versão da arte: entra no nome do cache, então mexer no SVG e subir este número
# invalida os PNGs antigos sem precisar limpar nada à mão. A paleta entra
# sozinha (via hash), não precisa de bump ao trocar cores.
VERSAO_ARTE = 4

# Cores do mascote. Trocar a paleta é editar este dicionário — ou definir
# VIDEO_AVATAR_PALETA no settings/.env com só as chaves que mudam. O cache é
# refeito automaticamente quando a paleta muda.
PALETA = {
    # Disco
    'disco_topo': '#28325C', 'disco_meio': '#161C33', 'disco_base': '#0A0E1B',
    'contraluz': '#2DD4BF', 'vinheta': '#050810',
    'aro_a': '#2DD4BF', 'aro_b': '#5BC8E8', 'aro_c': '#8B7CF6',
    # Pele
    'pele_luz': '#F7D2A8', 'pele': '#E0A87A', 'pele_sombra': '#B57A50',
    'pele_traco': '#B4794F', 'pescoco': '#A96C44', 'orelha': '#D2915F',
    'bochecha': '#E4836B', 'sombra_pele': '#8A5730',
    # Cabelo e sobrancelhas
    'cabelo_luz': '#4C4790', 'cabelo': '#332F5E', 'cabelo_sombra': '#221F42',
    'cabelo_brilho': '#9089DC', 'sobrancelha': '#2C2950',
    # Roupa
    'camisa_a': '#2AA396', 'camisa_b': '#4E43B4', 'camisa_luz': '#8FF0E2',
    # Headset
    'fone': '#6BEEDC', 'fone_sombra': '#12907F', 'fone_brilho': '#BFFBF0',
    'metal_luz': '#DCE2F2', 'metal': '#98A2C0', 'metal_sombra': '#68738F',
    # Rosto
    'olho': '#F9F4EC', 'iris': '#6486BC', 'iris_borda': '#243149',
    'pupila': '#0D1220', 'palpebra': '#9A7351', 'cilios': '#6B4526',
    'boca_fundo': '#3B1520', 'boca_base': '#7A3340', 'boca_traco': '#8A4048',
    'labio_luz': '#F0C09A', 'lingua': '#C4616B', 'dentes': '#F6F0E6',
    'sorriso': '#8C4B52',
}

# Variações da paleta. Cada trilha recebe uma delas, sorteada de forma
# determinística pelo id (ver `paleta_da_trilha`): a mesma trilha tem sempre o
# mesmo apresentador, trilhas diferentes têm apresentadores diferentes. Só as
# chaves listadas mudam; o resto vem de PALETA.
PALETAS = (
    {},  # teal/violeta — a paleta base
    {   # âmbar quente, cabelo castanho
        'aro_a': '#F5B54A', 'aro_b': '#F59E5B', 'aro_c': '#F472B6',
        'contraluz': '#F5B54A', 'disco_topo': '#3A2A46',
        'camisa_a': '#B26A0B', 'camisa_b': '#8C3B6B', 'camisa_luz': '#FFD9A0',
        'fone': '#F7C877', 'fone_sombra': '#8A5A12', 'fone_brilho': '#FFEBC4',
        'cabelo_luz': '#7A4A2E', 'cabelo': '#4E2E1E', 'cabelo_sombra': '#33200F',
        'cabelo_brilho': '#C08A58', 'sobrancelha': '#3D2416',
    },
    {   # verde/azul frio, pele mais escura
        'aro_a': '#34D399', 'aro_b': '#7DD3FC', 'aro_c': '#38BDF8',
        'contraluz': '#34D399', 'disco_topo': '#1B3A52',
        'camisa_a': '#0E7490', 'camisa_b': '#1E3A8A', 'camisa_luz': '#A5F3FC',
        'fone': '#7DD3FC', 'fone_sombra': '#075985', 'fone_brilho': '#E0F2FE',
        'pele_luz': '#D6A472', 'pele': '#B27B4E', 'pele_sombra': '#764B29',
        'orelha': '#A46F45', 'pescoco': '#764B29', 'pele_traco': '#8F5B36',
        'cabelo_luz': '#1F5E6E', 'cabelo': '#14424E', 'cabelo_sombra': '#0C2C35',
        'cabelo_brilho': '#5BAFC0', 'sobrancelha': '#12333C',
    },
    {   # magenta/roxo, cabelo claro
        'aro_a': '#F472B6', 'aro_b': '#C084FC', 'aro_c': '#8B7CF6',
        'contraluz': '#F472B6', 'disco_topo': '#3A2456',
        'camisa_a': '#9D2B62', 'camisa_b': '#5B3BB8', 'camisa_luz': '#FBCFE8',
        'fone': '#F9A8D4', 'fone_sombra': '#9D2B62', 'fone_brilho': '#FCE7F3',
        'pele_luz': '#FBDCC0', 'pele': '#EBB894', 'pele_sombra': '#C58C63',
        'orelha': '#DDA97F', 'pescoco': '#BE8459',
        'cabelo_luz': '#C9A227', 'cabelo': '#9A7B18', 'cabelo_sombra': '#6E570F',
        'cabelo_brilho': '#F0D57A', 'sobrancelha': '#7A6212',
    },
    {   # azul/índigo, pele escura e cabelo escuro
        'aro_a': '#60A5FA', 'aro_b': '#818CF8', 'aro_c': '#A78BFA',
        'contraluz': '#60A5FA', 'disco_topo': '#232C58',
        'camisa_a': '#1D4ED8', 'camisa_b': '#4C1D95', 'camisa_luz': '#BFDBFE',
        'fone': '#93C5FD', 'fone_sombra': '#1E40AF', 'fone_brilho': '#DBEAFE',
        'pele_luz': '#B98155', 'pele': '#94603A', 'pele_sombra': '#5E3A20',
        'orelha': '#87552F', 'pescoco': '#5E3A20', 'pele_traco': '#6F462A',
        'bochecha': '#C2664F', 'sombra_pele': '#4A2C16',
        'cabelo_luz': '#2B2B3E', 'cabelo': '#1B1B2A', 'cabelo_sombra': '#101018',
        'cabelo_brilho': '#5C5C7A', 'sobrancelha': '#14141F',
    },
    {   # coral/terra, cabelo ruivo
        'aro_a': '#FB7185', 'aro_b': '#FDA4AF', 'aro_c': '#F59E5B',
        'contraluz': '#FB7185', 'disco_topo': '#43263A',
        'camisa_a': '#9F1239', 'camisa_b': '#7C2D12', 'camisa_luz': '#FECDD3',
        'fone': '#FDA4AF', 'fone_sombra': '#9F1239', 'fone_brilho': '#FFE4E6',
        'pele_luz': '#FCE0C2', 'pele': '#F0C098', 'pele_sombra': '#C89468',
        'orelha': '#E3AE83', 'pescoco': '#C08F63',
        'cabelo_luz': '#C2410C', 'cabelo': '#9A3412', 'cabelo_sombra': '#6B240C',
        'cabelo_brilho': '#FB923C', 'sobrancelha': '#7C2D12',
    },
)

TAMANHO_PADRAO = 200   # diâmetro do mascote no quadro 1280×720
DISCO = 200            # unidades do sistema de coordenadas do SVG
TAXA_PCM = 8000        # Hz da amostragem usada só para medir volume
FPS_BOCA = 15          # análise do volume: uma janela a cada 1/15 s
NIVEIS_BOCA = 4        # fechada, pequena, média, ampla

# Faixas de volume (fração do volume de referência da narração) → abertura da
# boca. A referência é o percentil 90 do próprio áudio, então vale para vozes e
# volumes diferentes sem ajuste manual. Calibrados para a boca ficar quase
# sempre em abertura média e reservar a ampla às sílabas tônicas (~10% do
# tempo): com limiares mais baixos o mascote parece estar gritando.
LIMIARES = (0.15, 0.50, 0.92)

OLHOS = ('aberto', 'meio', 'fechado')
CEJAS = ('neutro', 'meio', 'alto')

# Recorte da cabeça, em unidades do disco: (x, y, largura, altura). O pivô de
# rotação cai no CENTRO do recorte, que é onde o Pillow gira — no caso, a base
# do pescoço (100, 148).
CAB_RECORTE = (25, 8, 150, 280)
MARGEM = 40  # folga do canvas de trabalho, para as camadas poderem transbordar

# --- Movimento contínuo (nunca para; períodos diferentes para não repetir) ---
RESP_PERIODO, RESP_AMPL = 4.3, 1.1
# Deriva da cabeça: pares (amplitude, período em s, fase) somados, para dx, dy
# e giro em graus. Somar dois senos de períodos incomensuráveis dá um vaivém
# que não fecha ciclo — é o que tira a cara de animação em laço.
DERIVA_DX = ((1.7, 5.7, 0.0), (0.8, 2.9, 1.1))
DERIVA_DY = ((1.2, 6.7, 0.6), (0.5, 3.3, 2.2))
DERIVA_GIRO = ((2.3, 6.1, 0.3), (0.9, 3.7, 1.7))

# --- Eventos ---
PISCADA_INTERVALOS = (3.1, 4.7, 2.6, 5.3, 3.8)  # s entre piscadas, em rodízio
PISCADA_DUR = 0.26
ENFASE_DUR, ENFASE_INTERVALO, ENFASE_ALTURA = 0.45, 2.5, 2.6
ACENO_DUR, ACENO_ALTURA = 0.95, 3.4
ENCOLHER_DUR, ENCOLHER_ALTURA = 0.8, 3.6
INCLINAR_DUR, INCLINAR_GRAUS = 1.4, 3.2
PAUSA_MINIMA = 0.66  # silêncio a partir do qual vale uma reação
# Reação de cada pausa, em rodízio (None = só a piscada): variar evita que o
# mascote repita o mesmo gesto de cabeça o vídeo inteiro.
ROTEIRO_PAUSAS = ('aceno', 'inclinar', None, 'encolher', 'aceno', None)

Quadro = namedtuple(
    'Quadro', 'boca olhos cejas corpo_dx corpo_dy cab_dx cab_dy cab_giro',
)


def ativo() -> bool:
    return bool(getattr(settings, 'VIDEO_AVATAR', True))


def _tamanho() -> int:
    return int(getattr(settings, 'VIDEO_AVATAR_TAMANHO', TAMANHO_PADRAO)
               or TAMANHO_PADRAO)


def _fps() -> int:
    """A animação roda na mesma cadência do vídeo: 1 quadro para 1 quadro."""
    return video_montagem.FPS


def cores(extra: dict | None = None) -> dict:
    """Paleta em uso: a padrão + VIDEO_AVATAR_PALETA + ``extra`` (a da trilha)."""
    paleta = dict(PALETA)
    paleta.update(getattr(settings, 'VIDEO_AVATAR_PALETA', None) or {})
    paleta.update(extra or {})
    return paleta


def paleta_da_trilha(trilha) -> dict:
    """Sorteia uma das PALETAS para a trilha, sempre a mesma para a mesma trilha.

    O sorteio é o resto da divisão de um hash do id — estável entre processos
    (ao contrário de hash(), que muda a cada execução) e sem campo novo no banco.
    """
    chave = str(getattr(trilha, 'pk', trilha) or 0).encode()
    indice = int(hashlib.sha1(chave).hexdigest(), 16) % len(PALETAS)
    return PALETAS[indice]


# ---------------------------------------------------------------------------
# Arte: camadas SVG → PNG transparente, em cache
# ---------------------------------------------------------------------------

def _defs(c: dict) -> str:
    return f'''
<radialGradient id="disco-fundo" cx="50%" cy="26%" r="84%">
  <stop offset="0" stop-color="{c['disco_topo']}"/>
  <stop offset=".55" stop-color="{c['disco_meio']}"/>
  <stop offset="1" stop-color="{c['disco_base']}"/>
</radialGradient>
<radialGradient id="contraluz" cx="50%" cy="50%" r="50%">
  <stop offset="0" stop-color="{c['contraluz']}" stop-opacity=".34"/>
  <stop offset=".6" stop-color="{c['aro_b']}" stop-opacity=".16"/>
  <stop offset="1" stop-color="{c['contraluz']}" stop-opacity="0"/>
</radialGradient>
<linearGradient id="aro" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="{c['aro_a']}"/>
  <stop offset=".5" stop-color="{c['aro_b']}"/>
  <stop offset="1" stop-color="{c['aro_c']}"/>
</linearGradient>
<radialGradient id="pele" cx="34%" cy="24%" r="84%">
  <stop offset="0" stop-color="{c['pele_luz']}"/>
  <stop offset=".55" stop-color="{c['pele']}"/>
  <stop offset="1" stop-color="{c['pele_sombra']}"/>
</radialGradient>
<linearGradient id="pescoco" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="{c['pescoco']}"/><stop offset="1" stop-color="{c['orelha']}"/>
</linearGradient>
<linearGradient id="cabelo" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="{c['cabelo_luz']}"/>
  <stop offset=".6" stop-color="{c['cabelo']}"/>
  <stop offset="1" stop-color="{c['cabelo_sombra']}"/>
</linearGradient>
<radialGradient id="camisa" cx="34%" cy="0%" r="104%">
  <stop offset="0" stop-color="{c['camisa_a']}"/><stop offset="1" stop-color="{c['camisa_b']}"/>
</radialGradient>
<radialGradient id="iris" cx="38%" cy="32%" r="72%">
  <stop offset="0" stop-color="{c['iris']}"/><stop offset="1" stop-color="{c['iris_borda']}"/>
</radialGradient>
<linearGradient id="metal" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="{c['metal_luz']}"/>
  <stop offset=".5" stop-color="{c['metal']}"/>
  <stop offset="1" stop-color="{c['metal_sombra']}"/>
</linearGradient>
<linearGradient id="fone" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="{c['fone']}"/><stop offset="1" stop-color="{c['fone_sombra']}"/>
</linearGradient>
<linearGradient id="boca-interna" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="{c['boca_fundo']}"/>
  <stop offset="1" stop-color="{c['boca_base']}"/>
</linearGradient>
<linearGradient id="luz-fade" x1="0" y1="0" x2="1" y2="0">
  <stop offset="0" stop-color="#000000"/><stop offset="1" stop-color="#FFFFFF"/>
</linearGradient>
<mask id="luz-borda">
  <rect x="0" y="0" width="200" height="200" fill="#000000"/>
  <rect x="112" y="24" width="70" height="140" fill="url(#luz-fade)"/>
</mask>
<filter id="borrao" x="-40%" y="-40%" width="180%" height="180%">
  <feGaussianBlur stdDeviation="2.4"/>
</filter>
<filter id="borrao-forte" x="-50%" y="-50%" width="200%" height="200%">
  <feGaussianBlur stdDeviation="5"/>
</filter>
<clipPath id="disco"><circle cx="100" cy="100" r="98"/></clipPath>
'''


def _documento(recorte, escala: float, corpo: str, c: dict) -> str:
    """Página autossuficiente com uma camada do mascote, fundo transparente."""
    x, y, larg, alt = recorte
    return f'''<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<style>html,body{{margin:0;padding:0;background:transparent}}svg{{display:block}}</style>
</head><body>
<svg xmlns="http://www.w3.org/2000/svg" width="{round(larg * escala)}"
     height="{round(alt * escala)}" viewBox="{x} {y} {larg} {alt}">
<defs>{_defs(c)}</defs>
{corpo}
</svg></body></html>'''


def _svg_fundo(c: dict) -> str:
    return f'''
<circle cx="100" cy="100" r="98" fill="url(#disco-fundo)"/>
<ellipse cx="100" cy="104" rx="82" ry="78" fill="url(#contraluz)"/>'''


def _svg_frente(c: dict) -> str:
    """Vinheta e aro: vão por cima de tudo e nunca se mexem."""
    return f'''
<g clip-path="url(#disco)">
  <circle cx="100" cy="100" r="99" fill="none" stroke="{c['vinheta']}" stroke-width="16"
          opacity=".32" filter="url(#borrao-forte)"/>
</g>
<circle cx="100" cy="100" r="96" fill="none" stroke="url(#aro)" stroke-width="3.5"
        opacity=".95"/>
<circle cx="100" cy="100" r="92.5" fill="none" stroke="#FFFFFF" stroke-opacity=".07"
        stroke-width="1.2"/>'''


def _svg_corpo(c: dict) -> str:
    return f'''
<ellipse cx="100" cy="212" rx="86" ry="54" fill="{c['vinheta']}" opacity=".38"
         filter="url(#borrao-forte)"/>
<ellipse cx="100" cy="208" rx="86" ry="54" fill="url(#camisa)"/>
<path d="M30,208 Q42,170 76,158" fill="none" stroke="{c['camisa_luz']}"
      stroke-opacity=".32" stroke-width="4.5" filter="url(#borrao)"/>
<path d="M76,158 Q100,183 124,158" fill="none" stroke="#FFFFFF" stroke-opacity=".22"
      stroke-width="3" stroke-linecap="round"/>
<rect x="88" y="120" width="24" height="42" rx="12" fill="url(#pescoco)"/>'''


def _boca_svg(nivel: int, c: dict) -> str:
    """Boca em quatro aberturas: 0 fechada (sorriso) a 3 ampla."""
    if nivel <= 0:
        return (
            f'<path d="M86,120.5 Q100,129 114,120.5" fill="none" stroke="{c["sorriso"]}"'
            ' stroke-width="3.6" stroke-linecap="round"/>'
            f'<path d="M89,125.5 Q100,130 111,125.5" fill="none" stroke="{c["labio_luz"]}"'
            ' stroke-opacity=".5" stroke-width="2.4" stroke-linecap="round"/>'
        )

    rx, ry = ((8.5, 4.0), (10.5, 7.5), (12.0, 10.5))[nivel - 1]
    cy = 123.0
    dentes = max(2.4, ry * 0.42)
    partes = [
        # Sombra projetada pelo lábio superior dentro da boca.
        f'<ellipse cx="100" cy="{cy + 1.5}" rx="{rx + 1.5}" ry="{ry + 1.5}"'
        f' fill="{c["sombra_pele"]}" opacity=".35" filter="url(#borrao)"/>',
        f'<clipPath id="cboca"><ellipse cx="100" cy="{cy}" rx="{rx}" ry="{ry}"/></clipPath>',
        f'<ellipse cx="100" cy="{cy}" rx="{rx}" ry="{ry}" fill="url(#boca-interna)"/>',
        '<g clip-path="url(#cboca)">',
        f'<rect x="{100 - rx}" y="{cy - ry}" width="{2 * rx}" height="{dentes:.1f}"'
        f' fill="{c["dentes"]}"/>',
    ]
    if nivel >= 2:  # língua só aparece nas aberturas maiores
        partes.append(
            f'<ellipse cx="100" cy="{cy + ry * 0.74:.1f}" rx="{rx * 0.64:.1f}"'
            f' ry="{ry * 0.52:.1f}" fill="{c["lingua"]}"/>'
        )
    partes.append('</g>')
    partes.append(
        f'<ellipse cx="100" cy="{cy}" rx="{rx}" ry="{ry}" fill="none"'
        f' stroke="{c["boca_traco"]}" stroke-width="1.4" opacity=".8"/>'
    )
    # Brilho do lábio inferior: devolve volume à boca aberta.
    partes.append(
        f'<path d="M{100 - rx * 0.7:.1f},{cy + ry + 1.6:.1f}'
        f' Q100,{cy + ry + 4.2:.1f} {100 + rx * 0.7:.1f},{cy + ry + 1.6:.1f}"'
        f' fill="none" stroke="{c["labio_luz"]}" stroke-opacity=".45" stroke-width="2.2"'
        ' stroke-linecap="round"/>'
    )
    return ''.join(partes)


def _olhos_svg(estado: str, c: dict) -> str:
    """Olho aberto, meio fechado ou fechado — os meios evitam a piscada em salto."""
    if estado == 'fechado':
        return (
            f'<path d="M79,96 Q86.5,102.5 94,96" fill="none" stroke="{c["cilios"]}"'
            ' stroke-width="2.8" stroke-linecap="round"/>'
            f'<path d="M106,96 Q113.5,102.5 121,96" fill="none" stroke="{c["cilios"]}"'
            ' stroke-width="2.8" stroke-linecap="round"/>'
        )

    # No meio-fechado a pálpebra cobre a metade de cima do olho.
    corte = 3.4 if estado == 'meio' else 0.0

    def olho(cx: float) -> str:
        return (
            f'<clipPath id="colho{int(cx)}">'
            f'<rect x="{cx - 8}" y="{89 + corte}" width="16" height="16"/></clipPath>'
            f'<g clip-path="url(#colho{int(cx)})">'
            f'<ellipse cx="{cx}" cy="97" rx="7.6" ry="8.2" fill="{c["olho"]}"/>'
            f'<ellipse cx="{cx}" cy="92.5" rx="7.6" ry="4.2" fill="{c["palpebra"]}"'
            ' opacity=".38" filter="url(#borrao)"/>'
            f'<circle cx="{cx + 0.8}" cy="98" r="4.2" fill="url(#iris)"/>'
            f'<circle cx="{cx + 0.8}" cy="98" r="1.9" fill="{c["pupila"]}"/>'
            f'<circle cx="{cx - 1.3}" cy="95.2" r="1.7" fill="#FFFFFF" opacity=".92"/>'
            f'<circle cx="{cx + 2.6}" cy="100.2" r="0.9" fill="#FFFFFF" opacity=".5"/>'
            '</g>'
            f'<path d="M{cx - 7.4},{95.4 + corte} Q{cx},{89.6 + corte * 1.6}'
            f' {cx + 7.4},{95.4 + corte}" fill="none" stroke="{c["cilios"]}"'
            ' stroke-width="1.7" stroke-linecap="round" opacity=".75"/>'
        )

    return olho(86) + olho(114)


def _sobrancelhas_svg(estado: str, c: dict) -> str:
    dy = {'neutro': 0.0, 'meio': -2.2, 'alto': -4.5}[estado]
    return (
        f'<path d="M78,{83 + dy} Q87,{77.5 + dy} 96,{82 + dy}" fill="none"'
        f' stroke="{c["sobrancelha"]}" stroke-width="3.4" stroke-linecap="round"/>'
        f'<path d="M104,{82 + dy} Q113,{77.5 + dy} 122,{83 + dy}" fill="none"'
        f' stroke="{c["sobrancelha"]}" stroke-width="3.4" stroke-linecap="round"/>'
    )


def _svg_cabeca(boca: int, olhos: str, cejas: str, c: dict) -> str:
    return f'''
<ellipse cx="100" cy="137" rx="27" ry="12" fill="{c['sombra_pele']}" opacity=".5"
         filter="url(#borrao)"/>
<ellipse cx="100" cy="94" rx="44" ry="48" fill="url(#pele)"/>
<ellipse cx="56" cy="101" rx="7.5" ry="10.5" fill="{c['orelha']}"/>
<ellipse cx="144" cy="101" rx="7.5" ry="10.5" fill="{c['orelha']}"/>
<ellipse cx="74" cy="111" rx="11" ry="7.5" fill="{c['bochecha']}" opacity=".28"
         filter="url(#borrao)"/>
<ellipse cx="126" cy="111" rx="11" ry="7.5" fill="{c['bochecha']}" opacity=".28"
         filter="url(#borrao)"/>
<ellipse cx="100" cy="94" rx="43" ry="47" fill="none" stroke="{c['fone']}"
         stroke-width="4" opacity=".8" mask="url(#luz-borda)" filter="url(#borrao)"/>
<path d="M62,80 Q100,92 138,80 L138,66 L62,66 Z" fill="{c['sombra_pele']}" opacity=".28"
      filter="url(#borrao)"/>
<path d="M54,98 C50,52 72,32 100,32 C128,32 150,52 146,98 C145,84 141,77 133,74
         C118,70 108,79 96,79 C83,79 71,74 65,79 C59,83 55,90 54,98 Z"
      fill="url(#cabelo)"/>
<path d="M72,52 Q88,40 108,42" fill="none" stroke="{c['cabelo_brilho']}"
      stroke-opacity=".45" stroke-width="5" stroke-linecap="round" filter="url(#borrao)"/>
<path d="M55,96 C49,44 75,26 100,26 C125,26 151,44 145,96" fill="none"
      stroke="url(#metal)" stroke-width="4.5" stroke-linecap="round"/>
<path d="M60,78 C58,48 78,32 100,31" fill="none" stroke="#FFFFFF" stroke-opacity=".35"
      stroke-width="1.6" stroke-linecap="round"/>
<rect x="43" y="87" width="18" height="30" rx="9" fill="url(#fone)"/>
<rect x="46.5" y="91" width="11" height="22" rx="5.5" fill="{c['fone_sombra']}" opacity=".5"/>
<path d="M47,93 Q45.5,102 47,111" fill="none" stroke="{c['fone_brilho']}"
      stroke-opacity=".55" stroke-width="1.8" stroke-linecap="round"/>
<rect x="139" y="87" width="18" height="30" rx="9" fill="url(#fone)"/>
<rect x="142.5" y="91" width="11" height="22" rx="5.5" fill="{c['fone_sombra']}" opacity=".5"/>
{_sobrancelhas_svg(cejas, c)}
{_olhos_svg(olhos, c)}
<ellipse cx="101" cy="110" rx="6.5" ry="5" fill="{c['pescoco']}" opacity=".38"
         filter="url(#borrao)"/>
<path d="M100,102 Q104.5,111 99.5,113" fill="none" stroke="{c['pele_traco']}"
      stroke-width="2.6" stroke-linecap="round" opacity=".9"/>
<ellipse cx="97.5" cy="105" rx="1.9" ry="4.2" fill="#FFE6C8" opacity=".4"
         filter="url(#borrao)"/>
{_boca_svg(boca, c)}
<path d="M51,115 Q53,139 77,136" fill="none" stroke="url(#metal)" stroke-width="2.8"
      stroke-linecap="round"/>
<circle cx="79" cy="136" r="4.8" fill="url(#fone)"/>
<circle cx="77.6" cy="134.6" r="1.6" fill="#FFFFFF" opacity=".45"/>'''


def _camadas() -> list[tuple[str, tuple, callable]]:
    """(nome do arquivo, recorte, função que devolve o miolo do SVG)."""
    disco = (0, 0, DISCO, DISCO)
    itens = [
        ('fundo', disco, _svg_fundo),
        ('frente', disco, _svg_frente),
        ('corpo', disco, _svg_corpo),
    ]
    for boca in range(NIVEIS_BOCA):
        for olhos in OLHOS:
            for cejas in CEJAS:
                itens.append((
                    f'cabeca_b{boca}_o{olhos}_s{cejas}', CAB_RECORTE,
                    lambda c, b=boca, o=olhos, s=cejas: _svg_cabeca(b, o, s, c),
                ))
    return itens


def _dir_cache(tam: int, c: dict) -> str:
    # A paleta entra por hash: trocar uma cor refaz a arte sozinho.
    marca = hashlib.sha1(repr(sorted(c.items())).encode()).hexdigest()[:8]
    return os.path.join(settings.MEDIA_ROOT, 'cache', 'avatar',
                        f'v{VERSAO_ARTE}_{tam}_{marca}')


def _limpar_caches_antigos(atual: str) -> None:
    """Descarta a arte de versões ou tamanhos anteriores.

    As paletas convivem: cada trilha usa a sua, e apagar as das outras faria a
    arte ser regerada toda vez que um vídeo de outra trilha fosse gerado.
    """
    raiz, atual_nome = os.path.dirname(atual), os.path.basename(atual)
    prefixo = atual_nome.rsplit('_', 1)[0] + '_'  # v{versão}_{tamanho}_
    for nome in os.listdir(raiz):
        caminho = os.path.join(raiz, nome)
        if os.path.isdir(caminho) and not nome.startswith(prefixo):
            shutil.rmtree(caminho, ignore_errors=True)


def camadas(tam: int | None = None, paleta: dict | None = None) -> dict[str, str]:
    """Devolve {nome da camada: caminho do PNG}, gerando o cache se preciso.

    O Chromium só é aberto na primeira vez de cada paleta (ou depois de mexer
    na arte ou no tamanho): nas gerações seguintes os PNGs já estão no disco.
    """
    tam = tam or _tamanho()
    c = cores(paleta)
    destino = _dir_cache(tam, c)
    itens = _camadas()
    mapa = {nome: os.path.join(destino, f'{nome}.png') for nome, _, _ in itens}
    if all(os.path.exists(p) for p in mapa.values()):
        return mapa

    from playwright.sync_api import sync_playwright  # dependência opcional da feature

    os.makedirs(destino, exist_ok=True)
    _limpar_caches_antigos(destino)
    escala = tam / DISCO
    # Temporário dentro do próprio cache: os.replace() abaixo exige que origem e
    # destino estejam no mesmo sistema de arquivos (/tmp pode ser outro).
    trabalho = tempfile.mkdtemp(prefix='.parcial_', dir=destino)
    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch(
                args=['--no-sandbox', '--force-color-profile=srgb'])
            pagina = navegador.new_page()
            for nome, recorte, desenhar in itens:
                if os.path.exists(mapa[nome]):
                    continue
                pagina.set_viewport_size({
                    'width': round(recorte[2] * escala),
                    'height': round(recorte[3] * escala),
                })
                html = os.path.join(trabalho, f'{nome}.html')
                with open(html, 'w', encoding='utf-8') as fh:
                    fh.write(_documento(recorte, escala, desenhar(c), c))
                pagina.goto(f'file://{html}', wait_until='load')
                png = os.path.join(trabalho, f'{nome}.png')
                pagina.screenshot(path=png, omit_background=True)
                # Publica pronto: dois workers gerando ao mesmo tempo não se
                # atrapalham nem deixam PNG pela metade no cache.
                os.replace(png, mapa[nome])
            navegador.close()
    finally:
        shutil.rmtree(trabalho, ignore_errors=True)
    return mapa


# ---------------------------------------------------------------------------
# Leitura da narração
# ---------------------------------------------------------------------------

def _rms_por_quadro(audio: str) -> list[float]:
    """Volume (RMS) da narração em janelas de 1/FPS_BOCA s."""
    proc = subprocess.run(
        [video_montagem._ffmpeg(), '-v', 'error', '-i', audio,
         '-f', 's16le', '-ac', '1', '-ar', str(TAXA_PCM), '-'],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError('ffmpeg falhou ao decodificar a narração do mascote.')

    bruto = proc.stdout
    amostras = array('h')
    amostras.frombytes(bruto[:len(bruto) - len(bruto) % amostras.itemsize])
    if not amostras:
        return []

    por_quadro = TAXA_PCM / FPS_BOCA
    total = math.ceil(len(amostras) / por_quadro)
    valores = []
    for i in range(total):
        ini, fim = int(i * por_quadro), int((i + 1) * por_quadro)
        janela = amostras[ini:fim]
        if not janela:
            valores.append(0.0)
            continue
        soma = sum(float(x) * x for x in janela)
        valores.append(math.sqrt(soma / len(janela)))
    return valores


def bocas_por_quadro(audio: str) -> list[int]:
    """Nível de abertura da boca (0–3) a cada 1/FPS_BOCA s."""
    rms = _rms_por_quadro(audio)
    if not rms:
        return []

    # Referência = percentil 90 do próprio áudio, com piso para não amplificar
    # ruído em trechos de silêncio.
    ordenado = sorted(rms)
    referencia = max(ordenado[min(len(ordenado) - 1, int(0.9 * len(ordenado)))], 300.0)

    niveis = []
    for i, valor in enumerate(rms):
        # Suaviza com o quadro anterior: evita a boca tremendo entre sílabas.
        suave = valor if i == 0 else 0.7 * valor + 0.3 * rms[i - 1]
        fracao = suave / referencia
        nivel = sum(1 for limiar in LIMIARES if fracao >= limiar)
        niveis.append(nivel)
    return niveis


# ---------------------------------------------------------------------------
# Coreografia: movimento contínuo + eventos
# ---------------------------------------------------------------------------

def _suave(u: float) -> float:
    """Smoothstep: sai e chega parado, sem tranco nas pontas."""
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def _pulso(u: float) -> float:
    """0 → 1 → 0, suave nas duas pontas."""
    return math.sin(math.pi * min(1.0, max(0.0, u)))


def _deriva(t: float, ondas) -> float:
    return sum(a * math.sin(2 * math.pi * t / p + f) for a, p, f in ondas)


def _pausas(niveis: list[int]) -> list[tuple[float, float]]:
    """Silêncios longos (início, fim) em segundos — fronteiras entre frases."""
    minimo = max(1, round(PAUSA_MINIMA * FPS_BOCA))
    trechos, inicio = [], None
    for i, nivel in enumerate(niveis + [1]):
        if nivel == 0 and inicio is None:
            inicio = i
        elif nivel != 0 and inicio is not None:
            if i - inicio >= minimo:
                trechos.append((inicio / FPS_BOCA, i / FPS_BOCA))
            inicio = None
    return trechos


def eventos(niveis: list[int], inicio_roteiro: int = 0) -> list[tuple[str, float, float]]:
    """Reações tiradas da narração: (tipo, início em s, duração em s).

    ``inicio_roteiro`` desloca o rodízio de reações. Cada slide é uma narração
    separada, com poucas pausas cada; sem esse deslocamento todos começariam do
    mesmo ponto e o mascote repetiria a primeira reação da lista o vídeo inteiro.
    """
    lista = []

    # Ênfase nas sílabas fortes, com intervalo mínimo entre elas.
    ultima = -ENFASE_INTERVALO
    for i, nivel in enumerate(niveis):
        t = i / FPS_BOCA
        if nivel >= NIVEIS_BOCA - 1 and t - ultima >= ENFASE_INTERVALO:
            lista.append(('enfase', t, ENFASE_DUR))
            ultima = t

    # Nas pausas: piscada e, em rodízio, uma reação. Só uma por pausa — somar
    # duas emboladas no mesmo instante embaralha o movimento.
    for n, (inicio, fim) in enumerate(_pausas(niveis)):
        vaga = fim - inicio
        lista.append(('piscada', inicio + 0.05, PISCADA_DUR))
        reacao = ROTEIRO_PAUSAS[(inicio_roteiro + n) % len(ROTEIRO_PAUSAS)]
        if reacao == 'aceno':
            lista.append(('aceno', inicio + 0.1, min(ACENO_DUR, max(0.4, vaga))))
        elif reacao == 'encolher':
            lista.append(('encolher', inicio + 0.1, ENCOLHER_DUR))
        elif reacao == 'inclinar':
            # A inclinação atravessa a pausa e entra na fala seguinte.
            lista.append(('inclinar', inicio + 0.1, INCLINAR_DUR))

    # Piscadas de repouso, em intervalos que se alternam.
    duracao = len(niveis) / FPS_BOCA
    t, n = PISCADA_INTERVALOS[0], 0
    while t < duracao:
        lista.append(('piscada', t, PISCADA_DUR))
        n += 1
        t += PISCADA_INTERVALOS[n % len(PISCADA_INTERVALOS)]

    return lista


def animacao(niveis: list[int], fps: int | None = None,
             inicio_roteiro: int = 0) -> list[Quadro]:
    """Estado completo do mascote em cada quadro do vídeo."""
    fps = fps or _fps()
    if not niveis:
        return []

    total = max(1, math.ceil(len(niveis) / FPS_BOCA * fps))
    reacoes = eventos(niveis, inicio_roteiro)
    quadros = []

    for i in range(total):
        t = i / fps
        # Base: respiração e deriva, sempre ativas.
        respira = math.sin(2 * math.pi * t / RESP_PERIODO)
        corpo_dy = RESP_AMPL * respira
        cab_dx = _deriva(t, DERIVA_DX)
        cab_dy = _deriva(t, DERIVA_DY) + 0.55 * respira
        cab_giro = _deriva(t, DERIVA_GIRO)
        corpo_dx = 0.35 * cab_dx
        olhos, cejas = 'aberto', 'neutro'

        for tipo, inicio, dur in reacoes:
            u = (t - inicio) / dur
            if not 0.0 <= u < 1.0:
                continue
            if tipo == 'piscada':
                olhos = ('meio' if u < 0.25 else 'fechado' if u < 0.6
                         else 'meio' if u < 0.85 else 'aberto')
            elif tipo == 'enfase':
                cab_dy -= ENFASE_ALTURA * _pulso(u)
                cejas = ('meio' if u < 0.2 else 'alto' if u < 0.75
                         else 'meio' if u < 0.95 else 'neutro')
            elif tipo == 'aceno':
                cab_dy += ACENO_ALTURA * _pulso(u)
                cab_giro += 0.8 * _pulso(u)
            elif tipo == 'encolher':
                corpo_dy -= ENCOLHER_ALTURA * _pulso(u)
                cab_dy -= 0.6 * ENCOLHER_ALTURA * _pulso(u)
            elif tipo == 'inclinar':
                # Pende a cabeça para um lado e volta; alterna o lado a cada vez.
                lado = 1 if int(inicio * 10) % 2 else -1
                cab_giro += lado * INCLINAR_GRAUS * _pulso(u)
                cab_dx += lado * 1.4 * _pulso(u)

        boca = niveis[min(len(niveis) - 1, int(i * FPS_BOCA / fps))]
        quadros.append(Quadro(boca, olhos, cejas, corpo_dx, corpo_dy,
                              cab_dx, cab_dy, cab_giro))
    return quadros


# ---------------------------------------------------------------------------
# Composição (Pillow) e clipe .webm com alfa
# ---------------------------------------------------------------------------

def _mascara_disco(lado: int, tam: int, margem: int):
    """Recorte circular do disco, com borda suavizada (desenhado em 4× e reduzido)."""
    from PIL import Image, ImageDraw

    escala = tam / DISCO
    raio = 98 * escala
    centro = margem + tam / 2
    grande = Image.new('L', (lado * 4, lado * 4), 0)
    ImageDraw.Draw(grande).ellipse(
        [(centro - raio) * 4, (centro - raio) * 4,
         (centro + raio) * 4, (centro + raio) * 4], fill=255)
    return grande.resize((lado, lado), Image.LANCZOS)


def _compor(anim: list[Quadro], arte: dict[str, str], tam: int, destino: str) -> None:
    """Compõe cada quadro e transmite direto ao ffmpeg, sem PNG intermediário."""
    from PIL import Image, ImageChops

    escala = tam / DISCO
    margem = round(MARGEM * escala)
    lado = tam + 2 * margem

    abrir = lambda nome: Image.open(arte[nome]).convert('RGBA')  # noqa: E731
    frente = abrir('frente')
    corpo = abrir('corpo')
    cabecas = {
        (b, o, s): abrir(f'cabeca_b{b}_o{o}_s{s}')
        for b in range(NIVEIS_BOCA) for o in OLHOS for s in CEJAS
    }

    base = Image.new('RGBA', (lado, lado), (0, 0, 0, 0))
    base.alpha_composite(abrir('fundo'), dest=(margem, margem))
    mascara = _mascara_disco(lado, tam, margem)

    cab_org = (margem + round(CAB_RECORTE[0] * escala),
               margem + round(CAB_RECORTE[1] * escala))

    # Girar é o passo caro: como o giro varia devagar, quadros vizinhos caem no
    # mesmo décimo de grau e reaproveitam o resultado.
    cache_giro: dict = {}

    def girado(imagem, chave, angulo):
        if abs(angulo) < 0.05:
            return imagem
        chave = (chave, round(angulo, 1))
        pronto = cache_giro.get(chave)
        if pronto is None:
            if len(cache_giro) > 96:
                cache_giro.clear()
            pronto = imagem.rotate(round(angulo, 1), resample=Image.BILINEAR)
            cache_giro[chave] = pronto
        return pronto

    # VP9 em WebM é o formato com alfa que cabe aqui: a 30 fps quase todo quadro
    # difere, e um codec sem perdas (qtrle) geraria centenas de MB de
    # temporário por vídeo. Com o preset realtime sai ~75× menor e mais rápido.
    proc = subprocess.Popen(
        [video_montagem._ffmpeg(), '-y', '-f', 'rawvideo', '-pix_fmt', 'rgba',
         '-s', f'{tam}x{tam}', '-r', str(_fps()), '-i', '-',
         '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p',
         '-crf', '26', '-b:v', '0', '-deadline', 'realtime', '-cpu-used', '8',
         '-f', 'webm', destino],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        for q in anim:
            quadro = base.copy()
            desloca_corpo = (margem + round(q.corpo_dx * escala),
                             margem + round(q.corpo_dy * escala))
            quadro.alpha_composite(corpo, dest=desloca_corpo)
            cabeca = girado(cabecas[(q.boca, q.olhos, q.cejas)],
                            (q.boca, q.olhos, q.cejas), q.cab_giro)
            quadro.alpha_composite(cabeca, dest=(
                cab_org[0] + round(q.cab_dx * escala),
                cab_org[1] + round(q.cab_dy * escala)))
            # Recorta no disco antes do aro, para nada vazar para fora dele.
            quadro.putalpha(ImageChops.multiply(quadro.getchannel('A'), mascara))
            quadro.alpha_composite(frente, dest=(margem, margem))
            proc.stdin.write(
                quadro.crop((margem, margem, margem + tam, margem + tam)).tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    erro = proc.stderr.read().decode('utf-8', 'replace')
    proc.stderr.close()
    if proc.wait() != 0:
        cauda = ' | '.join(erro.strip().splitlines()[-6:])
        raise RuntimeError(f'ffmpeg falhou ao montar o mascote: {cauda}')


def clipes(audios: list[str], out_dir: str,
           paleta: dict | None = None) -> list[str] | None:
    """Gera um clipe do mascote por narração, na mesma ordem; None se indisponível.

    ``paleta`` é o conjunto de cores da trilha (ver `paleta_da_trilha`).
    Devolver None (mascote desligado, Playwright ausente, ffmpeg reclamando) é
    parte do contrato: a montagem segue sem o mascote e o vídeo continua saindo.
    """
    if not ativo() or not audios:
        return None
    try:
        tam = _tamanho()
        arte = camadas(tam, paleta)
        os.makedirs(out_dir, exist_ok=True)
        saidas = [os.path.join(out_dir, f'mascote_{i:03d}.webm')
                  for i in range(len(audios))]

        def _um(i: int):
            anim = animacao(bocas_por_quadro(audios[i]), inicio_roteiro=i)
            if not anim:
                anim = [Quadro(0, 'aberto', 'neutro', 0, 0, 0, 0, 0)]
            _compor(anim, arte, tam, saidas[i])

        # Em paralelo, como os clipes da montagem: boa parte do tempo aqui é o
        # ffmpeg codificando o WebM, e o Pillow solta o GIL nas composições.
        video_utils.em_paralelo(_um, list(range(len(audios))),
                                video_montagem.workers())
        return saidas
    except Exception:
        logger.warning('Mascote do vídeo indisponível; seguindo sem ele.',
                       exc_info=True)
        return None
