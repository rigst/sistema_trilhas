import re
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

# Conectivos e palavras genéricas ignorados ao montar o monograma (sigla) da
# trilha, para que as iniciais reflitam o assunto (não o "Trilha de…").
CONECTIVOS_SIGLA = {
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
    "a",
    "o",
    "as",
    "os",
    "para",
    "com",
    "em",
    "no",
    "na",
    "nos",
    "nas",
    "ao",
    "à",
    "the",
    "of",
    "and",
    "to",
    "in",
    "for",
    "trilha",
    "trilhas",
    "curso",
    "cursos",
    "estudo",
    "estudos",
    "aula",
    "aulas",
    "módulo",
    "modulo",
    "introdução",
    "introducao",
}


# Faixa do nível → patamar da medalha (metal) conquistada na trilha.
FAIXA_TIER = {
    "iniciante": ("bronze", "Bronze"),
    "intermediario": ("prata", "Prata"),
    "avancado": ("ouro", "Ouro"),
    "especialista": ("platina", "Platina"),
    "mestre": ("diamante", "Diamante"),
}

# Emblemas usados quando a IA não definiu um para a trilha (determinístico por id).
EMBLEMAS_FALLBACK = [
    "📐",
    "🧠",
    "💻",
    "🎨",
    "🎼",
    "🔬",
    "📈",
    "🌍",
    "⚗️",
    "🧬",
    "🛠️",
    "📚",
    "🏛️",
    "⚙️",
    "🧮",
    "🩺",
    "⚖️",
    "🎯",
    "🚀",
    "🔭",
    "🧭",
    "💡",
]


