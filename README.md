# iBarber

SaaS de agendamento para barbearias. Cada barbearia (*tenant*) recebe um site
próprio em `slug.ibarber.shop`, com tema personalizável, agenda, controle de
caixa e loja de produtos.

- **Site do cliente** — agendamento, histórico, pagamento via Mercado Pago
- **Painel de gestão** (`/gestao`) — agenda, clientes, serviços, preços, caixa
- **Painel admin** (`/admin/painel`) — administração do SaaS: ativar, desativar
  e excluir barbearias
- **App Flutter** (`/admin`) — build web servido pelo próprio Flask

---

## Rodar localmente

**Windows**

```bat
run.bat
```

**macOS / Linux**

```bash
./run.sh
```

Na primeira execução o script cria o ambiente virtual, instala as dependências
e gera um `.env` de desenvolvimento com `SECRET_KEY` e `API_TOKEN` aleatórios,
apontando para um banco SQLite local. Depois abra:

| Onde | URL |
|---|---|
| Site | http://127.0.0.1:5000 |
| Gestão | http://127.0.0.1:5000/gestao/login |
| Admin do SaaS | http://127.0.0.1:5000/admin/painel |

O token do painel admin é o `API_TOKEN` gerado no `.env`.

Outros comandos:

```bat
run.bat deps      REM reinstala as dependencias
run.bat reset     REM apaga o banco SQLite e os uploads locais
```

### Requisitos

- **Python 3.9 ou superior**, com `hashlib.scrypt` disponível — é o algoritmo
  que o Werkzeug 3 usa para hash de senha.

  O Python de sistema do macOS é compilado com LibreSSL e **não** tem `scrypt`:
  cadastro e login falham com `AttributeError`. Use um Python oficial:

  ```bash
  brew install python@3.12     # ou baixe em python.org
  rm -rf venv && ./run.sh
  ```

  O instalador oficial do Windows já vem correto.

### Primeiro acesso

O banco começa vazio — não há nenhuma barbearia. Para criar a primeira, use o
fluxo de cadastro em http://127.0.0.1:5000/landing, ou ative uma manualmente
pelo painel admin.

> Uma barbearia só aparece publicamente com `assinatura_ativa = True`. Em
> desenvolvimento, ative pelo `/admin/painel` sem precisar pagar.

---

## Arquitetura

```
app.py                 aplicação inteira: modelos, rotas, API, jobs (~5.700 linhas)
templates/             Jinja2 — site do cliente
templates/gestao/      Jinja2 — painel de gestão
static/js/csrf.js      injeta o token CSRF em fetch/XHR/forms
admin_web/             build web do app Flutter, servido em /admin
scripts/               auditorias estáticas (rodar_tudo.py)
docs/                  documentação detalhada
```

Stack: Flask 3 · SQLAlchemy 2 · APScheduler · Mercado Pago · Cloudflare DNS.

### Multi-tenant

O tenant é resolvido em `get_tenant_atual()`, nesta ordem:

1. **Subdomínio** — `barbearia.ibarber.shop` → slug `barbearia`
2. **Sessão** — `path_tenant_id`, usado em desenvolvimento e no acesso por
   `/<slug>`

Todo dado é isolado por `tenant_id`. Localmente, como não há subdomínio, acesse
`http://127.0.0.1:5000/<slug>` para entrar no contexto de uma barbearia.

### Autenticação

Três universos independentes:

| Quem | Como entra | O que carrega |
|---|---|---|
| Cliente | sessão por cookie | `user_id` |
| Gestor / funcionário | sessão (`/gestao/login`) ou token da API | `gestao_tenant_id`, `gestao_func_id` |
| Admin do SaaS | `API_TOKEN` em `/admin/painel` | `admin_ok` na sessão |

O token da API é `base64(tenant_id:func_id:version:emissao:hmac)`, assinado com
`SECRET_KEY` e válido por `TOKEN_TTL_HORAS` (padrão 12h). `func_id = 0`
identifica o dono; qualquer outro valor é funcionário e passa pela checagem de
permissão em `verificar_token_perm()`.

Ver [docs/seguranca.md](docs/seguranca.md) para o modelo completo.

---

## Documentação

| Documento | Conteúdo |
|---|---|
| [docs/desenvolvimento.md](docs/desenvolvimento.md) | ambiente, banco, jobs, dicas de depuração |
| [docs/seguranca.md](docs/seguranca.md) | autenticação, permissões, CSRF, XSS, invariantes |
| [docs/deploy.md](docs/deploy.md) | produção na VPS (nginx, certbot, gunicorn) |
| [docs/variaveis-de-ambiente.md](docs/variaveis-de-ambiente.md) | referência de todas as variáveis |

---

## Testes e auditoria

Não há suíte de testes automatizados. O que existe:

```bash
venv/bin/python -m py_compile app.py     # sintaxe
cd scripts && python3 rodar_tudo.py      # auditorias estáticas
```

`scripts/rodar_tudo.py` procura imports órfãos, `debug=True`, credenciais no
código e rotas mortas. Ele não entende decorators nem `import ... as`, então
gera falsos positivos — confira cada achado antes de agir.

---

## Produção

Hoje roda numa VPS com nginx + gunicorn + MySQL. O provisionamento de
subdomínio (`_provisionar_ssl_tenant`) depende de **root**: chama `certbot`,
escreve em `/etc/nginx/sites-enabled/` e recarrega o nginx.

Isso torna a aplicação incompatível com plataformas de container (Render, Fly,
Heroku) sem alterações. Ver [docs/deploy.md](docs/deploy.md).
