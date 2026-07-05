"""Testes das utilidades de IA que não tocam a API."""

from django.test import SimpleTestCase

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
