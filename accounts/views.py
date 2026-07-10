from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .quota import excedeu_limite
from .services import criar_visitante


def _ip_cliente(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return xff.split(',')[0].strip() or request.META.get('REMOTE_ADDR', '')


@require_POST
def entrar_como_visitante(request):
    """Cria um visitante temporário e autentica a sessão."""
    # Cada visitante nasce com cota de IA própria: sem limite por IP, um
    # script criaria visitantes em massa e consumiria crédito de API à vontade.
    if excedeu_limite(f'visitante:{_ip_cliente(request)}', limite=5, janela_s=3600):
        messages.error(request, 'Muitos acessos de visitante deste endereço. Tente mais tarde.')
        return redirect('login')
    user, _senha = criar_visitante()
    login(request, user)
    messages.info(
        request,
        'Você entrou como visitante. Suas trilhas são temporárias e expiram por inatividade.',
    )
    return redirect('dashboard')
