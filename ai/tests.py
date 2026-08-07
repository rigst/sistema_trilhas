"""Testes das utilidades de IA que não tocam a API."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from ai.services import limpar_titulo


class LimparTituloTests(SimpleTestCase):
    def test_remove_prefixos_de_trilha_curso_guia(self):
        casos = {
            'Trilha de Python do Zero': 'Python do Zero',
            'Curso completo de Violão': 'Violão',
            'Trilha: História do Brasil': 'História do Brasil',
            'Guia de SQL': 'SQL',
            'trilha para concursos': 'concursos',
        }
        for entrada, esperado in casos.items():
            self.assertEqual(limpar_titulo(entrada), esperado)

    def test_preserva_titulos_ja_diretos(self):
        for t in ('Fotografia de Rua', 'Redes de Computadores', 'Direito Penal'):
            self.assertEqual(limpar_titulo(t), t)

    def test_prefixos_encadeados_sao_removidos(self):
        self.assertEqual(
            limpar_titulo('Trilha de Estudos: Batuque no RS'), 'Batuque no RS'
        )

    def test_nao_devolve_vazio(self):
        self.assertEqual(limpar_titulo('Trilha'), 'Trilha')
        self.assertEqual(limpar_titulo(''), '')


def _q(ordem, letras=('A', 'B', 'C', 'D'), gabarito='A', enunciado='Enunciado da questão?'):
    return {
        'ordem': ordem,
        'tipo': 'objetiva',
        'enunciado': enunciado,
        'alternativas': [{'letra': l, 'texto': f'Alternativa {l}'} for l in letras],
        'gabarito': gabarito,
        'peso': 1.0,
    }


class QuestoesValidasTests(SimpleTestCase):
    def test_aceita_questao_completa(self):
        from ai.services import _questoes_validas

        self.assertEqual(len(_questoes_validas([_q(1)])), 1)

    def test_descarta_degeneradas(self):
        from ai.services import _questoes_validas

        itens = [
            _q(1),
            _q(2, letras=('A',)),                      # 1 alternativa só
            _q(3, gabarito='Z'),                       # gabarito fora das letras
            _q(4, enunciado='  '),                     # sem enunciado
            _q(5, enunciado='[Duplicidade evitada]', letras=('A',)),  # caso real
        ]
        validas = _questoes_validas(itens)
        self.assertEqual([q['ordem'] for q in validas], [1])

    def test_normaliza_gabarito_minusculo(self):
        from ai.services import _questoes_validas

        validas = _questoes_validas([_q(1, gabarito='c')])
        self.assertEqual(validas[0]['gabarito'], 'C')

    def test_lista_vazia_ou_none(self):
        from ai.services import _questoes_validas

        self.assertEqual(_questoes_validas(None), [])
        self.assertEqual(_questoes_validas([]), [])

    def test_remove_duplicata_e_placeholder_da_ultima_alternativa(self):
        from ai.services import _questoes_validas

        q = _q(1, letras=('A', 'B', 'C', 'D', 'E', 'E'))
        q['alternativas'][-1]['texto'] = 'dup'
        validas = _questoes_validas([q])
        self.assertEqual([a['letra'] for a in validas[0]['alternativas']], ['A', 'B', 'C', 'D'])

    def test_normaliza_quinta_alternativa_para_o_padrao_a_d(self):
        from ai.services import _questoes_validas

        validas = _questoes_validas([_q(1, letras=('A', 'B', 'C', 'D', 'E'))])
        self.assertEqual([a['letra'] for a in validas[0]['alternativas']], ['A', 'B', 'C', 'D'])

    def test_rejeita_conjunto_sem_as_quatro_alternativas_base(self):
        from ai.services import _questoes_validas

        self.assertEqual(_questoes_validas([_q(1, letras=('A', 'B', 'C'))]), [])


class GerarAvaliacaoTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        from avaliacoes.models import Avaliacao
        from trilhas.models import Nivel, Trilha

        user = get_user_model().objects.create_user(username='aval_user', password='x')
        trilha = Trilha.objects.create(user=user, tema_livre='tema', titulo='Trilha')
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo='Nível 1', resumo='r')
        self.avaliacao = Avaliacao.objects.create(nivel=nivel)

    @patch('ai.services._gerar_json')
    def test_retenta_uma_vez_e_publica_10_renumeradas(self, gerar_json):
        from ai import services

        incompleta = {'questoes': [_q(i) for i in range(1, 7)]}
        completa = {'questoes': [_q(5) for _ in range(12)]}  # ordens duplicadas de propósito
        gerar_json.side_effect = [incompleta, completa]

        services.gerar_avaliacao(self.avaliacao)

        self.assertEqual(gerar_json.call_count, 2)
        ordens = list(self.avaliacao.questoes.order_by('ordem').values_list('ordem', flat=True))
        self.assertEqual(ordens, list(range(1, 11)))

    @patch('ai.services._gerar_json')
    def test_falha_apos_duas_tentativas_sem_apagar_questoes(self, gerar_json):
        from ai import services
        from avaliacoes.models import Questao

        Questao.objects.create(
            avaliacao=self.avaliacao, ordem=1, tipo='objetiva',
            enunciado_md='antiga', alternativas=[], gabarito='A',
        )
        gerar_json.return_value = {'questoes': [_q(i) for i in range(1, 7)]}

        with self.assertRaises(services.IAError):
            services.gerar_avaliacao(self.avaliacao)

        self.assertEqual(gerar_json.call_count, 2)
        self.assertEqual(self.avaliacao.questoes.count(), 1)  # conjunto antigo preservado

    @patch('ai.services._gerar_json')
    def test_exercicios_repetem_geracao_se_alternativa_estiver_incompleta(self, gerar_json):
        from ai import services
        from avaliacoes.models import Exercicio, ListaExercicios

        lista = ListaExercicios.objects.create(nivel=self.avaliacao.nivel)
        incompleto = {
            'exercicios': [
                {'enunciado': 'Pergunta', 'alternativas': [{'letra': 'A', 'texto': 'x'}],
                 'gabarito': 'A', 'explicacao': 'Explicação'}
            ]
        }
        completo = {
            'exercicios': [
                {'enunciado': f'Pergunta {i}',
                 'alternativas': [{'letra': l, 'texto': f'Alternativa {l}'} for l in ('A', 'B', 'C', 'D')],
                 'gabarito': 'A', 'explicacao': 'Explicação'}
                for i in range(1, 6)
            ]
        }
        gerar_json.side_effect = [incompleto, completo]

        services.gerar_exercicios(lista)

        self.assertEqual(gerar_json.call_count, 2)
        self.assertEqual(Exercicio.objects.filter(lista=lista).count(), 5)
