from django.conf import settings
from django.db import models

# Enquanto a task gera, o texto acumulado vive no cache e o polling o lê dali —
# gravar cada pedaço no banco seria um UPDATE por punhado de tokens.
PARCIAL_TTL_S = 300


def chave_parcial(mensagem_id):
    return f"chat:parcial:{mensagem_id}"


class Conversa(models.Model):
    """Fio de dúvidas de um aluno dentro de uma trilha.

    Uma conversa por (aluno, trilha): o fio acompanha o aluno por todos os
    níveis e tópicos daquela trilha, então uma dúvida puxa a outra mesmo depois
    de virar a página. Qual página estava aberta na hora fica em cada mensagem
    (`Mensagem.subtopico`), que é o que alimenta o contexto do modelo.
    A conversa sem trilha (`None`) é a "geral", usada fora das trilhas.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversas",
    )
    # CASCADE até o usuário e até a trilha: o expurgo de visitantes apaga a
    # conta com um `queryset.delete()`, e a política promete que os dados vão
    # junto. Nada de SET_NULL aqui, que deixaria conversa órfã para trás.
    trilha = models.ForeignKey(
        "trilhas.Trilha",
        on_delete=models.CASCADE,
        related_name="conversas",
        null=True,
        blank=True,
    )

    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "conversa"
        verbose_name_plural = "conversas"
        ordering = ["-atualizada_em"]
        constraints = [
            # UniqueConstraint em vez de unique_together: no Postgres, NULL não
            # conflita com NULL, então a conversa geral (subtopico=None) escapa
            # da restrição e o aluno acumularia uma por pergunta.
            models.UniqueConstraint(
                fields=["user", "trilha"],
                name="conversa_unica_por_trilha",
                condition=models.Q(trilha__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["user"],
                name="conversa_geral_unica",
                condition=models.Q(trilha__isnull=True),
            ),
        ]

    def __str__(self):
        return f"Conversa de {self.user} ({self.rotulo})"

    @property
    def rotulo(self):
        """Como a conversa aparece na lista de conversas salvas."""
        return self.trilha.titulo if self.trilha else "Conversa geral"

    @property
    def contexto(self):
        """De onde foi a última pergunta — a trilha é longa, o tópico situa."""
        for mensagem in reversed(list(self.mensagens.all())):
            if mensagem.subtopico is not None:
                return mensagem.subtopico.titulo
        return ""


class Mensagem(models.Model):
    """Uma fala do fio — a pergunta do aluno ou a resposta da IA."""

    class Papel(models.TextChoices):
        ALUNO = "aluno", "Aluno"
        IA = "ia", "IA"

    class Status(models.TextChoices):
        GERANDO = "gerando", "Gerando"
        PRONTA = "pronta", "Pronta"
        RECUSADA = "recusada", "Fora de escopo"
        ERRO = "erro", "Erro"

    conversa = models.ForeignKey(Conversa, on_delete=models.CASCADE, related_name="mensagens")
    # Página aberta quando a pergunta foi feita: é o material que vai no
    # contexto do modelo. SET_NULL porque apagar um tópico não pode apagar a
    # dúvida que o aluno teve sobre ele.
    subtopico = models.ForeignKey(
        "trilhas.Subtopico",
        on_delete=models.SET_NULL,
        related_name="mensagens_chat",
        null=True,
        blank=True,
    )
    papel = models.CharField(max_length=6, choices=Papel.choices)
    texto = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PRONTA, db_index=True
    )
    erro = models.TextField(blank=True)

    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_saida = models.PositiveIntegerField(default=0)

    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        ordering = ["criada_em", "pk"]

    def __str__(self):
        return f"{self.get_papel_display()}: {self.texto[:40]}"
