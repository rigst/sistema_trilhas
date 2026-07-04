"""Gunicorn configuration — Trilhas de Estudo."""

import multiprocessing

# Unix socket
bind = "unix:/var/www/sistema_trilhas/trilhas.sock"

# Garante que o socket seja acessível pelo grupo (www-data / nginx)
umask = 0o007

workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"

# Geração de conteúdo/avaliação por IA pode ser demorada.
timeout = 300
keepalive = 2

proc_name = "trilhas"

accesslog = "/var/www/sistema_trilhas/media/gunicorn.access.log"
errorlog = "/var/www/sistema_trilhas/media/gunicorn.error.log"
loglevel = "info"

daemon = False
