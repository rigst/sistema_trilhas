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
    html_out = _md.markdown(texto, extensions=_EXTENSIONS, extension_configs=_CONFIG)
    return mark_safe(html_out)
