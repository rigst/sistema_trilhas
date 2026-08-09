# Contribuindo

Obrigado pelo interesse. Este é um projeto mantido por uma pessoa só, então
issues e PRs podem levar alguns dias para receber resposta.

## Antes de abrir um PR

Abra uma issue primeiro se a mudança for grande ou mexer em modelo de dados.
Correção de bug, ajuste de texto e melhoria de documentação podem ir direto para
o PR.

## Ambiente

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # defina ANTHROPIC_API_KEY
python manage.py migrate
python manage.py runserver
```

Em desenvolvimento o Celery roda em modo *eager* (síncrono), sem Redis.

## O que o CI exige

O pipeline é o compartilhado de [rigst/ci](https://github.com/rigst/ci). Para
rodar as mesmas checagens localmente antes de subir:

```bash
pip install ruff mypy bandit pip-audit pytest pytest-django pytest-cov
APPS="accounts ai avaliacoes config legal trilhas"

ruff check .                              # bloqueia
ruff format --check .                     # bloqueia
pytest --cov --cov-report=term-missing    # bloqueia
bandit -r $APPS --severity-level high     # bloqueia a partir de "high"
pip-audit                                 # bloqueia
python manage.py makemigrations --check --dry-run   # bloqueia
python manage.py check --deploy --fail-level WARNING

mypy $APPS                                # ainda não bloqueia
```

**Só o `mypy` está em `soft-fail`** — roda e reporta sem derrubar o build. Todo
o resto bloqueia.

O `bandit` imprime o relatório inteiro, mas só reprova a partir de severidade
**alta**. Os achados médios que restam foram auditados um a um (são
`mark_safe`/`|safe` sobre HTML já sanitizado com allowlist via `nh3`). Se você
introduzir um achado alto, o build para.

Os testes rodam com `pytest` (configuração em `pytest.ini`). A convenção de
nomes aqui é `tests.py` e `tests_*.py`, não `test_*.py`.

```bash
pytest                    # suíte completa
pytest trilhas            # só um app
pytest --cov              # com cobertura
```

Cobertura acompanhada no [Codecov](https://codecov.io/gh/rigst/sistema_trilhas)
e no [SonarQube Cloud](https://sonarcloud.io/summary/new_code?id=rigst_sistema_trilhas).

### Uma armadilha ao escrever testes

Não chame `response.close()` dentro de uma `TestCase`. Isso dispara o signal
`request_finished`, que fecha a conexão do banco dentro do `atomic` do teste —
e todo teste seguinte da mesma classe morre com `the connection is closed`. Em
SQLite o sintoma não aparece, então passa despercebido até rodar no PostgreSQL.
Para consumir um `FileResponse`, itere `response.streaming_content`.

## Estilo

- `ruff` decide formatação e lint — não discuta estilo no review, rode a
  ferramenta.
- Mensagens de commit e comentários em português.
- Não commite `.env`, dump de banco, mídia de usuário nem chave de API. O CI
  roda `gitleaks` sobre todo o histórico e reprova o PR se encontrar segredo.

## Licença das contribuições

Ao enviar um PR você concorda em licenciar sua contribuição sob a
[AGPL-3.0](LICENSE), a mesma do projeto.
