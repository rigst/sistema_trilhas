"""Testes das tasks Celery de IA — o serviço subjacente é sempre mockado.

O que interessa aqui é o contrato de erro das tasks: o status de ERRO só é
persistido na ÚLTIMA tentativa, para o polling do frontend não piscar erro
durante o backoff. Por isso os testes de erro executam o corpo da task uma vez
só, num ponto escolhido do ciclo de retries (ver `rodar_uma_vez`).
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from ai import tasks
from avaliacoes.models import Avaliacao, ListaExercicios, Revisao
from trilhas.models import Nivel, Percurso, SessaoSugestao, Subtopico, Trilha

User = get_user_model()

# `TASK_KW["max_retries"]`: com este número de retries já gastos, a execução é
# a última e o erro deve ser persistido.
ULTIMA = tasks.TASK_KW["max_retries"]


def rodar(task, *args, **kwargs):
    """Executa a task pelo caminho normal (eager), devolvendo o EagerResult."""
    return task.apply(args=args, kwargs=kwargs, throw=False)


def rodar_uma_vez(task, *args, retries=0, **kwargs):
    """Executa o corpo da task UMA vez, fingindo estar em `retries` tentativas.

    `apply()` não serve aqui: em modo eager ele reexecuta sozinho até esgotar os
    retries, e o que se quer medir é justamente o comportamento em cada ponto
    do ciclo. Propaga a exceção original, como o worker veria.
    """
    task.push_request(retries=retries)
    try:
        return task._orig_run(*args, **kwargs)
    finally:
        task.pop_request()


class UltimaTentativaTests(TestCase):
    def test_so_e_a_ultima_quando_os_retries_se_esgotam(self):
        task = tasks.task_gerar_perguntas
        for retries, esperado in ((0, False), (ULTIMA - 1, False), (ULTIMA, True)):
            task.push_request(retries=retries)
            try:
                self.assertIs(tasks._ultima_tentativa(task), esperado)
            finally:
                task.pop_request()


class HelpersTests(TestCase):
    def test_profile_de_usuario_sem_perfil_e_none(self):
        self.assertIsNone(tasks._profile(object()))

    def test_profile_devolve_o_perfil_do_usuario(self):
        user = User.objects.create_user("h", password="x")
        self.assertEqual(tasks._profile(user), user.profile)


class PreGerarPrimeiroTopicoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pre", password="x")
        self.trilha = Trilha.objects.create(user=self.user, tema_livre="tema", titulo="T")

    def test_trilha_sem_nivel_nao_dispara_nada(self):
        with mock.patch.object(tasks.task_gerar_subtopico, "delay") as delay:
            tasks._pre_gerar_primeiro_topico(self.trilha)
        delay.assert_not_called()

    def test_nivel_sem_subtopico_nao_dispara_nada(self):
        Nivel.objects.create(trilha=self.trilha, ordem=1, titulo="N1")
        with mock.patch.object(tasks.task_gerar_subtopico, "delay") as delay:
            tasks._pre_gerar_primeiro_topico(self.trilha)
        delay.assert_not_called()

    def test_marca_como_gerando_e_dispara_o_primeiro_topico(self):
        nivel = Nivel.objects.create(trilha=self.trilha, ordem=1, titulo="N1")
        sub = Subtopico.objects.create(nivel=nivel, ordem=1, titulo="Sub 1")
        with mock.patch.object(tasks.task_gerar_subtopico, "delay") as delay:
            tasks._pre_gerar_primeiro_topico(self.trilha)

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subtopico.Status.GERANDO)
        delay.assert_called_once_with(sub.pk)

    def test_topico_ja_processado_nao_e_redisparado(self):
        nivel = Nivel.objects.create(trilha=self.trilha, ordem=1, titulo="N1")
        Subtopico.objects.create(
            nivel=nivel, ordem=1, titulo="Sub 1", status=Subtopico.Status.PRONTO
        )
        with mock.patch.object(tasks.task_gerar_subtopico, "delay") as delay:
            tasks._pre_gerar_primeiro_topico(self.trilha)
        delay.assert_not_called()


class TaskGerarPerguntasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tp", password="x")
        self.trilha = Trilha.objects.create(user=self.user, tema_livre="tema")

    def test_objeto_inexistente_encerra_sem_erro(self):
        self.assertEqual(rodar(tasks.task_gerar_perguntas, 999999).result, "trilha inexistente")

    @mock.patch("ai.services.gerar_perguntas_direcionadoras")
    def test_sucesso_marca_aguardando_respostas(self, gerar):
        rodar(tasks.task_gerar_perguntas, self.trilha.pk)
        self.trilha.refresh_from_db()
        self.assertEqual(self.trilha.status, Trilha.Status.AGUARDANDO_RESPOSTAS)
        gerar.assert_called_once_with(self.trilha, self.user.profile)

    @mock.patch("ai.services.gerar_perguntas_direcionadoras", side_effect=RuntimeError("boom"))
    def test_erro_na_ultima_tentativa_persiste_o_status_de_erro(self, _gerar):
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_gerar_perguntas, self.trilha.pk, retries=ULTIMA)
        self.trilha.refresh_from_db()
        self.assertEqual(self.trilha.status, Trilha.Status.ERRO)
        self.assertIn("boom", self.trilha.erro)

    @mock.patch("ai.services.gerar_perguntas_direcionadoras", side_effect=RuntimeError("boom"))
    def test_erro_no_meio_dos_retries_nao_marca_erro(self, _gerar):
        # Regressão: o polling do frontend não pode piscar erro durante o backoff.
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_gerar_perguntas, self.trilha.pk, retries=0)
        self.trilha.refresh_from_db()
        self.assertNotEqual(self.trilha.status, Trilha.Status.ERRO)

    @mock.patch("ai.services.gerar_perguntas_direcionadoras", side_effect=RuntimeError("x" * 5000))
    def test_mensagem_de_erro_e_truncada(self, _gerar):
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_gerar_perguntas, self.trilha.pk, retries=ULTIMA)
        self.trilha.refresh_from_db()
        self.assertEqual(len(self.trilha.erro), 2000)


class TaskGerarSumarioTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ts", password="x")
        self.trilha = Trilha.objects.create(user=self.user, tema_livre="tema")

    def test_objeto_inexistente_encerra_sem_erro(self):
        self.assertEqual(rodar(tasks.task_gerar_sumario, 999999).result, "trilha inexistente")

    @mock.patch("ai.tasks._pre_gerar_primeiro_topico")
    @mock.patch("ai.services.gerar_sumario")
    def test_sucesso_marca_sumario_gerado_e_pre_gera_o_topico(self, _gerar, pre_gerar):
        rodar(tasks.task_gerar_sumario, self.trilha.pk)
        self.trilha.refresh_from_db()
        self.assertEqual(self.trilha.status, Trilha.Status.SUMARIO_GERADO)
        pre_gerar.assert_called_once()

    @mock.patch("ai.services.gerar_sumario", side_effect=RuntimeError("boom"))
    def test_erro_na_ultima_tentativa_persiste(self, _gerar):
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_gerar_sumario, self.trilha.pk, retries=ULTIMA)
        self.trilha.refresh_from_db()
        self.assertEqual(self.trilha.status, Trilha.Status.ERRO)


class TaskGerarSubtopicoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tsub", password="x")
        trilha = Trilha.objects.create(user=self.user, tema_livre="tema")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1")
        self.sub = Subtopico.objects.create(nivel=nivel, ordem=1, titulo="Sub 1")

    def test_objeto_inexistente_encerra_sem_erro(self):
        self.assertEqual(rodar(tasks.task_gerar_subtopico, 999999).result, "subtópico inexistente")

    @mock.patch("ai.services.gerar_conteudo_subtopico", return_value="# Texto")
    def test_sucesso_grava_o_conteudo(self, gerar):
        rodar(tasks.task_gerar_subtopico, self.sub.pk)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.conteudo_md, "# Texto")
        self.assertEqual(self.sub.status, Subtopico.Status.PRONTO)
        self.assertIsNotNone(self.sub.gerado_em)

    @mock.patch("ai.services.gerar_conteudo_subtopico")
    def test_topico_ja_pronto_nao_regera(self, gerar):
        # Evita retrabalho quando a pré-geração já concluiu antes da leitura.
        Subtopico.objects.filter(pk=self.sub.pk).update(
            status=Subtopico.Status.PRONTO, conteudo_md="já tenho"
        )
        resultado = rodar(tasks.task_gerar_subtopico, self.sub.pk)

        self.assertIn("já pronto", resultado.result)
        gerar.assert_not_called()

    @mock.patch("ai.services.gerar_conteudo_subtopico", side_effect=RuntimeError("boom"))
    def test_erro_na_ultima_tentativa_persiste(self, _gerar):
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_gerar_subtopico, self.sub.pk, retries=ULTIMA)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, Subtopico.Status.ERRO)


class TaskAvaliacaoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ta", password="x")
        trilha = Trilha.objects.create(user=self.user, tema_livre="tema")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1")
        self.avaliacao = Avaliacao.objects.create(nivel=nivel)

    def test_objeto_inexistente_encerra_sem_erro(self):
        self.assertEqual(rodar(tasks.task_gerar_avaliacao, 999999).result, "avaliação inexistente")
        self.assertEqual(
            rodar(tasks.task_corrigir_avaliacao, 999999).result, "avaliação inexistente"
        )

    @mock.patch("ai.services.gerar_avaliacao")
    def test_geracao_bem_sucedida_marca_pronta(self, _gerar):
        rodar(tasks.task_gerar_avaliacao, self.avaliacao.pk)
        self.avaliacao.refresh_from_db()
        self.assertEqual(self.avaliacao.status, Avaliacao.Status.PRONTA)

    @mock.patch("ai.services.gerar_avaliacao", side_effect=RuntimeError("boom"))
    def test_erro_de_geracao_na_ultima_tentativa_persiste(self, _gerar):
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_gerar_avaliacao, self.avaliacao.pk, retries=ULTIMA)
        self.avaliacao.refresh_from_db()
        self.assertEqual(self.avaliacao.status, Avaliacao.Status.ERRO)

    @mock.patch("ai.services.corrigir_avaliacao")
    def test_correcao_delega_ao_servico(self, corrigir):
        rodar(tasks.task_corrigir_avaliacao, self.avaliacao.pk)
        corrigir.assert_called_once_with(self.avaliacao, self.user.profile)

    @mock.patch("ai.services.corrigir_avaliacao", side_effect=RuntimeError("boom"))
    def test_erro_de_correcao_na_ultima_tentativa_persiste(self, _corrigir):
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_corrigir_avaliacao, self.avaliacao.pk, retries=ULTIMA)
        self.avaliacao.refresh_from_db()
        self.assertEqual(self.avaliacao.status, Avaliacao.Status.ERRO)


class TaskExerciciosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("te", password="x")
        trilha = Trilha.objects.create(user=self.user, tema_livre="tema")
        nivel = Nivel.objects.create(trilha=trilha, ordem=1, titulo="N1")
        self.lista = ListaExercicios.objects.create(nivel=nivel)

    def test_objeto_inexistente_encerra_sem_erro(self):
        self.assertEqual(rodar(tasks.task_gerar_exercicios, 999999).result, "lista inexistente")

    @mock.patch("ai.services.gerar_exercicios")
    def test_sucesso_marca_pronta(self, _gerar):
        rodar(tasks.task_gerar_exercicios, self.lista.pk)
        self.lista.refresh_from_db()
        self.assertEqual(self.lista.status, ListaExercicios.Status.PRONTA)

    @mock.patch("ai.services.gerar_exercicios", side_effect=RuntimeError("boom"))
    def test_erro_na_ultima_tentativa_persiste(self, _gerar):
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_gerar_exercicios, self.lista.pk, retries=ULTIMA)
        self.lista.refresh_from_db()
        self.assertEqual(self.lista.status, ListaExercicios.Status.ERRO)


class TaskPercursoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tperc", password="x")
        self.percurso = Percurso.objects.create(user=self.user)

    def test_objeto_inexistente_encerra_sem_erro(self):
        self.assertEqual(rodar(tasks.task_gerar_percurso, 999999).result, "percurso inexistente")

    @mock.patch("ai.services.gerar_percurso")
    def test_sucesso_marca_pronto(self, _gerar):
        rodar(tasks.task_gerar_percurso, self.percurso.pk)
        self.percurso.refresh_from_db()
        self.assertEqual(self.percurso.status, Percurso.Status.PRONTO)

    @mock.patch("ai.services.gerar_percurso", side_effect=RuntimeError("boom"))
    def test_erro_na_ultima_tentativa_persiste(self, _gerar):
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_gerar_percurso, self.percurso.pk, retries=ULTIMA)
        self.percurso.refresh_from_db()
        self.assertEqual(self.percurso.status, Percurso.Status.ERRO)


class TaskSugestoesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tsug", password="x")
        self.sessao = SessaoSugestao.objects.create(user=self.user)

    def test_objeto_inexistente_encerra_sem_erro(self):
        self.assertEqual(rodar(tasks.task_gerar_sugestoes, 999999).result, "sessão inexistente")

    @mock.patch("ai.services.gerar_sugestoes")
    def test_sucesso_marca_pronto(self, _gerar):
        rodar(tasks.task_gerar_sugestoes, self.sessao.pk)
        self.sessao.refresh_from_db()
        self.assertEqual(self.sessao.status, SessaoSugestao.Status.PRONTO)

    @mock.patch("ai.services.gerar_sugestoes", side_effect=RuntimeError("boom"))
    def test_erro_na_ultima_tentativa_persiste(self, _gerar):
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_gerar_sugestoes, self.sessao.pk, retries=ULTIMA)
        self.sessao.refresh_from_db()
        self.assertEqual(self.sessao.status, SessaoSugestao.Status.ERRO)


class TaskRevisaoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("trev", password="x")
        self.revisao = Revisao.objects.create(user=self.user)

    def test_objeto_inexistente_encerra_sem_erro(self):
        self.assertEqual(rodar(tasks.task_gerar_revisao, 999999).result, "revisão inexistente")

    @mock.patch("ai.services.gerar_revisao")
    def test_sucesso_marca_pronta(self, gerar):
        rodar(tasks.task_gerar_revisao, self.revisao.pk)
        self.revisao.refresh_from_db()
        self.assertEqual(self.revisao.status, Revisao.Status.PRONTA)
        gerar.assert_called_once_with(self.revisao, self.user.profile, trilha_id=None)

    @mock.patch("ai.services.gerar_revisao")
    def test_revisao_de_uma_trilha_repassa_o_id(self, gerar):
        rodar(tasks.task_gerar_revisao, self.revisao.pk, trilha_id=7)
        gerar.assert_called_once_with(self.revisao, self.user.profile, trilha_id=7)

    @mock.patch("ai.services.gerar_revisao", side_effect=RuntimeError("boom"))
    def test_erro_na_ultima_tentativa_persiste(self, _gerar):
        with self.assertRaises(RuntimeError):
            rodar_uma_vez(tasks.task_gerar_revisao, self.revisao.pk, retries=ULTIMA)
        self.revisao.refresh_from_db()
        self.assertEqual(self.revisao.status, Revisao.Status.ERRO)
