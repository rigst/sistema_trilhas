"""Renderização de Markdown do conteúdo/exercícios, com callouts, destaque de
sintaxe e diagramas Mermaid."""

import html
import re

import markdown as _md
from django.utils.safestring import mark_safe

_EXTENSIONS = ['extra', 'admonition', 'codehilite', 'sane_lists', 'nl2br']
_CONFIG = {
    'codehilite': {
        'guess_lang': False,   # exige a linguagem na cerca (```python)
        'css_class': 'codehilite',
        'linenums': False,
    },
}

# Cercas ```mermaid viram <div class="mermaid"> ANTES do parser de Markdown,
# para o texto do diagrama chegar intacto ao renderizador no navegador.
_MERMAID_RE = re.compile(r'```mermaid[ \t]*\n(.*?)```', re.S)

_ADM_RE = re.compile(r'^\s*(!!!\s+\S.*)$')
_MARCADOR_BLOCO = ('!!!', '---', '#', '```', '===')


def _normalizar_admonitions(texto):
    """Torna as caixas `!!!` tolerantes à indentação que a IA gerar.

    O parser exige o corpo indentado com exatamente 4 espaços. A IA às vezes
    indenta com 8 (o corpo vira bloco de código) ou com 0 (o `!!!` aparece cru).
    Reindenta o corpo para 4, preservando a estrutura relativa das linhas.
    """
    linhas = texto.split('\n')
    out = []
    i = 0
    em_fence = False
    while i < len(linhas):
        linha = linhas[i]
        if linha.lstrip().startswith('```'):
            em_fence = not em_fence
            out.append(linha)
            i += 1
            continue
        m = None if em_fence else _ADM_RE.match(linha)
        if not m:
            out.append(linha)
            i += 1
            continue

        out.append(m.group(1))  # a linha `!!!` sempre na coluna 0
        i += 1
        corpo = []
        if i < len(linhas) and linhas[i].strip() and not linhas[i].startswith(' '):
            # Corpo sem indentação: pega o parágrafo contíguo (até linha vazia),
            # exceto se a linha já for outro marcador de bloco.
            while i < len(linhas) and linhas[i].strip() \
                    and not linhas[i].lstrip().startswith(_MARCADOR_BLOCO):
                corpo.append((linhas[i], len(linhas[i]) - len(linhas[i].lstrip())))
                i += 1
        else:
            # Corpo indentado (qualquer largura), com linhas vazias internas.
            while i < len(linhas):
                atual = linhas[i]
                if atual.strip() == '':
                    prox = next((l for l in linhas[i + 1:] if l.strip()), '')
                    if prox.startswith(' '):
                        corpo.append(('', None))
                        i += 1
                        continue
                    break
                if not atual.startswith(' '):
                    break
                corpo.append((atual, len(atual) - len(atual.lstrip())))
                i += 1
        indents = [ind for _, ind in corpo if ind is not None]
        if indents:
            base = min(indents)
            for l, ind in corpo:
                if ind is None:
                    out.append('')
                else:
                    out.append(' ' * (4 + ind - base) + l.lstrip())
    return '\n'.join(out)


def _extrair_mermaid(texto):
    def _rep(m):
        codigo = m.group(1).strip()
        # Linhas em branco dentro de HTML cru devolvem o parser ao Markdown;
        # o Mermaid não precisa delas.
        codigo = re.sub(r'\n\s*\n', '\n', codigo)
        return '\n<div class="mermaid">' + html.escape(codigo, quote=False) + '</div>\n'
    return _MERMAID_RE.sub(_rep, texto)


def render_md(texto):
    """Converte Markdown em HTML seguro para exibição.

    Suporta caixas de destaque (admonitions): `!!! conceito "Título"`, blocos de
    código com destaque de sintaxe via Pygments (classe .codehilite) e diagramas
    Mermaid (```mermaid), renderizados no navegador.
    """
    if not texto:
        return ''
    texto = _extrair_mermaid(texto)
    texto = _normalizar_admonitions(texto)
    html_out = _md.markdown(texto, extensions=_EXTENSIONS, extension_configs=_CONFIG)
    return mark_safe(html_out)
