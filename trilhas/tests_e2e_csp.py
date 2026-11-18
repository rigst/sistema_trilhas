"""CSP no navegador: as telas principais abrem sem nenhuma violação.

Teste de unidade só vê o cabeçalho; quem diz se a política quebra a tela é o
navegador — um `<script>` sem nonce, um `onclick=` ou uma imagem de domínio
não listado são bloqueados em silêncio, e a tela simplesmente para de
funcionar. Aqui cada tela é aberta logada (inclui o admin, que tem política
própria) e qualquer `securitypolicyviolation` ou mensagem de CSP no console
reprova.
"""

import pytest
from django.contrib.auth import get_user_model
from playwright.sync_api import expect, sync_playwright

from avaliacoes.models import Exercicio, ListaExercicios
from trilhas.models import Subtopico, Trilha, VideoNivel
from trilhas.tests import criar_trilha

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

SENHA = "senha-de-teste-bem-longa-123"

# Conteúdo com o que a política precisa acomodar: diagrama Mermaid (lib
# carregada sob demanda, <style> injetado no SVG), bloco de código com o botão
# "copiar", e foto embutida da Pexels (img-src). O diagrama abre o primeiro
# card porque o leitor só desenha os diagramas visíveis.
MD_TOPICO = (
    "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
    "---\n\n"
    "Introdução ao assunto.\n\n"
    "![Foto](https://images.pexels.com/photos/1/foto.jpeg)\n\n"
    "---\n\n"
    "```python\nprint('oi')\n```\n"
)

# PNG 1x1: a foto da Pexels é respondida localmente, sem depender de rede.
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)

ESCUTA_CSP = """
document.addEventListener("securitypolicyviolation", (e) => {
  window.__cspViolacao(`${e.violatedDirective} bloqueou ${e.blockedURI || "inline"}`
    + ` em ${e.sourceFile || document.location.pathname}:${e.lineNumber}`);
});
"""


@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def pagina_vigiada(navegador):
    """Página que anota toda violação de CSP (evento do DOM e console)."""
    contexto = navegador.new_context()
    violacoes = []
    contexto.expose_function("__cspViolacao", lambda texto: violacoes.append(texto))
    contexto.add_init_script(ESCUTA_CSP)
    contexto.route(
        "https://images.pexels.com/**",
        lambda rota: rota.fulfill(status=200, content_type="image/png", body=PNG_1X1),
    )
    pagina = contexto.new_page()
    pagina.on(
        "console",
        lambda msg: (
            violacoes.append(f"console: {msg.text}")
            if "Content Security Policy" in msg.text
            else None
        ),
    )
    yield pagina, violacoes
    contexto.close()


def entrar(pagina, base, usuario):
    pagina.goto(f"{base}/login/")
    pagina.fill("#id_username", usuario.username)
    pagina.fill("#id_password", SENHA)
    pagina.click("button[type=submit]")
    pagina.wait_for_load_state("networkidle")


def preparar_dados(usuario):
    trilha = criar_trilha(usuario)
    nivel = trilha.niveis.order_by("ordem").first()
    subs = list(nivel.subtopicos.all())
    for sub in subs:
        sub.conteudo_md = MD_TOPICO
        sub.lido = True
    Subtopico.objects.bulk_update(subs, ["conteudo_md", "lido"])
    VideoNivel.objects.create(
        nivel=nivel, status=VideoNivel.Status.PRONTO, arquivo="/media/videos/x.mp4"
    )
    lista = ListaExercicios.objects.create(nivel=nivel, status=ListaExercicios.Status.PRONTA)
    Exercicio.objects.create(
        lista=lista,
        ordem=1,
        tipo=Exercicio.Tipo.OBJETIVA,
        enunciado_md="Quanto é 1 + 1?",
        alternativas=[{"letra": "A", "texto": "2"}, {"letra": "B", "texto": "3"}],
        gabarito="A",
    )
    rascunho = Trilha.objects.create(
        user=usuario, tema_livre="Rascunho", titulo="Rascunho", status=Trilha.Status.RASCUNHO
    )
    return trilha, nivel, rascunho


def test_telas_principais_sem_violacao_de_csp(live_server, pagina_vigiada):
    pagina, violacoes = pagina_vigiada
    usuario = get_user_model().objects.create_superuser("csp", password=SENHA)
    trilha, nivel, rascunho = preparar_dados(usuario)
    base = live_server.url

    pagina.goto(f"{base}/offline/")
    pagina.wait_for_load_state("networkidle")
    entrar(pagina, base, usuario)

    telas = [
        "/",
        f"/trilhas/{trilha.pk}/",
        f"/trilhas/{rascunho.pk}/perguntas/",
        f"/trilhas/nivel/{nivel.pk}/",
        f"/trilhas/nivel/{nivel.pk}/topico/1/",
        f"/avaliacoes/nivel/{nivel.pk}/exercicios/",
        "/trilhas/salvos/",
        "/trilhas/nova/",
        "/trilhas/sugestoes/nova/",
        "/admin/",
        "/admin/trilhas/trilha/",
        f"/admin/trilhas/trilha/{trilha.pk}/change/",
    ]
    for tela in telas:
        resp = pagina.goto(f"{base}{tela}")
        assert resp.status == 200, (tela, resp.status, pagina.url)
        pagina.wait_for_load_state("networkidle")
    assert violacoes == [], violacoes

    # O tópico desenha o diagrama: prova que o mermaid.min.js rodou sob a CSP.
    pagina.goto(f"{base}/trilhas/nivel/{nivel.pk}/topico/1/")
    # Asserção por locator, não por wait_for_function: este avalia string com
    # eval na página e a própria CSP estrita o barra.
    expect(pagina.locator(".mermaid svg").first).to_be_attached()
    # O admin depende do Alpine.js (unsafe-eval só no prefixo /admin/).
    pagina.goto(f"{base}/admin/")
    expect(pagina.locator("body")).to_be_visible()
    assert pagina.evaluate("typeof window.Alpine") == "object"

    assert violacoes == [], violacoes


def test_acoes_sem_handler_inline_funcionam(live_server, pagina_vigiada):
    """Os antigos onclick/onsubmit viraram data-* tratados em static/js/acoes.js."""
    pagina, violacoes = pagina_vigiada
    usuario = get_user_model().objects.create_user("acoes", password=SENHA)
    trilha = criar_trilha(usuario, titulo="Nome antigo")
    entrar(pagina, live_server.url, usuario)
    pagina.goto(f"{live_server.url}/trilhas/{trilha.pk}/")

    # Renomear: prompt do navegador preenchido e o form enviado.
    pagina.once("dialog", lambda d: d.accept("Nome novo"))
    pagina.click("text=Renomear trilha")
    pagina.wait_for_load_state("networkidle")
    trilha.refresh_from_db()
    assert trilha.titulo == "Nome novo"

    # Desativar: confirm recusado não envia nada.
    pagina.once("dialog", lambda d: d.dismiss())
    pagina.click("text=Desativar trilha")
    pagina.wait_for_timeout(300)
    trilha.refresh_from_db()
    assert trilha.ativa

    # Confirm aceito envia.
    pagina.once("dialog", lambda d: d.accept())
    pagina.click("text=Desativar trilha")
    expect(pagina.locator("text=Reativar trilha")).to_be_visible()
    trilha.refresh_from_db()
    assert not trilha.ativa

    assert violacoes == [], violacoes
