# Variáveis de ambiente

Lidas de `.env` (via `python-dotenv`) ou do ambiente do processo. Modelo em
[`.env.example`](../.env.example).

## Obrigatórias

Sem qualquer uma delas o `app.py` levanta `RuntimeError` no import — a
aplicação não sobe.

| Variável | Descrição |
|---|---|
| `SECRET_KEY` | Assina a sessão e os tokens da API, e deriva a chave Fernet das credenciais de pagamento. Trocar invalida sessões, tokens e **torna ilegíveis as credenciais salvas do Mercado Pago**. |
| `DATABASE_URL` | `sqlite:///ibarber.db` local, `mysql+pymysql://user:senha@host/ibarber` em produção. |
| `API_TOKEN` | Chave mestra do `/admin/painel` e das rotas `/api/admin/*`. **Mínimo 32 caracteres** — validado no boot. Dá acesso a todas as barbearias. |
| `MAIL_USER` | Conta SMTP (Gmail exige senha de app). |
| `MAIL_PASSWORD` | Senha SMTP. |
| `MAIL_FROM` | Remetente exibido nos e-mails. |

## Pagamentos

| Variável | Padrão | Descrição |
|---|---|---|
| `MP_ACCESS_TOKEN` | — | Access Token do Mercado Pago da plataforma (assinaturas). Cada barbearia configura o seu em `/gestao/credenciais`. |
| `MP_PUBLIC_KEY` | — | Public key para o Brick de cartão. |
| `MP_WEBHOOK_SECRET` | — | Segredo de assinatura do webhook. **Sem ele, todas as notificações são rejeitadas** e nenhum pagamento é confirmado. |

## Domínio e CORS

| Variável | Padrão | Descrição |
|---|---|---|
| `APP_DOMAIN` | `ibarber.shop` | Domínio base. Define o subdomínio de cada tenant e o `Domain` do cookie. Em dev use `localhost`, senão o navegador descarta o cookie. |
| `APP_BASE_URL` | `https://ibarber.app.br` | URL usada em links de e-mail. |
| `CORS_ORIGINS` | vazio | Lista separada por vírgula. Vazio usa a allowlist embutida (`ibarber.shop`, `ibarber.app.br`). |

## Login com Google (opcional)

| Variável | Padrão |
|---|---|
| `GOOGLE_CLIENT_ID` | — |
| `GOOGLE_CLIENT_SECRET` | — |
| `GOOGLE_REDIRECT_URI` | `https://ibarber.shop/auth/google/callback` |

Sem `GOOGLE_CLIENT_ID`, o botão de login com Google apenas mostra um aviso.

## Provisionamento de subdomínio

Usado por `_provisionar_ssl_tenant()`, que **exige root**: cria o DNS na
Cloudflare, emite o certificado com `certbot` e escreve o vhost do nginx.

| Variável | Descrição |
|---|---|
| `CF_TOKEN` | Token da API Cloudflare com permissão de editar DNS da zona. |
| `CF_ZONE_ID` | ID da zona. |
| `VPS_IP` | IP de destino do registro A. |

Sem `CF_TOKEN` ou `CF_ZONE_ID`, a função retorna sem fazer nada — o cadastro
funciona, mas o subdomínio precisa ser criado à mão.

## Operação

| Variável | Padrão | Descrição |
|---|---|---|
| `REDIS_URL` | `memory://` | Contador do rate limiter. Com `memory://` o limite é por worker e zera a cada restart. |
| `RUN_SCHEDULER` | `0` | `1` **apenas** no processo dedicado aos jobs. Em vários workers, cada job roda N vezes. |
| `TOKEN_TTL_HORAS` | `12` | Validade dos tokens da API. |
| `FLASK_DEBUG` | `0` | `1` ativa reload, libera o scheduler no processo principal e afrouxa cookie e CORS. **Nunca `1` em produção.** |
| `FLASK_ENV` | — | `development` tem o mesmo efeito de `FLASK_DEBUG=1` sobre o cookie. |
| `FLASK_RUN_HOST` | `127.0.0.1` | Só no `python app.py`. Vira `0.0.0.0` com `FLASK_DEBUG=1`. |
| `FLASK_RUN_PORT` | `5000` | Só no `python app.py`. |

## Efeitos colaterais que surpreendem

**`SECRET_KEY`** faz três coisas ao mesmo tempo: assina a sessão, assina os
tokens da API e deriva a chave Fernet. Rotacionar desloga todo mundo *e* exige
que cada barbearia reconfigure o Access Token do Mercado Pago.

**`APP_DOMAIN`** vira o `Domain` do cookie de sessão. Como o cookie é
compartilhado com todos os subdomínios de tenant, `SameSite` não separa uma
barbearia da outra — a proteção efetiva contra CSRF é o token.

**`FLASK_DEBUG=1`** desliga `Secure` no cookie, remove `SESSION_COOKIE_DOMAIN`,
libera CORS para qualquer origem e faz `app.run()` escutar em `0.0.0.0`.

**`API_TOKEN`** com menos de 32 caracteres impede o boot. É proposital: a versão
antiga aceitava valor vazio, e aí `{"key": ""}` autorizava qualquer chamada
administrativa.
