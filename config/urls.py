"""URL configuration — Trilhas de Estudo com IA."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView

from legal import views as legal_views
from trilhas import views as trilhas_views

urlpatterns = [
    path("admin/", admin.site.urls),
    # Service worker servido da raiz para ter escopo "/" (PWA).
    path(
        "sw.js",
        TemplateView.as_view(
            template_name="sw.js",
            content_type="application/javascript",
        ),
        name="sw",
    ),
    path("accounts/", include("accounts.urls")),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Página estática de fallback offline (pré-cacheada pelo service worker).
    path("offline/", TemplateView.as_view(template_name="offline.html"), name="offline"),
    # Páginas legais (LGPD): acessíveis sem login. O texto vem do banco (app
    # `legal`), versionado — os nomes de rota seguem os mesmos de antes.
    path("privacidade/", legal_views.privacidade, name="privacidade"),
    path("termos/", legal_views.termos, name="termos"),
    path("legal/", include("legal.urls")),
    # Reset de senha — views nativas do Django; templates em registration/.
    path(
        "senha/reset/",
        auth_views.PasswordResetView.as_view(
            email_template_name="registration/password_reset_email.html",
            subject_template_name="registration/password_reset_subject.txt",
        ),
        name="password_reset",
    ),
    path(
        "senha/reset/enviado/",
        auth_views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "senha/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "senha/reset/completo/",
        auth_views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("", trilhas_views.dashboard, name="dashboard"),
    path("trilhas/", include("trilhas.urls")),
    path("avaliacoes/", include("avaliacoes.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
