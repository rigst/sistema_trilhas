from django.contrib import admin

from .models import (
    Avaliacao, Exercicio, ListaExercicios, Questao, Resposta, Titulo,
)


class ExercicioInline(admin.TabularInline):
    model = Exercicio
    extra = 0


@admin.register(ListaExercicios)
class ListaExerciciosAdmin(admin.ModelAdmin):
    list_display = ('nivel', 'status', 'criada_em')
    list_filter = ('status',)
    inlines = [ExercicioInline]


class QuestaoInline(admin.TabularInline):
    model = Questao
    extra = 0


@admin.register(Avaliacao)
class AvaliacaoAdmin(admin.ModelAdmin):
    list_display = ('nivel', 'tentativa', 'status', 'nota_final', 'aprovado', 'criada_em')
    list_filter = ('status', 'aprovado')
    inlines = [QuestaoInline]


@admin.register(Titulo)
class TituloAdmin(admin.ModelAdmin):
    list_display = ('nome', 'trilha', 'faixa', 'concedido_em')
    search_fields = ('nome',)


admin.site.register(Resposta)
