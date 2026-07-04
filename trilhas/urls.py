from django.urls import path

from . import views

app_name = 'trilhas'

urlpatterns = [
    path('nova/', views.trilha_criar, name='criar'),
    path('<int:pk>/', views.trilha_detalhe, name='detalhe'),
    path('<int:pk>/perguntas/', views.perguntas, name='perguntas'),
    path('<int:pk>/status/', views.trilha_status, name='status'),
    path('<int:pk>/excluir/', views.trilha_excluir, name='excluir'),
    path('nivel/<int:pk>/', views.nivel_detalhe, name='nivel'),
    path('nivel/<int:pk>/status/', views.nivel_status, name='nivel_status'),
]
