"""
Configurações base do Django — Trilhas de Estudo com IA.
Compartilhadas entre development e production.
"""

import os
from pathlib import Path

from celery.schedules import crontab
from dotenv import load_dotenv

# Carrega variáveis de ambiente do .env
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()
]


# Application definition

INSTALLED_APPS = [
    # O unfold precisa vir antes do admin: é assim que os templates dele
    # sobrescrevem os do django.contrib.admin.
    'unfold',
    'unfold.contrib.filters',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Apps do projeto
    'accounts',
    'trilhas',
    'avaliacoes',
    'ai',
    'legal',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Depois do Authentication (precisa de request.user) e antes do
    # VisitorExpiryMiddleware: nova versão dos termos bloqueia o uso até ser
    # aceita, inclusive para visitantes.
    'legal.middleware.AceiteObrigatorioMiddleware',
    'accounts.middleware.VisitorExpiryMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'accounts.context_processors.profile_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# Default primary key field type

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Auth redirects
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

# Auto-cadastro por e-mail — implementado, porém DESLIGADO por padrão. As rotas
# de cadastro respondem 404 e o link some do login enquanto isto for False.
SIGNUP_ENABLED = os.getenv('SIGNUP_ENABLED', 'False').lower() in ('true', '1', 'yes')
# Validade do link de confirmação de e-mail / reset de senha (segundos).
PASSWORD_RESET_TIMEOUT = int(os.getenv('PASSWORD_RESET_TIMEOUT', str(3 * 24 * 3600)))


# ==============================================================================
# Celery Configuration
# ==============================================================================

# Broker/backend em DB Redis próprio (/3), separado de cache (/1) e sessões (/2).
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', os.getenv('REDIS_URL', 'redis://localhost:6379/3'))
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', os.getenv('REDIS_URL', 'redis://localhost:6379/3'))
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

CELERY_BEAT_SCHEDULE = {
    'cleanup-expired-visitors': {
        'task': 'accounts.tasks.cleanup_expired_visitors',
        'schedule': int(os.getenv('CLEANUP_INTERVAL_MINUTES', '60')) * 60,
    },
    # Lembrete diário de ofensiva (streak) — no-op até STREAK_REMINDERS_ENABLED.
    'streak-reminders': {
        'task': 'accounts.tasks.enviar_lembretes_streak',
        'schedule': crontab(
            hour=int(os.getenv('STREAK_REMINDER_HOUR', '20')), minute=0,
        ),
    },
}

# A geração de vídeo é pesada (Chromium + ffmpeg) e vai para uma fila própria,
# consumida por um worker dedicado (deploy/systemd/trilhas_celery_video.service),
# para não bloquear as tasks de IA na fila default.
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_TASK_ROUTES = {
    'trilhas.tasks.task_gerar_video_subtopico': {'queue': 'video'},
}


# ==============================================================================
# E-mail
# ==============================================================================

EMAIL_BACKEND = os.getenv(
    'EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend'
)
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
# Porta 587 → STARTTLS (EMAIL_USE_TLS). Porta 465 → SSL implícito (EMAIL_USE_SSL).
# São mutuamente exclusivos; defina apenas um como True conforme a porta.
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False').lower() in ('true', '1', 'yes')
DEFAULT_FROM_EMAIL = os.getenv(
    'DEFAULT_FROM_EMAIL', 'Trilhas de Estudo <no-reply@trilhas.stolben.com>'
)
SITE_URL = os.getenv('SITE_URL', 'https://trilhas.stolben.com')

# Lembretes de ofensiva por e-mail só disparam quando explicitamente ligados.
STREAK_REMINDERS_ENABLED = os.getenv(
    'STREAK_REMINDERS_ENABLED', 'False'
).lower() in ('true', '1', 'yes')


# ==============================================================================
# IA (Anthropic / Claude) Configuration
# ==============================================================================

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

# Divisão de modelos por tarefa:
#   - Planejamento (sumário/percurso do mentor) → Opus 4.8 (mais julgamento).
#   - Demais tarefas (perguntas, conteúdo, avaliação, exercícios) → Sonnet 5 (rápido/barato,
#     qualidade quase-Opus em conteúdo/agentic; ~30% mais tokens no tokenizador novo).
#   - Correção de avaliação é determinística (gabarito), não usa IA.
AI_MODEL_GERAL = os.getenv('AI_MODEL', 'claude-sonnet-5')
AI_MODEL_PLANEJAMENTO = os.getenv('AI_MODEL_PLANEJAMENTO', 'claude-opus-4-8')
# Compatibilidade: alguns lugares ainda leem AI_MODEL.
AI_MODEL = AI_MODEL_GERAL

# Esforço de raciocínio: mais alto no planejamento/correção; médio no volume (velocidade).
AI_EFFORT = os.getenv('AI_EFFORT', 'high')
AI_EFFORT_GERAL = os.getenv('AI_EFFORT_GERAL', 'medium')

# max_tokens para respostas estruturadas (sumário/avaliação/correção/exercícios)
AI_MAX_TOKENS = int(os.getenv('AI_MAX_TOKENS', '16000'))
# max_tokens para o conteúdo longo de cada nível (usa streaming)
AI_MAX_TOKENS_CONTEUDO = int(os.getenv('AI_MAX_TOKENS_CONTEUDO', '32000'))

# Preços por 1M tokens (USD) por modelo. Fallback = Opus.
AI_PRICE_INPUT_PER_MTOK = float(os.getenv('AI_PRICE_INPUT_PER_MTOK', '5.0'))
AI_PRICE_OUTPUT_PER_MTOK = float(os.getenv('AI_PRICE_OUTPUT_PER_MTOK', '25.0'))
# Preços por 1M tokens (input, output) em USD. Sonnet 5 tem preço promocional
# de US$2/US$10 até 2026-08-31; usamos o preço-cheio (US$3/US$15) para a quota
# não subestimar o custo quando a promoção acabar.
AI_PRICES = {
    'claude-opus-4-8': (5.0, 25.0),
    'claude-sonnet-5': (3.0, 15.0),
    'claude-sonnet-4-6': (3.0, 15.0),
}

# Quotas mensais (tokens) — usuário comum e visitante
QUOTA_TOKENS_DEFAULT = int(os.getenv('QUOTA_TOKENS_DEFAULT', '3000000'))
QUOTA_TOKENS_VISITOR = int(os.getenv('QUOTA_TOKENS_VISITOR', '300000'))


# ==============================================================================
# Vídeo do tópico (slideshow narrado gerado sob demanda)
# ==============================================================================

# Voz PT-BR do edge-tts (grátis). Alternativa feminina: pt-BR-FranciscaNeural.
VIDEO_ENABLED = os.getenv('VIDEO_ENABLED', 'True') == 'True'
CERTIFICADO_ENABLED = os.getenv('CERTIFICADO_ENABLED', 'True') == 'True'
VIDEO_TTS_VOICE = os.getenv('VIDEO_TTS_VOICE', 'pt-BR-AntonioNeural')
# Binários de mídia (ajuste se não estiverem no PATH do worker).
VIDEO_FFMPEG_BIN = os.getenv('VIDEO_FFMPEG_BIN', 'ffmpeg')
VIDEO_FFPROBE_BIN = os.getenv('VIDEO_FFPROBE_BIN', 'ffprobe')
# Música de fundo: por padrão a faixa é escolhida pela CATEGORIA da trilha
# (ver trilhas/video_pipeline.MUSICAS). Definir VIDEO_MUSICA_PATH força uma
# faixa fixa para todos os vídeos; vazio = seleção automática por categoria.
VIDEO_MUSICA_PATH = os.getenv('VIDEO_MUSICA_PATH', '')

# Expiração do visitante (horas de inatividade)
VISITOR_EXPIRY_HOURS = int(os.getenv('VISITOR_EXPIRY_HOURS', '48'))


# ==============================================================================
# Regras da trilha
# ==============================================================================

# Nota média mínima (0–10) para aprovar num nível e ganhar o título.
NOTA_MINIMA_APROVACAO = float(os.getenv('NOTA_MINIMA_APROVACAO', '7.0'))

UNFOLD = {
    'SITE_TITLE': 'Trilhas de Estudo',
    'SITE_HEADER': 'Trilhas de Estudo',
    'SITE_SUBHEADER': 'Administração',
    'SHOW_HISTORY': True,
    'SHOW_VIEW_ON_SITE': False,
    'COLORS': {
        # Teal do tema do app.
        'primary': {
            '50': '240 253 250', '100': '204 251 241', '200': '153 246 228',
            '300': '94 234 212', '400': '45 212 191', '500': '20 184 166',
            '600': '13 148 136', '700': '15 118 110', '800': '17 94 89',
            '900': '19 78 74', '950': '4 47 46',
        },
    },
}

# Destino após o aceite nas telas do app `legal`.
LEGAL_REDIRECT_URL = 'dashboard'

# Para onde a tela de aceite de visitante posta. A criação do visitante tem
# rota própria e não precisa de campos extras.
LEGAL_VISITOR_ACTION = 'accounts:entrar_visitante'
LEGAL_VISITOR_EXTRA = {}

# Rotas do PWA que o middleware de re-aceite não pode interceptar: um 302 para
# a tela de aceite seria cacheado pelo service worker.
LEGAL_ALLOWLIST_EXTRA = ('/sw.js', '/offline/')
