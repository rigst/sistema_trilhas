"""Endpoints do chat de dúvidas (JSON, consumidos pelo painel flutuante)."""

import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.humanize.templatetags.humanize import naturaltime
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.text import Truncator
from django.views.decorators.http import require_GET, require_POST

from accounts.quota import bloqueio_chat
from ai import tasks as ai_tasks
from trilhas.mdrender import render_md
from trilhas.models import Subtopico, Trilha

from .models import Conversa, Mensagem, chave_parcial

# Estimativa grosseira do que uma pergunta consome (contexto do tópico +
# histórico + resposta). Serve só para barrar quem já não tem esse saldo.
TOKENS_POR_PERGUNTA = 3000

# Marcação de Markdown na prévia da lista de conversas: ali o texto é exibido
# como texto puro, e "**Atraso** — o tempo…" fica com os asteriscos à mostra.
_MARCACAO_MD = re.compile(r"[*_`~#>]+|\[(.*?)\]\(.*?\)")


def _previa(texto):
    limpo = _MARCACAO_MD.sub(lambda m: m.group(1) or "", texto).strip()
    return Truncator(" ".join(limpo.split())).chars(90)


def _exige_chat_ligado():
    if not getattr(settings, "CHAT_ENABLED", True):
        raise Http404("Chat desativado.")


def _param(request, nome):
    return (request.POST.get(nome) or request.GET.get(nome) or "").strip()


def _conversa_do_pedido(request, criar=False):
    """Conversa pedida: por id (uma salva, reaberta pela lista) ou pelo
    subtópico da página — e, sem nenhum dos dois, a conversa geral.

    Os dois vêm do cliente, então a posse é conferida no próprio filtro: 404
    para o que não é do usuário, como no resto do app. Só o envio cria a
    conversa; abrir o painel numa página nova não pode deixar linha vazia para
    trás em cada visita.
    """
    escolhida = _param(request, "conversa")
    if escolhida.isdigit():
        return get_object_or_404(
            Conversa.objects.select_related("trilha"),
            pk=int(escolhida),
            user=request.user,
        )

    trilha = _trilha_da_pagina(request)
    if criar:
        conversa, _ = Conversa.objects.get_or_create(user=request.user, trilha=trilha)
        return conversa
    return Conversa.objects.filter(user=request.user, trilha=trilha).first()


def _trilha_da_pagina(request):
    """Trilha em que o aluno está, pelo id que o template põe no `body`.

    Aceita também o subtópico e sobe até a trilha dele: assim uma página que
    conhece só o tópico não precisa passar os dois.
    """
    bruto = _param(request, "trilha")
    if bruto.isdigit():
        return get_object_or_404(Trilha, pk=int(bruto), user=request.user)
    sub = _subtopico_da_pagina(request)
    return sub.nivel.trilha if sub else None


def _subtopico_da_pagina(request):
    """Página de leitura aberta, se houver — é o material que vai no contexto."""
    bruto = _param(request, "subtopico")
    if not bruto.isdigit():
        return None
    return get_object_or_404(
        Subtopico.objects.select_related("nivel__trilha"),
        pk=int(bruto),
        nivel__trilha__user=request.user,
    )


def _payload(mensagem):
    """Como uma fala aparece para o painel. O texto da IA já vai renderizado:
    Markdown -> allowlist do nh3 -> HTML. Texto cru nunca chega ao innerHTML."""
    dados = {
        "id": mensagem.pk,
        "papel": mensagem.papel,
        "status": mensagem.status,
        "html": render_md(mensagem.texto) if mensagem.texto else "",
    }
    if mensagem.status == Mensagem.Status.ERRO:
        # A causa fica no banco para depuração; o aluno vê o que dá para fazer.
        dados["erro"] = "Não consegui responder agora. Tente perguntar de novo."
    return dados


