from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse


class VisitorExpiryMiddleware:
    """Expira automaticamente sessões de visitantes inativos e renova a janela."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            profile = getattr(user, 'profile', None)
            if profile is not None and profile.is_visitor:
                if profile.expirado:
                    logout(request)
                    messages.info(
                        request,
                        'Sua sessão de visitante expirou. Os dados temporários foram encerrados.',
                    )
                    return redirect(reverse('login'))
                # Renova a janela de inatividade a cada acesso.
                profile.renovar_expiracao()
        return self.get_response(request)
