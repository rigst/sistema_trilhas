from django.urls import path

from . import views

app_name = 'avaliacoes'

urlpatterns = [
    path('nivel/<int:nivel_pk>/iniciar/', views.avaliacao_iniciar, name='iniciar'),
    path('<int:pk>/', views.avaliacao_detalhe, name='detalhe'),
    path('<int:pk>/submeter/', views.avaliacao_submeter, name='submeter'),
    path('<int:pk>/resultado/', views.avaliacao_resultado, name='resultado'),
    path('<int:pk>/status/', views.avaliacao_status, name='status'),
    # Exercícios de prática
    path('nivel/<int:nivel_pk>/exercicios/', views.exercicios, name='exercicios'),
    path('exercicios/<int:pk>/status/', views.exercicios_status, name='exercicios_status'),
    path('exercicio/<int:pk>/verificar/', views.exercicio_verificar, name='exercicio_verificar'),
]
