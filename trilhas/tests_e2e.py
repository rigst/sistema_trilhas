"""Testes de ponta a ponta num navegador de verdade.

Ficam sob o marcador `e2e` e fora da suíte padrão (o `-m "not e2e"` do
pytest.ini): exigem Chromium instalado, que o job `e2e` do CI providencia.

Usamos a API síncrona do Playwright direto, e não o plugin pytest-playwright,
porque o `playwright` já é dependência de runtime do projeto (renderiza os
slides do vídeo) — assim a suíte roda aqui sem instalar mais nada.
"""

import pytest
from django.contrib.auth import get_user_model
from playwright.sync_api import expect, sync_playwright

from chat.models import Conversa, Mensagem
from trilhas.models import Trilha

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

SENHA = "senha-de-teste-bem-longa-123"

# O Playwright não abre teclado virtual. Este script, injetado antes da página,
# troca o window.visualViewport por um que o teste encolhe à mão, do mesmo
# jeito que o Chrome do Android faz quando o teclado sobe: o visualViewport
# diminui e o innerHeight (viewport de layout) continua o mesmo.
TECLADO_FALSO = """
(() => {
  const alvo = new EventTarget();
  let altura = window.innerHeight;
  const vv = {
    get height() { return altura; },
    get width() { return window.innerWidth; },
    offsetTop: 0, offsetLeft: 0, pageTop: 0, pageLeft: 0, scale: 1,
    addEventListener: (...a) => alvo.addEventListener(...a),
    removeEventListener: (...a) => alvo.removeEventListener(...a),
  };
  Object.defineProperty(window, "visualViewport", { get: () => vv });
  window.__teclado = (px) => {
    altura = window.innerHeight - px;
    alvo.dispatchEvent(new Event("resize"));
  };
})();
"""


@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def pagina(navegador):
    contexto = navegador.new_context()
    pagina = contexto.new_page()
    yield pagina
    contexto.close()


@pytest.fixture
def celular(navegador):
    contexto = navegador.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    contexto.add_init_script(TECLADO_FALSO)
    pagina = contexto.new_page()
    yield pagina
    contexto.close()


def entrar(pagina, live_server, usuario):
    pagina.goto(f"{live_server.url}/login/")
    pagina.fill("#id_username", usuario.username)
    pagina.fill("#id_password", SENHA)
    pagina.click("button[type=submit]")


def test_login_leva_ao_painel(live_server, pagina):
    usuario = get_user_model().objects.create_user("aluna", password=SENHA)
    entrar(pagina, live_server, usuario)
    # O HUD (nível, diamantes) só existe para quem está autenticado.
    expect(pagina.locator("#nav-hud")).to_be_visible()


def test_aviso_aparece_como_toast_e_o_x_dispensa(live_server, pagina):
    """Cobre o caminho que teste de unidade não alcança: o JS que dispensa.

    Reativar é o gatilho mais barato — desativar abre um confirm() do
    navegador, e reativar não.
    """
    usuario = get_user_model().objects.create_user("aluno", password=SENHA)
    # Sem status a trilha nasce rascunho, e o detalhe redireciona para as
    # perguntas — a página das ações só existe depois que a trilha anda.
    trilha = Trilha.objects.create(
        user=usuario,
        tema_livre="Cálculo",
        titulo="Cálculo",
        status=Trilha.Status.EM_ANDAMENTO,
        ativa=False,
    )
    entrar(pagina, live_server, usuario)

    pagina.goto(f"{live_server.url}/trilhas/{trilha.pk}/")
    pagina.click("text=Reativar trilha")

    toast = pagina.locator(".toast")
    expect(toast).to_be_visible()
    expect(toast).to_contain_text("Trilha reativada")
    # Cartão flutuante, não bloco no fluxo: sai da caixa do <main>.
    assert toast.evaluate("el => getComputedStyle(el.parentElement).position") == "fixed"

    pagina.click(".toast-fechar")
    expect(toast).to_have_count(0)


