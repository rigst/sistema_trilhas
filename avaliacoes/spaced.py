"""Repetição espaçada (SM-2) aplicada a partir das revisões concluídas.

Cada revisão global mistura questões de vários níveis já concluídos. Ao concluir
a revisão, olhamos o desempenho por nível e reagendamos a próxima revisão de cada
um com o algoritmo SM-2 (implementado em Nivel.registrar_revisao).
"""

from collections import defaultdict
from typing import Any

from trilhas.models import Nivel


def _qualidade(fracao_acertos):
    """Mapeia o % de acertos do nível (0–1) para a qualidade SM-2 (0–5)."""
    f = fracao_acertos
    if f >= 0.9:
        return 5
    if f >= 0.75:
        return 4
    if f >= 0.5:
        return 3
    if f >= 0.25:
        return 2
    return 1


def aplicar_sm2(revisao):
    """Reagenda a revisão espaçada de cada nível avaliado nesta revisão."""
    # nivel -> [acertos, total]
    por_nivel: defaultdict[Any, list[int]] = defaultdict(lambda: [0, 0])
    for q in revisao.questoes.select_related("nivel").all():
        if q.nivel_id is None or q.respondido_em is None:
            continue
        por_nivel[q.nivel][1] += 1
        if (q.nota or 0) >= 10:
            por_nivel[q.nivel][0] += 1

    for nivel, (acertos, total) in por_nivel.items():
        if not total or nivel.status != Nivel.Status.APROVADO:
            continue
        nivel.registrar_revisao(_qualidade(acertos / total))
