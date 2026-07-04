from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .services import criar_visitante


@require_POST
def entrar_como_visitante(request):
    """Cria um visitante temporário e autentica a sessão."""
    user, _senha = criar_visitante()
    login(request, user)
    messages.info(
        request,
        'Você entrou como visitante. Suas trilhas são temporárias e expiram por inatividade.',
    )
    return redirect('dashboard')
