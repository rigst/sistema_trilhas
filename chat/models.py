from django.conf import settings
from django.db import models

# Enquanto a task gera, o texto acumulado vive no cache e o polling o lê dali —
# gravar cada pedaço no banco seria um UPDATE por punhado de tokens.
PARCIAL_TTL_S = 300


def chave_parcial(mensagem_id):
    return f"chat:parcial:{mensagem_id}"


class Conversa(models.Model):
    """Fio de dúvidas de um aluno sobre um subtópico.

    Uma conversa por (aluno, subtópico): quem volta ao tópico reencontra o que
    perguntou ali, e o contexto mandado ao modelo casa sempre com o histórico.
    A conversa sem subtópico (`None`) é a "geral", usada fora do leitor.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversas",
    )
    # CASCADE até o usuário e até o subtópico: o expurgo de visitantes apaga a
    # conta com um `queryset.delete()`, e a política promete que os dados vão
    # junto. Nada de SET_NULL aqui, que deixaria conversa órfã para trás.
    subtopico = models.ForeignKey(
        "trilhas.Subtopico",
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
                fields=["user", "subtopico"],
                name="conversa_unica_por_subtopico",
                condition=models.Q(subtopico__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["user"],
                name="conversa_geral_unica",
                condition=models.Q(subtopico__isnull=True),
            ),
        ]

    def __str__(self):
        alvo = self.subtopico.titulo if self.subtopico else "geral"
        return f"Conversa de {self.user} ({alvo})"


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
