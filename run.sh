#!/usr/bin/env bash
# =============================================================================
#  iBarber - execucao local (macOS / Linux)
#  Equivalente ao run.bat. Uso:
#     ./run.sh          inicia o servidor
#     ./run.sh reset    apaga o banco SQLite e recria do zero
#     ./run.sh deps     apenas reinstala as dependencias
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

VENV="venv"
PY="$VENV/bin/python"

# ------------------------------------------------------------------ Python ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERRO] python3 nao encontrado no PATH."
  exit 1
fi

# O Python de sistema do macOS e compilado com LibreSSL, que nao tem
# hashlib.scrypt — o algoritmo que o Werkzeug 3 usa para hash de senha.
# Sem ele, cadastro e login quebram com AttributeError.
if ! python3 -c 'import hashlib; hashlib.scrypt' >/dev/null 2>&1; then
  echo
  echo "[AVISO] Este Python nao tem hashlib.scrypt (compilado com LibreSSL)."
  echo "        Cadastro e login vao falhar. Instale um Python oficial:"
  echo "          brew install python@3.12        ou   https://www.python.org/downloads/"
  echo "        Depois: rm -rf venv && ./run.sh"
  echo
  read -r -p "Continuar mesmo assim? [s/N] " resp
  case "$resp" in [sS]) ;; *) exit 1 ;; esac
fi

# -------------------------------------------------------------------- venv ---
# Um venv copiado de outra maquina tem o symlink do python quebrado; nesse
# caso recria em vez de falhar com um erro obscuro.
if [ ! -x "$PY" ]; then
  if [ -d "$VENV" ]; then
    echo "[1/4] venv invalido (symlink quebrado) — recriando..."
    rm -rf "$VENV"
  else
    echo "[1/4] Criando ambiente virtual..."
  fi
  python3 -m venv "$VENV"
  FORCE_DEPS=1
else
  echo "[1/4] Ambiente virtual encontrado."
fi

# ------------------------------------------------------------- dependencias --
if [ "${1:-}" = "deps" ]; then FORCE_DEPS=1; fi
if [ -n "${FORCE_DEPS:-}" ]; then
  echo "[2/4] Instalando dependencias..."
  "$PY" -m pip install --upgrade pip --quiet
  "$PY" -m pip install -r requirements.txt --quiet
else
  echo "[2/4] Dependencias ja instaladas (./run.sh deps para reinstalar)."
fi
[ "${1:-}" = "deps" ] && { echo "Pronto."; exit 0; }

# -------------------------------------------------------------------- .env ---
if [ ! -f .env ]; then
  echo "[3/4] Gerando .env de desenvolvimento..."
  SECRET=$("$PY" -c 'import secrets;print(secrets.token_hex(32))')
  TOKEN=$("$PY" -c 'import secrets;print(secrets.token_urlsafe(32))')
  cat > .env <<EOF
# Gerado por run.sh para desenvolvimento local. Nao use em producao.
SECRET_KEY=$SECRET
API_TOKEN=$TOKEN
DATABASE_URL=sqlite:///ibarber.db

# SMTP: sem credenciais reais o envio falha e apenas registra no log,
# o que nao impede o restante da aplicacao de funcionar.
MAIL_USER=dev@localhost
MAIL_PASSWORD=dev
MAIL_FROM=dev@localhost

APP_DOMAIN=localhost
APP_BASE_URL=http://127.0.0.1:5000
FLASK_DEBUG=1
FLASK_RUN_HOST=127.0.0.1
FLASK_RUN_PORT=5000
EOF
  echo "      .env criado com SECRET_KEY e API_TOKEN aleatorios."
else
  echo "[3/4] .env encontrado."
fi

# ------------------------------------------------------------------- reset ---
if [ "${1:-}" = "reset" ]; then
  echo "      Apagando banco e uploads locais..."
  rm -f ibarber.db instance/ibarber.db
  rm -rf static/uploads
  echo "      Banco zerado. Sera recriado ao iniciar."
fi

# ---------------------------------------------------------------- servidor ---
echo "[4/4] Iniciando..."
echo
echo "  Site      http://127.0.0.1:5000"
echo "  Gestao    http://127.0.0.1:5000/gestao/login"
echo "  Admin     http://127.0.0.1:5000/admin/painel"
echo
echo "  O token do /admin/painel esta em .env (API_TOKEN)."
echo "  Ctrl+C encerra."
echo
exec "$PY" app.py
