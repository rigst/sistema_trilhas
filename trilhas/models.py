from django.conf import settings
from django.db import models


# Faixa do nível → patamar da medalha (metal) conquistada na trilha.
FAIXA_TIER = {
    'iniciante': ('bronze', 'Bronze'),
    'intermediario': ('prata', 'Prata'),
    'avancado': ('ouro', 'Ouro'),
    'especialista': ('platina', 'Platina'),
    'mestre': ('diamante', 'Diamante'),
}

# Emblemas usados quando a IA não definiu um para a trilha (determinístico por id).
EMBLEMAS_FALLBACK = [
    '📐', '🧠', '💻', '🎨', '🎼', '🔬', '📈', '🌍', '⚗️', '🧬', '🛠️',
    '📚', '🏛️', '⚙️', '🧮', '🩺', '⚖️', '🎯', '🚀', '🔭', '🧭', '💡',
]


class Trilha(models.Model):
    """Uma trilha de estudo pessoal, do básico ao avançado, gerada por IA."""

    class Status(models.TextChoices):
        RASCUNHO = 'rascunho', 'Rascunho'
        GERANDO_PERGUNTAS = 'gerando_perguntas', 'Gerando perguntas'
        AGUARDANDO_RESPOSTAS = 'aguardando_respostas', 'Aguardando respostas'
        GERANDO_SUMARIO = 'gerando_sumario', 'Gerando sumário'
        SUMARIO_GERADO = 'sumario_gerado', 'Sumário gerado'
        EM_ANDAMENTO = 'em_andamento', 'Em andamento'
        CONCLUIDA = 'concluida', 'Concluída'
        ERRO = 'erro', 'Erro'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='trilhas'
    )
    tema_livre = models.TextField('tema (descrição livre)')
    titulo = models.CharField('título', max_length=200, blank=True)
    descricao = models.TextField('descrição', blank=True)
    objetivos = models.JSONField('objetivos de aprendizagem', default=list, blank=True)
    emblema = models.CharField('emblema (emoji)', max_length=8, blank=True)

    status = models.CharField(
        max_length=25, choices=Status.choices, default=Status.RASCUNHO, db_index=True
    )
    nota_minima_aprovacao = models.FloatField('nota mínima de aprovação', default=7.0)

    erro = models.TextField('mensagem de erro', blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'trilha'
        verbose_name_plural = 'trilhas'
        ordering = ['-criada_em']

    def __str__(self):
        return self.titulo or self.tema_livre[:60]

    # -- Progresso / gamificação ----------------------------------------
    @property
    def total_niveis(self):
        return self.niveis.count()

    @property
    def niveis_aprovados(self):
        return self.niveis.filter(status=Nivel.Status.APROVADO).count()

    @property
    def progresso_pct(self):
        total = self.total_niveis
        if not total:
            return 0
        return round(self.niveis_aprovados / total * 100)

    @property
    def nivel_atual(self):
        """Primeiro nível ainda não aprovado (o foco de estudo do usuário)."""
        return (
            self.niveis.exclude(status=Nivel.Status.APROVADO)
            .order_by('ordem')
            .first()
        )

    @property
    def concluida(self):
        return self.total_niveis > 0 and self.niveis_aprovados == self.total_niveis

    @property
    def titulo_atual(self):
        """O título mais avançado já conquistado na trilha (os anteriores são
        substituídos por ele na exibição)."""
        return self.titulos.order_by('-nivel__ordem').first()

    @property
    def emblema_display(self):
        """Emoji/decalque da trilha (definido pela IA, com fallback estável)."""
        if self.emblema:
            return self.emblema
        return EMBLEMAS_FALLBACK[(self.pk or 0) % len(EMBLEMAS_FALLBACK)]

    @property
    def medalha(self):
        """Medalha da trilha: emblema + patamar (metal) do título mais avançado.
        Retorna None enquanto nenhum título foi conquistado."""
        titulo = self.titulo_atual
        if titulo is None:
            return None
        tier, label = FAIXA_TIER.get(titulo.faixa, ('bronze', 'Bronze'))
        return {
            'emblema': self.emblema_display,
            'tier': tier,
            'tier_label': label,
            'nome': titulo.nome,
            'estrelas': self.niveis_aprovados,
            'total': self.total_niveis,
        }


class PerguntaDirecionadora(models.Model):
    """Pergunta gerada pela IA para calibrar o sumário antes de criá-lo."""

    class Tipo(models.TextChoices):
        ABERTA = 'aberta', 'Aberta'
        ESCOLHA_UNICA = 'escolha_unica', 'Escolha única'

    trilha = models.ForeignKey(
        Trilha, on_delete=models.CASCADE, related_name='perguntas'
    )
    ordem = models.PositiveIntegerField(default=0)
    pergunta = models.TextField()
    tipo = models.CharField(max_length=15, choices=Tipo.choices, default=Tipo.ABERTA)
    opcoes = models.JSONField(default=list, blank=True)  # apenas escolha_unica
    resposta = models.TextField(blank=True)

    class Meta:
        verbose_name = 'pergunta direcionadora'
        verbose_name_plural = 'perguntas direcionadoras'
        ordering = ['ordem']

    def __str__(self):
        return self.pergunta[:60]


class Nivel(models.Model):
    """Um nível da trilha, com seus subtópicos, conteúdo e título a conceder."""

    class Faixa(models.TextChoices):
        INICIANTE = 'iniciante', 'Iniciante'
        INTERMEDIARIO = 'intermediario', 'Intermediário'
        AVANCADO = 'avancado', 'Avançado'
        ESPECIALISTA = 'especialista', 'Especialista'
        MESTRE = 'mestre', 'Mestre'

    class Status(models.TextChoices):
        BLOQUEADO = 'bloqueado', 'Bloqueado'
        DISPONIVEL = 'disponivel', 'Disponível'
        CONTEUDO_GERANDO = 'conteudo_gerando', 'Gerando conteúdo'
        CONTEUDO_PRONTO = 'conteudo_pronto', 'Conteúdo pronto'
        APROVADO = 'aprovado', 'Aprovado'
        ERRO = 'erro', 'Erro'

    trilha = models.ForeignKey(Trilha, on_delete=models.CASCADE, related_name='niveis')
    ordem = models.PositiveIntegerField(default=0)
    titulo = models.CharField(max_length=200)
    resumo = models.TextField(blank=True)
    faixa = models.CharField(max_length=15, choices=Faixa.choices, default=Faixa.INICIANTE)
    # Título concedido ao aprovar (ex.: "Iniciante em Python")
    titulo_concedido = models.CharField(max_length=200, blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.BLOQUEADO, db_index=True
    )
    conteudo_md = models.TextField('conteúdo (Markdown)', blank=True)
    erro = models.TextField(blank=True)
    gerado_em = models.DateTimeField(null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'nível'
        verbose_name_plural = 'níveis'
        ordering = ['trilha', 'ordem']

    def __str__(self):
        return f'{self.trilha_id} · {self.ordem}. {self.titulo}'

    # -- Progresso de leitura -------------------------------------------
    @property
    def total_subtopicos(self):
        return self.subtopicos.count()

    @property
    def subtopicos_lidos(self):
        return self.subtopicos.filter(lido=True).count()

    @property
    def leitura_pct(self):
        t = self.total_subtopicos
        return round(self.subtopicos_lidos / t * 100) if t else 0

    @property
    def conteudo_lido(self):
        t = self.total_subtopicos
        return t > 0 and self.subtopicos_lidos == t

    @property
    def primeiro_nao_lido(self):
        return self.subtopicos.filter(lido=False).order_by('ordem').first() \
            or self.subtopicos.order_by('ordem').first()


class Subtopico(models.Model):
    """Subtópico de um nível — cada um é uma página de leitura, gerada sob demanda."""

    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        GERANDO = 'gerando', 'Gerando'
        PRONTO = 'pronto', 'Pronto'
        ERRO = 'erro', 'Erro'

    nivel = models.ForeignKey(Nivel, on_delete=models.CASCADE, related_name='subtopicos')
    ordem = models.PositiveIntegerField(default=0)
    titulo = models.CharField(max_length=200)
    descricao_curta = models.TextField(blank=True)

    conteudo_md = models.TextField('conteúdo (Markdown)', blank=True)
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.PENDENTE, db_index=True
    )
    lido = models.BooleanField('lido', default=False)
    erro = models.TextField(blank=True)
    gerado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'subtópico'
        verbose_name_plural = 'subtópicos'
        ordering = ['ordem']

    def __str__(self):
        return self.titulo

    @property
    def eh_ultimo(self):
        return not self.nivel.subtopicos.filter(ordem__gt=self.ordem).exists()

    @property
    def desbloqueado(self):
        """Um tópico só abre depois que o anterior foi lido (leitura em ordem).
        O primeiro tópico e os já lidos ficam sempre disponíveis."""
        if self.lido:
            return True
        anterior = (
            self.nivel.subtopicos.filter(ordem__lt=self.ordem)
            .order_by('-ordem').first()
        )
        return anterior is None or anterior.lido
