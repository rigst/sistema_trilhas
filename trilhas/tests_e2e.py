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

from trilhas.models import Trilha

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

SENHA = "senha-de-teste-bem-longa-123"


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

    pagina.keyboard.press("Escape")
    expect(painel).to_be_hidden()
    expect(botao).to_have_attribute("aria-expanded", "false")
