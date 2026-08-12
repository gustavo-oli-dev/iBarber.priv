"""Configuração do gunicorn para o Render.

O Render injeta PORT e espera que o processo escute em 0.0.0.0:$PORT.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# 2 workers já cobrem o tráfego inicial e mantêm o uso de memória baixo no
# plano free/starter. Cada worker abre seu próprio pool do SQLAlchemy
# (pool_size=10 + max_overflow=20), então subir workers exige rever o limite
# de conexões do Postgres.
workers = int(os.environ.get('WEB_CONCURRENCY', '2'))
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
worker_class = 'gthread'

# Uploads de imagem e chamadas ao Mercado Pago podem passar de 30s.
timeout = 120
graceful_timeout = 30
keepalive = 5

# Recicla workers periodicamente para conter vazamento de memória.
max_requests = 1000
max_requests_jitter = 100

accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('LOG_LEVEL', 'info')
# %({x-forwarded-for}i)s: atrás do proxy do Render, o IP real vem no header
access_log_format = '%({x-forwarded-for}i)s %(m)s %(U)s %(s)s %(M)sms'

# preload_app=False de propósito: o app.py roda migrações e cria tabelas no
# import. Com preload isso aconteceria uma vez no master (bom), mas o
# BackgroundScheduler herdado pelo fork ficaria em estado inconsistente.
preload_app = False
