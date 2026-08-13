# Desenvolvimento

## Subir o ambiente

`run.bat` (Windows) ou `./run.sh` (macOS/Linux) fazem tudo: criam o venv,
instalam dependências, geram o `.env` e sobem o servidor.

Para fazer à mão:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env      # preencha SECRET_KEY, API_TOKEN, DATABASE_URL, MAIL_*
venv/bin/python app.py
```

Quatro variáveis são obrigatórias — sem elas o `app.py` levanta `RuntimeError`
já no import: `SECRET_KEY`, `DATABASE_URL`, `API_TOKEN` (mínimo 32 caracteres)
e o trio `MAIL_USER` / `MAIL_PASSWORD` / `MAIL_FROM`.

Em desenvolvimento, SMTP inválido não atrapalha: `_enviar_email()` captura a
exceção, registra no log e devolve `False`.

## Banco

Local usa SQLite (`sqlite:///ibarber.db`); produção usa MySQL. O schema é criado
no import do `app.py` — não há Alembic. O bloco de boot:

1. Cria as tabelas que ainda não existem
2. Adiciona colunas novas via `ALTER TABLE`, a partir de uma allowlist
3. Torna `funcionario.email` nullable (falha silenciosamente no SQLite, o que é
   esperado)

Consequência prática: **mudar um modelo não altera uma tabela existente**. Em
desenvolvimento, recrie o banco:

```bash
./run.sh reset      # apaga ibarber.db e os uploads locais
```

Em produção, a coluna precisa entrar na allowlist do bloco de boot
(`_ALLOWED_*_COLS` + `_*_COL_TYPES`) ou ser aplicada manualmente.

## Jobs periódicos

Cinco jobs rodam no APScheduler: lembretes (30 min), assinaturas (12h), limpeza
de convidados (24h), trials expirados (24h) e retorno automático (12h).

Eles só iniciam quando:

- `RUN_SCHEDULER=1` — o processo dedicado em produção, **ou**
- `FLASK_DEBUG=1` e `WERKZEUG_RUN_MAIN=true` — o processo principal em dev

Rodar com `RUN_SCHEDULER=1` em vários workers do gunicorn faz cada job executar
N vezes: lembretes duplicados para o cliente e limpezas concorrentes.

Para testar lembretes sem esperar: `POST /api/testar-lembretes` com token de
dono.

## Multi-tenant em desenvolvimento

Não há subdomínio local, então `get_tenant_atual()` cai no segundo caminho: o
`path_tenant_id` da sessão. Acesse `http://127.0.0.1:5000/<slug>` uma vez e a
sessão passa a carregar aquele tenant.

Uma barbearia só é visível publicamente com `assinatura_ativa = True`. Ative
pelo `/admin/painel` — em dev não é preciso passar por pagamento.

## Depuração

**Sessão e cookie.** `SESSION_COOKIE_DOMAIN` vira `.APP_DOMAIN` fora de dev. Se
o `.env` tiver `APP_DOMAIN=ibarber.shop` enquanto você acessa `127.0.0.1`, o
navegador descarta o cookie e nada de login funciona. Em dev use
`APP_DOMAIN=localhost`.

**CSRF.** Todo `POST`/`PUT`/`PATCH`/`DELETE` autenticado por cookie exige o
token. O `static/js/csrf.js` injeta sozinho em `fetch`, `XMLHttpRequest` e
formulários. Chamando a API por fora (curl, Postman), envie `X-CSRF-Token` ou
use `Authorization: Bearer <token>`, que é isento.

**Token da API expirado.** Vale `TOKEN_TTL_HORAS` (padrão 12h). Um `403` súbito
no painel depois de deixar a aba aberta é isso — basta recarregar.

**Uploads.** Vão para `static/uploads/`, que está no `.gitignore`. Limite de
10 MB por imagem, com EXIF removido e redimensionamento para 1400px.

## Estilo do código

Tudo vive em `app.py`. Convenções observadas:

- Funções auxiliares privadas com `_` (`_gestao_tid`, `_safe_json`)
- Nomes e mensagens em português; código e chaves de API em inglês
- Guardas de autorização na primeira linha da view, com retorno imediato
- Consultas sempre filtradas por `tenant_id`

Ao adicionar um endpoint, escolha a guarda certa:

| Guarda | Quando |
|---|---|
| `verificar_token_admin` | operação do dono (credenciais, funcionários, config) |
| `verificar_token_perm(req, 'x')` | recurso com permissão correspondente |
| `verificar_token` | leitura que qualquer funcionário ativo pode fazer |
| `_gestao_login_required` | página HTML do painel |
| `admin_key_required` | administração do SaaS |