@login_required
@require_POST
def enviar(request):
    """Grava a pergunta, agenda a resposta e devolve o id para o polling."""
    _exige_chat_ligado()

    erro = bloqueio_chat(request.user, tokens_estimados=TOKENS_POR_PERGUNTA)
    if erro:
        return JsonResponse({"erro": erro}, status=429)

    pergunta = (request.POST.get("pergunta") or "").strip()
    if not pergunta:
        return JsonResponse({"erro": "Escreva sua dúvida."}, status=400)
    limite = getattr(settings, "CHAT_MAX_CHARS_PERGUNTA", 1000)
    if len(pergunta) > limite:
        return JsonResponse(
            {"erro": f"Pergunta longa demais (máximo {limite} caracteres)."}, status=400
        )

    conversa = _conversa_do_pedido(request, criar=True)
    # A conversa é da trilha inteira, mas cada pergunta lembra de que página
    # saiu: é dali que sai o material mandado ao modelo.
    aqui = _subtopico_da_pagina(request)
    feita = Mensagem.objects.create(
        conversa=conversa,
        subtopico=aqui,
        papel=Mensagem.Papel.ALUNO,
        texto=pergunta,
        status=Mensagem.Status.PRONTA,
    )
    resposta = Mensagem.objects.create(
        conversa=conversa,
        subtopico=aqui,
        papel=Mensagem.Papel.IA,
        status=Mensagem.Status.GERANDO,
    )
    # Marca a atividade já no envio: se a resposta falhar, a conversa ainda
    # sobe na lista de salvas — perguntar É atividade.
    conversa.save(update_fields=["atualizada_em"])
    ai_tasks.task_responder_duvida.delay(resposta.pk)
    return JsonResponse({"pergunta": _payload(feita), "resposta": _payload(resposta)})


@require_GET
@login_required
def mensagem_status(request, pk):
    """Polling de uma resposta em andamento."""
    _exige_chat_ligado()
    mensagem = get_object_or_404(
        Mensagem.objects.select_related("conversa"),
        pk=pk,
        conversa__user=request.user,
    )
    dados = _payload(mensagem)
    if mensagem.status == Mensagem.Status.GERANDO:
        # Texto ainda chegando: vai como texto puro, sem passar por Markdown —
        # um bloco de código pela metade não tem como ser renderizado.
        dados["parcial"] = cache.get(chave_parcial(mensagem.pk)) or ""
    return JsonResponse(dados)


@require_GET
@login_required
def historico(request):
    """Falas já trocadas na conversa desta página, para reabrir o painel."""
    _exige_chat_ligado()
    conversa = _conversa_do_pedido(request)
    mensagens = list(conversa.mensagens.all()) if conversa else []
    profile = getattr(request.user, "profile", None)
    return JsonResponse(
        {
            "conversa": conversa.pk if conversa else None,
            "rotulo": conversa.rotulo if conversa else "",
            "contexto": conversa.contexto if conversa else "",
            "mensagens": [_payload(m) for m in mensagens],
            "restantes": profile.chat_tokens_restantes if profile else 0,
        }
    )


@require_GET
@login_required
def conversas(request):
    """Conversas salvas do aluno, para reabrir e continuar.

    Só as que têm alguma fala: uma conversa criada e abandonada no meio do
    envio não é histórico, é lixo de tela.
    """
    _exige_chat_ligado()
    fila = (
        Conversa.objects.filter(user=request.user)
        .select_related("trilha")
        .prefetch_related("mensagens__subtopico")
        .order_by("-atualizada_em")[:50]
    )
    itens = []
    for conversa in fila:
        falas = [m for m in conversa.mensagens.all() if m.texto]
        if not falas:
            continue
        itens.append(
            {
                "id": conversa.pk,
                "rotulo": conversa.rotulo,
                "contexto": conversa.contexto,
                "previa": _previa(falas[-1].texto),
                "quando": naturaltime(conversa.atualizada_em),
                "total": len(falas),
                "url": (
                    reverse("trilhas:detalhe", args=[conversa.trilha_id])
                    if conversa.trilha_id
                    else ""
                ),
            }
        )
    return JsonResponse({"conversas": itens})


@login_required
@require_POST
def limpar(request):
    """Apaga a conversa desta página (é o 'recomeçar' e também o direito de
    eliminação da LGPD, no lugar onde o dado foi criado)."""
    _exige_chat_ligado()
    conversa = _conversa_do_pedido(request)
    if conversa is not None:
        conversa.delete()
    return JsonResponse({"ok": True})
