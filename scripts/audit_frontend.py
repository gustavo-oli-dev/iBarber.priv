"""
Auditoria automatica do frontend (templates + static)
Verifica: rotas inexistentes, variaveis Jinja2 invalidas, JS errors
"""
import re, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE = '..'

def ler(path):
    with open(os.path.join(BASE, path), encoding='utf-8') as f:
        return f.read(), f.name

erros  = []
avisos = []

# Carrega rotas do backend
with open(os.path.join(BASE, 'app.py'), encoding='utf-8') as f:
    backend = f.read()

rotas_flask = set(re.findall(r"@app\.route\(['\"]([^'\"]+)['\"]", backend))
funcoes_flask = set(re.findall(r"^def (\w+)\(", backend, re.M))

# Endpoints usados pelo JS nos templates
templates = [
    'templates/servicos.html',
    'templates/index.html',
    'templates/perfil.html',
    'templates/login.html',
    'templates/register.html',
    'templates/esqueci_senha.html',
    'templates/redefinir_senha.html',
    'templates/base.html',
]

fetch_urls = set()
url_for_calls = set()

for tpl in templates:
    try:
        src, nome = ler(tpl)
    except FileNotFoundError:
        avisos.append(f"[TEMPLATE] {tpl} nao encontrado")
        continue

    # Fetch URLs no JS
    for m in re.finditer(r"fetch\(['\"]([^'\"]+)['\"]", src):
        fetch_urls.add((m.group(1), tpl))

    # url_for no Jinja2
    for m in re.finditer(r"url_for\(['\"](\w+)['\"]", src):
        if m.group(1) != 'static':  # 'static' e endpoint interno do Flask
            url_for_calls.add((m.group(1), tpl))

    # Variaveis Jinja2 suspeitas
    for m in re.finditer(r"\{\{\s*(\w+(?:\.\w+)*)\s*\}\}", src):
        var = m.group(1)
        if 'user.plain_password' in var or var == 'user.plain_password':
            erros.append(f"[JINJA2] {tpl}: variavel '{var}' nao existe no modelo User")

# Checa fetch URLs contra rotas Flask
print("=" * 60)
print("AUDIT FRONTEND - templates + static")
print("=" * 60)

print(f"\n📡 FETCH URLs encontradas no JS:")
for url, origem in sorted(fetch_urls):
    # Normaliza rota com parametros
    rota_norm = re.sub(r'/\d+', '/<id>', url).split('?')[0]
    match = any(
        re.fullmatch(re.sub(r'<[^>]+>', r'[^/]+', r.replace('<int:', '<').replace('<string:', '<')), rota_norm)
        or r == rota_norm
        for r in rotas_flask
    )
    status = "✅" if match else "❓"
    print(f"  {status} {url}  ← {os.path.basename(origem)}")

print(f"\n🔗 url_for chamados nos templates:")
for fn, origem in sorted(url_for_calls):
    existe = fn in funcoes_flask
    status = "✅" if existe else "🔴"
    if not existe:
        erros.append(f"[URL_FOR] {origem}: url_for('{fn}') mas funcao '{fn}' nao existe no backend")
    print(f"  {status} url_for('{fn}')  ← {os.path.basename(origem)}")

if erros:
    print(f"\n🔴 ERROS ({len(erros)}):")
    for e in erros:
        print(f"  {e}")
else:
    print("\n✅ Nenhum erro critico encontrado no frontend")

if avisos:
    print(f"\n🟡 AVISOS ({len(avisos)}):")
    for a in avisos:
        print(f"  {a}")

print("=" * 60)
