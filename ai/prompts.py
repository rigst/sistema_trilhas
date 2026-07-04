"""
System prompts e JSON schemas usados na integração com o Claude.

Todos os textos são em português. Os schemas usam apenas construtos suportados
por structured outputs (sem minItems/maxItems); a quantidade é pedida no prompt.
"""

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_PERGUNTAS = (
    'Você é um tutor especialista em desenhar trilhas de estudo personalizadas. '
    'Antes de montar um plano, você faz poucas e boas perguntas de MÚLTIPLA ESCOLHA '
    'para calibrar o nível atual da pessoa, o objetivo e o foco de interesse. '
    'Você NÃO pergunta sobre tempo disponível nem sobre formato de estudo, e NUNCA '
    'faz perguntas abertas/dissertativas. Responda sempre em português.'
)

SYSTEM_SUMARIO = (
    'Você é um tutor especialista que projeta trilhas de estudo completas, do '
    'básico ao avançado. A partir do tema e das respostas do aluno, você monta um '
    'sumário progressivo em níveis, cada um com subtópicos coerentes e um título '
    'motivador a ser concedido (ex.: "Iniciante em X", "Especialista em X", '
    '"Mestre em X"). Responda sempre em português.'
)

SYSTEM_CONTEUDO = (
    'Você é um tutor especialista que escreve material didático completo, '
    'aprofundado e VISUALMENTE BEM ESTRUTURADO em português, formatado em Markdown. '
    'Para o nível pedido, produza um texto rico cobrindo TODOS os subtópicos.\n\n'
    'REGRAS DE FORMATAÇÃO (siga à risca):\n'
    '- Organize com subtítulos: use "## " para cada subtópico e "### " para partes internas.\n'
    '- Use caixas de destaque (admonitions) com a sintaxe exata abaixo, com o '
    'conteúdo indentado em 4 espaços. Tipos disponíveis (use o tipo em minúsculas):\n'
    '    !!! conceito "Título curto"\n'
    '        Definição ou ideia central.\n'
    '  Tipos: `conceito` (ideias-chave), `exemplo` (exemplos aplicados), '
    '`dica` (boas práticas), `atencao` (pegadinhas/erros comuns), `resumo` '
    '(fechamento do subtópico). Use várias ao longo do texto.\n'
    '- TODO bloco de código deve declarar a linguagem na cerca, ex.: ```python … ```, '
    '```sql … ```, ```bash … ``` — nunca use cercas sem linguagem (é o que ativa as '
    'cores de sintaxe).\n'
    '- Use listas, **negrito** e tabelas Markdown quando ajudarem a leitura.\n\n'
    'CONTEÚDO: introdução com objetivos; conceitos bem explicados; exemplos '
    'práticos com código; ao menos um exercício resolvido passo a passo; uma seção '
    '"## Bibliografia e referências"; e uma seção "## Vídeos e materiais" (descreva '
    'o que procurar e canais/autores de referência, sem inventar URLs específicas). '
    'Seja completo, didático e correto, adequando a profundidade à faixa do nível.'
)

SYSTEM_SUBTOPICO = (
    'Você é um tutor especialista que escreve material didático de UM subtópico por '
    'vez, em português, formatado em Markdown, aprofundado e visualmente bem '
    'estruturado.\n\n'
    'REGRAS DE FORMATAÇÃO (siga à risca):\n'
    '- Comece direto no conteúdo do subtópico. NÃO repita o título do subtópico como '
    'H1 (a interface já mostra). Use "### " para as partes internas.\n'
    '- Use caixas de destaque (admonitions) com esta sintaxe exata, conteúdo '
    'indentado em 4 espaços:\n'
    '    !!! conceito "Título curto"\n'
    '        Definição ou ideia central.\n'
    '  Tipos (em minúsculas): `conceito`, `exemplo`, `dica`, `atencao`, `resumo`. '
    'Use várias ao longo do texto.\n'
    '- TODO bloco de código deve declarar a linguagem na cerca (```python, ```sql, '
    '```bash …) — nunca use cercas sem linguagem.\n'
    '- Use listas, **negrito** e tabelas quando ajudarem.\n\n'
    'CONTEÚDO: explique os conceitos com clareza, traga exemplos práticos com código '
    'e um exemplo resolvido/comentado quando fizer sentido. Termine com uma caixa '
    '`!!! resumo` recapitulando os pontos-chave.\n'
    'IMPORTANTE: NÃO inclua listas de "exercícios propostos", perguntas com gabarito, '
    'nem respostas de quiz — a prática acontece numa etapa separada. Foque em ensinar.'
)

