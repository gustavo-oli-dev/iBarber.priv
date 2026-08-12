"""Entrypoint do processo de jobs periódicos (Render Background Worker).

Importar `app` já inicia o BackgroundScheduler quando RUN_SCHEDULER=1.
Como o scheduler roda em threads daemon, a thread principal precisa ficar viva —
é só isso que este arquivo faz.

Rode APENAS neste processo. Com RUN_SCHEDULER=1 nos workers do gunicorn, cada
job executaria uma vez por worker (lembretes duplicados, limpezas concorrentes).
"""
import os
import sys
import threading

if os.environ.get('RUN_SCHEDULER') != '1':
    sys.exit('worker.py exige RUN_SCHEDULER=1 no ambiente')

import app  # noqa: F401 — o import é o que registra e inicia os jobs

app.app.logger.info('[WORKER] scheduler ativo — aguardando os jobs')

# Bloqueia para sempre sem consumir CPU. O Render encerra o processo por SIGTERM.
threading.Event().wait()
