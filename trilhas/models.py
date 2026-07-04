import re

from django.conf import settings
from django.db import models


# Conectivos e palavras genéricas ignorados ao montar o monograma (sigla) da
# trilha, para que as iniciais reflitam o assunto (não o "Trilha de…").
CONECTIVOS_SIGLA = {
    'de', 'da', 'do', 'das', 'dos', 'e', 'a', 'o', 'as', 'os', 'para', 'com',
    'em', 'no', 'na', 'nos', 'nas', 'ao', 'à', 'the', 'of', 'and', 'to', 'in', 'for',
    'trilha', 'trilhas', 'curso', 'cursos', 'estudo', 'estudos', 'aula', 'aulas',
    'módulo', 'modulo', 'introdução', 'introducao',
}


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
    categoria = models.CharField('categoria', max_length=60, blank=True, db_index=True)

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
    def sigla(self):
        """Monograma da trilha (1–2 iniciais do título) para gravar na medalha.
        Sempre condiz com o assunto, ao contrário de um emoji genérico."""
        base = (self.titulo or self.tema_livre or '').strip()
        # Palavras sem pontuação nas bordas (ex.: "Estudos:" -> "Estudos").
        palavras = [w for w in (re.sub(r'^\W+|\W+$', '', p) for p in base.split()) if w]
        signif = [p for p in palavras if p.lower() not in CONECTIVOS_SIGLA] or palavras
        if len(signif) >= 2:
            return (signif[0][:1] + signif[1][:1]).upper()
        if signif:
            return signif[0][:2].upper() if len(signif[0]) > 1 else signif[0][:1].upper()
        return '★'

    @property
    def categoria_display(self):
        """Nome da 'pasta' onde a trilha aparece (fallback: 'Outras')."""
        return self.categoria.strip() or 'Outras'

    @property
    def proximo_topico(self):
        """(nível, subtópico) para continuar estudando, ou None se concluída."""
        nivel = self.nivel_atual
        if nivel is None or nivel.status == Nivel.Status.BLOQUEADO:
            return None
        sub = nivel.primeiro_nao_lido
        if sub is None:
            return None
        return nivel, sub

    @property
    def medalha(self):
        """Medalha da trilha: emblema + patamar (metal) do título mais avançado.
        Retorna None enquanto nenhum título foi conquistado."""
        titulo = self.titulo_atual
        if titulo is None:
            return None
        tier, label = FAIXA_TIER.get(titulo.faixa, ('bronze', 'Bronze'))
        return {
            'sigla': self.sigla,
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


class Percurso(models.Model):
    """Percurso personalizado do Mentor: uma sequência de passos (aprender/
    revisar/avaliar) equilibrada entre as trilhas do usuário."""

    class Status(models.TextChoices):
        GERANDO = 'gerando', 'Gerando'
        PRONTO = 'pronto', 'Pronto'
        ERRO = 'erro', 'Erro'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='percursos'
    )
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.GERANDO, db_index=True
    )
    resumo = models.TextField('recado do mentor', blank=True)
    erro = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'percurso'
        verbose_name_plural = 'percursos'
        ordering = ['-criado_em']

    def __str__(self):
        return f'Percurso {self.pk} de {self.user_id}'


class PassoPercurso(models.Model):
    """Um passo recomendado pelo Mentor, com o motivo e o alvo da ação."""

    class Tipo(models.TextChoices):
        APRENDER = 'aprender', 'Aprender'
        REVISAR = 'revisar', 'Revisar'
        AVALIAR = 'avaliar', 'Avaliar'
        REVISAR_GLOBAL = 'revisar_global', 'Revisão geral'

    percurso = models.ForeignKey(
        Percurso, on_delete=models.CASCADE, related_name='passos'
    )
    ordem = models.PositiveIntegerField(default=0)
    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    titulo = models.CharField(max_length=200)
    motivo = models.TextField(blank=True)
    nivel = models.ForeignKey(
        Nivel, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    subtopico_ordem = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = 'passo do percurso'
        verbose_name_plural = 'passos do percurso'
        ordering = ['ordem']

    def __str__(self):
        return f'{self.get_tipo_display()}: {self.titulo}'
