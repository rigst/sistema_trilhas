"""Renderização de Markdown do conteúdo/exercícios, com callouts e destaque de sintaxe."""

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


def render_md(texto):
    """Converte Markdown em HTML seguro para exibição.

    Suporta caixas de destaque (admonitions): `!!! conceito "Título"` e blocos de
    código com destaque de sintaxe via Pygments (classe .codehilite).
    """
    if not texto:
        return ''
    html = _md.markdown(texto, extensions=_EXTENSIONS, extension_configs=_CONFIG)
    return mark_safe(html)
