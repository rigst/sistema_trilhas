"""Cabeçalhos de segurança que o SecurityMiddleware do Django não emite.

- Permissions-Policy: desliga no navegador as APIs poderosas que o app não usa
  (câmera, microfone, localização, pagamento, USB) e o FLoC. Nenhuma tela grava
  áudio ou vídeo: o vídeo do tópico é gerado no servidor (edge-tts + Playwright)
  e só tocado aqui num `<video>`, que não depende de permissão alguma.
- Content-Security-Policy: estrita no app, com uma exceção (`'unsafe-eval'`)
  presa ao prefixo do admin, no padrão do sistema_orcamentos.
"""

import secrets

from django.conf import settings


class CabecalhosSegurancaMiddleware:
    """Aplica Permissions-Policy e CSP, sem sobrescrever o que a view já definiu.

    O admin não cabe na política estrita porque o tema (django-unfold) usa
    Alpine.js, que compila as expressões de `x-data`/`x-init` com
    `new Function()` e portanto exige `'unsafe-eval'`. Em vez de afrouxar o
    site inteiro por causa do painel, a exceção fica restrita ao prefixo do
    admin — que só quem tem `is_staff` alcança.

    O nonce fica em `request.csp_nonce` para os `<script>` inline dos templates.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _politica(request):
        if request.path.startswith(settings.ADMIN_PATH_PREFIX):
            return settings.CONTENT_SECURITY_POLICY_ADMIN or settings.CONTENT_SECURITY_POLICY
        return settings.CONTENT_SECURITY_POLICY

    def __call__(self, request):
        nonce = secrets.token_urlsafe(16)
        request.csp_nonce = nonce
        response = self.get_response(request)
        if settings.PERMISSIONS_POLICY:
            response.setdefault("Permissions-Policy", settings.PERMISSIONS_POLICY)
        if settings.ENABLE_CSP:
            response.setdefault(
                "Content-Security-Policy", self._politica(request).format(nonce=nonce)
            )
        return response
