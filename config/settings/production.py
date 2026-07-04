"""
Configurações de produção.
Usa PostgreSQL, Redis para cache/sessões, e configurações de segurança.
"""

import os

from .base import *  # noqa: F401, F403

DEBUG = False


def _required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f'A variável de ambiente {name} é obrigatória em produção.')
    return value


SECRET_KEY = _required_env('SECRET_KEY')
if SECRET_KEY.startswith('django-insecure-'):
    raise RuntimeError('SECRET_KEY de produção não pode usar valor django-insecure.')

ALLOWED_HOSTS = [
    h.strip() for h in _required_env('ALLOWED_HOSTS').split(',') if h.strip()
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': _required_env('DB_NAME'),
        'USER': _required_env('DB_USER'),
        'PASSWORD': _required_env('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': 600,
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.getenv('REDIS_URL', 'redis://localhost:6379/3'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        },
    }
}

SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'

# Segurança
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

STATIC_ROOT = os.getenv('STATIC_ROOT', str(BASE_DIR / 'staticfiles'))

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage',
    },
}

CSRF_TRUSTED_ORIGINS = ['https://' + h for h in ALLOWED_HOSTS if h]


# ==============================================================================
# Monitoramento de erros (Sentry) — ativo só quando SENTRY_DSN está definido.
# ==============================================================================

SENTRY_DSN = os.getenv('SENTRY_DSN', '').strip()
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration(), CeleryIntegration()],
            environment=os.getenv('SENTRY_ENVIRONMENT', 'production'),
            release=os.getenv('SENTRY_RELEASE') or None,
            traces_sample_rate=float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.0')),
            send_default_pii=False,
        )
    except ImportError:
        # Pacote ainda não instalado: seguimos sem monitoramento (sem quebrar o app).
        pass
