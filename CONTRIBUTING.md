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
pip install ruff mypy bandit pip-audit
ruff check .                              # precisa passar
ruff format --check .                     # precisa passar
python manage.py makemigrations --check --dry-run
bandit -r accounts ai avaliacoes config legal trilhas
pip-audit
mypy accounts ai avaliacoes config legal trilhas   # ainda não bloqueia
```

`mypy` e `pytest` estão em `soft-fail`: rodam e reportam, mas não derrubam o
build. O projeto ainda usa `manage.py test`; migrar os testes para `pytest` é
uma contribuição bem-vinda.

Os testes atuais:

```bash
python manage.py test
```

## Estilo

- `ruff` decide formatação e lint — não discuta estilo no review, rode a
  ferramenta.
- Mensagens de commit e comentários em português.
- Não commite `.env`, dump de banco, mídia de usuário nem chave de API. O CI
  roda `gitleaks` sobre todo o histórico e reprova o PR se encontrar segredo.

## Licença das contribuições

Ao enviar um PR você concorda em licenciar sua contribuição sob a
[AGPL-3.0](LICENSE), a mesma do projeto.
