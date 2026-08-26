from django.urls import path

from . import views

app_name = "avaliacoes"

urlpatterns = [
    path("nivel/<int:nivel_pk>/iniciar/", views.avaliacao_iniciar, name="iniciar"),
    path("<int:pk>/", views.avaliacao_detalhe, name="detalhe"),
    path("<int:pk>/submeter/", views.avaliacao_submeter, name="submeter"),
    path("<int:pk>/resultado/", views.avaliacao_resultado, name="resultado"),
    path("<int:pk>/status/", views.avaliacao_status, name="status"),
    # Exercícios de prática
    path("nivel/<int:nivel_pk>/exercicios/", views.exercicios, name="exercicios"),
    path("exercicios/<int:pk>/status/", views.exercicios_status, name="exercicios_status"),
    path("exercicio/<int:pk>/verificar/", views.exercicio_verificar, name="exercicio_verificar"),
    # Revisão espaçada (várias trilhas)
    path("revisao-rapida/<int:nivel_pk>/", views.revisao_rapida, name="revisao_rapida"),
    path("revisar/", views.revisar_iniciar, name="revisar"),
    path("revisar/trilha/<int:trilha_pk>/", views.revisar_trilha_iniciar, name="revisar_trilha"),
    path("revisao/<int:pk>/", views.revisao_detalhe, name="revisao"),
    path("revisao/<int:pk>/status/", views.revisao_status, name="revisao_status"),
    path(
        "revisao/questao/<int:pk>/verificar/",
        views.questao_revisao_verificar,
        name="revisao_verificar",
    ),
    path("retrieval/<int:pk>/verificar/", views.retrieval_verificar, name="retrieval_verificar"),
]
