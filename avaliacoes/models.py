from django.conf import settings
from django.db import models

from trilhas.models import FAIXA_TIER, Nivel, Trilha


class Avaliacao(models.Model):
    """Avaliação de um nível — questões objetivas, corrigidas pelo gabarito."""

    class Status(models.TextChoices):
        GERANDO = 'gerando', 'Gerando'
        PRONTA = 'pronta', 'Pronta'
        CORRIGINDO = 'corrigindo', 'Corrigindo'
        CORRIGIDA = 'corrigida', 'Corrigida'
        ERRO = 'erro', 'Erro'

    nivel = models.ForeignKey(
        Nivel, on_delete=models.CASCADE, related_name='avaliacoes'
    )
    tentativa = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.GERANDO, db_index=True
    )
    nota_final = models.FloatField(null=True, blank=True)
    aprovado = models.BooleanField(default=False)
    feedback_geral = models.TextField(blank=True)
    erro = models.TextField(blank=True)

    criada_em = models.DateTimeField(auto_now_add=True)
    corrigida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'avaliação'
        verbose_name_plural = 'avaliações'
        ordering = ['-criada_em']

    def __str__(self):
        return f'Avaliação n{self.nivel_id} (tentativa {self.tentativa})'


class Questao(models.Model):
    """Questão objetiva de uma avaliação (corrigida pelo gabarito)."""

    class Tipo(models.TextChoices):
        OBJETIVA = 'objetiva', 'Objetiva'

    avaliacao = models.ForeignKey(
        Avaliacao, on_delete=models.CASCADE, related_name='questoes'
    )
    ordem = models.PositiveIntegerField(default=0)
    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    enunciado_md = models.TextField()
    # Objetivas: [{"letra": "A", "texto": "..."}, ...]
    alternativas = models.JSONField(default=list, blank=True)
    # Objetivas: letra do gabarito. Dissertativas: rubrica esperada.
    gabarito = models.TextField(blank=True)
    peso = models.FloatField(default=1.0)

    class Meta:
        verbose_name = 'questão'
        verbose_name_plural = 'questões'
        ordering = ['ordem']

    def __str__(self):
        return f'{self.get_tipo_display()} {self.ordem}'


class Resposta(models.Model):
    """Resposta do usuário a uma questão, com a nota e feedback da IA."""

    questao = models.OneToOneField(
        Questao, on_delete=models.CASCADE, related_name='resposta'
    )
    alternativa_escolhida = models.CharField(max_length=5, blank=True)
    nota = models.FloatField(null=True, blank=True)  # 0–10
    feedback_md = models.TextField(blank=True)
    corrigida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'resposta'
        verbose_name_plural = 'respostas'

    def __str__(self):
        return f'Resposta q{self.questao_id}'


class ListaExercicios(models.Model):
    """Lista de exercícios de PRÁTICA de um nível (não valem nota)."""

    class Status(models.TextChoices):
        GERANDO = 'gerando', 'Gerando'
        PRONTA = 'pronta', 'Pronta'
        ERRO = 'erro', 'Erro'

    nivel = models.OneToOneField(
        Nivel, on_delete=models.CASCADE, related_name='lista_exercicios'
    )
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.GERANDO, db_index=True
    )
    erro = models.TextField(blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'lista de exercícios'
        verbose_name_plural = 'listas de exercícios'

    def __str__(self):
        return f'Exercícios do nível {self.nivel_id}'

    @property
    def total(self):
        return self.exercicios.count()

    @property
    def respondidos(self):
        return self.exercicios.filter(respondido_em__isnull=False).count()

    @property
    def concluida(self):
        """Prática concluída quando todos os exercícios foram respondidos ao menos uma vez."""
        return self.status == self.Status.PRONTA and self.total > 0 \
            and self.respondidos == self.total


class Exercicio(models.Model):
    """Exercício de prática objetivo, com feedback imediato (sem nota)."""

    class Tipo(models.TextChoices):
        OBJETIVA = 'objetiva', 'Objetiva'

    lista = models.ForeignKey(
        ListaExercicios, on_delete=models.CASCADE, related_name='exercicios'
    )
    ordem = models.PositiveIntegerField(default=0)
    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    enunciado_md = models.TextField()
    alternativas = models.JSONField(default=list, blank=True)
    gabarito = models.TextField(blank=True)
    explicacao_md = models.TextField(blank=True)

    # Última tentativa do usuário (prática livre — pode refazer).
    alternativa_escolhida = models.CharField(max_length=5, blank=True)
    nota = models.FloatField(null=True, blank=True)
    feedback_md = models.TextField(blank=True)
    respondido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'exercício'
        verbose_name_plural = 'exercícios'
        ordering = ['ordem']

    def __str__(self):
        return f'Exercício {self.ordem} ({self.get_tipo_display()})'


class Titulo(models.Model):
    """Título conquistado ao aprovar num nível (base da gamificação)."""

    trilha = models.ForeignKey(
        Trilha, on_delete=models.CASCADE, related_name='titulos'
    )
    nivel = models.OneToOneField(
        Nivel, on_delete=models.CASCADE, related_name='titulo_conquistado'
    )
    nome = models.CharField(max_length=200)
    faixa = models.CharField(max_length=15)
    concedido_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'título'
        verbose_name_plural = 'títulos'
        ordering = ['concedido_em']

    def __str__(self):
        return self.nome

    @property
    def tier(self):
        """Slug do patamar da medalha (bronze/prata/ouro/platina/diamante)."""
        return FAIXA_TIER.get(self.faixa, ('bronze', 'Bronze'))[0]

    @property
    def tier_label(self):
        return FAIXA_TIER.get(self.faixa, ('bronze', 'Bronze'))[1]


class Revisao(models.Model):
    """Revisão espaçada: quiz objetivo misturando os níveis já concluídos das
    várias trilhas do usuário. Feedback imediato, sem impacto na progressão."""

    class Status(models.TextChoices):
        GERANDO = 'gerando', 'Gerando'
        PRONTA = 'pronta', 'Pronta'
        ERRO = 'erro', 'Erro'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='revisoes'
    )
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.GERANDO, db_index=True
    )
    erro = models.TextField(blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'revisão'
        verbose_name_plural = 'revisões'
        ordering = ['-criada_em']

    def __str__(self):
        return f'Revisão {self.pk} de {self.user_id}'

    @property
    def total(self):
        return self.questoes.count()

    @property
    def respondidos(self):
        return self.questoes.filter(respondido_em__isnull=False).count()

    @property
    def acertos(self):
        return self.questoes.filter(nota=10).count()

    @property
    def concluida(self):
        return self.status == self.Status.PRONTA and self.total > 0 \
            and self.respondidos == self.total


class QuestaoRevisao(models.Model):
    """Questão objetiva de uma revisão, com origem no nível que a inspirou."""

    revisao = models.ForeignKey(
        Revisao, on_delete=models.CASCADE, related_name='questoes'
    )
    ordem = models.PositiveIntegerField(default=0)
    nivel = models.ForeignKey(
        Nivel, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    origem = models.CharField(max_length=200, blank=True)  # rótulo "Trilha · Nível"
    enunciado_md = models.TextField()
    alternativas = models.JSONField(default=list, blank=True)
    gabarito = models.TextField(blank=True)
    explicacao_md = models.TextField(blank=True)

    alternativa_escolhida = models.CharField(max_length=5, blank=True)
    nota = models.FloatField(null=True, blank=True)
    respondido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'questão de revisão'
        verbose_name_plural = 'questões de revisão'
        ordering = ['ordem']

    def __str__(self):
        return f'Questão de revisão {self.ordem}'
