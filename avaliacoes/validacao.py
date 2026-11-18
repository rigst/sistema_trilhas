"""Barreira contra questões-placeholder vazando para produção.

A geração por IA já passa por `_questoes_validas`, mas uma questão criada à
mão (admin inline, shell) não passa por validação nenhuma — foi assim que um
registro de teste (enunciado "placeholder", alternativas "w"/"x"/"y"/"z")
acabou publicado. Este módulo centraliza a detecção para ser usada tanto no
`clean()` do modelo `Questao` (admin, shell) quanto no validador da IA, sem
depender de nada do Django.
"""

import re

# Enunciados que são claramente rascunho/teste, não uma pergunta de verdade.
ENUNCIADOS_PLACEHOLDER = {
    "placeholder",
    "teste",
    "test",
    "testando",
    "exemplo",
    "example",
    "lorem",
    "lorem ipsum",
    "tbd",
    "todo",
    "xxx",
    "foo",
    "bar",
    "pergunta",
    "questao",
    "questão",
    "enunciado",
    "n/a",
    "na",
}

# Texto de alternativa que não diz nada (mesmo conjunto usado pela IA).
ALTERNATIVAS_PLACEHOLDER = {
    "dup",
    "outra",
    "outro",
    "outras",
    "outros",
    "alternativa",
    "opcao",
    "opção",
    "nenhuma",
    "nenhum",
    "n/a",
    "na",
    "etc",
    "foo",
    "bar",
    "baz",
}

# Letra isolada do alfabeto latino ("w", "x", "y", "z"): assinatura de rascunho.
# Dígitos ("4", "5") NÃO entram aqui — são respostas numéricas legítimas.
_LETRA_UNICA = re.compile(r"^[a-z]$")


def _norm(texto):
    """Minúsculas, espaços colapsados e pontuação de borda removida."""
    return " ".join(str(texto or "").split()).casefold().strip(" .,;:!?…")


def enunciado_e_placeholder(enunciado):
    norm = _norm(enunciado)
    return (not norm) or norm in ENUNCIADOS_PLACEHOLDER


def _alternativa_degenerada(texto_norm):
    if not texto_norm:
        return True
    if texto_norm in ALTERNATIVAS_PLACEHOLDER:
        return True
    return _LETRA_UNICA.match(texto_norm) is not None


def alternativas_sao_placeholder(alternativas):
    """True quando TODAS as alternativas são degeneradas (ex.: w/x/y/z).

    Exige que todas sejam degeneradas para não reprovar um conjunto em que só
    uma opção legítima é curta.
    """
    textos = []
    for alternativa in alternativas or []:
        if isinstance(alternativa, dict):
            textos.append(_norm(alternativa.get("texto")))
        else:
            textos.append(_norm(alternativa))
    textos = [t for t in textos if t]
    if not textos:
        return True
    return all(_alternativa_degenerada(t) for t in textos)


def questao_e_placeholder(enunciado, alternativas):
    """True quando o enunciado ou o conjunto de alternativas é placeholder."""
    return enunciado_e_placeholder(enunciado) or alternativas_sao_placeholder(alternativas)
