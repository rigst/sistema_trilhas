"""Endpoints do chat de dúvidas (JSON, consumidos pelo painel flutuante)."""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from accounts.quota import bloqueio_chat
from ai import tasks as ai_tasks
from trilhas.mdrender import render_md
from trilhas.models import Subtopico

from .models import Conversa, Mensagem, chave_parcial

# Estimativa grosseira do que uma pergunta consome (contexto do tópico +
# histórico + resposta). Serve só para barrar quem já não tem esse saldo.
TOKENS_POR_PERGUNTA = 3000


def _exige_chat_ligado():
    if not getattr(settings, "CHAT_ENABLED", True):
        raise Http404("Chat desativado.")


def _conversa_do_pedido(request, criar=False):
    """Conversa do subtópico informado (ou a geral).

    O subtópico vem do cliente, então a posse é conferida no próprio filtro —
    404 para o que não é do usuário, como no resto do app. Só o envio cria a
    conversa: abrir o painel numa página nova não pode deixar linha vazia para
    trás em cada visita.
    """
    bruto = (request.POST.get("subtopico") or request.GET.get("subtopico") or "").strip()
    subtopico = None
    if bruto.isdigit():
        subtopico = get_object_or_404(
            Subtopico.objects.select_related("nivel__trilha"),
            pk=int(bruto),
            nivel__trilha__user=request.user,
        )
    if criar:
        conversa, _ = Conversa.objects.get_or_create(user=request.user, subtopico=subtopico)
        return conversa
    return Conversa.objects.filter(user=request.user, subtopico=subtopico).first()


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
    feita = Mensagem.objects.create(
        conversa=conversa,
        papel=Mensagem.Papel.ALUNO,
        texto=pergunta,
        status=Mensagem.Status.PRONTA,
    )
    resposta = Mensagem.objects.create(
        conversa=conversa,
        papel=Mensagem.Papel.IA,
        status=Mensagem.Status.GERANDO,
    )
    ai_tasks.task_responder_duvida.delay(resposta.pk)
    return JsonResponse({"pergunta": _payload(feita), "resposta": _payload(resposta)})


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


@login_required
def historico(request):
    """Falas já trocadas na conversa desta página, para reabrir o painel."""
    _exige_chat_ligado()
    conversa = _conversa_do_pedido(request)
    mensagens = list(conversa.mensagens.all()) if conversa else []
    profile = getattr(request.user, "profile", None)
    return JsonResponse(
        {
            "mensagens": [_payload(m) for m in mensagens],
            "restantes": profile.chat_tokens_restantes if profile else 0,
        }
    )


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
