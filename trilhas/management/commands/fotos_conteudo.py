"""Insere (ou audita) fotos reais da Pexels no conteúdo de subtópicos prontos.

Sem opções: para cada subtópico sem imagens, a IA aponta em quais seções uma
fotografia real traria informação ESSENCIAL (pode ser nenhuma); os marcadores
são resolvidos e auditados pela mesma rotina da geração de conteúdo novo.

Com --auditar: revisa as imagens JÁ INSERIDAS — as que não condizem com o
conteúdo ou são meramente decorativas são removidas.
"""
import re

from django.core.management.base import BaseCommand

from ai.services import IAError, _gerar_json, _model_geral, inserir_fotos_conteudo
from trilhas.models import Subtopico

SCHEMA_FOTOS = {
    'type': 'object',
    'properties': {
        'fotos': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'secao': {'type': 'integer'},
                    'query': {'type': 'string'},
                    'legenda': {'type': 'string'},
                },
                'required': ['secao', 'query', 'legenda'],
                'additionalProperties': False,
            },
        }
    },
    'required': ['fotos'],
    'additionalProperties': False,
}

SYSTEM = (
    'Você decide onde uma FOTO real de banco de imagens (Pexels) traria informação '
    'ESSENCIAL a um material didático dividido em seções — ver o objeto faz parte '
    'do aprendizado (identificar um instrumento, uma obra, um lugar, um equipamento, '
    'uma época). Foto decorativa não vale: se nenhuma seção precisa, retorne lista '
    'vazia. Nunca escolha seções de código, diagrama ou conceito abstrato. '
    'Responda em português.'
)

SCHEMA_AUDIT = {
    'type': 'object',
    'properties': {'remover': {'type': 'array', 'items': {'type': 'integer'}}},
    'required': ['remover'],
    'additionalProperties': False,
}

SYSTEM_AUDIT = (
    'Você audita imagens já inseridas em um material didático. Marque para '
    'REMOVER toda imagem cuja descrição não condiz com o trecho onde está '
    '(mostra outra coisa, é ambígua ou pode induzir o aluno a erro) ou que é '
    'meramente decorativa (não acrescenta informação essencial ao trecho). '
    'Na dúvida, remova. Responda em português.'
)

# Imagem inserida pelo sistema: linha ![alt](url pexels) + legenda em itálico.
IMG_RE = re.compile(
    r'!\[(?P<alt>[^\]]*)\]\((?P<url>https://images\.pexels\.com[^)]+)\)'
    r'(?:\n\*(?P<legenda>[^*\n]*)\*)?'
)


def _dividir_secoes(texto):
    """Divide o markdown nas seções entre linhas `---` (fora de cerca de código)."""
    secoes, atual, em_fence = [], [], False
    for linha in texto.split('\n'):
        if linha.lstrip().startswith('```'):
            em_fence = not em_fence
        # Só o `---` na coluna 0 separa seções (indentado pode ser corpo de admonition).
        if not em_fence and not linha.startswith(' ') and linha.strip() == '---':
            secoes.append(atual)
            atual = []
            continue
        atual.append(linha)
    secoes.append(atual)
    return secoes


