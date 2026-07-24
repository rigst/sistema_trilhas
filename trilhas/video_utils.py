"""Utilidades compartilhadas da geração de vídeo do subtópico.

O conteúdo do subtópico (`conteudo_md`) é escrito pela IA em seções separadas
por uma linha contendo apenas `---` (cada seção vira um "card"/slide). Aqui
fatiamos o Markdown nessas seções, respeitando cercas de código (um `---` dentro
de ```...``` NÃO é separador).
"""

import re

_FENCE_RE = re.compile(r'^\s*(```|~~~)')
_HR_RE = re.compile(r'^\s*---+\s*$')


def fatiar_secoes(md: str) -> list[str]:
    """Divide o Markdown em seções pelos separadores `---` fora de cercas de código.

    Retorna a lista de seções (texto Markdown), sem os separadores e sem seções
    vazias. Se não houver separadores, devolve o texto inteiro como seção única.
    """
    if not md or not md.strip():
        return []
    linhas = md.split('\n')
    secoes: list[list[str]] = [[]]
    em_fence = False
    for linha in linhas:
        if _FENCE_RE.match(linha):
            em_fence = not em_fence
            secoes[-1].append(linha)
            continue
        if not em_fence and _HR_RE.match(linha):
            secoes.append([])
            continue
        secoes[-1].append(linha)
    resultado = ['\n'.join(s).strip() for s in secoes]
    return [s for s in resultado if s]
