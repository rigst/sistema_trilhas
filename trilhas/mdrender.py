"""Renderização de Markdown do conteúdo/exercícios, com callouts, destaque de
sintaxe e diagramas Mermaid."""

import html
import re

import markdown as _md
import nh3
from django.core.cache import cache
from django.utils.safestring import mark_safe

_EXTENSIONS = ["extra", "admonition", "codehilite", "sane_lists", "nl2br"]
_CONFIG = {
    "codehilite": {
        "guess_lang": False,  # exige a linguagem na cerca (```python)
        "css_class": "codehilite",
        "linenums": False,
    },
}

# Cercas ```mermaid viram <div class="mermaid"> ANTES do parser de Markdown,
# para o texto do diagrama chegar intacto ao renderizador no navegador.
_MERMAID_RE = re.compile(r"```mermaid[ \t]*\n(.*?)```", re.S)

_ADM_RE = re.compile(r"^\s*(!!!\s+\S.*)$")
_MARCADOR_BLOCO = ("!!!", "---", "#", "```", "===")


def _normalizar_admonitions(texto):
    """Torna as caixas `!!!` tolerantes à indentação que a IA gerar.

    O parser exige o corpo indentado com exatamente 4 espaços. A IA às vezes
    indenta com 8 (o corpo vira bloco de código) ou com 0 (o `!!!` aparece cru).
    Reindenta o corpo para 4, preservando a estrutura relativa das linhas.
    """
    linhas = texto.split("\n")
    out = []
    i = 0
    em_fence = False
    while i < len(linhas):
        linha = linhas[i]
        if linha.lstrip().startswith("```"):
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
        if i < len(linhas) and linhas[i].strip() and not linhas[i].startswith(" "):
            # Corpo sem indentação: pega o parágrafo contíguo (até linha vazia),
            # exceto se a linha já for outro marcador de bloco.
            while (
                i < len(linhas)
                and linhas[i].strip()
                and not linhas[i].lstrip().startswith(_MARCADOR_BLOCO)
            ):
                corpo.append((linhas[i], len(linhas[i]) - len(linhas[i].lstrip())))
                i += 1
        else:
            # Corpo indentado (qualquer largura), com linhas vazias internas.
            # Fences ```lang dentro do corpo são convertidas para :::lang (8 espaços)
            # porque o fenced_code preprocessor não enxerga fences dentro de admonitions.
            in_inner_fence = False
            inner_lang = ""
            while i < len(linhas):
                atual = linhas[i]
                stripped = atual.lstrip()
                if atual.strip() == "":
                    if in_inner_fence:
                        # linha vazia dentro de fence: preserva
                        corpo.append(("", None))
                        i += 1
                        continue
                    prox = next((linha for linha in linhas[i + 1 :] if linha.strip()), "")
                    if prox.startswith(" "):
                        corpo.append(("", None))
                        i += 1
                        continue
                    break
                if not atual.startswith(" ") and not in_inner_fence:
                    break
                if stripped.startswith("```"):
                    if not in_inner_fence:
                        in_inner_fence = True
                        inner_lang = stripped[3:].strip()
                        lang_tag = inner_lang if inner_lang else "text"
                        # ind=-1 significa "8 espaços fixos" — bypass da normalização relativa
                        corpo.append(("", None))
                        corpo.append((":::" + lang_tag, -1))
                    else:
                        in_inner_fence = False
                        inner_lang = ""
                    i += 1
                    continue
                if in_inner_fence:
                    corpo.append((stripped, -1))
                else:
                    corpo.append((atual, len(atual) - len(atual.lstrip())))
                i += 1
        indents = [ind for _, ind in corpo if ind is not None and ind >= 0]
        base = min(indents) if indents else 0
        for linha, ind in corpo:
            if ind is None:
                out.append("")
            elif ind == -1:
                # código dentro de fence em admonition: sempre 8 espaços fixos
                out.append("        " + linha)
            else:
                out.append(" " * (4 + ind - base) + linha.lstrip())
    return "\n".join(out)


