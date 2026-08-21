from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Conversa, Mensagem


class MensagemInline(TabularInline):
    model = Mensagem
    extra = 0
    fields = ("papel", "status", "texto", "tokens_entrada", "tokens_saida", "criada_em")
    readonly_fields = ("criada_em",)


@admin.register(Conversa)
class ConversaAdmin(ModelAdmin):
    list_display = ("user", "subtopico", "criada_em", "atualizada_em")
    list_filter = ("criada_em",)
    search_fields = ("user__username", "subtopico__titulo")
    raw_id_fields = ("subtopico",)
    inlines = [MensagemInline]


@admin.register(Mensagem)
class MensagemAdmin(ModelAdmin):
    list_display = ("conversa", "papel", "status", "criada_em")
    list_filter = ("papel", "status", "criada_em")
