"""Renderização dos slides do vídeo do subtópico com fidelidade total.

Cada seção `---` do conteúdo é convertida no MESMO HTML seguro exibido na leitura
(`mdrender.render_md`), embrulhada numa página 16:9 com o CSS do app, e capturada
por um Chromium headless (Playwright). Assim diagramas Mermaid, código destacado,
tabelas e fotos aparecem no vídeo exatamente como o aluno vê — nada se perde.
"""

import html as _html
import os

from django.conf import settings

from .mdrender import render_md

_BASE = settings.BASE_DIR
_FONTS_CSS = _BASE / "static" / "fonts" / "fonts.css"
_PYGMENTS_CSS = _BASE / "static" / "css" / "pygments.css"
_APP_CSS = _BASE / "static" / "css" / "app.css"
_SLIDE_CSS = _BASE / "static" / "css" / "video_slide.css"
_MERMAID_JS = _BASE / "static" / "js" / "mermaid.min.js"

# Dimensões do slide (16:9). O vídeo sai em 720p e o Ken Burns amplia no máximo
# 1,10×, então 1,5× (1920×1080) já entrega mais pixels do que o quadro final
# consome — capturar em 2× só gerava PNG maior para o ffmpeg reduzir depois.
LARGURA, ALTURA = 1280, 720
ESCALA = 1.5

# Script executado no navegador para renderizar os diagramas Mermaid do slide,
# reproduzindo a configuração de tema do app (base.html).
_MERMAID_RUN = """
async () => {
  const nodes = Array.from(document.querySelectorAll('.mermaid'));
  if (!nodes.length || !window.mermaid) return;
  window.mermaid.initialize({
    startOnLoad: false, theme: 'dark',
    fontFamily: 'Inter, system-ui, sans-serif',
    themeVariables: { fontSize: '15px' },
    flowchart: { useMaxWidth: true }, sequence: { useMaxWidth: true },
    timeline: { useMaxWidth: true }, mindmap: { useMaxWidth: true },
    pie: { useMaxWidth: true }, journey: { useMaxWidth: true },
  });
  nodes.forEach((n) => { n.removeAttribute('data-processed'); });
  try { await window.mermaid.run({ nodes, suppressErrors: true }); } catch (e) {}
}
"""


def _link(path):
    return f'<link rel="stylesheet" href="file://{path}">'


def _documento(corpo_html: str, classe: str = "") -> str:
    """Monta a página HTML completa e autossuficiente de um slide."""
    return (
        '<!doctype html><html lang="pt-br" data-theme="escuro"><head>'
        '<meta charset="utf-8">'
        f"{_link(_FONTS_CSS)}{_link(_PYGMENTS_CSS)}{_link(_APP_CSS)}{_link(_SLIDE_CSS)}"
        '</head><body class="is-reader">'
        f'<div class="vslide {classe}">{corpo_html}</div>'
        "</body></html>"
    )


def _slide_conteudo(secao_md: str, mascote: bool = False) -> str:
    fragmento = render_md(secao_md)
    return _documento(
        f'<div class="vslide-in markdown-body">{fragmento}</div>',
        classe="vslide--mascote" if mascote else "",
    )


def _slide_capa(
    kicker: str, titulo: str, sub: str = "", emblema: str = "", creditos: str = ""
) -> str:
    partes = ['<div class="vslide-in">']
    if emblema:
        partes.append(f'<div class="cover-emblema">{_html.escape(emblema)}</div>')
    if kicker:
        partes.append(f'<div class="cover-kicker">{_html.escape(kicker)}</div>')
    partes.append(f'<h1 class="cover-titulo">{_html.escape(titulo)}</h1>')
    if sub:
        partes.append(f'<p class="cover-sub">{_html.escape(sub)}</p>')
    partes.append('<div class="cover-marca">Trilhas de Estudo</div>')
    if creditos:
        partes.append(f'<div class="cover-cred">{_html.escape(creditos)}</div>')
    partes.append("</div>")
    return _documento("".join(partes), classe="vslide--cover")


def render_slides(paginas: list[dict], out_dir: str, mascote: bool = False) -> list[str]:
    """Renderiza cada página em um PNG e devolve os caminhos, na ordem recebida.

    ``paginas`` é uma lista de dicts:
      - {'tipo': 'conteudo', 'md': <markdown da seção>}
      - {'tipo': 'capa', 'kicker', 'titulo', 'sub', 'emblema'}
    ``mascote`` reserva o canto inferior direito para o apresentador do vídeo
    (as capas não precisam: o conteúdo delas é centralizado).
    Levanta se o Playwright/Chromium não estiver instalado (dependência da feature).
    """
    from playwright.sync_api import sync_playwright  # import tardio (dependência opcional)

    os.makedirs(out_dir, exist_ok=True)
    caminhos = []
    with sync_playwright() as p:
        navegador = p.chromium.launch(args=["--no-sandbox", "--force-color-profile=srgb"])
        pagina = navegador.new_page(
            viewport={"width": LARGURA, "height": ALTURA},
            device_scale_factor=ESCALA,
        )
        for i, pg in enumerate(paginas):
            if pg.get("tipo") == "capa":
                doc = _slide_capa(
                    pg.get("kicker", ""),
                    pg.get("titulo", ""),
                    pg.get("sub", ""),
                    pg.get("emblema", ""),
                    pg.get("creditos", ""),
                )
            else:
                doc = _slide_conteudo(pg.get("md", ""), mascote)
            html_path = os.path.join(out_dir, f"slide_{i:03d}.html")
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(doc)
            # 'load' basta: a página é autossuficiente (CSS, fontes e imagens em
            # file://), então não há requisição de rede para 'networkidle' esperar.
            pagina.goto(f"file://{html_path}", wait_until="load")
            # O Mermaid só entra nos slides que têm diagrama: carregar e rodar a
            # biblioteca em todos custava ~0,5s por slide sem nada para desenhar.
            if 'class="mermaid"' in doc:
                pagina.add_script_tag(path=str(_MERMAID_JS))
                pagina.evaluate(_MERMAID_RUN)
                pagina.wait_for_timeout(300)  # deixa o SVG assentar
            png_path = os.path.join(out_dir, f"slide_{i:03d}.png")
            pagina.screenshot(path=png_path, full_page=True)
            caminhos.append(png_path)
        navegador.close()
    return caminhos
