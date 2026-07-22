from django.urls import path

from . import views

app_name = 'trilhas'

urlpatterns = [
    path('nova/', views.trilha_criar, name='criar'),
    path('salvos/', views.salvos, name='salvos'),
    path('salvos/toggle/', views.salvo_toggle, name='salvo_toggle'),
    path('estudar-agora/', views.estudar_agora, name='estudar_agora'),
    path('mentor/', views.mentor, name='mentor'),
    path('mentor/atualizar/', views.mentor_atualizar, name='mentor_atualizar'),
    path('sugestoes/', views.sugestoes, name='sugestoes'),
    path('sugestoes/<int:pk>/', views.sugestoes_detalhe, name='sugestoes_detalhe'),
    path('sugestoes/<int:pk>/status/', views.sugestoes_status, name='sugestoes_status'),
    path('sugestao/<int:pk>/aceitar/', views.sugestao_aceitar, name='sugestao_aceitar'),
    path('percurso/<int:pk>/status/', views.percurso_status, name='percurso_status'),
    path('<int:pk>/', views.trilha_detalhe, name='detalhe'),
    path('<int:pk>/perguntas/', views.perguntas, name='perguntas'),
    path('<int:pk>/status/', views.trilha_status, name='status'),
    path('<int:pk>/excluir/', views.trilha_excluir, name='excluir'),
    path('<int:pk>/ativa/', views.trilha_alternar_ativa, name='alternar_ativa'),
    path('<int:pk>/nova-capa/', views.trilha_nova_capa, name='nova_capa'),
    path('<int:pk>/certificado/', views.certificado, name='certificado'),
    path('nivel/<int:pk>/', views.nivel_detalhe, name='nivel'),
    path('nivel/<int:nivel_pk>/topico/<int:ordem>/', views.topico, name='topico'),
    path('topico/<int:pk>/status/', views.topico_status, name='topico_status'),
]