def test_chat_de_duvidas_abre_e_fecha(live_server, pagina):
    """O painel é JS puro (abrir, Esc, foco), então só o navegador prova.

    A resposta da IA não entra aqui: o que se cobre é o widget flutuante.
    """
    usuario = get_user_model().objects.create_user("curiosa", password=SENHA)
    entrar(pagina, live_server, usuario)

    botao = pagina.locator("#chat-abrir")
    painel = pagina.locator("#chat-painel")
    expect(botao).to_be_visible()
    expect(painel).to_be_hidden()

    botao.click()
    expect(painel).to_be_visible()
    # Flutuante de verdade: sai da caixa do <main> e fica na quina.
    assert painel.evaluate("el => getComputedStyle(el.parentElement).position") == "fixed"
    expect(pagina.locator("#chat-pergunta")).to_be_focused()

    # A lista de conversas salvas troca de lugar com o fluxo da conversa.
    pagina.click("#chat-salvas")
    expect(pagina.locator("#chat-lista")).to_be_visible()
    expect(pagina.locator("#chat-fluxo")).to_be_hidden()
    expect(pagina.locator(".chat-lista-vazia")).to_be_visible()

    # Esc fecha a lista primeiro; só o segundo fecha o painel.
    pagina.keyboard.press("Escape")
    expect(pagina.locator("#chat-lista")).to_be_hidden()
    expect(painel).to_be_visible()

    pagina.keyboard.press("Escape")
    expect(painel).to_be_hidden()
    expect(botao).to_have_attribute("aria-expanded", "false")


def test_no_celular_o_campo_do_chat_fica_acima_do_teclado(live_server, celular):
    """No Chrome do Android o teclado não encolhe o viewport de layout, e o
    painel de tela cheia (100dvh) deixava o campo de escrita embaixo das
    teclas. Com o teclado aberto, o campo tem de ficar inteiro na área visível
    e o fim da conversa logo acima dele."""
    usuario = get_user_model().objects.create_user("celular", password=SENHA)
    conversa = Conversa.objects.create(user=usuario)
    for i in range(30):
        Mensagem.objects.create(
            conversa=conversa,
            papel=Mensagem.Papel.ALUNO if i % 2 == 0 else Mensagem.Papel.IA,
            texto=f"Mensagem número {i} da conversa, para encher o painel.",
            status=Mensagem.Status.PRONTA,
        )
    entrar(celular, live_server, usuario)
    celular.click("#chat-abrir")
    expect(celular.locator(".chat-bolha")).to_have_count(30)

    teclado = 340
    celular.evaluate(f"window.__teclado({teclado})")
    visivel = 844 - teclado

    form = celular.locator("#chat-form").bounding_box()
    # O campo inteiro acima do teclado, não atrás dele.
    assert form["y"] + form["height"] <= visivel + 1, form
    assert form["y"] >= 0

    # A última mensagem fica à vista, logo acima do campo.
    ultima = celular.locator(".chat-bolha").last.bounding_box()
    assert ultima["y"] + ultima["height"] <= form["y"] + 1, (ultima, form)
    assert ultima["y"] >= 0

    # Teclado fecha: o painel volta a ocupar a tela toda.
    celular.evaluate("window.__teclado(0)")
    painel = celular.locator("#chat-painel")
    assert not painel.evaluate("el => el.classList.contains('com-teclado')")
    assert painel.bounding_box()["height"] > visivel


def test_apagar_conversa_so_some_depois_que_o_servidor_confirma(live_server, pagina):
    """Antes, uma falha no POST de limpar tirava o item da tela mesmo assim, e
    a conversa voltava na próxima abertura — exclusão que parecia ignorada."""
    usuario = get_user_model().objects.create_user("apaga", password=SENHA)
    conversa = Conversa.objects.create(user=usuario)
    Mensagem.objects.create(
        conversa=conversa,
        papel=Mensagem.Papel.ALUNO,
        texto="O que é derivada?",
        status=Mensagem.Status.PRONTA,
    )
    entrar(pagina, live_server, usuario)
    pagina.on("dialog", lambda d: d.accept())

    pagina.click("#chat-abrir")
    pagina.click("#chat-salvas")
    item = pagina.locator(".chat-item")
    expect(item).to_have_count(1)

    # Servidor recusa: o item fica e o rodapé avisa.
    pagina.route("**/limpar/", lambda rota: rota.fulfill(status=500, body="erro"))
    pagina.click(".chat-item-apagar")
    expect(pagina.locator("#chat-rodape")).to_contain_text("Não deu para apagar")
    expect(item).to_have_count(1)
    assert Conversa.objects.filter(pk=conversa.pk).exists()

    # Servidor confirma: o item some e a conversa foi mesmo apagada.
    pagina.unroute("**/limpar/")
    pagina.click(".chat-item-apagar")
    expect(item).to_have_count(0)
    expect(pagina.locator("#chat-rodape")).not_to_contain_text("Não deu para apagar")
    assert not Conversa.objects.filter(pk=conversa.pk).exists()
