"""
Integração com a API da Anthropic (Claude).

Divisão de modelos por tarefa (configurável em settings):
  - Planejamento do sumário e correção de dissertativas → Opus 4.8 (mais julgamento).
  - Perguntas, conteúdo, avaliação e exercícios → Sonnet 4.6 (rápido/barato).

Fluxo:
  1. gerar_perguntas_direcionadoras
  2. gerar_sumario
  3. gerar_conteudo_nivel (streaming)
  4. gerar_avaliacao / corrigir_avaliacao (com progressão e título)
  5. gerar_exercicios / verificar_exercicio_dissertativa (prática, sem nota)

Toda chamada debita a quota de tokens do Profile do usuário.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from . import prompts


class IAError(Exception):
    pass


# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------

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


def _debitar(profile, usage, model):
    if profile is None or usage is None:
        return
    it = getattr(usage, 'input_tokens', 0) or 0
    ot = getattr(usage, 'output_tokens', 0) or 0
    profile.registrar_uso(it, ot, custo_usd(model, it, ot))


def _texto(content):
    return ''.join(b.text for b in content if getattr(b, 'type', '') == 'text')


def _gerar_json(system, user, schema, profile, model, effort, max_tokens=None):
    """Chamada estruturada (JSON schema) com raciocínio adaptativo."""
    client = get_client()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens or getattr(settings, 'AI_MAX_TOKENS', 16000),
        system=system,
        messages=[{'role': 'user', 'content': user}],
        thinking={'type': 'adaptive'},
        output_config={
            'effort': effort,
            'format': {'type': 'json_schema', 'schema': schema},
        },
    )
    _debitar(profile, resp.usage, model)
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
        'Gere de 4 a 6 perguntas direcionadoras para calibrar um plano de estudos '
        'personalizado. Cubra: nível/experiência atual no tema; objetivo ou '
        'aplicação pretendida; subtemas de maior interesse; tempo semanal '
        'disponível; e preferência entre teoria e prática. Use perguntas de '
        'escolha única quando fizer sentido (preenchendo "opcoes"); caso contrário, '
        'perguntas abertas com "opcoes" vazio. Numere em "ordem" a partir de 1.'
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
            tipo=p.get('tipo', 'aberta'),
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
        'com 4 a 7 níveis. Para cada nível: defina a faixa (iniciante → mestre) de '
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

    trilha.titulo = (data.get('titulo') or trilha.tema_livre[:120]).strip()
    trilha.descricao = (data.get('descricao') or '').strip()
    trilha.objetivos = data.get('objetivos', []) or []
    trilha.save(update_fields=['titulo', 'descricao', 'objetivos', 'atualizada_em'])

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
# 3. Conteúdo de um nível (Sonnet — texto longo via streaming)
# ---------------------------------------------------------------------------

def gerar_conteudo_nivel(nivel, profile=None):
    trilha = nivel.trilha
    subs = '\n'.join(
        f'- {s.titulo}: {s.descricao_curta}' for s in nivel.subtopicos.all()
    ) or '- (defina os subtópicos a partir do título do nível)'

    user = (
        f'Trilha: {trilha.titulo}\n'
        f'Tema geral: {trilha.tema_livre.strip()}\n'
        f'Nível {nivel.ordem} — {nivel.titulo} (faixa: {nivel.get_faixa_display()})\n'
        f'Resumo do nível: {nivel.resumo}\n\n'
        f'Subtópicos a cobrir:\n{subs}\n\n'
        'Escreva o material completo deste nível em Markdown, seguindo à risca as '
        'regras de formatação (subtítulos, caixas de destaque !!! e blocos de código '
        'com a linguagem declarada). Cubra todos os subtópicos.'
    )

    client = get_client()
    model = _model_geral()
    max_tokens = getattr(settings, 'AI_MAX_TOKENS_CONTEUDO', 32000)
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=prompts.SYSTEM_CONTEUDO,
        messages=[{'role': 'user', 'content': user}],
        thinking={'type': 'adaptive'},
        output_config={'effort': getattr(settings, 'AI_EFFORT_GERAL', 'medium')},
    ) as stream:
        final = stream.get_final_message()

    _debitar(profile, final.usage, model)
    return _texto(final.content)


# ---------------------------------------------------------------------------
# 4. Avaliação (Sonnet gera; Opus corrige dissertativas)
# ---------------------------------------------------------------------------

def gerar_avaliacao(avaliacao, profile=None):
    from avaliacoes.models import Questao

    nivel = avaliacao.nivel
    subs = '\n'.join(f'- {s.titulo}' for s in nivel.subtopicos.all())

    user = (
        f'Nível: {nivel.titulo} (faixa: {nivel.get_faixa_display()})\n'
        f'Resumo: {nivel.resumo}\n'
        f'Subtópicos:\n{subs}\n\n'
        'Elabore uma avaliação com 5 questões objetivas (múltipla escolha, 4 a 5 '
        'alternativas com uma correta) e 2 questões dissertativas. Para objetivas, '
        'preencha "alternativas" com objetos {letra, texto} e "gabarito" com a '
        'letra correta. Para dissertativas, deixe "alternativas" vazio e use '
        '"gabarito" como rubrica do que a resposta ideal deve conter. Defina "peso" '
        '(dissertativas costumam pesar mais). Numere em "ordem" a partir de 1.'
    )
    data = _gerar_json(
        prompts.SYSTEM_AVALIACAO, user, prompts.SCHEMA_AVALIACAO, profile,
        model=_model_geral(), effort=getattr(settings, 'AI_EFFORT_GERAL', 'medium'),
    )

    avaliacao.questoes.all().delete()
    objs = []
    for i, q in enumerate(data.get('questoes', []), start=1):
        objs.append(Questao(
            avaliacao=avaliacao,
            ordem=q.get('ordem') or i,
            tipo=q.get('tipo', 'objetiva'),
            enunciado_md=(q.get('enunciado') or '').strip(),
            alternativas=q.get('alternativas', []) or [],
            gabarito=(q.get('gabarito') or '').strip(),
            peso=q.get('peso') or 1.0,
        ))
    Questao.objects.bulk_create(objs)
    return avaliacao


def _corrigir_dissertativa(enunciado, rubrica, resposta_texto, profile, model, effort):
    user = (
        f'Enunciado da questão:\n{enunciado}\n\n'
        f'Rubrica esperada:\n{rubrica}\n\n'
        f'Resposta do aluno:\n{resposta_texto or "(em branco)"}\n\n'
        'Avalie a resposta de 0 a 10 conforme a rubrica e dê feedback em Markdown.'
    )
    return _gerar_json(
        prompts.SYSTEM_CORRECAO, user, prompts.SCHEMA_CORRECAO, profile,
        model=model, effort=effort,
    )


def corrigir_avaliacao(avaliacao, profile=None):
    """Corrige (objetivas determinísticas, dissertativas via Opus), nota e progressão."""
    model = _model_planejamento()
    effort = getattr(settings, 'AI_EFFORT', 'high')

    soma_pesos = 0.0
    soma_notas = 0.0

    for questao in avaliacao.questoes.all().prefetch_related('resposta'):
        resposta = getattr(questao, 'resposta', None)
        if resposta is None:
            continue
        peso = questao.peso or 1.0

        if questao.tipo == questao.Tipo.OBJETIVA:
            correta = (questao.gabarito or '').strip().upper()
            escolhida = (resposta.alternativa_escolhida or '').strip().upper()
            nota = 10.0 if escolhida and escolhida == correta else 0.0
            resposta.nota = nota
            resposta.feedback_md = (
                'Resposta correta.' if nota else
                f'Resposta incorreta. Gabarito: **{correta or "—"}**.'
            )
        else:
            res = _corrigir_dissertativa(
                questao.enunciado_md, questao.gabarito, resposta.resposta_texto,
                profile, model, effort,
            )
            nota = max(0.0, min(10.0, float(res.get('nota') or 0.0)))
            resposta.nota = nota
            resposta.feedback_md = _montar_feedback(res)

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
    return avaliacao


def _montar_feedback(res):
    partes = [res.get('feedback_md', '').strip()]
    fortes = res.get('pontos_fortes') or []
    melhorar = res.get('pontos_a_melhorar') or []
    if fortes:
        partes.append('**Pontos fortes:**\n' + '\n'.join(f'- {x}' for x in fortes))
    if melhorar:
        partes.append('**A melhorar:**\n' + '\n'.join(f'- {x}' for x in melhorar))
    return '\n\n'.join(p for p in partes if p)


def _aprovar_nivel(nivel):
    from avaliacoes.models import Titulo
    from trilhas.models import Nivel, Trilha

    nivel.status = Nivel.Status.APROVADO
    nivel.save(update_fields=['status', 'atualizado_em'])

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
        'Crie 6 exercícios de prática (4 objetivos e 2 dissertativos curtos) que '
        'ajudem a fixar este nível. Para objetivos: "alternativas" com {letra, texto} '
        'e "gabarito" com a letra correta. Para dissertativos: "alternativas" vazio e '
        '"gabarito" com a rubrica/resposta ideal. Sempre preencha "explicacao" com '
        'um comentário didático que será mostrado como feedback. Numere em "ordem".'
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


def verificar_exercicio_dissertativa(exercicio, resposta_texto, profile=None):
    """Feedback + nota (0–10) de um exercício dissertativo de prática (Sonnet)."""
    res = _corrigir_dissertativa(
        exercicio.enunciado_md, exercicio.gabarito, resposta_texto, profile,
        model=_model_geral(), effort=getattr(settings, 'AI_EFFORT_GERAL', 'medium'),
    )
    nota = max(0.0, min(10.0, float(res.get('nota') or 0.0)))
    return nota, _montar_feedback(res)