class Trilha(models.Model):
    """Uma trilha de estudo pessoal, do básico ao avançado, gerada por IA."""

    class Status(models.TextChoices):
        RASCUNHO = "rascunho", "Rascunho"
        GERANDO_PERGUNTAS = "gerando_perguntas", "Gerando perguntas"
        AGUARDANDO_RESPOSTAS = "aguardando_respostas", "Aguardando respostas"
        GERANDO_SUMARIO = "gerando_sumario", "Gerando sumário"
        SUMARIO_GERADO = "sumario_gerado", "Sumário gerado"
        EM_ANDAMENTO = "em_andamento", "Em andamento"
        CONCLUIDA = "concluida", "Concluída"
        ERRO = "erro", "Erro"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trilhas"
    )
    tema_livre = models.TextField("tema (descrição livre)")
    titulo = models.CharField("título", max_length=200, blank=True)
    descricao = models.TextField("descrição", blank=True)
    objetivos = models.JSONField("objetivos de aprendizagem", default=list, blank=True)
    emblema = models.CharField("emblema (emoji)", max_length=8, blank=True)
    cover_url = models.URLField("imagem de capa", max_length=500, blank=True)
    # ID da foto na Pexels, guardado à parte porque a capa é baixada para /media
    # (a URL local não carrega o ID); permite não repetir foto ao trocar a capa.
    cover_pexels_id = models.PositiveBigIntegerField("foto Pexels (id)", null=True, blank=True)
    categoria = models.CharField("categoria", max_length=60, blank=True, db_index=True)
    ativa = models.BooleanField("ativa", default=True, db_index=True)

    status = models.CharField(
        max_length=25, choices=Status.choices, default=Status.RASCUNHO, db_index=True
    )
    nota_minima_aprovacao = models.FloatField("nota mínima de aprovação", default=7.0)
    # Marca que 1 diamante foi gasto ao criar esta trilha (habilita reembolso
    # se excluída dentro da janela). Trilhas antigas ficam False → não reembolsam.
    diamante_gasto = models.BooleanField("diamante gasto na criação", default=False)

    erro = models.TextField("mensagem de erro", blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "trilha"
        verbose_name_plural = "trilhas"
        ordering = ["-criada_em"]

    def __str__(self):
        return self.titulo or self.tema_livre[:60]

    # Janela (horas) em que excluir a trilha devolve o diamante gasto.
    JANELA_REEMBOLSO_HORAS = 48

    @property
    def reembolsavel(self):
        """True se excluir agora devolve o diamante (gasto + dentro da janela)."""
        if not self.diamante_gasto:
            return False
        from django.utils import timezone

        limite = timedelta(hours=self.JANELA_REEMBOLSO_HORAS)
        return (timezone.now() - self.criada_em) <= limite

    # -- Progresso / gamificação ----------------------------------------
    @property
    def total_niveis(self):
        # Usa o cache de prefetch quando disponível (evita N+1 no dashboard).
        return len(self.niveis.all())

    @property
    def niveis_aprovados(self):
        return sum(1 for n in self.niveis.all() if n.status == Nivel.Status.APROVADO)

    @property
    def progresso_pct(self):
        total = self.total_niveis
        if not total:
            return 0
        return round(self.niveis_aprovados / total * 100)

    @property
    def nivel_atual(self):
        """Primeiro nível ainda não aprovado (o foco de estudo do usuário)."""
        # O cache de prefetch já vem ordenado por ordem (Meta.ordering).
        for n in self.niveis.all():
            if n.status != Nivel.Status.APROVADO:
                return n
        return None

    @property
    def concluida(self):
        return self.total_niveis > 0 and self.niveis_aprovados == self.total_niveis

    @property
    def titulo_atual(self):
        """O título mais avançado já conquistado na trilha (os anteriores são
        substituídos por ele na exibição)."""
        if "titulos" in getattr(self, "_prefetched_objects_cache", {}):
            titulos = list(self.titulos.all())
            return max(titulos, key=lambda t: t.nivel.ordem) if titulos else None
        return self.titulos.order_by("-nivel__ordem").first()

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
        base = (self.titulo or self.tema_livre or "").strip()
        # Palavras sem pontuação nas bordas (ex.: "Estudos:" -> "Estudos").
        palavras = [w for w in (re.sub(r"(?:^\W+)|(?:\W+$)", "", p) for p in base.split()) if w]
        signif = [p for p in palavras if p.lower() not in CONECTIVOS_SIGLA] or palavras
        if len(signif) >= 2:
            return (signif[0][:1] + signif[1][:1]).upper()
        if signif:
            return signif[0][:2].upper() if len(signif[0]) > 1 else signif[0][:1].upper()
        return "★"

    @property
    def categoria_display(self):
        """Nome da 'pasta' onde a trilha aparece (fallback: 'Outras')."""
        return self.categoria.strip() or "Outras"

    @property
    def hue(self):
        """Matiz (0–359) estável da trilha — colore a capa-constelação."""
        return ((self.pk or 0) * 137) % 360

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
        tier, label = FAIXA_TIER.get(titulo.faixa, ("bronze", "Bronze"))
        return {
            "sigla": self.sigla,
            "tier": tier,
            "tier_label": label,
            "nome": titulo.nome,
            "estrelas": self.niveis_aprovados,
            "total": self.total_niveis,
        }


class PerguntaDirecionadora(models.Model):
    """Pergunta gerada pela IA para calibrar o sumário antes de criá-lo."""

    class Tipo(models.TextChoices):
        ABERTA = "aberta", "Aberta"
        ESCOLHA_UNICA = "escolha_unica", "Escolha única"

    trilha = models.ForeignKey(Trilha, on_delete=models.CASCADE, related_name="perguntas")
    ordem = models.PositiveIntegerField(default=0)
    pergunta = models.TextField()
    tipo = models.CharField(max_length=15, choices=Tipo.choices, default=Tipo.ABERTA)
    opcoes = models.JSONField(default=list, blank=True)  # apenas escolha_unica
    resposta = models.TextField(blank=True)

    class Meta:
        verbose_name = "pergunta direcionadora"
        verbose_name_plural = "perguntas direcionadoras"
        ordering = ["ordem"]

    def __str__(self):
        return self.pergunta[:60]


class Nivel(models.Model):
    """Um nível da trilha, com seus subtópicos, conteúdo e título a conceder."""

    class Faixa(models.TextChoices):
        INICIANTE = "iniciante", "Iniciante"
        INTERMEDIARIO = "intermediario", "Intermediário"
        AVANCADO = "avancado", "Avançado"
        ESPECIALISTA = "especialista", "Especialista"
        MESTRE = "mestre", "Mestre"

    class Status(models.TextChoices):
        BLOQUEADO = "bloqueado", "Bloqueado"
        DISPONIVEL = "disponivel", "Disponível"
        CONTEUDO_GERANDO = "conteudo_gerando", "Gerando conteúdo"
        CONTEUDO_PRONTO = "conteudo_pronto", "Conteúdo pronto"
        APROVADO = "aprovado", "Aprovado"
        ERRO = "erro", "Erro"

    trilha = models.ForeignKey(Trilha, on_delete=models.CASCADE, related_name="niveis")
    ordem = models.PositiveIntegerField(default=0)
    titulo = models.CharField(max_length=200)
    resumo = models.TextField(blank=True)
    faixa = models.CharField(max_length=15, choices=Faixa.choices, default=Faixa.INICIANTE)
    # Título concedido ao aprovar (ex.: "Iniciante em Python")
    titulo_concedido = models.CharField(max_length=200, blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.BLOQUEADO, db_index=True
    )
    conteudo_md = models.TextField("conteúdo (Markdown)", blank=True)
    erro = models.TextField(blank=True)
    gerado_em = models.DateTimeField(null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Repetição espaçada (SM-2) — agendamento de revisão do nível já aprovado.
    revisao_ef = models.FloatField("fator de facilidade (SM-2)", default=2.5)
    revisao_intervalo = models.PositiveIntegerField("intervalo de revisão (dias)", default=0)
    revisao_repeticoes = models.PositiveIntegerField("repetições de revisão", default=0)
    revisao_proxima = models.DateField("próxima revisão", null=True, blank=True, db_index=True)
    revisao_ultima = models.DateField("última revisão", null=True, blank=True)

    class Meta:
        verbose_name = "nível"
        verbose_name_plural = "níveis"
        ordering = ["trilha", "ordem"]

    def __str__(self):
        return f"{self.trilha_id} · {self.ordem}. {self.titulo}"

    # -- Progresso de leitura -------------------------------------------
    @property
    def total_subtopicos(self):
        return len(self.subtopicos.all())

    @property
    def subtopicos_lidos(self):
        return sum(1 for s in self.subtopicos.all() if s.lido)

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
        # Usa o cache de prefetch (subtopicos já ordenados por ordem em Meta).
        subs = list(self.subtopicos.all())
        for s in subs:
            if not s.lido:
                return s
        return subs[0] if subs else None

    # -- Repetição espaçada (SM-2) --------------------------------------
    def iniciar_revisao_espacada(self):
        """Agenda a 1ª revisão ao aprovar o nível (amanhã)."""
        from django.utils import timezone

        self.revisao_ef = 2.5
        self.revisao_intervalo = 1
        self.revisao_repeticoes = 0
        self.revisao_ultima = None
        self.revisao_proxima = timezone.localdate() + timedelta(days=1)
        self.save(
            update_fields=[
                "revisao_ef",
                "revisao_intervalo",
                "revisao_repeticoes",
                "revisao_ultima",
                "revisao_proxima",
                "atualizado_em",
            ]
        )

    def registrar_revisao(self, qualidade):
        """Atualiza o agendamento SM-2 após revisar (qualidade 0–5)."""
        from django.utils import timezone

        hoje = timezone.localdate()
        q = max(0, min(5, int(qualidade)))
        if q < 3:
            self.revisao_repeticoes = 0
            self.revisao_intervalo = 1
        else:
            if self.revisao_repeticoes == 0:
                self.revisao_intervalo = 1
            elif self.revisao_repeticoes == 1:
                self.revisao_intervalo = 6
            else:
                base = self.revisao_intervalo or 1
                self.revisao_intervalo = max(1, round(base * (self.revisao_ef or 2.5)))
            self.revisao_repeticoes += 1
        ef = (self.revisao_ef or 2.5) + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
        self.revisao_ef = max(1.3, round(ef, 3))
        self.revisao_ultima = hoje
        self.revisao_proxima = hoje + timedelta(days=self.revisao_intervalo)
        self.save(
            update_fields=[
                "revisao_ef",
                "revisao_intervalo",
                "revisao_repeticoes",
                "revisao_ultima",
                "revisao_proxima",
                "atualizado_em",
            ]
        )

    @property
    def revisao_devida(self):
        from django.utils import timezone

        return (
            self.status == Nivel.Status.APROVADO
            and self.revisao_proxima is not None
            and self.revisao_proxima <= timezone.localdate()
        )


class Subtopico(models.Model):
    """Subtópico de um nível — cada um é uma página de leitura, gerada sob demanda."""

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        GERANDO = "gerando", "Gerando"
        PRONTO = "pronto", "Pronto"
        ERRO = "erro", "Erro"

    nivel = models.ForeignKey(Nivel, on_delete=models.CASCADE, related_name="subtopicos")
    ordem = models.PositiveIntegerField(default=0)
    titulo = models.CharField(max_length=200)
    descricao_curta = models.TextField(blank=True)

    conteudo_md = models.TextField("conteúdo (Markdown)", blank=True)
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.PENDENTE, db_index=True
    )
    lido = models.BooleanField("lido", default=False)
    erro = models.TextField(blank=True)
    gerado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "subtópico"
        verbose_name_plural = "subtópicos"
        ordering = ["ordem"]

    def __str__(self):
        return self.titulo

    @property
    def eh_ultimo(self):
        return not self.nivel.subtopicos.filter(ordem__gt=self.ordem).exists()

    @property
    def desbloqueado(self):
        """Leitura livre: todo tópico do nível fica disponível em qualquer
        ordem (a progressão da trilha continua sendo por NÍVEL, via avaliação)."""
        return True


class CardSalvo(models.Model):
    """Card de leitura guardado pelo usuário (como salvar um post) — vira a
    biblioteca pessoal de destaques para revisão espontânea."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cards_salvos"
    )
    subtopico = models.ForeignKey(Subtopico, on_delete=models.CASCADE, related_name="cards_salvos")
    indice = models.PositiveIntegerField("índice do card no tópico")
    # Trecho do conteúdo já renderizado (HTML do próprio app, não do usuário).
    html = models.TextField("conteúdo do card (HTML)")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "card salvo"
        verbose_name_plural = "cards salvos"
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "subtopico", "indice"], name="card_salvo_unico"
            ),
        ]

    def __str__(self):
        return f"Card {self.indice} de {self.subtopico_id} ({self.user_id})"


class VideoNivel(models.Model):
    """Vídeo narrado gerado sob demanda com TODO o conteúdo de um nível.

    Os subtópicos prontos do nível entram em ordem: cada um abre com um slide de
    capítulo e segue com suas seções `---`, renderizadas com fidelidade
    (screenshot do HTML real) e narradas por TTS a partir de um roteiro escrito
    pela IA. O binário fica em MEDIA_ROOT/videos e o caminho é guardado em
    `arquivo` (mesmo padrão de `cover_url`)."""

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        GERANDO = "gerando", "Gerando"
        PRONTO = "pronto", "Pronto"
        ERRO = "erro", "Erro"

    nivel = models.OneToOneField(Nivel, on_delete=models.CASCADE, related_name="video")
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.PENDENTE, db_index=True
    )
    progresso_pct = models.PositiveIntegerField("progresso (%)", default=0)
    # Etapa corrente, em texto ("Narrando os slides…") — some na tela junto da
    # barra de progresso, e com ela alimenta a estimativa de tempo restante.
    etapa = models.CharField("etapa atual", max_length=80, blank=True)
    iniciado_em = models.DateTimeField("início da geração", null=True, blank=True)
    # URL local servida pelo nginx, ex.: /media/videos/<nivel_pk>/<uuid>.mp4
    arquivo = models.CharField("arquivo (URL local)", max_length=300, blank=True)
    duracao_seg = models.PositiveIntegerField("duração (s)", default=0)
    # gerado_em MAIS RECENTE entre os subtópicos no momento da geração — detecta
    # conteúdo alterado (permite oferecer "regerar" quando algum tópico mudou).
    fonte_gerado_em = models.DateTimeField(null=True, blank=True)
    erro = models.TextField(blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "vídeo de nível"
        verbose_name_plural = "vídeos de níveis"

    def __str__(self):
        return f"Vídeo do nível {self.nivel_id} ({self.status})"

    @property
    def desatualizado(self):
        """True quando algum subtópico do nível foi regerado após o vídeo."""
        if self.status != VideoNivel.Status.PRONTO or self.fonte_gerado_em is None:
            return False
        return self.nivel.subtopicos.filter(gerado_em__gt=self.fonte_gerado_em).exists()

    @property
    def decorrido_seg(self):
        """Segundos desde o início da geração (0 se ainda não começou)."""
        if not self.iniciado_em:
            return 0
        return max(0, int((timezone.now() - self.iniciado_em).total_seconds()))

    @property
    def restante_seg(self):
        """Estimativa de quanto falta, extrapolando o ritmo até aqui.

        Só arrisca um número depois de PISO_ESTIMATIVA: no comecinho o
        percentual é ruído e a conta daria minutos absurdos.
        """
        PISO_ESTIMATIVA = 5
        if self.status != VideoNivel.Status.GERANDO:
            return None
        pct, decorrido = self.progresso_pct, self.decorrido_seg
        if pct < PISO_ESTIMATIVA or not decorrido:
            return None
        return max(0, int(decorrido * (100 - pct) / pct))


class Percurso(models.Model):
    """Percurso personalizado do Mentor: uma sequência de passos (aprender/
    revisar/avaliar) equilibrada entre as trilhas do usuário."""

    class Status(models.TextChoices):
        GERANDO = "gerando", "Gerando"
        PRONTO = "pronto", "Pronto"
        ERRO = "erro", "Erro"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="percursos"
    )
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.GERANDO, db_index=True
    )
    resumo = models.TextField("recado do mentor", blank=True)
    erro = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "percurso"
        verbose_name_plural = "percursos"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"Percurso {self.pk} de {self.user_id}"


class SessaoSugestao(models.Model):
    """Uma rodada de sugestões de novas trilhas geradas pela IA, a partir de um
    conjunto de trilhas escolhidas pelo usuário como base (para aprofundar ou
    abrir novas direções)."""

    class Status(models.TextChoices):
        GERANDO = "gerando", "Gerando"
        PRONTO = "pronto", "Pronto"
        ERRO = "erro", "Erro"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sessoes_sugestao",
    )
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.GERANDO, db_index=True
    )
    # Trilhas que o usuário marcou para a IA levar em consideração.
    trilhas_base = models.ManyToManyField(Trilha, blank=True, related_name="+")
    erro = models.TextField(blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "sessão de sugestões"
        verbose_name_plural = "sessões de sugestões"
        ordering = ["-criada_em"]

    def __str__(self):
        return f"Sugestões {self.pk} de {self.user_id}"


class TrilhaSugerida(models.Model):
    """Uma trilha proposta pela IA dentro de uma sessão de sugestões. Pode ser
    aceita com um clique, o que cria uma Trilha de verdade."""

    class Tipo(models.TextChoices):
        APROFUNDAR = "aprofundar", "Aprofundar"
        DIRECAO = "direcao", "Nova direção"

    sessao = models.ForeignKey(SessaoSugestao, on_delete=models.CASCADE, related_name="sugestoes")
    ordem = models.PositiveIntegerField(default=0)
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.DIRECAO)
    titulo = models.CharField(max_length=200)
    enfoque = models.TextField("enfoque", blank=True)
    topicos = models.JSONField("principais tópicos", default=list, blank=True)
    # Trilha criada ao aceitar a sugestão (evita aceitar duas vezes).
    trilha = models.ForeignKey(
        Trilha, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "trilha sugerida"
        verbose_name_plural = "trilhas sugeridas"
        ordering = ["ordem"]

    def __str__(self):
        return self.titulo

    def como_tema(self):
        """Monta a descrição livre usada para criar a trilha ao aceitar — junta
        título, enfoque e tópicos para dar contexto rico à geração."""
        partes = [self.titulo.strip()]
        if self.enfoque.strip():
            partes.append(self.enfoque.strip())
        topicos = [str(t).strip() for t in (self.topicos or []) if str(t).strip()]
        if topicos:
            partes.append("Principais tópicos: " + "; ".join(topicos) + ".")
        return "\n\n".join(partes)


class PassoPercurso(models.Model):
    """Um passo recomendado pelo Mentor, com o motivo e o alvo da ação."""

    class Tipo(models.TextChoices):
        APRENDER = "aprender", "Aprender"
        REVISAR = "revisar", "Revisar"
        AVALIAR = "avaliar", "Avaliar"
        REVISAR_GLOBAL = "revisar_global", "Revisão geral"

    percurso = models.ForeignKey(Percurso, on_delete=models.CASCADE, related_name="passos")
    ordem = models.PositiveIntegerField(default=0)
    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    titulo = models.CharField(max_length=200)
    motivo = models.TextField(blank=True)
    nivel = models.ForeignKey(
        Nivel, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    subtopico_ordem = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "passo do percurso"
        verbose_name_plural = "passos do percurso"
        ordering = ["ordem"]

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.titulo}"
