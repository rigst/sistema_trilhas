# Trilhas de Estudo com IA

App Django onde o usuário descreve livremente um tema, a IA (Claude Opus 4.8)
faz perguntas direcionadoras, monta um sumário do básico ao avançado e gera o
conteúdo de cada nível sob demanda. Cada nível termina numa avaliação
(objetiva + dissertativa) corrigida pela IA; ao atingir a nota mínima, o usuário
ganha um título (Iniciante/Especialista/Mestre em X) e desbloqueia o próximo.

Segue os padrões dos demais apps em `/var/www` (design system Stölben, Celery +
Redis, settings split, login com visitante).

## Stack
Django 6 · Celery + Redis (DB 3) · Anthropic SDK · SQLite (dev) / PostgreSQL (prod).
UI própria tema **"Foco"** (noturno gamificado, Sora + Inter). Markdown com callouts
(admonitions) e destaque de sintaxe via **Pygments**.

## Divisão de modelos (configurável no .env)
- **Opus 4.8** (`AI_MODEL_PLANEJAMENTO`): sumário e correção das dissertativas.
- **Sonnet 4.6** (`AI_MODEL`): perguntas, conteúdo, avaliação e exercícios (rápido/barato).

## Funcionalidades
- Perguntas direcionadoras → sumário → conteúdo sob demanda (todas com barra/skeleton de progresso).
- Conteúdo rico: subtítulos, caixas de destaque coloridas e código com cores de sintaxe.
- **Exercícios de prática** (rota separada) respondidos com feedback imediato (sem nota).
- Avaliação por nível corrigida pela IA → título + desbloqueio do próximo nível.

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

## Produção
1. Instalar em `/var/www/sistema_trilhas`, criar `venv`, `pip install -r requirements.txt`.
2. `.env` com `DJANGO_SETTINGS_MODULE=config.settings.production`, `SECRET_KEY`,
   `ALLOWED_HOSTS`, `DATABASE_*`, `REDIS_URL=redis://localhost:6379/3`, `ANTHROPIC_API_KEY`.
3. `python manage.py migrate && python manage.py collectstatic`.
4. Copiar os units de `deploy/systemd/` e habilitar `trilhas.service` + `trilhas_celery.service`.
5. Nginx a partir de `deploy/nginx.conf`.

## Apps
- `accounts` — `Profile` (quota de tokens, visitante).
- `trilhas` — `Trilha`, `PerguntaDirecionadora`, `Nivel`, `Subtopico`.
- `avaliacoes` — `Avaliacao`, `Questao`, `Resposta`, `Titulo`.
- `ai` — `services.py` (Claude), `tasks.py` (Celery), `prompts.py`.

## Licença

Software **proprietário** — todos os direitos reservados (ver [LICENSE](LICENSE)).
O código não é aberto nem redistribuível; o uso do serviço é regido pelos Termos de
Uso publicados em trilhas.stolben.com.

As bibliotecas de terceiros permanecem sob suas próprias licenças; o inventário está
em [docs/LICENCAS-TERCEIROS.md](docs/LICENCAS-TERCEIROS.md), regenerável com:

```bash
./venv/bin/python scripts/licencas_terceiros.py
```

A geração de vídeo chama **FFmpeg** (GPL-2.0+ no build do Ubuntu) e o **Chromium** do
Playwright como processos externos, sem linkagem com este código e sem distribuir os
binários — a reciprocidade da GPL não alcança o projeto. As fontes em `static/fonts/`
usam a SIL Open Font License (`OFL.txt` junto aos arquivos).
