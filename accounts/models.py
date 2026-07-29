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
    # Diamantes: moeda para criar trilhas. Começa com 3; ganha +1 a cada
    # XP_POR_DIAMANTE de XP (≈ 10 níveis de jogador ≈ 1 trilha concluída).
    diamantes = models.IntegerField('diamantes', default=3)
    # Quantos diamantes já foram creditados por marcos de XP (evita recrédito).
    diamantes_xp_creditados = models.IntegerField('diamantes creditados por XP', default=0)
    # Idem para os diamantes extras concedidos a cada N níveis de jogador.
    diamantes_nivel_creditados = models.IntegerField('diamantes creditados por nível', default=0)
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

    # Economia de diamantes: 1000 XP ≈ 10 níveis de jogador ≈ 1 trilha concluída.
    # Assim, concluir uma trilha rende ~1 diamante (break-even sustentável).
    XP_POR_DIAMANTE = 1000

    # Diamante extra concedido a cada N níveis de jogador (5, 10, 15…).
    DIAMANTE_A_CADA_N_NIVEIS = 5

    # Curva de nível do jogador: cada nível exige um pouco mais de XP que o
    # anterior (progressão suave), em vez de 100 fixo para todos.
    XP_NIVEL_BASE = 100        # XP para sair do nível 1
    XP_NIVEL_INCREMENTO = 25   # a mais exigido a cada nível seguinte

    def _nivel_info(self):
        """(nível, xp_no_nível, xp_para_completar_o_nível) a partir do XP total,
        usando a curva progressiva. Memoiza pelo XP para não recalcular."""
        xp = max(0, int(self.xp or 0))
        cache = getattr(self, '_nivel_info_cache', None)
        if cache is not None and cache[0] == xp:
            return cache[1]
        restante = xp
        nivel = 1
        req = self.XP_NIVEL_BASE
        while restante >= req:
            restante -= req
            nivel += 1
            req = self.XP_NIVEL_BASE + (nivel - 1) * self.XP_NIVEL_INCREMENTO
        info = (nivel, restante, req)
        self._nivel_info_cache = (xp, info)
        return info

    @property
    def nivel_xp(self):
        """Nível de jogador derivado do XP (curva progressiva)."""
        return self._nivel_info()[0]

    @property
    def xp_no_nivel(self):
        return self._nivel_info()[1]

    @property
    def xp_prox_nivel(self):
        """XP total exigido para completar o nível atual."""
        return self._nivel_info()[2]

    @property
    def xp_no_nivel_pct(self):
        """Progresso (0–100) dentro do nível atual, para a barra de XP."""
        _, no_nivel, req = self._nivel_info()
        return round(no_nivel / req * 100) if req else 0

    def registrar_atividade(self, xp=0):
        """Soma XP e atualiza a sequência (streak) de dias de estudo.

        A lógica de streak/XP-diário/mapa-semanal é read-modify-write (depende do
        estado atual), então serializa-se com um lock de linha dentro de uma
        transação: gerações e ações concorrentes no mesmo perfil não perdem XP
        (lost update). Opera numa cópia recarregada e sincroniza de volta em self.
        """
        from django.db import transaction

        xp = int(xp)
        hoje = timezone.localdate()
        iso = hoje.isocalendar()
        semana_atual = f"{iso[0]}-W{iso[1]:02d}"

        with transaction.atomic():
            p = Profile.objects.select_for_update().get(pk=self.pk)

            if p.ultimo_estudo == hoje:
                pass
            elif p.ultimo_estudo == hoje - timezone.timedelta(days=1):
                p.streak_dias += 1
            else:
                p.streak_dias = 1
            p.ultimo_estudo = hoje
            p.xp = (p.xp or 0) + xp

            # Rastreia XP ganho hoje
            if p.xp_hoje_ref == hoje:
                p.xp_hoje_acc += xp
            else:
                p.xp_hoje_acc = xp
                p.xp_hoje_ref = hoje

            # Mapa semanal: reseta na virada de semana ISO
            if p.semana_ref != semana_atual:
                p.dias_xp_semana = '0000000'
                p.semana_ref = semana_atual
            mask = list((p.dias_xp_semana or '0000000').ljust(7, '0')[:7])
            mask[hoje.weekday()] = '1'  # 0=Seg, 6=Dom
            p.dias_xp_semana = ''.join(mask)

            p.save(update_fields=[
                'xp', 'streak_dias', 'ultimo_estudo',
                'xp_hoje_ref', 'xp_hoje_acc', 'semana_ref', 'dias_xp_semana',
                'atualizado_em',
            ])

        # Sincroniza o estado recém-gravado de volta na instância chamadora.
        for campo in (
            'xp', 'streak_dias', 'ultimo_estudo',
            'xp_hoje_ref', 'xp_hoje_acc', 'semana_ref', 'dias_xp_semana',
        ):
            setattr(self, campo, getattr(p, campo))
        self.__dict__.pop('_nivel_info_cache', None)

        self._creditar_diamantes_por_xp()
        self._creditar_diamantes_por_nivel()

    # -- Diamantes -------------------------------------------------------
    def _creditar_diamantes_por_nivel(self):
        """Credita 1 diamante extra a cada DIAMANTE_A_CADA_N_NIVEIS níveis.

        Idempotente (contador próprio) e atômico, no mesmo molde do crédito
        por marcos de XP — os dois bônus se acumulam.
        """
        merecidos = self.nivel_xp // self.DIAMANTE_A_CADA_N_NIVEIS
        delta = merecidos - (self.diamantes_nivel_creditados or 0)
        if delta <= 0:
            return
        Profile.objects.filter(pk=self.pk).update(
            diamantes=models.F('diamantes') + delta,
            diamantes_nivel_creditados=merecidos,
            atualizado_em=timezone.now(),
        )
        self.diamantes = (self.diamantes or 0) + delta
        self.diamantes_nivel_creditados = merecidos

    def _creditar_diamantes_por_xp(self):
        """Credita 1 diamante a cada XP_POR_DIAMANTE de XP acumulado.

        Idempotente: só credita marcos ainda não creditados. Update atômico
        (F expressions) para não conflitar com gasto/reembolso concorrentes.
        """
        merecidos = int(self.xp) // self.XP_POR_DIAMANTE
        delta = merecidos - (self.diamantes_xp_creditados or 0)
        if delta <= 0:
            return
        Profile.objects.filter(pk=self.pk).update(
            diamantes=models.F('diamantes') + delta,
            diamantes_xp_creditados=merecidos,
            atualizado_em=timezone.now(),
        )
        self.diamantes = (self.diamantes or 0) + delta
        self.diamantes_xp_creditados = merecidos

    @property
    def xp_para_proximo_diamante(self):
        """XP que falta para o próximo diamante por marco de XP."""
        return self.XP_POR_DIAMANTE - (int(self.xp) % self.XP_POR_DIAMANTE)

    def gastar_diamante(self):
        """Debita 1 diamante de forma atômica. Retorna True se havia saldo."""
        atualizados = Profile.objects.filter(pk=self.pk, diamantes__gte=1).update(
            diamantes=models.F('diamantes') - 1,
            atualizado_em=timezone.now(),
        )
        if atualizados:
            self.refresh_from_db(fields=['diamantes'])
            return True
        return False

    def creditar_diamante(self, n=1):
        """Devolve/credita n diamantes de forma atômica (ex.: reembolso)."""
        Profile.objects.filter(pk=self.pk).update(
            diamantes=models.F('diamantes') + int(n),
            atualizado_em=timezone.now(),
        )
        self.refresh_from_db(fields=['diamantes'])

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
