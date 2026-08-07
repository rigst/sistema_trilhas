"""
Configuração do Celery para o projeto Trilhas de Estudo.
"""

import os

from celery import Celery

# Em produção o worker deve ser seguro mesmo se o EnvironmentFile não definir
# DJANGO_SETTINGS_MODULE. Para desenvolvimento, exporte config.settings.development.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("config")

# Carrega configurações do Django com prefixo CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-descobre tasks nos apps instalados
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
