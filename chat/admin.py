from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Conversa, Mensagem


class MensagemInline(TabularInline):
    model = Mensagem
    extra = 0
    fields = (
        "papel",
        "subtopico",
        "status",
        "texto",
        "tokens_entrada",
        "tokens_saida",
        "criada_em",
    )
    readonly_fields = ("criada_em",)
    raw_id_fields = ("subtopico",)


@admin.register(Conversa)
class ConversaAdmin(ModelAdmin):
    list_display = ("user", "trilha", "criada_em", "atualizada_em")
    list_filter = ("criada_em",)
    search_fields = ("user__username", "trilha__titulo")
    raw_id_fields = ("trilha",)
    inlines = [MensagemInline]


@admin.register(Mensagem)
class MensagemAdmin(ModelAdmin):
    list_display = ("conversa", "papel", "status", "criada_em")
    list_filter = ("papel", "status", "criada_em")
