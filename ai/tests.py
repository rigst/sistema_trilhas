"""Testes das utilidades de IA que não tocam a API."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from ai.services import limpar_titulo


class LimparTituloTests(SimpleTestCase):
    def test_remove_prefixos_de_trilha_curso_guia(self):
        casos = {
            "Trilha de Python do Zero": "Python do Zero",
            "Curso completo de Violão": "Violão",
            "Trilha: História do Brasil": "História do Brasil",
            "Guia de SQL": "SQL",
            "trilha para concursos": "concursos",
        }
        for entrada, esperado in casos.items():
            self.assertEqual(limpar_titulo(entrada), esperado)

    def test_preserva_titulos_ja_diretos(self):
        for t in ("Fotografia de Rua", "Redes de Computadores", "Direito Penal"):
            self.assertEqual(limpar_titulo(t), t)

    def test_prefixos_encadeados_sao_removidos(self):
        self.assertEqual(limpar_titulo("Trilha de Estudos: Batuque no RS"), "Batuque no RS")

    def test_nao_devolve_vazio(self):
        self.assertEqual(limpar_titulo("Trilha"), "Trilha")
        self.assertEqual(limpar_titulo(""), "")


def _q(ordem, letras=("A", "B", "C", "D"), gabarito="A", enunciado="Enunciado da questão?"):
    return {
        "ordem": ordem,
        "tipo": "objetiva",
        "enunciado": enunciado,
        "alternativas": [{"letra": letra, "texto": f"Alternativa {letra}"} for letra in letras],
        "gabarito": gabarito,
        "peso": 1.0,
    }


class QuestoesValidasTests(SimpleTestCase):
    def test_aceita_questao_completa(self):
        from ai.services import _questoes_validas

        self.assertEqual(len(_questoes_validas([_q(1)])), 1)

    def test_descarta_degeneradas(self):
        from ai.services import _questoes_validas

        itens = [
            _q(1),
            _q(2, letras=("A",)),  # 1 alternativa só
            _q(3, gabarito="Z"),  # gabarito fora das letras
            _q(4, enunciado="  "),  # sem enunciado
            _q(5, enunciado="[Duplicidade evitada]", letras=("A",)),  # caso real
        ]
        validas = _questoes_validas(itens)
        self.assertEqual([q["ordem"] for q in validas], [1])

    def test_normaliza_gabarito_minusculo(self):
        from ai.services import _questoes_validas

        validas = _questoes_validas([_q(1, gabarito="c")])
        self.assertEqual(validas[0]["gabarito"], "C")

    def test_lista_vazia_ou_none(self):
        from ai.services import _questoes_validas

        self.assertEqual(_questoes_validas(None), [])
        self.assertEqual(_questoes_validas([]), [])

    def test_remove_duplicata_e_placeholder_da_ultima_alternativa(self):
        from ai.services import _questoes_validas

        q = _q(1, letras=("A", "B", "C", "D", "E", "E"))
        q["alternativas"][-1]["texto"] = "dup"
        validas = _questoes_validas([q])
        self.assertEqual([a["letra"] for a in validas[0]["alternativas"]], ["A", "B", "C", "D"])

    def test_normaliza_quinta_alternativa_para_o_padrao_a_d(self):
        from ai.services import _questoes_validas

        validas = _questoes_validas([_q(1, letras=("A", "B", "C", "D", "E"))])
        self.assertEqual([a["letra"] for a in validas[0]["alternativas"]], ["A", "B", "C", "D"])

    def test_rejeita_conjunto_sem_as_quatro_alternativas_base(self):
        from ai.services import _questoes_validas

        self.assertEqual(_questoes_validas([_q(1, letras=("A", "B", "C"))]), [])


class GerarAvaliacaoTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        from avaliacoes.models import Avaliacao
        from trilhas.models import Nivel, Trilha

        user = get_user_model().objects.create_user(username="aval_user", password="x")
        trilha = Trilha.objects.create(user=user, tema_livre="tema", titulo="Trilha")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="Nível 1", resumo="r")
        self.avaliacao = Avaliacao.objects.create(nivel=nivel)

    @patch("ai.services._gerar_json")
    def test_retenta_uma_vez_e_publica_10_renumeradas(self, gerar_json):
        from ai import services

        incompleta = {"questoes": [_q(i) for i in range(1, 7)]}
        completa = {"questoes": [_q(5) for _ in range(12)]}  # ordens duplicadas de propósito
        gerar_json.side_effect = [incompleta, completa]

        services.gerar_avaliacao(self.avaliacao)

        self.assertEqual(gerar_json.call_count, 2)
        ordens = list(self.avaliacao.questoes.order_by("ordem").values_list("ordem", flat=True))
        self.assertEqual(ordens, list(range(1, 11)))

    @patch("ai.services._gerar_json")
    def test_publica_com_a_resposta_certa_distribuida_entre_as_letras(self, gerar_json):
        from collections import Counter

        from ai import services

        # A IA devolve as 10 com gabarito A; a publicação tem de espalhar.
        questoes = []
        for i in range(1, 11):
            questao = _q(i)
            questao["alternativas"] = [
                {"letra": letra, "texto": f"Resposta {i}{letra}"} for letra in "ABCD"
            ]
            questoes.append(questao)
        gerar_json.return_value = {"questoes": questoes}

        services.gerar_avaliacao(self.avaliacao)

        publicadas = list(self.avaliacao.questoes.order_by("ordem"))
        contagem = Counter(q.gabarito for q in publicadas)
        self.assertEqual(sorted(contagem), ["A", "B", "C", "D"])
        self.assertTrue(all(2 <= n <= 3 for n in contagem.values()))
        for i, questao in enumerate(publicadas, start=1):
            correta = next(
                a["texto"] for a in questao.alternativas if a["letra"] == questao.gabarito
            )
            self.assertEqual(correta, f"Resposta {i}A")  # o texto certo continua o mesmo

    @patch("ai.services._gerar_json")
    def test_falha_apos_duas_tentativas_sem_apagar_questoes(self, gerar_json):
        from ai import services
        from avaliacoes.models import Questao

        Questao.objects.create(
            avaliacao=self.avaliacao,
            ordem=1,
            tipo="objetiva",
            enunciado_md="antiga",
            alternativas=[],
            gabarito="A",
        )
        gerar_json.return_value = {"questoes": [_q(i) for i in range(1, 7)]}

        with self.assertRaises(services.IAError):
            services.gerar_avaliacao(self.avaliacao)

        self.assertEqual(gerar_json.call_count, 2)
        self.assertEqual(self.avaliacao.questoes.count(), 1)  # conjunto antigo preservado

    @patch("ai.services._gerar_json")
    def test_exercicios_repetem_geracao_se_alternativa_estiver_incompleta(self, gerar_json):
        from ai import services
        from avaliacoes.models import Exercicio, ListaExercicios

        lista = ListaExercicios.objects.create(nivel=self.avaliacao.nivel)
        incompleto = {
            "exercicios": [
                {
                    "enunciado": "Pergunta",
                    "alternativas": [{"letra": "A", "texto": "x"}],
                    "gabarito": "A",
                    "explicacao": "Explicação",
                }
            ]
        }
        completo = {
            "exercicios": [
                {
                    "enunciado": f"Pergunta {i}",
                    "alternativas": [
                        {"letra": letra, "texto": f"Alternativa {letra}"}
                        for letra in ("A", "B", "C", "D")
                    ],
                    "gabarito": "A",
                    "explicacao": "Explicação",
                }
                for i in range(1, 6)
            ]
        }
        gerar_json.side_effect = [incompleto, completo]

        services.gerar_exercicios(lista)

        self.assertEqual(gerar_json.call_count, 2)
        self.assertEqual(Exercicio.objects.filter(lista=lista).count(), 5)


def _qe(ordem, gabarito="A", explicacao="Comentário didático.", alternativas=None):
    """Questão válida (já normalizada) com texto distinto por letra."""
    letras = ("A", "B", "C", "D")
    return {
        "ordem": ordem,
        "enunciado": f"Enunciado {ordem}?",
        "alternativas": alternativas
        or [{"letra": letra, "texto": f"Texto {ordem}{letra}"} for letra in letras],
        "gabarito": gabarito,
        "explicacao": explicacao,
    }


class EmbaralharGabaritosTests(SimpleTestCase):
    def test_distribui_as_letras_de_forma_equilibrada(self):
        import random
        from collections import Counter

        from ai.services import embaralhar_gabaritos

        questoes = [_qe(i) for i in range(1, 13)]  # todas com gabarito A
        saida = embaralhar_gabaritos(questoes, rng=random.Random(7))

        contagem = Counter(q["gabarito"] for q in saida)
        self.assertEqual(contagem, Counter({"A": 3, "B": 3, "C": 3, "D": 3}))

    def test_dez_questoes_ficam_entre_duas_e_tres_por_letra(self):
        import random
        from collections import Counter

        from ai.services import embaralhar_gabaritos

        saida = embaralhar_gabaritos([_qe(i) for i in range(1, 11)], rng=random.Random(3))

        contagem = Counter(q["gabarito"] for q in saida)
        self.assertEqual(sorted(contagem), ["A", "B", "C", "D"])
        self.assertTrue(all(2 <= n <= 3 for n in contagem.values()))

    def test_preserva_o_texto_correto_e_as_quatro_alternativas(self):
        import random

        from ai.services import embaralhar_gabaritos

        questoes = [_qe(i) for i in range(1, 9)]
        saida = embaralhar_gabaritos(questoes, rng=random.Random(11))

        for original, nova in zip(questoes, saida, strict=True):
            correta = next(
                a["texto"] for a in original["alternativas"] if a["letra"] == original["gabarito"]
            )
            nova_correta = next(
                a["texto"] for a in nova["alternativas"] if a["letra"] == nova["gabarito"]
            )
            self.assertEqual(correta, nova_correta)
            self.assertEqual([a["letra"] for a in nova["alternativas"]], ["A", "B", "C", "D"])
            self.assertEqual(
                sorted(a["texto"] for a in nova["alternativas"]),
                sorted(a["texto"] for a in original["alternativas"]),
            )

    def test_nao_embaralha_quando_a_explicacao_cita_a_letra(self):
        import random

        from ai.services import embaralhar_gabaritos

        questao = _qe(1, explicacao="A alternativa A está correta porque o índice existe.")
        saida = embaralhar_gabaritos([questao], rng=random.Random(1))

        self.assertEqual(saida[0], questao)

    def test_nao_embaralha_alternativa_que_depende_da_posicao(self):
        import random

        from ai.services import embaralhar_gabaritos

        alternativas = [{"letra": letra, "texto": f"Texto {letra}"} for letra in "ABC"]
        alternativas.append({"letra": "D", "texto": "Nenhuma das anteriores"})
        questao = _qe(1, alternativas=alternativas)
        saida = embaralhar_gabaritos([questao], rng=random.Random(1))

        self.assertEqual(saida[0], questao)

    def test_questoes_fixas_contam_na_distribuicao(self):
        import random
        from collections import Counter

        from ai.services import embaralhar_gabaritos

        # Duas questões travadas em A: as demais evitam A até emparelhar.
        travada = "A resposta certa é a alternativa A."
        questoes = [_qe(1, explicacao=travada), _qe(2, explicacao=travada)] + [
            _qe(i) for i in range(3, 7)
        ]
        contagem = Counter(
            q["gabarito"] for q in embaralhar_gabaritos(questoes, rng=random.Random(5))
        )

        self.assertEqual(contagem["A"], 2)
        self.assertEqual(sorted(contagem.values()), [1, 1, 2, 2])

    def test_enunciado_com_codigo_normal_continua_embaralhavel(self):
        from ai.services import _pode_embaralhar

        questao = _qe(1, explicacao="O `SELECT` varre a tabela inteira sem o índice.")
        questao["enunciado"] = "Dado o trecho ```sql\nSELECT * FROM t\n``` qual o efeito?"
        self.assertTrue(_pode_embaralhar(questao))
