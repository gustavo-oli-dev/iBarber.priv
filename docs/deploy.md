# Deploy

Produção roda numa VPS Linux com nginx, gunicorn e MySQL.

## Topologia

```
Cloudflare DNS  →  nginx (443, TLS via certbot)  →  gunicorn (127.0.0.1:8000)  →  app.py
                                                     + 1 processo com RUN_SCHEDULER=1
                                                     MySQL
```

O nginx encerra o TLS e encaminha para o gunicorn. Cada barbearia tem um vhost
próprio em `/etc/nginx/sites-enabled/ibarber`, criado automaticamente no
cadastro por `_provisionar_ssl_tenant()`.

## Requisitos da máquina

- Python 3.9+ com `hashlib.scrypt` (OpenSSL 1.1.1+)
- nginx e certbot com o plugin nginx
- MySQL
- Redis (opcional, mas necessário para o rate limit funcionar de verdade)
- **root** — o provisionamento de subdomínio escreve em `/etc/nginx/` e chama
  `systemctl reload nginx`

## Processos

Dois processos distintos, com a mesma base de código:

```bash
# API + site
gunicorn -w 2 -b 127.0.0.1:8000 app:app

# Jobs periódicos — UM processo só
RUN_SCHEDULER=1 python app.py
```

`RUN_SCHEDULER` precisa ficar em `0` nos workers do gunicorn. Com `1` em todos,
cada job roda uma vez por worker: lembretes duplicados para o cliente e limpezas
concorrentes.

## Atualizar

```bash
cd /var/www/ibarber
git pull
venv/bin/pip install -r requirements.txt
systemctl restart ibarber ibarber-scheduler
```

O schema é atualizado no import do `app.py`, a partir da allowlist de colunas.
Colunas fora dessa lista precisam de `ALTER TABLE` manual.

> Vários workers importam `app.py` ao mesmo tempo e executam o DDL em paralelo.
> Em MySQL isso costuma passar, mas não é seguro por construção — reiniciar com
> um worker só durante uma mudança de schema evita a corrida.

## Novo tenant

1. `POST /api/cadastro-personalizar` cria a barbearia com
   `assinatura_ativa = False`
2. O pagamento é confirmado pelo webhook do Mercado Pago
3. `_provisionar_ssl_tenant()` roda em background: cria o registro A na
   Cloudflare, aguarda o DNS propagar (até 10 min), emite o certificado com
   certbot e adiciona o vhost
4. `assinatura_ativa = True` libera o site publicamente

Sem `CF_TOKEN` e `CF_ZONE_ID`, o passo 3 não faz nada e o subdomínio precisa ser
criado manualmente.

## Backup

O que precisa de backup:

- **Banco MySQL** — tudo que importa
- **`static/uploads/`** — fotos de funcionários, produtos, serviços e logos.
  Não estão no Git e não são recuperáveis.
- **`.env`** — perder o `SECRET_KEY` torna ilegíveis as credenciais de
  pagamento de todas as barbearias

## Plataformas de container

A aplicação **não** roda em Render, Fly ou Heroku sem alterações:

| Bloqueio | Motivo |
|---|---|
| `_provisionar_ssl_tenant()` | Chama `certbot`, escreve em `/etc/nginx/` e roda `systemctl` — precisa de root e de nginx local |
| `static/uploads/` | Filesystem efêmero: toda imagem some no deploy seguinte |
| Driver do banco | `requirements.txt` traz PyMySQL; Postgres exigiria `psycopg` |
| DDL de boot | Vários processos executam `CREATE`/`ALTER TABLE` em paralelo, sem lock |
| SQL específico de MySQL | `MODIFY COLUMN`, `` `user` `` com crase, `DATETIME`, `db.func.date()` |

Migrar exige: mover o TLS para domínio wildcard da plataforma, trocar uploads
por object storage ou disco persistente, tornar o DDL agnóstico de dialeto e
serializar a migração de boot.

## Checklist de produção

- [ ] `FLASK_DEBUG=0`
- [ ] `API_TOKEN` com 32+ caracteres, aleatório
- [ ] `MP_WEBHOOK_SECRET` configurado — sem ele nenhum pagamento é confirmado
- [ ] `REDIS_URL` apontando para o Redis
- [ ] `RUN_SCHEDULER=1` em exatamente um processo
- [ ] `ProxyFix` ativo e nginx enviando `X-Forwarded-For`
- [ ] Backup do banco e de `static/uploads/` agendado
- [ ] `.env` fora do Git, com permissão restrita