SYSTEM_EXERCICIOS = (
    'Você é um tutor que cria exercícios de PRÁTICA para fixar um nível. Use APENAS '
    'questões objetivas (múltipla escolha), cobrindo os subtópicos, em dificuldade '
    'progressiva (das mais simples às mais difíceis). Para cada exercício, escreva '
    'uma explicação clara da resposta, que será mostrada como feedback imediato ao '
    'aluno. Não crie questões dissertativas. Responda em português.'
)

SYSTEM_AVALIACAO = (
    'Você é um avaliador especialista. Elabora avaliações que medem de verdade o '
    'aprendizado de um nível usando APENAS questões objetivas (múltipla escolha), '
    'em dificuldade progressiva (das mais simples às mais difíceis). As questões '
    'devem cobrir os subtópicos do nível e o conteúdo estudado. Não crie questões '
    'dissertativas. Responda sempre em português.'
)

SYSTEM_CORRECAO = (
    'Você é um corretor especialista, rigoroso e justo. Avalia respostas '
    'dissertativas comparando-as à rubrica esperada e atribui uma nota de 0 a 10, '
    'com feedback construtivo em português (Markdown).'
)


# ---------------------------------------------------------------------------
# JSON Schemas (structured outputs)
# ---------------------------------------------------------------------------

SCHEMA_PERGUNTAS = {
    'type': 'object',
    'properties': {
        'perguntas': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'ordem': {'type': 'integer'},
                    'pergunta': {'type': 'string'},
                    'tipo': {'type': 'string', 'enum': ['escolha_unica']},
                    'opcoes': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['ordem', 'pergunta', 'tipo', 'opcoes'],
                'additionalProperties': False,
            },
        }
    },
    'required': ['perguntas'],
    'additionalProperties': False,
}

SCHEMA_SUMARIO = {
    'type': 'object',
    'properties': {
        'titulo': {'type': 'string'},
        'descricao': {'type': 'string'},
        'emblema': {'type': 'string'},
        'objetivos': {'type': 'array', 'items': {'type': 'string'}},
        'niveis': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'ordem': {'type': 'integer'},
                    'titulo': {'type': 'string'},
                    'resumo': {'type': 'string'},
                    'faixa': {
                        'type': 'string',
                        'enum': [
                            'iniciante', 'intermediario', 'avancado',
                            'especialista', 'mestre',
                        ],
                    },
                    'titulo_concedido': {'type': 'string'},
                    'subtopicos': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'ordem': {'type': 'integer'},
                                'titulo': {'type': 'string'},
                                'descricao_curta': {'type': 'string'},
                            },
                            'required': ['ordem', 'titulo', 'descricao_curta'],
                            'additionalProperties': False,
                        },
                    },
                },
                'required': [
                    'ordem', 'titulo', 'resumo', 'faixa',
                    'titulo_concedido', 'subtopicos',
                ],
                'additionalProperties': False,
            },
        },
    },
    'required': ['titulo', 'descricao', 'emblema', 'objetivos', 'niveis'],
    'additionalProperties': False,
}

SCHEMA_AVALIACAO = {
    'type': 'object',
    'properties': {
        'questoes': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'ordem': {'type': 'integer'},
                    'tipo': {'type': 'string', 'enum': ['objetiva']},
                    'enunciado': {'type': 'string'},
                    'alternativas': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'letra': {'type': 'string'},
                                'texto': {'type': 'string'},
                            },
                            'required': ['letra', 'texto'],
                            'additionalProperties': False,
                        },
                    },
                    'gabarito': {'type': 'string'},
                    'peso': {'type': 'number'},
                },
                'required': ['ordem', 'tipo', 'enunciado', 'alternativas', 'gabarito', 'peso'],
                'additionalProperties': False,
            },
        }
    },
    'required': ['questoes'],
    'additionalProperties': False,
}

SCHEMA_EXERCICIOS = {
    'type': 'object',
    'properties': {
        'exercicios': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'ordem': {'type': 'integer'},
                    'tipo': {'type': 'string', 'enum': ['objetiva']},
                    'enunciado': {'type': 'string'},
                    'alternativas': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'letra': {'type': 'string'},
                                'texto': {'type': 'string'},
                            },
                            'required': ['letra', 'texto'],
                            'additionalProperties': False,
                        },
                    },
                    'gabarito': {'type': 'string'},
                    'explicacao': {'type': 'string'},
                },
                'required': ['ordem', 'tipo', 'enunciado', 'alternativas', 'gabarito', 'explicacao'],
                'additionalProperties': False,
            },
        }
    },
    'required': ['exercicios'],
    'additionalProperties': False,
}

SCHEMA_CORRECAO = {
    'type': 'object',
    'properties': {
        'nota': {'type': 'number'},
        'feedback_md': {'type': 'string'},
        'pontos_fortes': {'type': 'array', 'items': {'type': 'string'}},
        'pontos_a_melhorar': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['nota', 'feedback_md', 'pontos_fortes', 'pontos_a_melhorar'],
    'additionalProperties': False,
}
