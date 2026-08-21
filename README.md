# Trilhas de Estudo com IA

[![CI](https://github.com/rigst/sistema_trilhas/actions/workflows/ci.yml/badge.svg)](https://github.com/rigst/sistema_trilhas/actions/workflows/ci.yml)
[![Cobertura](https://codecov.io/gh/rigst/sistema_trilhas/branch/main/graph/badge.svg)](https://codecov.io/gh/rigst/sistema_trilhas)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=rigst_sistema_trilhas&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=rigst_sistema_trilhas)
[![Licença: AGPL v3](https://img.shields.io/badge/licen%C3%A7a-AGPL--3.0-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Django 6](https://img.shields.io/badge/django-6.0-092E20.svg)](https://www.djangoproject.com/)

App Django onde o usuário descreve livremente um tema, a IA (Claude) faz
perguntas direcionadoras, monta um sumário do básico ao avançado e gera o
conteúdo de cada nível sob demanda. Cada nível termina numa avaliação objetiva
corrigida por gabarito; ao atingir a nota mínima, o usuário ganha um título
(Iniciante/Especialista/Mestre em X) e desbloqueia o próximo. Em cima disso há
uma camada de gamificação (XP, níveis de jogador, ofensiva e **diamantes** como
moeda para criar trilhas) e de revisão espaçada (SM-2).

Compartilha as convenções dos demais projetos do autor: design system Stölben,
Celery + Redis, settings divididos por ambiente e login com visitante.

## Stack
Django 6 · Celery + Redis (DB 3) · Anthropic SDK · SQLite (dev) / PostgreSQL (prod).
UI própria tema **"Foco"** (noturno gamificado, Sora + Inter). Markdown com callouts
(admonitions) e destaque de sintaxe via **Pygments**, sanitizado por allowlist (**nh3**).

## Divisão de modelos (configurável no .env)
- **Opus 4.8** (`AI_MODEL_PLANEJAMENTO`): planejamento que exige mais julgamento —
  sumário da trilha, percurso do mentor e sugestões de novas trilhas.
- **Sonnet 5** (`AI_MODEL`): perguntas, conteúdo dos tópicos, avaliação,
  exercícios e revisão (rápido/barato).

O raciocínio é adaptativo e o esforço (`AI_EFFORT`, `AI_EFFORT_GERAL`) é
configurável. O system prompt vai com `cache_control` efêmero para cortar o custo
de input nas chamadas seguintes. Todo uso debita a quota de tokens do `Profile`.

## Funcionalidades
- Perguntas direcionadoras → sumário → conteúdo sob demanda (com barra/skeleton de progresso).
- Conteúdo rico: subtítulos, caixas de destaque coloridas e código com cores de sintaxe.
- **Exercícios de prática** (rota separada) respondidos com feedback imediato (sem nota).
- Avaliação objetiva por nível → título + desbloqueio do próximo nível.
- **Mentor**: percurso personalizado (aprender/revisar/avaliar) equilibrado entre as trilhas.
- **Sugestões** de novas trilhas geradas pela IA a partir das existentes, aceitáveis num clique.
- **Cards salvos**: biblioteca pessoal de destaques da leitura.
- **Vídeo narrado do nível** sob demanda: um vídeo por nível, cobrindo todos os
  tópicos em capítulos (slideshow via edge-tts + Playwright/FFmpeg), com
  **mascote apresentador** no canto — boca sincronizada pelo volume da narração,
  animação contínua (respiração, deriva da cabeça) e reações tiradas do próprio
  áudio: ênfase, aceno, inclinação e encolher de ombros. Cada trilha recebe uma
  das paletas de `trilhas/video_avatar.PALETAS`, sorteada pelo id (sempre a
  mesma para a mesma trilha).
- **Capa** buscada na Pexels com termo escolhido pela IA e baixada para `/media`.
- **Chat de dúvidas**: painel flutuante na quina, disponível em todas as páginas.
  A conversa é **da trilha inteira** — segue o aluno por tópicos, níveis e
  avaliações —, e cada pergunta leva no contexto o material da página aberta na
  hora. Só responde sobre os temas das trilhas dele; fora disso recusa e sugere
  criar uma trilha do assunto. A resposta chega **sendo escrita** (a task publica
  o parcial no Redis e o painel revela em ritmo constante), e um botão lista as
  conversas salvas para retomar. Tem **balde de tokens próprio**
  (`QUOTA_CHAT_TOKENS_*`), separado do que gera trilhas, limite de rajada e
  conversas expurgadas em 90 dias.

## Economia e progressão
Regras centrais da gamificação (em `accounts/models.py::Profile` e nos serviços de
avaliação/revisão). Manter estas invariantes ao mexer no fluxo — elas evitam farm
de XP/diamante e corrida em ações concorrentes:

- **XP por atividade**: ler tópico (10), responder questão (5), enviar avaliação (20),
  ser aprovado num nível (50), concluir trilha (+100). O crédito de XP é **atômico**
  (`registrar_atividade` roda em transação com `select_for_update`).
- **Nível de jogador**: curva progressiva (base 100, +25 por nível).
- **Diamantes** (moeda para criar trilha): começa com 3; +1 a cada 1000 XP e +1 a cada
  5 níveis de jogador (contadores próprios idempotentes). Criar trilha custa 1 diamante,
  debitado de forma atômica; excluir em até **48h** devolve o diamante (uma única vez).
- **Aprovação concede XP só na primeira vez**: reavaliar um nível já aprovado é
  bloqueado e não reganha XP nem reinicia o agendamento de revisão.
- **Revisão espaçada (SM-2)**: cada nível aprovado é reagendado; o flashcard do
  dashboard só concede XP quando a revisão estava realmente **devida**.

## Desenvolvimento
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # defina ANTHROPIC_API_KEY
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
Em dev o Celery roda em modo *eager* (síncrono), sem precisar de Redis.

As dependências são **pin exato** e o `requirements.lock` trava a árvore
resolvida com hash de cada artefato. Ao mexer em `requirements.txt`, regere o
lock com o script do CI central (`scripts/gerar_lock.py` de `rigst/ci`) — o job
`lock` acusa se os dois saírem de sincronia.

Testes (`pytest`, configurado em `pytest.ini`):
```bash
./venv/bin/pytest                # suíte completa (sem os e2e)
./venv/bin/pytest --cov          # com cobertura
./venv/bin/pytest -m e2e         # ponta a ponta; exige `playwright install chromium`
```
Os testes `e2e` sobem um servidor de verdade e abrem o Chromium, por isso ficam
fora da suíte padrão via `-m "not e2e"`.

## Produção
1. Instalar no diretório da aplicação (o deploy de referência usa
   `/var/www/sistema_trilhas`), criar `venv` e
   `pip install --require-hashes -r requirements.lock` — é o que reproduz
   exatamente a árvore que o CI validou, transitivas incluídas.
2. `.env` com `DJANGO_SETTINGS_MODULE=config.settings.production`, `SECRET_KEY`,
   `ALLOWED_HOSTS`, `DATABASE_*`, `REDIS_URL=redis://localhost:6379/3`, `ANTHROPIC_API_KEY`.
3. `python manage.py migrate && python manage.py collectstatic`.
   > `collectstatic` exige `DJANGO_SETTINGS_MODULE=config.settings.production`
   > (o `manage.py` usa dev por padrão, sem manifest de estáticos).
4. Copiar os units de `deploy/systemd/` e habilitar `trilhas.service`,
   `trilhas_celery.service` e `trilhas_celery_video.service` (fila dedicada ao vídeo).
5. Nginx a partir de `deploy/nginx/`.

Alterações em template (ex.: `templates/partials/icons.html`) pedem só um restart do
`trilhas.service`; alterações em `models`/`services`/`tasks` pedem restart também do
`trilhas_celery*`.

## Apps
- `accounts` — `Profile` (quota de tokens, XP/diamantes/streak, visitante).
- `trilhas` — `Trilha`, `PerguntaDirecionadora`, `Nivel`, `Subtopico`, `CardSalvo`,
  `VideoSubtopico`, `Percurso`/`PassoPercurso`, `SessaoSugestao`/`TrilhaSugerida`.
- `avaliacoes` — `Avaliacao`, `Questao`, `Resposta`, `Titulo`, `ListaExercicios`/
  `Exercicio`, `Revisao`/`QuestaoRevisao` (revisão espaçada em `spaced.py`).
- `ai` — `services.py` (Claude), `tasks.py` (Celery), `prompts.py`.
- `chat` — `Conversa` (uma por trilha) e `Mensagem` do chat de dúvidas; a
  chamada à IA fica em `ai`.
- `legal` — versionamento de Termos/Privacidade e registro de aceites.

## Conformidade legal (LGPD / Marco Civil)

O app `legal` versiona os Termos de Uso e a Política de Privacidade e registra cada aceite
com data, hora, IP, navegador e o `sha256` do texto exato aceito. O checkbox nasce
desmarcado e é obrigatório no servidor; publicar uma versão com mudança material obriga
todos a aceitarem de novo antes de continuar usando o sistema.

A política descreve a transferência internacional para a API do Claude (art. 33 e 33, VI
da LGPD), o papel da Anthropic como operadora sob o *Data Processing Addendum* e a
retenção de até 30 dias do lado dela.

Os registros de acesso do nginx são mantidos por **6 meses**, como exige o art. 15 do
Marco Civil (`deploy/logrotate/stolben-acesso` e `deploy/nginx_acesso.py`).

As conversas do chat de dúvidas são apagadas **90 dias** depois da última mensagem
(`chat.tasks.purgar_conversas_antigas`, no beat), e o aluno pode apagá-las na hora pelo
botão do painel.

O procedimento completo está em [docs/CONFORMIDADE.md](docs/CONFORMIDADE.md).

```bash
./venv/bin/python manage.py importar_documentos_legais --publicar  # seed inicial
./venv/bin/python manage.py exportar_documentos_legais             # espelho em git
```

## Qualidade

O CI é o pipeline compartilhado de [rigst/ci](https://github.com/rigst/ci) e
precisa passar antes do merge. Todo push roda, em paralelo:

As doze etapas estão ligadas e **nenhuma** está em `soft-fail`: qualquer uma
que falhe derruba o build.

| Etapa | Ferramenta | Estado |
|---|---|---|
| Lint e formatação | `ruff` | bloqueia |
| Tipos | `mypy` | bloqueia |
| Testes e cobertura | `pytest` + `pytest-cov` | bloqueia |
| Segurança do código | `bandit` | bloqueia a partir de severidade alta |
| Dependências | `pip-audit` | bloqueia |
| Segredos | `gitleaks` (histórico completo) | bloqueia |
| Django | `check --deploy` + `makemigrations --check` | bloqueia |
| Licenças | `liccheck` + inventário `pip-licenses` | bloqueia |
| SBOM | CycloneDX | publica artefato |
| Lock | `requirements.lock` + `pip --require-hashes` | bloqueia |
| Ponta a ponta | `pytest -m e2e` com Playwright | bloqueia |
| Acessibilidade | `axe-core` sobre as telas públicas | bloqueia em impacto `serious` |
| Agregação | SonarQube Cloud | bloqueia |

Cobertura no [Codecov](https://codecov.io/gh/rigst/sistema_trilhas); bugs, code
smells e duplicação no
[SonarQube Cloud](https://sonarcloud.io/summary/new_code?id=rigst_sistema_trilhas).

A proteção de branch deve exigir um único check, `CI` — ele consolida todos os
outros.

## Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md).

## Segurança

Política de reporte e verificações automáticas: [SECURITY.md](SECURITY.md).
Não abra issue pública para vulnerabilidade.

## Licença

[**AGPL-3.0**](LICENSE) — Copyright (C) 2026 Rodrigo Caballero Stölben.

Você pode usar, estudar, modificar e redistribuir. A cláusula que caracteriza a
AGPL: se você rodar uma versão modificada como serviço acessível pela rede, os
usuários desse serviço têm direito ao código-fonte correspondente.

O uso da instância hospedada em trilhas.stolben.com continua regido pelos Termos
de Uso publicados lá — a licença cobre o software, não o serviço operado pelo autor.

As bibliotecas de terceiros permanecem sob suas próprias licenças; o inventário está
em [docs/LICENCAS-TERCEIROS.md](docs/LICENCAS-TERCEIROS.md), regenerável com:

```bash
./venv/bin/python scripts/licencas_terceiros.py
```

A geração de vídeo chama **FFmpeg** (GPL-2.0+ no build do Ubuntu) e o **Chromium** do
Playwright como processos externos, sem linkagem com este código e sem distribuir os
binários — a reciprocidade da GPL não alcança o projeto. As fontes em `static/fonts/`
usam a SIL Open Font License (`OFL.txt` junto aos arquivos).
