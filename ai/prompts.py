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

SYSTEM_CATEGORIA = (
    'Você organiza trilhas de estudo em categorias amplas (áreas de conhecimento), '
    'como se fossem pastas. Agrupa temas semelhantes sob o MESMO nome de categoria, '
    'curto e reconhecível (ex.: "Programação", "Direito", "História", "Idiomas", '
    '"Música", "Saúde", "Negócios", "Matemática", "Ciências"). Reutilize exatamente '
    'o mesmo rótulo para trilhas afins. Responda sempre em português.'
)

SYSTEM_MENTOR = (
    'Você é um mentor de estudos pessoal. Analisa o estado do aluno em TODAS as '
    'trilhas e monta um percurso curto e equilibrado para o momento atual, de modo '
    'que ele mantenha todas as trilhas evoluindo (sem abandonar nenhuma) e reforce '
    'o que já aprendeu por revisão espaçada. Escolha os passos APENAS entre as '
    'opções fornecidas (cada uma tem um "ref"). Equilibre aprender e revisar, dê '
    'prioridade ao que está parado há mais tempo e ao que precisa de reforço, e '
    'explique em "motivo" por que cada passo faz sentido agora. Escreva também um '
    '"resumo" acolhedor (1-3 frases) como abertura do mentor. Responda em português.'
)

SYSTEM_REVISAO = (
    'Você é um tutor que cria REVISÕES espaçadas: um quiz objetivo que mistura '
    'perguntas sobre níveis que o aluno já concluiu em diferentes trilhas, para '
    'reforçar a memória. Use APENAS questões objetivas (múltipla escolha), '
    'distribuídas entre os níveis fornecidos, com dificuldade variada. Para cada '
    'questão indique em "origem" o número do nível de referência e escreva uma '
    '"explicacao" clara da resposta. Responda em português.'
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
        'categoria': {'type': 'string'},
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
    'required': ['titulo', 'descricao', 'emblema', 'categoria', 'objetivos', 'niveis'],
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

SCHEMA_CATEGORIA = {
    'type': 'object',
    'properties': {
        'categorias': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'integer'},
                    'categoria': {'type': 'string'},
                },
                'required': ['id', 'categoria'],
                'additionalProperties': False,
            },
        }
    },
    'required': ['categorias'],
    'additionalProperties': False,
}

SCHEMA_MENTOR = {
    'type': 'object',
    'properties': {
        'resumo': {'type': 'string'},
        'passos': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'ref': {'type': 'string'},
                    'titulo': {'type': 'string'},
                    'motivo': {'type': 'string'},
                },
                'required': ['ref', 'titulo', 'motivo'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['resumo', 'passos'],
    'additionalProperties': False,
}

SCHEMA_REVISAO = {
    'type': 'object',
    'properties': {
        'questoes': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'ordem': {'type': 'integer'},
                    'origem': {'type': 'integer'},
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
                'required': ['ordem', 'origem', 'enunciado', 'alternativas', 'gabarito', 'explicacao'],
                'additionalProperties': False,
            },
        }
    },
    'required': ['questoes'],
    'additionalProperties': False,
}
