"""
Configurações da suíte de testes no CI: as de development, com o banco em
PostgreSQL.

Produção roda em PostgreSQL e a suíte rodava em SQLite, que aceita coisas que
o Postgres recusa (ex.: string maior que o max_length, comparação de tipos
diferentes, ordem implícita de resultados). Só o banco muda: Celery eager,
sessões em banco e o resto continuam os de development, para os testes não
dependerem de Redis.

As variáveis são as mesmas DB_* dos settings de produção; no CI vêm do
`django-env` do ci.yml e apontam para o Postgres descartável do job. Para
rodar local sem Postgres, use config.settings.development (padrão do
pytest.ini).
"""

import os

from .development import *

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "postgres"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}
