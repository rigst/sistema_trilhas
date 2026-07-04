from django.contrib import admin

from .models import Nivel, PerguntaDirecionadora, Subtopico, Trilha


class SubtopicoInline(admin.TabularInline):
    model = Subtopico
    extra = 0


class NivelInline(admin.TabularInline):
    model = Nivel
    extra = 0
    fields = ('ordem', 'titulo', 'faixa', 'status')
    show_change_link = True


class PerguntaInline(admin.TabularInline):
    model = PerguntaDirecionadora
    extra = 0


@admin.register(Trilha)
class TrilhaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'user', 'status', 'total_niveis', 'niveis_aprovados', 'criada_em')
    list_filter = ('status',)
    search_fields = ('titulo', 'tema_livre', 'user__username')
    inlines = [PerguntaInline, NivelInline]


@admin.register(Nivel)
class NivelAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'trilha', 'ordem', 'faixa', 'status')
    list_filter = ('faixa', 'status')
    search_fields = ('titulo',)
    inlines = [SubtopicoInline]
