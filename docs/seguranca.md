# Segurança

Modelo de autenticação, autorização e as invariantes que não podem ser
quebradas ao mexer no código.

## Os três universos de acesso

| Quem | Entra por | Guarda na sessão | Escopo |
|---|---|---|---|
| Cliente | `/api/auth/telefone`, Google OAuth | `user_id` | os próprios agendamentos |
| Gestor / funcionário | `/gestao/login` ou `/api/admin/login` | `gestao_tenant_id`, `gestao_func_id` | uma barbearia |
| Admin do SaaS | `API_TOKEN` em `/admin/painel` | `admin_ok` | todas as barbearias |

São independentes: estar logado como cliente não dá nenhum acesso à gestão.

## Token da API

```
base64( tenant_id : func_id : token_version : emissao : hmac_sha256 )
```

Assinado com `SECRET_KEY`. Validade em `TOKEN_TTL_HORAS` (padrão 12h).

- `func_id = 0` → dono da barbearia
- `func_id = N` → funcionário; precisa estar **ativo** e pertencer ao tenant

Invalidação imediata acontece em três situações: troca de senha do dono
(incrementa `token_version`), `POST /api/rotar-token`, e desativação do
funcionário (checada a cada request).

> **Nunca emita um token com `func_id = 0` para um funcionário.** Use sempre
> `_gestao_token()`, que lê o `gestao_func_id` da sessão. Esse foi um caminho
> real de escalação: as páginas de gestão entregavam token de dono para
> qualquer funcionário, que então lia as credenciais do Mercado Pago.

## Permissões

Nove flags no modelo `Funcionario`: `agendamentos`, `calendario`, `marcar`,
`clientes`, `servicos`, `precos`, `pedidos`, `entradas`, `fotos`.

A verificação é sempre no servidor, a cada request:

```python
tid = verificar_token_perm(request, 'entradas')
if not tid:
    return jsonify({'erro': 'não autorizado'}), 403
```

Escolha da guarda:

| Guarda | Uso |
|---|---|
| `verificar_token_admin` | credenciais, funcionários, horários, config, export |
| `verificar_token_perm` | recurso mapeado numa permissão |
| `verificar_token` | leitura genérica de funcionário ativo |
| `admin_key_required` | `/api/admin/*` — administração do SaaS |

O dono passa em qualquer `verificar_token_perm`.

## CSRF

`_csrf_protect()` roda como `before_request` e cobre todo método que altera
estado. Isentos:

- `GET`, `HEAD`, `OPTIONS`, `TRACE`
- `/api/pagamento/webhook` — autenticado pelo HMAC do Mercado Pago
- `/auth/google/callback`
- Requisições com `Authorization: Bearer` — não usam cookie
- Requisições com a chave mestra correta — não dependem de cookie

No cliente, `static/js/csrf.js` injeta o token em `fetch`, `XMLHttpRequest` e
formulários. Ele precisa carregar **antes** de qualquer script que faça
requisição. Páginas fora de `base.html` / `base_gestao.html` devem incluir:

```html
<meta name="csrf-token" content="{{ csrf_token }}">
<script src="{{ url_for('static', filename='js/csrf.js') }}?v=1"></script>
```

Formulários submetidos por JavaScript (`form.submit()`) **não** disparam o
evento `submit`, então o `csrf.js` não alcança: nesses casos o campo oculto
`_csrf` precisa estar no HTML.

## XSS

O vetor real não é o Jinja2 (que escapa por padrão) — é o JavaScript do painel
montando HTML com dados do banco.

Nome de cliente e nome de serviço são preenchidos por **visitantes anônimos**,
e aparecem no painel do dono, onde vive o token de admin. Duas camadas:

1. **Entrada** — `_nome_seguro()` recusa nome que não seja letras, espaços,
   apóstrofo e hífen. `/confirmar-pedido` só aceita serviço que exista no
   catálogo do tenant, e grava nome e preço vindos do banco.
2. **Saída** — `esc()` em todo `innerHTML`, `escJs()` em atributo `onclick`.

```javascript
elemento.innerHTML = `<td>${esc(cliente.nome)}</td>`;
botao.innerHTML = `<button onclick="acao(${escJs(cliente.nome)})">`;
```

> `_safe_json()` protege só o embedding em `<script>` (evita fechar a tag). Ele
> **não** protege `innerHTML`: o JavaScript decodifica `<` de volta para
> `<` ao fazer o parse.

## Pagamentos

- O webhook exige assinatura HMAC (`MP_WEBHOOK_SECRET`); sem o segredo
  configurado, **toda** notificação é rejeitada
- Idempotência por `mp_payment_id`
- A assinatura é casada por `tenant_id` + `status='pendente'` + `plano`
- Confirmar pagamento exige que o pedido seja do usuário logado, não só do
  mesmo tenant
- Preço vem sempre do catálogo; valor enviado pelo cliente é ignorado
- Credenciais do Mercado Pago são cifradas com Fernet derivado de `SECRET_KEY`

> Trocar `SECRET_KEY` torna as credenciais salvas ilegíveis. Cada barbearia
> precisa reconfigurar o Access Token.

## Rate limiting

`ProxyFix(x_for=1)` é obrigatório atrás do nginx — sem ele `remote_addr` é
sempre `127.0.0.1`, todos dividem o mesmo balde e um atacante sozinho derruba o
login de todo mundo.

`x_for=1` significa exatamente um proxy confiável. Aumentar esse número permite
ao cliente forjar o IP pelo header `X-Forwarded-For`.

Sem `REDIS_URL`, o contador é por worker: o limite real vira N vezes o
declarado e zera a cada restart.

## Uploads

`_processar_imagem()` valida por *magic bytes*, remove EXIF, redimensiona para
1400px e reencoda. O limite de 10 MB é medido pelos **bytes lidos**, não pelo
`Content-Length` — esse header é controlado pelo cliente e some em
`Transfer-Encoding: chunked`.

`Image.MAX_IMAGE_PIXELS = 40_000_000` limita a expansão na descompressão.

## Checklist ao adicionar um endpoint

- [ ] Guarda de autorização na primeira linha
- [ ] Toda consulta filtrada por `tenant_id`
- [ ] Objeto de outro tenant devolve 404, nunca 403 com detalhe
- [ ] Entrada validada: tipo, faixa e tamanho
- [ ] `int()` de valor externo dentro de `try`, com faixa checada
- [ ] Dado do banco escapado com `esc()` no JavaScript
- [ ] `db.session.rollback()` no tratamento de erro
- [ ] Rate limit se o endpoint for público ou caro

## Histórico

A auditoria de agosto/2026 corrigiu 18 achados, entre eles: XSS armazenado
levando a takeover do painel, escalação de privilégio de funcionário para dono,
DoS não autenticado por laço infinito, manipulação de preço, CSRF que falhava
aberto e ausência de expiração de token.

Pendente: `/api/auth/telefone` autentica **só** com o número de telefone, sem
verificação. Quem souber o telefone de um cliente entra como ele. A correção é
um OTP por SMS/WhatsApp e depende de contratar um provedor.
