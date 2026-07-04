from django.contrib import admin

from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'is_visitor', 'expires_at',
        'tokens_usados_mes', 'quota_tokens_mes', 'custo_acumulado',
    )
    list_filter = ('is_visitor',)
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('criado_em', 'atualizado_em')
