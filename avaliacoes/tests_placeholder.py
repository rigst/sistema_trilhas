"""Barreira contra questões-placeholder (enunciado de rascunho ou alternativas
degeneradas como w/x/y/z) tanto no admin/shell quanto na geração por IA."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from ai.services import _questoes_validas
from avaliacoes.models import Avaliacao, Questao
from avaliacoes.validacao import (
    alternativas_sao_placeholder,
    enunciado_e_placeholder,
    questao_e_placeholder,
)
from trilhas.models import Nivel, Trilha

ALTS_WXYZ = [
    {"letra": "A", "texto": "w"},
    {"letra": "B", "texto": "y"},
    {"letra": "C", "texto": "z"},
    {"letra": "D", "texto": "x"},
]
ALTS_OK = [
    {"letra": "A", "texto": "Trocar 100% da água de uma vez"},
    {"letra": "B", "texto": "Fazer trocas parciais semanais"},
    {"letra": "C", "texto": "Nunca trocar a água"},
    {"letra": "D", "texto": "Usar só água destilada"},
]


class ValidacaoPlaceholderTests(SimpleTestCase):
    def test_enunciado_placeholder_literal(self):
        self.assertTrue(enunciado_e_placeholder("placeholder"))
        self.assertTrue(enunciado_e_placeholder("  Placeholder. "))
        self.assertTrue(enunciado_e_placeholder(""))
        self.assertTrue(enunciado_e_placeholder("teste"))

    def test_enunciado_real_passa(self):
        self.assertFalse(
            enunciado_e_placeholder("Qual a melhor prática na troca de água do aquário?")
        )

    def test_alternativas_wxyz_sao_placeholder(self):
        self.assertTrue(alternativas_sao_placeholder(ALTS_WXYZ))

    def test_alternativas_reais_passam(self):
        self.assertFalse(alternativas_sao_placeholder(ALTS_OK))

    def test_alternativas_numericas_legitimas_passam(self):
        # "4"/"5" são respostas válidas, não placeholders: só LETRA isolada conta.
        numericas = [
            {"letra": "A", "texto": "4"},
            {"letra": "B", "texto": "5"},
            {"letra": "C", "texto": "6"},
            {"letra": "D", "texto": "7"},
        ]
        self.assertFalse(alternativas_sao_placeholder(numericas))

    def test_uma_alternativa_curta_no_meio_de_reais_passa(self):
        mix = list(ALTS_OK)
        mix[0] = {"letra": "A", "texto": "x"}
        self.assertFalse(alternativas_sao_placeholder(mix))

    def test_questao_placeholder_combina_os_dois(self):
        self.assertTrue(questao_e_placeholder("placeholder", ALTS_WXYZ))
        self.assertTrue(questao_e_placeholder("Pergunta real", ALTS_WXYZ))
        self.assertTrue(questao_e_placeholder("placeholder", ALTS_OK))
        self.assertFalse(questao_e_placeholder("Pergunta real de verdade?", ALTS_OK))


class QuestaoCleanTests(TestCase):
    """O vetor real: questão criada à mão pelo admin/shell, sem passar pela IA."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("clean", password="x")
        trilha = Trilha.objects.create(user=self.user, tema_livre="Aquarismo", titulo="Aquarismo")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="Fundamentos")
        self.aval = Avaliacao.objects.create(nivel=nivel)

    def _questao(self, enunciado, alts):
        return Questao(
            avaliacao=self.aval,
            ordem=8,
            tipo=Questao.Tipo.OBJETIVA,
            enunciado_md=enunciado,
            alternativas=alts,
            gabarito="D",
        )

    def test_clean_rejeita_enunciado_placeholder(self):
        with self.assertRaises(ValidationError) as ctx:
            self._questao("placeholder", ALTS_WXYZ).full_clean()
        self.assertIn("enunciado_md", ctx.exception.message_dict)

    def test_clean_rejeita_alternativas_wxyz(self):
        with self.assertRaises(ValidationError) as ctx:
            self._questao("Qual a melhor prática na troca de água?", ALTS_WXYZ).full_clean()
        self.assertIn("alternativas", ctx.exception.message_dict)

    def test_clean_aceita_questao_real(self):
        # full_clean não deve levantar para uma questão legítima.
        self._questao("Qual a melhor prática na troca de água do aquário?", ALTS_OK).full_clean()


class ValidadorIaTests(SimpleTestCase):
    """Defesa em profundidade: se a IA devolver um placeholder, é descartado."""

    def test_questao_placeholder_e_descartada(self):
        items = [
            {"enunciado": "placeholder", "gabarito": "D", "alternativas": ALTS_WXYZ},
        ]
        self.assertEqual(_questoes_validas(items), [])

    def test_questao_real_passa(self):
        items = [
            {
                "enunciado": "Qual a melhor prática na troca de água do aquário?",
                "gabarito": "B",
                "alternativas": ALTS_OK,
            }
        ]
        self.assertEqual(len(_questoes_validas(items)), 1)
