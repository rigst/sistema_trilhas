from django.urls import path

from . import views

app_name = 'trilhas'

urlpatterns = [
    path('nova/', views.trilha_criar, name='criar'),
    path('estudar-agora/', views.estudar_agora, name='estudar_agora'),
    path('<int:pk>/', views.trilha_detalhe, name='detalhe'),
    path('<int:pk>/perguntas/', views.perguntas, name='perguntas'),
    path('<int:pk>/status/', views.trilha_status, name='status'),
    path('<int:pk>/excluir/', views.trilha_excluir, name='excluir'),
    path('<int:pk>/certificado/', views.certificado, name='certificado'),
    path('nivel/<int:pk>/', views.nivel_detalhe, name='nivel'),
    path('nivel/<int:nivel_pk>/topico/<int:ordem>/', views.topico, name='topico'),
    path('topico/<int:pk>/status/', views.topico_status, name='topico_status'),
]
