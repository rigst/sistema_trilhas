from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.http import Http404
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST

from .forms import CadastroForm
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


# ---------------------------------------------------------------------------
# Auto-cadastro por e-mail — DESLIGADO por padrão (SIGNUP_ENABLED=False).
# Enquanto a flag for False as três views abaixo respondem 404, então o recurso
# fica pronto mas invisível/inacessível.
# ---------------------------------------------------------------------------

def _cadastro_ativo():
    if not getattr(settings, 'SIGNUP_ENABLED', False):
        raise Http404()


def _enviar_confirmacao(request, user):
    """Envia o e-mail com o link de confirmação da conta."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    contexto = {
        'user': user,
        'protocol': 'https' if request.is_secure() else 'http',
        'domain': request.get_host(),
        'uid': uid,
        'token': token,
    }
    corpo = render_to_string('registration/cadastro_email.html', contexto)
    send_mail(
        subject='Confirme sua conta · Trilhas de Estudo',
        message=corpo,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        recipient_list=[user.email],
        fail_silently=True,
    )


def cadastrar(request):
    """Cria uma conta inativa e dispara o e-mail de confirmação."""
    _cadastro_ativo()
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        # Limite anti-abuso: no máximo 5 tentativas por IP por hora.
        if excedeu_limite(f'cadastro:{_ip_cliente(request)}', limite=5, janela_s=3600):
            messages.error(request, 'Muitas tentativas deste endereço. Tente mais tarde.')
            return redirect('login')
        form = CadastroForm(request.POST)
        if form.is_valid():
            user = form.save()
            _enviar_confirmacao(request, user)
            return redirect('accounts:cadastro_enviado')
    else:
        form = CadastroForm()
    return render(request, 'registration/cadastro.html', {'form': form})


def cadastro_enviado(request):
    _cadastro_ativo()
    return render(request, 'registration/cadastro_enviado.html')


def confirmar_email(request, uidb64, token):
    """Valida o link e ativa a conta, autenticando o usuário."""
    _cadastro_ativo()
    User = get_user_model()
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if user is not None and not user.is_active and \
            default_token_generator.check_token(user, token):
        user.is_active = True
        user.save(update_fields=['is_active'])
        login(request, user)
        messages.success(request, 'Conta confirmada! Bem-vindo(a) ao Trilhas de Estudo.')
        return redirect('dashboard')
    messages.error(request, 'Link de confirmação inválido ou expirado.')
    return redirect('login')
