"""Filtros de Markdown para enunciados e alternativas de questões."""

import re

from django import template
from django.utils.safestring import mark_safe

from trilhas.mdrender import render_md

register = template.Library()

_P_UNICO = re.compile(r"^<p>(.*)</p>$", re.S)


@register.filter(name="md")
def md(texto):
    """Markdown completo (blocos de código, admonitions, Mermaid)."""
    return render_md(texto)


@register.filter(name="md_inline")
def md_inline(texto):
    """Markdown para alternativas: parágrafo único fica inline (sem <p>);
    conteúdo com blocos (código cercado, listas) mantém a forma de bloco."""
    html = render_md(texto)
    if not html:
        return ""
    m = _P_UNICO.match(html.strip())
    if m and "<p>" not in m.group(1):
        return mark_safe(m.group(1))
    return html
