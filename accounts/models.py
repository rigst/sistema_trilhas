from django.conf import settings
from django.db import models
from django.utils import timezone


class Profile(models.Model):
    """Perfil do usuário com controle de visitante e quota de uso de IA."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    is_visitor = models.BooleanField('é visitante', default=False)
    expires_at = models.DateTimeField('expira em', null=True, blank=True)

    # Quota de uso de IA (tokens no mês corrente)
    tokens_usados_mes = models.BigIntegerField('tokens usados no mês', default=0)
    quota_tokens_mes = models.BigIntegerField('quota mensal de tokens', default=0)
    quota_ref = models.DateField('mês de referência da quota', default=timezone.localdate)

    custo_acumulado = models.DecimalField(
        'custo acumulado (USD)', max_digits=12, decimal_places=4, default=0
    )

    # Gamificação
    xp = models.BigIntegerField('XP', default=0)
    streak_dias = models.PositiveIntegerField('sequência de dias', default=0)
    ultimo_estudo = models.DateField('último dia de estudo', null=True, blank=True)
    lembrete_streak_em = models.DateField(
        'último lembrete de ofensiva enviado em', null=True, blank=True
    )

    # XP diário e mapa semanal
    xp_hoje_ref = models.DateField('ref XP diário', null=True, blank=True)
    xp_hoje_acc = models.IntegerField('XP ganho hoje', default=0)
    semana_ref = models.CharField('semana de ref', max_length=8, blank=True)  # "YYYY-Www"
    dias_xp_semana = models.CharField('dias com XP na semana', max_length=7, default='0000000')

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'perfil'
        verbose_name_plural = 'perfis'

    def __str__(self):
        return f'Perfil de {self.user}'

    # -- Quota -----------------------------------------------------------
    def _rollover_se_novo_mes(self):
        hoje = timezone.localdate()
        if (self.quota_ref.year, self.quota_ref.month) != (hoje.year, hoje.month):
            self.tokens_usados_mes = 0
            self.quota_ref = hoje
            self.save(update_fields=['tokens_usados_mes', 'quota_ref', 'atualizado_em'])

    @property
    def tokens_restantes(self):
        self._rollover_se_novo_mes()
        return max(self.quota_tokens_mes - self.tokens_usados_mes, 0)

    def tem_quota(self, tokens_estimados=0):
        return self.tokens_restantes >= tokens_estimados

    @property
    def quota_pct_usado(self):
        """% da quota mensal de tokens já consumido (0–100), para a barra na UI."""
        if not self.quota_tokens_mes:
            return 0
        self._rollover_se_novo_mes()
        return min(round(self.tokens_usados_mes / self.quota_tokens_mes * 100), 100)

    def registrar_uso(self, input_tokens, output_tokens, custo_usd):
        """Debita o uso de IA da quota do usuário.

        Débito atômico no banco (F expressions): gerações concorrentes não se
        sobrescrevem (lost update) — cada task Celery soma sobre o valor atual.
        """
        self._rollover_se_novo_mes()
        total = int(input_tokens) + int(output_tokens)
        Profile.objects.filter(pk=self.pk).update(
            tokens_usados_mes=models.F('tokens_usados_mes') + total,
            custo_acumulado=models.F('custo_acumulado') + custo_usd,
            atualizado_em=timezone.now(),
        )
        self.refresh_from_db(fields=['tokens_usados_mes', 'custo_acumulado'])

    # -- Gamificação -----------------------------------------------------
    # Faixa de XP por atividade
    XP_TOPICO = 10            # ler um tópico (primeira vez)
    XP_EXERCICIO = 5          # responder uma questão (exercício, revisão)
    XP_AVALIACAO = 20         # submeter uma avaliação (por tentativa)
    XP_APROVACAO = 50         # ser aprovado num nível
    XP_TRILHA_CONCLUIDA = 100 # completar todos os níveis de uma trilha

    @property
    def nivel_xp(self):
        """Nível de jogador derivado do XP (100 XP por nível)."""
        return self.xp // 100 + 1

    @property
    def xp_no_nivel(self):
        return self.xp % 100

    def registrar_atividade(self, xp=0):
        """Soma XP e atualiza a sequência (streak) de dias de estudo."""
        hoje = timezone.localdate()
        if self.ultimo_estudo == hoje:
            pass
        elif self.ultimo_estudo == hoje - timezone.timedelta(days=1):
            self.streak_dias += 1
        else:
            self.streak_dias = 1
        self.ultimo_estudo = hoje
        self.xp = (self.xp or 0) + int(xp)

        # Rastreia XP ganho hoje
        if self.xp_hoje_ref == hoje:
            self.xp_hoje_acc += int(xp)
        else:
            self.xp_hoje_acc = int(xp)
            self.xp_hoje_ref = hoje

        # Mapa semanal: reseta na virada de semana ISO
        iso = hoje.isocalendar()
        semana_atual = f"{iso[0]}-W{iso[1]:02d}"
        if self.semana_ref != semana_atual:
            self.dias_xp_semana = '0000000'
            self.semana_ref = semana_atual
        mask = list((self.dias_xp_semana or '0000000').ljust(7, '0')[:7])
        mask[hoje.weekday()] = '1'  # 0=Seg, 6=Dom
        self.dias_xp_semana = ''.join(mask)

        self.save(update_fields=[
            'xp', 'streak_dias', 'ultimo_estudo',
            'xp_hoje_ref', 'xp_hoje_acc', 'semana_ref', 'dias_xp_semana',
            'atualizado_em',
        ])

    # -- Visitante -------------------------------------------------------
    @property
    def expirado(self):
        return bool(self.is_visitor and self.expires_at and self.expires_at < timezone.now())

    def renovar_expiracao(self):
        if not self.is_visitor:
            return
        horas = getattr(settings, 'VISITOR_EXPIRY_HOURS', 48)
        novo = timezone.now() + timezone.timedelta(hours=horas)
        # Evita um UPDATE por request: só grava se a janela avançou >30 min
        # (o middleware chama isto a cada acesso do visitante).
        if self.expires_at and (novo - self.expires_at) < timezone.timedelta(minutes=30):
            return
        self.expires_at = novo
        self.save(update_fields=['expires_at', 'atualizado_em'])