class Command(BaseCommand):
    help = 'Insere (ou audita, com --auditar) fotos da Pexels nos subtópicos prontos'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Só mostra o que seria feito, sem salvar')
        parser.add_argument('--auditar', action='store_true',
                            help='Revisa as imagens já inseridas e remove as inadequadas')

    def handle(self, *args, **options):
        if options['auditar']:
            self._auditar(options)
        else:
            self._inserir(options)

    # -- inserção (subtópicos ainda sem imagem) ---------------------------
    def _inserir(self, options):
        qs = (
            Subtopico.objects.filter(status=Subtopico.Status.PRONTO)
            .exclude(conteudo_md='')
            .exclude(conteudo_md__contains='![')
            .select_related('nivel__trilha')
            .order_by('nivel__trilha_id', 'nivel__ordem', 'ordem')
        )
        self.stdout.write(f'{qs.count()} subtópico(s) sem imagens.')
        for sub in qs:
            trilha = sub.nivel.trilha
            secoes = _dividir_secoes(sub.conteudo_md)
            resumo = []
            for i, linhas in enumerate(secoes, start=1):
                trecho = ' '.join(
                    l.strip().lstrip('#').strip() for l in linhas if l.strip()
                )[:150]
                resumo.append(f'{i}. {trecho}')
            user = (
                f'Trilha: {trilha.titulo}\n'
                f'Tópico: {sub.titulo}\n\n'
                'Seções do texto (número + começo do conteúdo):\n'
                + '\n'.join(resumo)
                + '\n\nEscolha NO MÁXIMO 2 seções onde uma foto real traria informação '
                  'ESSENCIAL (ver o objeto faz parte do aprendizado). Se nenhuma '
                  'precisar, retorne "fotos" vazio. Para cada escolhida: "secao" '
                  '(número), "query" (termo de busca EM INGLÊS, 2 a 4 palavras, '
                  'cena/objeto concreto) e "legenda" (curta, em português).'
            )
            try:
                data = _gerar_json(SYSTEM, user, SCHEMA_FOTOS, None,
                                   model=_model_geral(), effort='low', max_tokens=2000)
            except IAError as exc:
                self.stdout.write(self.style.WARNING(f'  ✗ {sub.titulo[:50]} — {exc}'))
                continue
            n = 0
            for f in data.get('fotos', []):
                i = (f.get('secao') or 0) - 1
                query = (f.get('query') or '').strip()
                legenda = (f.get('legenda') or '').strip()
                if not (0 <= i < len(secoes)) or not query:
                    continue
                secoes[i].append(f'\n{{{{foto: {query} | {legenda}}}}}')
                n += 1
            if not n:
                self.stdout.write(f'  · {sub.titulo[:50]} — nenhuma foto necessária')
                continue
            novo = '\n---\n'.join('\n'.join(s) for s in secoes)
            novo = inserir_fotos_conteudo(
                novo, contexto=f'{trilha.titulo} — tópico "{sub.titulo}"')
            inseridas = novo.count('![')
            if options['dry_run']:
                self.stdout.write(f'  · {sub.titulo[:50]} — {n} marcador(es), '
                                  f'{inseridas} foto(s) aprovadas (dry-run)')
                continue
            if inseridas:
                sub.conteudo_md = novo
                sub.save(update_fields=['conteudo_md'])
                self.stdout.write(f'  ✓ {sub.titulo[:50]} — {inseridas} foto(s)')
            else:
                self.stdout.write(f'  · {sub.titulo[:50]} — nenhuma foto aprovada')
        self.stdout.write(self.style.SUCCESS('Concluído.'))

    # -- auditoria (imagens já inseridas) ----------------------------------
    def _auditar(self, options):
        qs = (
            Subtopico.objects.filter(status=Subtopico.Status.PRONTO,
                                     conteudo_md__contains='![')
            .select_related('nivel__trilha')
            .order_by('nivel__trilha_id', 'nivel__ordem', 'ordem')
        )
        self.stdout.write(f'{qs.count()} subtópico(s) com imagens para auditar.')
        removidas_total = 0
        for sub in qs:
            trilha = sub.nivel.trilha
            texto = sub.conteudo_md
            matches = list(IMG_RE.finditer(texto))
            if not matches:
                continue
            linhas = []
            for i, m in enumerate(matches, start=1):
                # Trecho de texto imediatamente anterior à imagem (contexto real).
                antes = texto[:m.start()]
                contexto = ' '.join(antes.split('\n')[-6:]).strip()[-260:]
                legenda = (m.group('legenda') or m.group('alt') or '').strip()
                linhas.append(f'{i}. Imagem: "{legenda}"\n   Trecho onde está: "…{contexto}"')
            user = (
                f'Trilha: {trilha.titulo}\n'
                f'Tópico: {sub.titulo}\n\n'
                'Imagens inseridas e o trecho em que aparecem:\n\n'
                + '\n'.join(linhas)
                + '\n\nRetorne em "remover" os números das imagens que não condizem '
                  'com o trecho ou que são apenas decorativas.'
            )
            try:
                data = _gerar_json(SYSTEM_AUDIT, user, SCHEMA_AUDIT, None,
                                   model=_model_geral(), effort='low', max_tokens=1500)
            except IAError as exc:
                self.stdout.write(self.style.WARNING(f'  ✗ {sub.titulo[:50]} — {exc}'))
                continue
            remover = {int(i) for i in data.get('remover', [])}
            if not remover:
                self.stdout.write(f'  · {sub.titulo[:50]} — todas ok')
                continue
            novo = texto
            for i in sorted(remover, reverse=True):
                if not (1 <= i <= len(matches)):
                    continue
                m = matches[i - 1]
                novo = novo[:m.start()] + novo[m.end():]
            novo = re.sub(r'\n{3,}', '\n\n', novo)
            removidas = len([i for i in remover if 1 <= i <= len(matches)])
            removidas_total += removidas
            if options['dry_run']:
                self.stdout.write(f'  · {sub.titulo[:50]} — removeria {removidas} (dry-run)')
                continue
            sub.conteudo_md = novo
            sub.save(update_fields=['conteudo_md'])
            self.stdout.write(f'  ✓ {sub.titulo[:50]} — {removidas} imagem(ns) removida(s)')
        self.stdout.write(self.style.SUCCESS(
            f'Auditoria concluída — {removidas_total} imagem(ns) removida(s).'))
