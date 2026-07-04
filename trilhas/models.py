from django.conf import settings
from django.db import models


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


class Subtopico(models.Model):
    """Subtópico de um nível — guia a geração do conteúdo e das questões."""

    nivel = models.ForeignKey(Nivel, on_delete=models.CASCADE, related_name='subtopicos')
    ordem = models.PositiveIntegerField(default=0)
    titulo = models.CharField(max_length=200)
    descricao_curta = models.TextField(blank=True)

    class Meta:
        verbose_name = 'subtópico'
        verbose_name_plural = 'subtópicos'
        ordering = ['ordem']

    def __str__(self):
        return self.titulo