def _extrair_mermaid(texto):
    def _rep(m):
        codigo = m.group(1).strip()
        # Linhas em branco dentro de HTML cru devolvem o parser ao Markdown;
        # o Mermaid não precisa delas.
        codigo = re.sub(r"\n\s*\n", "\n", codigo)
        # A IA costuma representar quebras de linha dentro dos rótulos do
        # Mermaid como os dois caracteres literais ``\\n``. O Mermaid não os
        # interpreta como quebra visual e acaba exibindo "\\n" no diagrama;
        # convertemos somente dentro do código Mermaid (e não no Markdown ou
        # em exemplos de Python) para a forma suportada pelo renderizador.
        codigo = codigo.replace(r"\n", "<br/>")
        return '\n<div class="mermaid">' + html.escape(codigo, quote=False) + "</div>\n"

    return _MERMAID_RE.sub(_rep, texto)


# O python-markdown NÃO escapa HTML cru (e a extensão `extra` permite HTML
# inline), então todo HTML gerado passa por uma allowlist antes do mark_safe.
# O conteúdo vem da IA a partir de tema livre do usuário — prompt injection
# não pode virar <script> armazenado.
_TAGS_PERMITIDAS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "caption",
    "code",
    "dd",
    "del",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "ins",
    "kbd",
    "li",
    "mark",
    "ol",
    "p",
    "pre",
    "q",
    "s",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_ATTRS_PERMITIDOS = {
    "*": {"class", "title"},
    # data-src preserva a fonte do diagrama Mermaid em cards salvos; o SVG
    # renderizado e o data-processed são descartados e o cliente re-renderiza.
    "div": {"data-src"},
    # 'rel' é gerido por link_rel='noopener noreferrer' no nh3.clean.
    "a": {"href", "target"},
    "img": {"src", "alt", "loading", "width", "height"},
    "td": {"colspan", "rowspan", "align", "data-label"},
    "th": {"colspan", "rowspan", "align", "scope"},
    "ol": {"start", "type"},
}
_URLS_PERMITIDAS = {"http", "https", "mailto"}


def _sanitizar(html_out):
    return nh3.clean(
        html_out,
        tags=_TAGS_PERMITIDAS,
        attributes=_ATTRS_PERMITIDOS,
        url_schemes=_URLS_PERMITIDAS,
        link_rel="noopener noreferrer",
    )


def render_md(texto):
    """Converte Markdown em HTML seguro para exibição.

    Suporta caixas de destaque (admonitions): `!!! conceito "Título"`, blocos de
    código com destaque de sintaxe via Pygments (classe .codehilite) e diagramas
    Mermaid (```mermaid), renderizados no navegador. A saída é sanitizada por
    allowlist (nh3) — HTML cru vindo do modelo nunca chega ativo ao DOM.
    """
    if not texto:
        return ""
    texto = _extrair_mermaid(texto)
    texto = _normalizar_admonitions(texto)
    html_out = _md.markdown(texto, extensions=_EXTENSIONS, extension_configs=_CONFIG)
    return mark_safe(_sanitizar(html_out))


# TTL longo: o conteúdo do subtópico é imutável depois de gerado; a chave inclui
# gerado_em, então uma eventual regeração invalida naturalmente o cache.
_CACHE_RENDER_TTL = 60 * 60 * 24 * 30


def render_subtopico(subtopico):
    """render_md do conteúdo do subtópico, memorizado no Redis por (pk, gerado_em).

    Markdown + Pygments + regex + sanitização são caros e rodavam a cada request
    sobre conteúdo que nunca muda. Cacheia o HTML final já seguro.
    """
    if not subtopico.conteudo_md:
        return ""
    carimbo = subtopico.gerado_em.isoformat() if subtopico.gerado_em else "sem"
    chave = f"md:sub:{subtopico.pk}:{carimbo}"
    cached = cache.get(chave)
    if cached is not None:
        return mark_safe(cached)
    html_out = render_md(subtopico.conteudo_md)
    cache.set(chave, str(html_out), _CACHE_RENDER_TTL)
    return html_out
