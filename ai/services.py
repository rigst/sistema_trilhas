"""
Integração com a API da Anthropic (Claude).

Divisão de modelos por tarefa (configurável em settings):
  - Planejamento do sumário e percurso do mentor → Opus 4.8 (mais julgamento).
  - Perguntas, conteúdo, avaliação, exercícios e revisão → Sonnet 4.6 (rápido/barato).

Fluxo:
  1. gerar_perguntas_direcionadoras
  2. gerar_sumario
  3. gerar_conteudo_subtopico (streaming, um tópico por vez)
  4. gerar_avaliacao / corrigir_avaliacao (objetivas, com progressão e título)
  5. gerar_exercicios (prática objetiva, feedback imediato)
  6. categorizar_trilhas / gerar_revisao / gerar_percurso (organização e revisão)

Toda chamada debita a quota de tokens do Profile do usuário.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from . import prompts


class IAError(Exception):
    pass


# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------

import re


_SCHEMA_QUERY_CAPA = {
    'type': 'object',
    'properties': {'query': {'type': 'string'}},
    'required': ['query'],
    'additionalProperties': False,
}


def _query_capa(titulo: str, categoria: str = '', descricao: str = '', profile=None) -> str:
    """Pede à IA um termo de busca EM INGLÊS, concreto e fotografável, para o tema.

    Jogar o título em português direto no banco de imagens rende fotos aleatórias
    ("Direito Penal" não acha nada; "courtroom gavel" acha). Fallback: heurística
    de palavras do título.
    """
    try:
        data = _gerar_json(
            'Você escolhe termos de busca para encontrar FOTOS de banco de imagens '
            '(Pexels) que sirvam de capa para trilhas de estudo.',
            f'Tema da trilha: "{titulo}"'
            + (f'\nÁrea: {categoria}' if categoria else '')
            + (f'\nDescrição: {descricao[:300]}' if descricao else '')
            + '\n\nRetorne em "query" um termo de busca EM INGLÊS, de 2 a 4 palavras, '
              'que descreva uma CENA ou OBJETO concreto e fotografável que represente '
              'visualmente esse tema (ex.: "courtroom gavel justice" para Direito '
              'Penal; "vintage camera street" para Fotografia de Rua). Evite '
              'conceitos abstratos, nomes próprios e a palavra "study".',
            _SCHEMA_QUERY_CAPA, profile,
            model=_model_geral(), effort='low', max_tokens=1000,
        )
        query = (data.get('query') or '').strip()
        if query:
            return query
    except Exception:
        pass
    palavras = [w for w in (titulo + ' ' + categoria).split() if len(w) > 3][:5]
    return ' '.join(palavras)


def _pexels_search(query: str, per_page: int = 8, orientation: str = 'landscape') -> list:
    """Busca fotos na Pexels; retorna a lista `photos` da API (ou [])."""
    api_key = getattr(settings, 'PEXELS_API_KEY', '')
    if not api_key or not query:
        return []
    api_url = (
        'https://api.pexels.com/v1/search?query=' + urllib.parse.quote(query)
        + f'&per_page={per_page}&orientation={orientation}'
    )
    try:
        req = urllib.request.Request(
            api_url,
            headers={'Authorization': api_key, 'User-Agent': 'sistema_trilhas/1.0'},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        return data.get('photos') or []
    except Exception:
        return []


_PEXELS_ID_RE = re.compile(r'/photos/(\d+)/')


def pexels_id_da_url(url: str):
    """Extrai o ID numérico da foto de uma URL da Pexels (ou None)."""
    if not url:
        return None
    m = _PEXELS_ID_RE.search(url)
    return int(m.group(1)) if m else None


def baixar_para_media(url: str, subpasta: str = 'covers') -> str:
    """Baixa uma imagem remota para MEDIA_ROOT/<subpasta> e devolve a URL local.

    Remove a dependência de hotlink da Pexels (imagem servida por nós, cacheável
    pelo nginx). Em qualquer falha devolve a URL original — nunca quebra a capa.
    """
    if not url or not url.startswith('http'):
        return url
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'sistema_trilhas/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            ctype = (resp.headers.get('Content-Type') or '').lower()
            if not ctype.startswith('image/'):
                return url
            dados = resp.read(8 * 1024 * 1024)  # teto de 8 MB
        ext = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}.get(
            ctype.split(';')[0].strip(), '.jpg'
        )
        nome = f'{uuid.uuid4().hex}{ext}'
        destino_dir = os.path.join(settings.MEDIA_ROOT, subpasta)
        os.makedirs(destino_dir, exist_ok=True)
        with open(os.path.join(destino_dir, nome), 'wb') as fh:
            fh.write(dados)
        return f'{settings.MEDIA_URL}{subpasta}/{nome}'
    except Exception:
        return url


def _foto_mais_aderente(photos: list, query: str, excluir=None):
    """A foto cujo alt mais bate com os termos da query (fallback: a 1ª)."""
    excluir = excluir or set()
    photos = [p for p in photos if p.get('id') not in excluir]
    if not photos:
        return None
    termos = {w.lower() for w in query.split() if len(w) > 2}
    return max(photos, key=lambda p: sum(
        1 for w in termos if w in (p.get('alt') or '').lower()
    ))


_SCHEMA_ESCOLHA_FOTO = {
    'type': 'object',
    'properties': {'indice': {'type': 'integer'}},
    'required': ['indice'],
    'additionalProperties': False,
}


def buscar_capa(titulo: str, categoria: str = '', descricao: str = '',
                profile=None, excluir_ids=None) -> str:
    """Busca uma imagem de capa relevante na Pexels (requer PEXELS_API_KEY no .env).

    A IA traduz o tema num termo de busca visual; entre as fotos retornadas,
    a própria IA escolhe pela descrição (alt) a mais representativa do tema —
    com fallback na heurística de aderência à query. `excluir_ids` evita repetir
    a capa de outra trilha (temas afins convergem na mesma busca). Retorna a
    URL da imagem landscape, ou string vazia em caso de falha.
    """
    query = _query_capa(titulo, categoria, descricao, profile)
    photos = _pexels_search(query, per_page=12)
    if excluir_ids:
        photos = [p for p in photos if p.get('id') not in excluir_ids]
    if not photos:
        return ''
    melhor = None
    alts = [
        f'{i}. {(p.get("alt") or "").strip() or "(sem descrição)"}'
        for i, p in enumerate(photos, start=1)
    ]
    try:
        data = _gerar_json(
            'Você escolhe a melhor FOTO de capa para uma trilha de estudos, '
            'a partir das descrições das fotos candidatas.',
            f'Tema da trilha: "{titulo}"'
            + (f' (área: {categoria})' if categoria else '')
            + '\n\nFotos candidatas:\n' + '\n'.join(alts)
            + '\n\nRetorne em "indice" o número da foto que melhor representa o '
              'TEMA da trilha (evite fotos genéricas de pessoas estudando, telas '
              'desfocadas ou objetos sem relação direta).',
            _SCHEMA_ESCOLHA_FOTO, profile,
            model=_model_geral(), effort='low', max_tokens=800,
        )
        i = int(data.get('indice') or 0)
        if 1 <= i <= len(photos):
            melhor = photos[i - 1]
    except Exception:
        pass
    if melhor is None:
        melhor = _foto_mais_aderente(photos, query)
    src = (melhor or {}).get('src', {})
    # Usa o variante large2x (940×650): o pré-corte da Pexels centraliza o
    # assunto da foto muito melhor do que recortar o arquivo original em CSS
    # (testado lado a lado — o original deixa rostos/objetos cortados na borda).
    return src.get('large2x') or src.get('original', '')


# Marcador de foto que a IA insere no conteúdo: {{foto: query em inglês | legenda}}.
# O modelo não conhece URLs reais de banco de imagens (inventaria links quebrados);
# ele só marca ONDE uma foto ajuda e O QUE ela deve mostrar — a busca é feita aqui.
_FOTO_MARCADOR_RE = re.compile(r'\{\{\s*foto:\s*([^|{}]+?)\s*(?:\|\s*([^{}]*?))?\s*\}\}')


_SCHEMA_AUDITORIA_FOTOS = {
    'type': 'object',
    'properties': {'aprovadas': {'type': 'array', 'items': {'type': 'integer'}}},
    'required': ['aprovadas'],
    'additionalProperties': False,
}

_SYSTEM_AUDITORIA_FOTOS = (
    'Você audita fotos de banco de imagens escolhidas para material didático. '
    'Aprove uma foto SOMENTE se a descrição dela mostra de fato o que a legenda '
    'e o pedido afirmam — mesmo assunto, mesmo objeto, sem ambiguidade. Foto '
    'genérica, apenas "relacionada" ou que poderia induzir o aluno a erro deve '
    'ser REPROVADA. Na dúvida, reprove. Responda em português.'
)


def _auditar_fotos(itens, contexto='', profile=None):
    """IA aprova/reprova cada foto encontrada (a descrição `alt` da foto deve
    condizer com o que o marcador pediu). Retorna o set de índices aprovados;
    em caso de falha da chamada, aprova todas (não trava a geração)."""
    if not itens:
        return set()
    linhas = []
    for i, it in enumerate(itens, start=1):
        linhas.append(
            f'{i}. Pedido: "{it["query"]}" — legenda: "{it["legenda"] or "(sem)"}"\n'
            f'   Foto encontrada (descrição do banco): "{it["alt"] or "(sem descrição)"}"'
        )
    try:
        data = _gerar_json(
            _SYSTEM_AUDITORIA_FOTOS,
            (f'Material: {contexto}\n\n' if contexto else '')
            + 'Fotos candidatas:\n\n' + '\n'.join(linhas)
            + '\n\nRetorne em "aprovadas" apenas os números das fotos cuja descrição '
              'confirma que mostram exatamente o que foi pedido.',
            _SCHEMA_AUDITORIA_FOTOS, profile,
            model=_model_geral(), effort='low', max_tokens=1500,
        )
        return {int(i) for i in data.get('aprovadas', [])}
    except Exception:
        return set(range(1, len(itens) + 1))


def inserir_fotos_conteudo(texto: str, contexto: str = '', profile=None) -> str:
    """Troca cada marcador {{foto: …}} por uma fotografia real da Pexels.

    A foto só entra se passar na auditoria da IA (a descrição da foto precisa
    condizer com o pedido — imagem errada é pior que nenhuma). Marcador sem
    foto aprovada é removido; fotos não se repetem no mesmo texto. A legenda
    vira uma linha em itálico abaixo da imagem, com o crédito do fotógrafo
    (diretriz da Pexels)."""
    if not texto or '{{' not in texto:
        return texto
    usadas = set()
    itens = []
    for m in _FOTO_MARCADOR_RE.finditer(texto):
        query = m.group(1).strip()
        legenda = (m.group(2) or '').strip().rstrip('.')
        foto = _foto_mais_aderente(_pexels_search(query, per_page=6), query, excluir=usadas)
        if foto:
            usadas.add(foto.get('id'))
        itens.append({
            'query': query,
            'legenda': legenda,
            'foto': foto,
            'alt': ((foto or {}).get('alt') or '').strip(),
        })
    com_foto = [it for it in itens if it['foto']]
    aprovadas = _auditar_fotos(com_foto, contexto, profile)
    for i, it in enumerate(com_foto, start=1):
        it['aprovada'] = i in aprovadas

    fila = iter(itens)

    def _rep(m):
        it = next(fila)
        foto = it['foto']
        if not foto or not it.get('aprovada'):
            return ''
        src = foto.get('src', {})
        url = src.get('large') or src.get('original', '')
        if not url:
            return ''
        alt = it['legenda'] or it['alt'] or it['query']
        fotografo = (foto.get('photographer') or '').strip()
        credito = f' · foto de {fotografo} (Pexels)' if fotografo else ''
        return f'![{alt}]({url})\n*{it["legenda"] or alt}{credito}*'

    return _FOTO_MARCADOR_RE.sub(_rep, texto)


# "Trilha de Python" -> "Python": o título é o nome do assunto, sem prefixo.
_PREFIXO_TITULO = re.compile(
    r'^(trilha|curso|jornada|guia|estudo)s?( completo| completa)?'
    r'( de| do| da| dos| das| para| em| sobre)?[\s:–-]+',
    re.IGNORECASE,
)


def limpar_titulo(titulo):
    limpo = (titulo or '').strip()
    # Itera: "Trilha de Estudos: X" -> "Estudos: X" -> "X".
    for _ in range(3):
        novo = _PREFIXO_TITULO.sub('', limpo).strip()
        if novo == limpo:
            break
        limpo = novo
    return (limpo or (titulo or '').strip())[:200]


def _model_geral():
    return getattr(settings, 'AI_MODEL_GERAL', 'claude-sonnet-4-6')


def _model_planejamento():
    return getattr(settings, 'AI_MODEL_PLANEJAMENTO', 'claude-opus-4-8')


def get_client():
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise IAError('ANTHROPIC_API_KEY não configurada.')
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def _precos(model):
    tabela = getattr(settings, 'AI_PRICES', {})
    if model in tabela:
        return tabela[model]
    return (
        getattr(settings, 'AI_PRICE_INPUT_PER_MTOK', 5.0),
        getattr(settings, 'AI_PRICE_OUTPUT_PER_MTOK', 25.0),
    )


def custo_usd(model, input_tokens, output_tokens):
    pin, pout = _precos(model)
    return (Decimal(input_tokens) / 1_000_000 * Decimal(str(pin))) + \
           (Decimal(output_tokens) / 1_000_000 * Decimal(str(pout)))


def _system_cache(system):
    """Empacota o system prompt num bloco com cache_control ephemeral.

    O prompt do sistema é estável e reenviado em toda geração; caching de prefixo
    corta ~90% do custo de input nas chamadas seguintes (mínimo ~2048 tokens de
    prefixo no Sonnet 5 — abaixo disso o cache é ignorado, sem erro).
    """
    return [{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}]


def _debitar(profile, usage, model):
    if profile is None or usage is None:
        return
    it = getattr(usage, 'input_tokens', 0) or 0
    ot = getattr(usage, 'output_tokens', 0) or 0
    cr = getattr(usage, 'cache_read_input_tokens', 0) or 0
    cw = getattr(usage, 'cache_creation_input_tokens', 0) or 0
    pin, _ = _precos(model)
    # Cache: leitura ~0,1x e escrita ~1,25x do preço de input.
    custo_cache = (Decimal(cr) * Decimal('0.1') + Decimal(cw) * Decimal('1.25')) \
        / 1_000_000 * Decimal(str(pin))
    custo = custo_usd(model, it, ot) + custo_cache
    # Todos os tokens de entrada (inclusive cache) contam para a quota do mês.
    profile.registrar_uso(it + cr + cw, ot, custo)


def _texto(content):
    return ''.join(b.text for b in content if getattr(b, 'type', '') == 'text')


def _gerar_json(system, user, schema, profile, model, effort, max_tokens=None):
    """Chamada estruturada (JSON schema) com raciocínio adaptativo."""
    client = get_client()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens or getattr(settings, 'AI_MAX_TOKENS', 16000),
        system=_system_cache(system),
        messages=[{'role': 'user', 'content': user}],
        thinking={'type': 'adaptive'},
        output_config={
            'effort': effort,
            'format': {'type': 'json_schema', 'schema': schema},
        },
    )
    _debitar(profile, resp.usage, model)
    if resp.stop_reason == 'max_tokens':
        raise IAError('Resposta da IA truncada pelo limite de tokens (max_tokens).')
    try:
        return json.loads(_texto(resp.content))
    except json.JSONDecodeError as exc:
        raise IAError(f'Resposta da IA não é JSON válido: {exc}') from exc


# ---------------------------------------------------------------------------
# 1. Perguntas direcionadoras (Sonnet)
# ---------------------------------------------------------------------------

def gerar_perguntas_direcionadoras(trilha, profile=None):
    from trilhas.models import PerguntaDirecionadora

    user = (
        'A pessoa quer aprender o seguinte (descrição livre):\n\n'
        f'"""{trilha.tema_livre.strip()}"""\n\n'
        'Gere de 3 a 5 perguntas direcionadoras de MÚLTIPLA ESCOLHA para calibrar um '
        'plano de estudos personalizado. Cubra: nível/experiência atual no tema; '
        'objetivo ou aplicação pretendida; e subtemas de maior interesse. NÃO '
        'pergunte sobre tempo disponível por semana nem sobre formato/estilo de '
        'estudo. TODAS as perguntas devem ser de escolha única (tipo "escolha_unica"), '
        'cada uma com 3 a 5 opções claras em "opcoes". Não faça perguntas abertas ou '
        'dissertativas. Numere em "ordem" a partir de 1.'
    )
    data = _gerar_json(
        prompts.SYSTEM_PERGUNTAS, user, prompts.SCHEMA_PERGUNTAS, profile,
        model=_model_geral(), effort=getattr(settings, 'AI_EFFORT_GERAL', 'medium'),
    )

    trilha.perguntas.all().delete()
    objs = []
    for i, p in enumerate(data.get('perguntas', []), start=1):
        objs.append(PerguntaDirecionadora(
            trilha=trilha,
            ordem=p.get('ordem') or i,
            pergunta=p.get('pergunta', '').strip(),
            tipo=p.get('tipo', 'escolha_unica'),
            opcoes=p.get('opcoes', []) or [],
        ))
    PerguntaDirecionadora.objects.bulk_create(objs)
    return objs


# ---------------------------------------------------------------------------
# 2. Sumário (Opus — planejamento)
# ---------------------------------------------------------------------------

def gerar_sumario(trilha, profile=None):
    from trilhas.models import Nivel, Subtopico

    linhas = []
    for p in trilha.perguntas.all():
        resp = (p.resposta or '').strip() or '(sem resposta)'
        linhas.append(f'- {p.pergunta}\n  Resposta: {resp}')
    qa = '\n'.join(linhas) if linhas else '(nenhuma pergunta respondida)'

    user = (
        'Tema desejado (descrição livre):\n\n'
        f'"""{trilha.tema_livre.strip()}"""\n\n'
        'Respostas às perguntas direcionadoras:\n\n'
        f'{qa}\n\n'
        'Monte um sumário de trilha de estudos progressiva, do básico ao avançado, '
        'com 4 a 7 níveis. Em "titulo", use o NOME DIRETO do assunto (ex.: '
        '"Fotografia de Rua", "Python do Zero") — NUNCA comece com "Trilha de", '
        '"Curso de", "Guia de" ou similares. Escolha em "emblema" UM único emoji que represente '
        'visualmente o tema (será o brasão da medalha da trilha). Em "categoria", '
        'informe a área de conhecimento ampla desta trilha (ex.: "Programação", '
        '"Direito", "História", "Idiomas", "Música"), usando um rótulo curto e '
        'reutilizável para agrupar trilhas afins. Para cada nível: '
        'defina a faixa (iniciante → mestre) de '
        'forma crescente; um título claro; um resumo do que será aprendido; um '
        '"titulo_concedido" motivador que a pessoa ganha ao ser aprovada (ex.: '
        '"Iniciante em <tema>"); e de 3 a 6 subtópicos coerentes. Numere níveis e '
        'subtópicos em "ordem" a partir de 1. Adapte a profundidade e o foco às '
        'respostas fornecidas.'
    )
    data = _gerar_json(
        prompts.SYSTEM_SUMARIO, user, prompts.SCHEMA_SUMARIO, profile,
        model=_model_planejamento(), effort=getattr(settings, 'AI_EFFORT', 'high'),
    )

    trilha.titulo = limpar_titulo(data.get('titulo') or trilha.tema_livre[:120])
    trilha.descricao = (data.get('descricao') or '').strip()
    trilha.emblema = (data.get('emblema') or '').strip()[:8]
    trilha.categoria = (data.get('categoria') or '').strip()[:60]
    trilha.objetivos = data.get('objetivos', []) or []
    _cover_pexels = buscar_capa(trilha.titulo, trilha.categoria, trilha.descricao, profile)
    trilha.cover_pexels_id = pexels_id_da_url(_cover_pexels)
    trilha.cover_url = baixar_para_media(_cover_pexels)
    trilha.save(update_fields=[
        'titulo', 'descricao', 'emblema', 'categoria', 'objetivos',
        'cover_url', 'cover_pexels_id', 'atualizada_em',
    ])

    trilha.niveis.all().delete()
    for i, nv in enumerate(data.get('niveis', []), start=1):
        nivel = Nivel.objects.create(
            trilha=trilha,
            ordem=nv.get('ordem') or i,
            titulo=(nv.get('titulo') or f'Nível {i}').strip(),
            resumo=(nv.get('resumo') or '').strip(),
            faixa=nv.get('faixa') or 'iniciante',
            titulo_concedido=(nv.get('titulo_concedido') or '').strip(),
            status=Nivel.Status.DISPONIVEL if i == 1 else Nivel.Status.BLOQUEADO,
        )
        subs = []
        for j, st in enumerate(nv.get('subtopicos', []), start=1):
            subs.append(Subtopico(
                nivel=nivel,
                ordem=st.get('ordem') or j,
                titulo=(st.get('titulo') or '').strip(),
                descricao_curta=(st.get('descricao_curta') or '').strip(),
            ))
        Subtopico.objects.bulk_create(subs)
    return trilha


# ---------------------------------------------------------------------------
# 3. Conteúdo de UM subtópico (Sonnet — sob demanda, via streaming)
# ---------------------------------------------------------------------------

def gerar_conteudo_subtopico(subtopico, profile=None):
    """Gera o texto didático de um único subtópico (uma página de leitura)."""
    nivel = subtopico.nivel
    trilha = nivel.trilha
    outros = ', '.join(s.titulo for s in nivel.subtopicos.all())

    fechamento = ''
    if subtopico.eh_ultimo:
        fechamento = (
            '\n\nComo este é o ÚLTIMO subtópico do nível, ao final do texto acrescente '
            'duas seções que fecham o nível inteiro: "## Bibliografia e referências" e '
            '"## Vídeos e materiais" (descreva o que procurar, canais e autores de '
            'referência, sem inventar URLs específicas).'
        )

    user = (
        f'Trilha: {trilha.titulo}\n'
        f'Tema geral: {trilha.tema_livre.strip()}\n'
        f'Nível {nivel.ordem} — {nivel.titulo} (faixa: {nivel.get_faixa_display()})\n'
        f'Subtópicos do nível (contexto): {outros}\n\n'
        f'Escreva o material APENAS do subtópico "{subtopico.titulo}"'
        f'{f" — {subtopico.descricao_curta}" if subtopico.descricao_curta else ""}. '
        'Aprofunde só este subtópico, sem invadir os outros.'
        f'{fechamento}'
    )

    client = get_client()
    model = _model_geral()
    max_tokens = getattr(settings, 'AI_MAX_TOKENS_CONTEUDO', 32000)
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=_system_cache(prompts.SYSTEM_SUBTOPICO),
        messages=[{'role': 'user', 'content': user}],
        thinking={'type': 'adaptive'},
        output_config={'effort': getattr(settings, 'AI_EFFORT_GERAL', 'medium')},
    ) as stream:
        final = stream.get_final_message()

    _debitar(profile, final.usage, model)
    return inserir_fotos_conteudo(
        _texto(final.content),
        contexto=f'{trilha.titulo} — tópico "{subtopico.titulo}"',
        profile=profile,
    )


# ---------------------------------------------------------------------------
# 3b. Roteiro falado para o vídeo do subtópico (Sonnet)
# ---------------------------------------------------------------------------

def gerar_roteiro_video(subtopico, profile=None):
    """Gera a narração falada de cada seção do subtópico para o vídeo narrado.

    Fatia o `conteudo_md` nas mesmas seções `---` que viram cards na leitura e
    pede à IA uma narração por seção que cubra TODO o conteúdo (inclusive código,
    diagramas e tabelas explicados em voz). Retorna uma lista alinhada às seções:
    ``[{'md': <markdown da seção>, 'narracao': <texto falado>}, ...]``.
    """
    from trilhas.video_utils import fatiar_secoes

    secoes = fatiar_secoes(subtopico.conteudo_md)
    if not secoes:
        return []

    blocos = '\n\n'.join(
        f'### Seção {i}\n{s}' for i, s in enumerate(secoes, start=1)
    )
    user = (
        f'Tópico: "{subtopico.titulo}"'
        f'{f" — {subtopico.descricao_curta}" if subtopico.descricao_curta else ""}.\n\n'
        f'O material tem {len(secoes)} seções, numeradas abaixo. Escreva a narração '
        'falada de CADA seção (uma entrada em "slides" por seção, com o mesmo número '
        'em "ordem"), cobrindo todo o conteúdo — explicando em voz o que houver de '
        'código, diagrama ou tabela.\n\n'
        f'{blocos}'
    )
    data = _gerar_json(
        prompts.SYSTEM_ROTEIRO_VIDEO, user, prompts.SCHEMA_ROTEIRO, profile,
        model=_model_geral(), effort=getattr(settings, 'AI_EFFORT_GERAL', 'medium'),
        max_tokens=getattr(settings, 'AI_MAX_TOKENS', 16000),
    )

    # Alinha por "ordem" (1-based); fallback = descrição curta ou 1ª linha da seção.
    por_ordem = {}
    for item in data.get('slides', []):
        try:
            k = int(item.get('ordem'))
        except (TypeError, ValueError):
            continue
        narr = (item.get('narracao') or '').strip()
        if narr:
            por_ordem[k] = narr

    roteiro = []
    for i, secao in enumerate(secoes, start=1):
        narr = por_ordem.get(i)
        if not narr:
            narr = (subtopico.descricao_curta or '').strip() \
                or secao.splitlines()[0].strip('# ').strip()
        roteiro.append({'md': secao, 'narracao': narr})
    return roteiro


# ---------------------------------------------------------------------------
# 4. Avaliação (Sonnet gera; correção objetiva determinística por gabarito)
# ---------------------------------------------------------------------------

def _questoes_validas(items):
    """Filtra questões estruturalmente completas: enunciado preenchido,
    4+ alternativas com letra/texto e gabarito apontando para uma delas.
    O JSON schema dos structured outputs não impõe minItems, então modelos
    ocasionalmente devolvem menos itens ou itens degenerados."""
    validas = []
    for q in items or []:
        enunciado = (q.get('enunciado') or '').strip()
        alts = [
            a for a in (q.get('alternativas') or [])
            if isinstance(a, dict)
            and str(a.get('letra', '')).strip() and str(a.get('texto', '')).strip()
        ]
        letras = {str(a['letra']).strip().upper() for a in alts}
        gabarito = (q.get('gabarito') or '').strip().upper()
        if enunciado and len(alts) >= 4 and gabarito in letras:
            validas.append({**q, 'alternativas': alts, 'gabarito': gabarito})
    return validas


def gerar_avaliacao(avaliacao, profile=None):
    from avaliacoes.models import Questao

    nivel = avaliacao.nivel
    subs = '\n'.join(f'- {s.titulo}' for s in nivel.subtopicos.all())

    user = (
        f'Nível: {nivel.titulo} (faixa: {nivel.get_faixa_display()})\n'
        f'Resumo: {nivel.resumo}\n'
        f'Subtópicos:\n{subs}\n\n'
        'Elabore uma avaliação com EXATAMENTE 10 questões objetivas (múltipla '
        'escolha, 4 a 5 alternativas com uma correta), em dificuldade MÉDIA e ALTA — '
        'sem questões fáceis ou triviais; exija raciocínio, aplicação e análise. Preencha '
        '"alternativas" com objetos {letra, texto} e "gabarito" com a letra correta. '
        'Use "peso" 1.0 em todas. Não crie questões dissertativas. Numere em '
        '"ordem" de 1 a 10. Não repita questões nem insira itens de aviso ou '
        'placeholder: cada item deve ser uma questão completa e distinta.'
    )

    questoes = []
    for _tentativa in range(2):  # 1 chamada + 1 nova tentativa se vier incompleta
        data = _gerar_json(
            prompts.SYSTEM_AVALIACAO, user, prompts.SCHEMA_AVALIACAO, profile,
            model=_model_geral(), effort=getattr(settings, 'AI_EFFORT_GERAL', 'medium'),
        )
        questoes = _questoes_validas(data.get('questoes'))
        if len(questoes) >= 10:
            break
    if len(questoes) < 10:
        raise IAError(
            f'A IA retornou {len(questoes)} questões válidas em vez de 10; avaliação não publicada.'
        )

    # Só substitui as questões existentes depois de validar o novo conjunto.
    avaliacao.questoes.all().delete()
    objs = []
    for i, q in enumerate(questoes[:10], start=1):
        objs.append(Questao(
            avaliacao=avaliacao,
            ordem=i,  # renumera em sequência; a "ordem" do modelo pode vir duplicada
            tipo=q.get('tipo', 'objetiva'),
            enunciado_md=(q.get('enunciado') or '').strip(),
            alternativas=q['alternativas'],
            gabarito=q['gabarito'],
            peso=q.get('peso') or 1.0,
        ))
    Questao.objects.bulk_create(objs)
    return avaliacao


def corrigir_avaliacao(avaliacao, profile=None):
    """Corrige a avaliação (objetivas, gabarito determinístico), nota e progressão."""
    soma_pesos = 0.0
    soma_notas = 0.0

    for questao in avaliacao.questoes.all().prefetch_related('resposta'):
        resposta = getattr(questao, 'resposta', None)
        if resposta is None:
            continue
        peso = questao.peso or 1.0

        correta = (questao.gabarito or '').strip().upper()
        escolhida = (resposta.alternativa_escolhida or '').strip().upper()
        nota = 10.0 if escolhida and escolhida == correta else 0.0
        resposta.nota = nota
        resposta.feedback_md = (
            'Resposta correta.' if nota else
            f'Resposta incorreta. Gabarito: **{correta or "—"}**.'
        )

        resposta.corrigida_em = timezone.now()
        resposta.save(update_fields=['nota', 'feedback_md', 'corrigida_em'])

        soma_pesos += peso
        soma_notas += resposta.nota * peso

    nota_final = round(soma_notas / soma_pesos, 2) if soma_pesos else 0.0
    trilha = avaliacao.nivel.trilha
    aprovado = nota_final >= (trilha.nota_minima_aprovacao or 7.0)

    avaliacao.nota_final = nota_final
    avaliacao.aprovado = aprovado
    avaliacao.status = avaliacao.Status.CORRIGIDA
    avaliacao.corrigida_em = timezone.now()
    avaliacao.feedback_geral = (
        f'Você atingiu {nota_final:.1f}. '
        + ('Aprovado! Nível concluído.' if aprovado
           else f'Nota mínima é {trilha.nota_minima_aprovacao:.1f}. Tente novamente.')
    )
    avaliacao.save()

    if aprovado:
        _aprovar_nivel(avaliacao.nivel)
        if profile is not None:
            profile.registrar_atividade(profile.XP_APROVACAO)
    return avaliacao


def _aprovar_nivel(nivel):
    from avaliacoes.models import Titulo
    from trilhas.models import Nivel, Trilha

    nivel.status = Nivel.Status.APROVADO
    nivel.save(update_fields=['status', 'atualizado_em'])
    nivel.iniciar_revisao_espacada()  # agenda a 1ª revisão espaçada

    nome_titulo = nivel.titulo_concedido or f'{nivel.get_faixa_display()} em {nivel.trilha.titulo}'
    Titulo.objects.get_or_create(
        nivel=nivel,
        defaults={'trilha': nivel.trilha, 'nome': nome_titulo, 'faixa': nivel.faixa},
    )

    proximo = (
        nivel.trilha.niveis
        .filter(ordem__gt=nivel.ordem, status=Nivel.Status.BLOQUEADO)
        .order_by('ordem')
        .first()
    )
    if proximo:
        proximo.status = Nivel.Status.DISPONIVEL
        proximo.save(update_fields=['status', 'atualizado_em'])

    trilha = nivel.trilha
    trilha.status = Trilha.Status.CONCLUIDA if trilha.concluida else Trilha.Status.EM_ANDAMENTO
    trilha.save(update_fields=['status', 'atualizada_em'])


# ---------------------------------------------------------------------------
# 5. Exercícios de prática (Sonnet — sem nota)
# ---------------------------------------------------------------------------

def gerar_exercicios(lista, profile=None):
    from avaliacoes.models import Exercicio

    nivel = lista.nivel
    subs = '\n'.join(f'- {s.titulo}' for s in nivel.subtopicos.all())
    user = (
        f'Nível: {nivel.titulo} (faixa: {nivel.get_faixa_display()})\n'
        f'Resumo: {nivel.resumo}\n'
        f'Subtópicos:\n{subs}\n\n'
        'Crie EXATAMENTE 5 exercícios de prática, TODOS objetivos (múltipla escolha), '
        'em dificuldade MÉDIA e ALTA — sem exercícios fáceis ou triviais; exija '
        'aplicação e raciocínio. Para cada um: "alternativas" com {letra, texto} e "gabarito" '
        'com a letra correta. Não crie exercícios dissertativos. Sempre preencha '
        '"explicacao" com um comentário didático que será mostrado como feedback. '
        'Numere em "ordem" de 1 a 5.'
    )
    data = _gerar_json(
        prompts.SYSTEM_EXERCICIOS, user, prompts.SCHEMA_EXERCICIOS, profile,
        model=_model_geral(), effort=getattr(settings, 'AI_EFFORT_GERAL', 'medium'),
    )

    lista.exercicios.all().delete()
    objs = []
    for i, e in enumerate(data.get('exercicios', []), start=1):
        objs.append(Exercicio(
            lista=lista,
            ordem=e.get('ordem') or i,
            tipo=e.get('tipo', 'objetiva'),
            enunciado_md=(e.get('enunciado') or '').strip(),
            alternativas=e.get('alternativas', []) or [],
            gabarito=(e.get('gabarito') or '').strip(),
            explicacao_md=(e.get('explicacao') or '').strip(),
        ))
    Exercicio.objects.bulk_create(objs)
    return lista


# ---------------------------------------------------------------------------
# 6. Categorização das trilhas (Sonnet — agrupa trilhas semelhantes)
# ---------------------------------------------------------------------------

def categorizar_trilhas(trilhas, profile=None):
    """Atribui uma categoria (área) a cada trilha da lista, agrupando afins.
    Recebe um iterável de Trilha; salva o campo `categoria`."""
    trilhas = [t for t in trilhas]
    if not trilhas:
        return {}

    linhas = []
    for t in trilhas:
        titulo = (t.titulo or t.tema_livre or '').strip()[:120]
        linhas.append(f'- id {t.pk}: {titulo}')
    user = (
        'Classifique cada trilha abaixo em uma categoria ampla (área de '
        'conhecimento), agrupando temas semelhantes sob o MESMO rótulo:\n\n'
        + '\n'.join(linhas)
        + '\n\nResponda com um item por trilha, repetindo "id" e a "categoria".'
    )
    data = _gerar_json(
        prompts.SYSTEM_CATEGORIA, user, prompts.SCHEMA_CATEGORIA, profile,
        model=_model_geral(), effort=getattr(settings, 'AI_EFFORT_GERAL', 'medium'),
    )
    por_id = {t.pk: t for t in trilhas}
    resultado = {}
    for item in data.get('categorias', []):
        t = por_id.get(item.get('id'))
        cat = (item.get('categoria') or '').strip()[:60]
        if t is not None and cat:
            t.categoria = cat
            t.save(update_fields=['categoria', 'atualizada_em'])
            resultado[t.pk] = cat
    return resultado


# ---------------------------------------------------------------------------
# 8. Mentor — percurso personalizado entre as trilhas (Opus — planejamento)
# ---------------------------------------------------------------------------

def _catalogo_percurso(user):
    """Levanta as ações possíveis agora, cada uma com um 'ref' estável."""
    from trilhas.models import Nivel, Trilha

    prontas = (
        Trilha.objects.filter(user=user, ativa=True)
        .exclude(status__in=[
            Trilha.Status.RASCUNHO, Trilha.Status.GERANDO_PERGUNTAS,
            Trilha.Status.AGUARDANDO_RESPOSTAS, Trilha.Status.GERANDO_SUMARIO,
            Trilha.Status.ERRO,
        ])
        .prefetch_related('niveis')
    )
    agora = timezone.now()
    catalogo = {}   # ref -> dict(descricao, tipo, nivel_id, sub_ordem)
    linhas = []
    tem_aprovado = False

    for t in prontas:
        dias = max((agora - t.atualizada_em).days, 0)
        cat = t.categoria_display

        alvo = t.proximo_topico
        if alvo:
            nivel, sub = alvo
            ref = f'aprender:{nivel.pk}:{sub.ordem}'
            catalogo[ref] = {'tipo': 'aprender', 'nivel_id': nivel.pk, 'sub_ordem': sub.ordem}
            linhas.append(
                f'- ref "{ref}" [APRENDER] trilha "{t.titulo}" ({cat}), nível '
                f'"{nivel.titulo}", próximo tópico "{sub.titulo}". Parada há {dias} dia(s).'
            )

        nv = t.nivel_atual
        if nv and nv.conteudo_lido:
            lista = getattr(nv, 'lista_exercicios', None)
            if lista and lista.concluida:
                ref = f'avaliar:{nv.pk}'
                catalogo[ref] = {'tipo': 'avaliar', 'nivel_id': nv.pk, 'sub_ordem': None}
                linhas.append(
                    f'- ref "{ref}" [AVALIAR] trilha "{t.titulo}" ({cat}): nível '
                    f'"{nv.titulo}" lido e praticado, pronto para a avaliação do título.'
                )

        for nivel in t.niveis.all():
            if nivel.status == Nivel.Status.APROVADO:
                tem_aprovado = True
                ref = f'revisar:{nivel.pk}'
                catalogo[ref] = {'tipo': 'revisar', 'nivel_id': nivel.pk, 'sub_ordem': None}
                vencida = ' — REVISÃO VENCIDA (priorize)' if nivel.revisao_devida else ''
                linhas.append(
                    f'- ref "{ref}" [REVISAR] trilha "{t.titulo}" ({cat}): revisar o '
                    f'nível concluído "{nivel.titulo}" (faixa {nivel.get_faixa_display()}){vencida}.'
                )

    if tem_aprovado:
        catalogo['revisar_global'] = {'tipo': 'revisar_global', 'nivel_id': None, 'sub_ordem': None}
        linhas.append(
            '- ref "revisar_global" [REVISAR] revisão espaçada única misturando '
            'perguntas de vários níveis já concluídos.'
        )

    return catalogo, '\n'.join(linhas)


def gerar_percurso(percurso, profile=None):
    from trilhas.models import Nivel, PassoPercurso

    catalogo, texto = _catalogo_percurso(percurso.user)
    if not catalogo:
        raise IAError('Ainda não há trilhas suficientes para o mentor planejar.')

    user = (
        'Estado atual do aluno — ações possíveis agora (escolha os passos apenas '
        'entre estes "ref"):\n\n'
        f'{texto}\n\n'
        'Monte um percurso de 3 a 6 passos para este momento, equilibrando as '
        'trilhas (não concentre tudo em uma só), misturando aprender e revisar, e '
        'priorizando o que está parado há mais tempo ou precisa de reforço. Para '
        'cada passo devolva o "ref" exato, um "titulo" curto e o "motivo". Inclua '
        'também um "resumo" de abertura.'
    )
    data = _gerar_json(
        prompts.SYSTEM_MENTOR, user, prompts.SCHEMA_MENTOR, profile,
        model=_model_planejamento(), effort=getattr(settings, 'AI_EFFORT', 'high'),
    )

    percurso.resumo = (data.get('resumo') or '').strip()
    percurso.save(update_fields=['resumo'])

    percurso.passos.all().delete()
    objs = []
    ordem = 0
    for p in data.get('passos', []):
        ref = (p.get('ref') or '').strip()
        info = catalogo.get(ref)
        if info is None:
            continue
        ordem += 1
        objs.append(PassoPercurso(
            percurso=percurso,
            ordem=ordem,
            tipo=info['tipo'],
            titulo=(p.get('titulo') or '').strip()[:200],
            motivo=(p.get('motivo') or '').strip(),
            nivel_id=info['nivel_id'],
            subtopico_ordem=info['sub_ordem'],
        ))
    PassoPercurso.objects.bulk_create(objs)
    return percurso


# ---------------------------------------------------------------------------
# 6b. Sugestões de novas trilhas (Opus — planejamento)
# ---------------------------------------------------------------------------

def _catalogo_sugestoes(trilhas):
    """Resume as trilhas escolhidas como base, para a IA propor novas trilhas."""
    linhas = []
    for t in trilhas:
        titulo = (t.titulo or t.tema_livre or '').strip()
        cat = t.categoria_display
        partes = [f'- "{titulo}" (área: {cat})']
        if t.descricao:
            partes.append(f'  resumo: {t.descricao.strip()[:300]}')
        objetivos = [str(o).strip() for o in (t.objetivos or []) if str(o).strip()]
        if objetivos:
            partes.append('  objetivos: ' + '; '.join(objetivos[:6]))
        atual = t.titulo_atual
        if atual:
            partes.append(f'  progresso: já alcançou o título "{atual.nome}".')
        linhas.append('\n'.join(partes))
    return '\n'.join(linhas)


def gerar_sugestoes(sessao, profile=None):
    from trilhas.models import TrilhaSugerida

    trilhas = list(sessao.trilhas_base.all())
    if not trilhas:
        raise IAError('Escolha ao menos uma trilha para basear as sugestões.')

    texto = _catalogo_sugestoes(trilhas)
    user = (
        'Trilhas que a pessoa já estuda (leve-as em consideração):\n\n'
        f'{texto}\n\n'
        'Proponha de 6 a 8 sugestões de novas trilhas, equilibrando os tipos '
        '"aprofundar" (ir mais fundo no que ela já estuda) e "direcao" (novas '
        'direções adjacentes). Para cada uma dê "titulo", "tipo", "enfoque" '
        '(1-2 frases) e "topicos" (4 a 7 tópicos principais). Não repita as '
        'trilhas acima.'
    )
    data = _gerar_json(
        prompts.SYSTEM_SUGESTOES, user, prompts.SCHEMA_SUGESTOES, profile,
        model=_model_planejamento(), effort=getattr(settings, 'AI_EFFORT', 'high'),
    )

    sessao.sugestoes.all().delete()
    objs = []
    for i, s in enumerate(data.get('sugestoes', []), start=1):
        titulo = (s.get('titulo') or '').strip()
        if not titulo:
            continue
        tipo = s.get('tipo') if s.get('tipo') in ('aprofundar', 'direcao') else 'direcao'
        topicos = [str(t).strip() for t in (s.get('topicos') or []) if str(t).strip()]
        objs.append(TrilhaSugerida(
            sessao=sessao,
            ordem=i,
            tipo=tipo,
            titulo=titulo[:200],
            enfoque=(s.get('enfoque') or '').strip(),
            topicos=topicos,
        ))
    if not objs:
        raise IAError('A IA não devolveu sugestões válidas.')
    TrilhaSugerida.objects.bulk_create(objs)
    return objs


# ---------------------------------------------------------------------------
# 7. Revisão espaçada (Sonnet — quiz misto sobre níveis já concluídos)
# ---------------------------------------------------------------------------

def gerar_revisao(revisao, profile=None, trilha_id=None):
    from avaliacoes.models import QuestaoRevisao
    from trilhas.models import Nivel

    qs = Nivel.objects.filter(
        trilha__user=revisao.user, trilha__ativa=True, status=Nivel.Status.APROVADO
    )
    if trilha_id:
        qs = qs.filter(trilha_id=trilha_id)
    niveis = list(qs.select_related('trilha').order_by('?')[:10])
    if not niveis:
        raise IAError('Nenhum nível concluído para revisar ainda.')

    blocos = []
    for i, nv in enumerate(niveis, start=1):
        subs = ', '.join(s.titulo for s in nv.subtopicos.all())
        blocos.append(
            f'Nível {i} — trilha "{nv.trilha.titulo}", nível "{nv.titulo}" '
            f'(faixa {nv.get_faixa_display()}). Subtópicos: {subs}'
        )
    n_questoes = 10 if trilha_id else min(12, max(6, len(niveis) * 3))
    user = (
        'Níveis já concluídos pelo aluno (use-os como base da revisão):\n\n'
        + '\n'.join(blocos)
        + f'\n\nCrie {n_questoes} questões objetivas de revisão, misturando os '
        'níveis acima, em dificuldade MÉDIA e ALTA (sem questões fáceis ou triviais). Em "origem" coloque o NÚMERO do '
        'nível de referência (1 a ' + str(len(niveis)) + '). Preencha '
        '"alternativas" com {letra, texto}, "gabarito" com a letra correta e '
        '"explicacao" com um comentário didático. Numere em "ordem" a partir de 1.'
    )
    data = _gerar_json(
        prompts.SYSTEM_REVISAO, user, prompts.SCHEMA_REVISAO, profile,
        model=_model_geral(), effort=getattr(settings, 'AI_EFFORT_GERAL', 'medium'),
    )

    revisao.questoes.all().delete()
    objs = []
    for i, q in enumerate(data.get('questoes', []), start=1):
        idx = q.get('origem') or 1
        nv = niveis[idx - 1] if 1 <= idx <= len(niveis) else niveis[0]
        objs.append(QuestaoRevisao(
            revisao=revisao,
            ordem=q.get('ordem') or i,
            nivel=nv,
            origem=f'{nv.trilha.titulo} · {nv.titulo}',
            enunciado_md=(q.get('enunciado') or '').strip(),
            alternativas=q.get('alternativas', []) or [],
            gabarito=(q.get('gabarito') or '').strip(),
            explicacao_md=(q.get('explicacao') or '').strip(),
        ))
    QuestaoRevisao.objects.bulk_create(objs)
    return revisao
