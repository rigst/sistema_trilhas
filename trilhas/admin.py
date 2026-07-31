from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Nivel, PerguntaDirecionadora, Subtopico, Trilha, VideoNivel


class SubtopicoInline(TabularInline):
    model = Subtopico
    extra = 0


class NivelInline(TabularInline):
    model = Nivel
    extra = 0
    fields = ('ordem', 'titulo', 'faixa', 'status')
    show_change_link = True


class PerguntaInline(TabularInline):
    model = PerguntaDirecionadora
    extra = 0


@admin.register(Trilha)
class TrilhaAdmin(ModelAdmin):
    list_display = ('titulo', 'user', 'status', 'total_niveis', 'niveis_aprovados', 'criada_em')
    list_filter = ('status',)
    search_fields = ('titulo', 'tema_livre', 'user__username')
    inlines = [PerguntaInline, NivelInline]


@admin.register(Nivel)
class NivelAdmin(ModelAdmin):
    list_display = ('titulo', 'trilha', 'ordem', 'faixa', 'status')
    list_filter = ('faixa', 'status')
    search_fields = ('titulo',)
    inlines = [SubtopicoInline]


@admin.register(VideoNivel)
class VideoNivelAdmin(ModelAdmin):
    list_display = ('nivel', 'status', 'progresso_pct', 'duracao_seg', 'atualizado_em')
    list_filter = ('status',)
    search_fields = ('nivel__titulo',)
    readonly_fields = ('criado_em', 'atualizado_em')
