from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("enviar/", views.enviar, name="enviar"),
    path("mensagem/<int:pk>/status/", views.mensagem_status, name="mensagem_status"),
    path("historico/", views.historico, name="historico"),
    path("conversas/", views.conversas, name="conversas"),
    path("limpar/", views.limpar, name="limpar"),
]
