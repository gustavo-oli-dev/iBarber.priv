@echo off
REM ============================================================================
REM  iBarber - execucao local (Windows)
REM
REM  Uso:  run.bat            inicia o servidor
REM        run.bat reset      apaga o banco SQLite e recria do zero
REM        run.bat deps       apenas reinstala as dependencias
REM
REM  Na primeira execucao cria o venv, instala as dependencias e gera um .env
REM  de desenvolvimento com SECRET_KEY e API_TOKEN aleatorios.
REM ============================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "VENV=venv"
set "PY=%VENV%\Scripts\python.exe"

REM ---------------------------------------------------------------- Python ---
where py >nul 2>&1
if %errorlevel%==0 (set "BASEPY=py -3") else (set "BASEPY=python")
%BASEPY% --version >nul 2>&1
if errorlevel 1 (
  echo [ERRO] Python nao encontrado no PATH.
  echo        Instale em https://www.python.org/downloads/ marcando
  echo        "Add Python to PATH" e rode este script de novo.
  exit /b 1
)

REM O Werkzeug 3 usa hashlib.scrypt para hash de senha; builds sem OpenSSL
REM 1.1.1+ nao tem essa funcao e o cadastro/login quebra. O instalador
REM oficial do Windows ja vem correto — este teste so pega casos exoticos.
%BASEPY% -c "import hashlib; hashlib.scrypt" >nul 2>&1
if errorlevel 1 (
  echo [ERRO] Este Python nao tem hashlib.scrypt e o login nao vai funcionar.
  echo        Instale o Python oficial: https://www.python.org/downloads/
  exit /b 1
)

REM ------------------------------------------------------------------ venv ---
if not exist "%PY%" (
  echo [1/4] Criando ambiente virtual...
  %BASEPY% -m venv "%VENV%"
  if errorlevel 1 (echo [ERRO] Falha ao criar o venv. & exit /b 1)
  set "FORCE_DEPS=1"
) else (
  echo [1/4] Ambiente virtual encontrado.
)

REM ------------------------------------------------------------ dependencias -
if "%1"=="deps" set "FORCE_DEPS=1"
if defined FORCE_DEPS (
  echo [2/4] Instalando dependencias...
  "%PY%" -m pip install --upgrade pip --quiet
  "%PY%" -m pip install -r requirements.txt --quiet
  if errorlevel 1 (echo [ERRO] Falha ao instalar as dependencias. & exit /b 1)
) else (
  echo [2/4] Dependencias ja instaladas ^(run.bat deps para reinstalar^).
)
if "%1"=="deps" (echo Pronto. & exit /b 0)

REM ------------------------------------------------------------------- .env ---
if not exist ".env" (
  echo [3/4] Gerando .env de desenvolvimento...
  REM SECRET_KEY e API_TOKEN aleatorios; API_TOKEN precisa de 32+ caracteres
  for /f "delims=" %%k in ('"%PY%" -c "import secrets;print(secrets.token_hex(32))"') do set "GEN_SECRET=%%k"
  for /f "delims=" %%k in ('"%PY%" -c "import secrets;print(secrets.token_urlsafe(32))"') do set "GEN_TOKEN=%%k"
  (
    echo # Gerado por run.bat para desenvolvimento local. Nao use em producao.
    echo SECRET_KEY=!GEN_SECRET!
    echo API_TOKEN=!GEN_TOKEN!
    echo DATABASE_URL=sqlite:///ibarber.db
    echo.
    echo # SMTP: sem credenciais reais o envio falha e apenas registra no log,
    echo # o que nao impede o restante da aplicacao de funcionar.
    echo MAIL_USER=dev@localhost
    echo MAIL_PASSWORD=dev
    echo MAIL_FROM=dev@localhost
    echo.
    echo APP_DOMAIN=localhost
    echo APP_BASE_URL=http://127.0.0.1:5000
    echo FLASK_DEBUG=1
    echo FLASK_RUN_HOST=127.0.0.1
    echo FLASK_RUN_PORT=5000
  ) > .env
  echo       .env criado com SECRET_KEY e API_TOKEN aleatorios.
) else (
  echo [3/4] .env encontrado.
)

REM ------------------------------------------------------------------ reset ---
if "%1"=="reset" (
  echo       Apagando banco e uploads locais...
  if exist "ibarber.db" del /q "ibarber.db"
  if exist "instance\ibarber.db" del /q "instance\ibarber.db"
  if exist "static\uploads" rd /s /q "static\uploads"
  echo       Banco zerado. Sera recriado ao iniciar.
)

REM ----------------------------------------------------------------- servidor -
echo [4/4] Iniciando...
echo.
echo   Site      http://127.0.0.1:5000
echo   Gestao    http://127.0.0.1:5000/gestao/login
echo   Admin     http://127.0.0.1:5000/admin/painel
echo.
echo   O token do /admin/painel esta em .env ^(API_TOKEN^).
echo   Ctrl+C encerra.
echo.
"%PY%" app.py
endlocal
