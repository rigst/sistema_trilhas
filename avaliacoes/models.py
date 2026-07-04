from django.db import models

from trilhas.models import Nivel, Trilha


class Avaliacao(models.Model):
    """Avaliação de um nível — questões objetivas e dissertativas, corrigidas pela IA."""

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
    """Questão de uma avaliação (objetiva ou dissertativa)."""

    class Tipo(models.TextChoices):
        OBJETIVA = 'objetiva', 'Objetiva'
        DISSERTATIVA = 'dissertativa', 'Dissertativa'

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
    resposta_texto = models.TextField(blank=True)
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


class Exercicio(models.Model):
    """Exercício de prática com feedback imediato (sem impacto na progressão)."""

    class Tipo(models.TextChoices):
        OBJETIVA = 'objetiva', 'Objetiva'
        DISSERTATIVA = 'dissertativa', 'Dissertativa'

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
    resposta_texto = models.TextField(blank=True)
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
