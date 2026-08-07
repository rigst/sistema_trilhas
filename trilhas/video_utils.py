"""Utilidades compartilhadas da geração de vídeo do subtópico.

O conteúdo do subtópico (`conteudo_md`) é escrito pela IA em seções separadas
por uma linha contendo apenas `---` (cada seção vira um "card"/slide). Aqui
fatiamos o Markdown nessas seções, respeitando cercas de código (um `---` dentro
de ```...``` NÃO é separador).

Aqui mora também o `em_paralelo`, usado pelas etapas caras do vídeo (chamadas de
IA, mascote, clipes do ffmpeg): todas são listas de trabalhos independentes
sobre índices, e todas ganham em rodar algumas ao mesmo tempo.
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HR_RE = re.compile(r"^\s*---+\s*$")


def em_paralelo(fn, itens: list, max_workers: int, progresso=None) -> list:
    """Aplica ``fn`` a cada item em threads e devolve os resultados NA ORDEM.

    Serve para trabalho que espera por algo de fora (rede da IA/TTS, ffmpeg em
    subprocesso), que é o caso de todas as etapas do vídeo — o GIL fica livre
    durante a espera.

    ``progresso`` é um callback opcional ``fn(prontos, total)``, chamado na
    ordem real de término. A primeira exceção cancela o que ainda não começou e
    sobe: sem isso, uma falha no primeiro trabalho ainda esperaria a fila
    inteira terminar antes de ser reportada.
    """
    total = len(itens)
    if total < 2 or max_workers < 2:
        saida = []
        for i, item in enumerate(itens):
            saida.append(fn(item))
            if progresso:
                progresso(i + 1, total)
        return saida

    with ThreadPoolExecutor(max_workers=min(max_workers, total)) as pool:
        futuros = {pool.submit(fn, item): i for i, item in enumerate(itens)}
        saida = [None] * total
        prontos = 0
        try:
            for f in as_completed(futuros):
                saida[futuros[f]] = f.result()
                prontos += 1
                if progresso:
                    progresso(prontos, total)
        except BaseException:
            for f in futuros:
                f.cancel()
            raise
    return saida


def fatiar_secoes(md: str) -> list[str]:
    """Divide o Markdown em seções pelos separadores `---` fora de cercas de código.

    Retorna a lista de seções (texto Markdown), sem os separadores e sem seções
    vazias. Se não houver separadores, devolve o texto inteiro como seção única.
    """
    if not md or not md.strip():
        return []
    linhas = md.split("\n")
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
    resultado = ["\n".join(s).strip() for s in secoes]
    return [s for s in resultado if s]
