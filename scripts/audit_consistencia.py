"""
Auditoria de consistencia entre Flutter (main.dart) e Flask (app.py)
Verifica: rotas do Flutter que nao existem no Flask, campos de API inconsistentes
"""
import re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

with open('../app.py', encoding='utf-8') as f:
    backend = f.read()
with open('../barbearia_admin/lib/main.dart', encoding='utf-8') as f:
    flutter = f.read()

rotas_flask = set(re.findall(r"@app\.route\(['\"]([^'\"]+)['\"]", backend))

# Rotas usadas no Flutter
flutter_urls = re.findall(r"Uri\.parse\(['\"]?\$BASE_URL([^'\")\s]+)['\"]?\)", flutter)

erros  = []
avisos = []

print("=" * 60)
print("AUDIT CONSISTENCIA - Flutter <-> Flask")
print("=" * 60)

print(f"\n📱 Rotas chamadas pelo Flutter ({len(flutter_urls)}):")
for url in sorted(set(flutter_urls)):
    url_norm = re.sub(r'\$\w+', '<id>', url).split('?')[0]
    match = any(
        re.fullmatch(
            re.sub(r'<[^>]+>', r'[^/]+', r.replace('<int:', '<').replace('<string:', '<')),
            url_norm
        ) or r == url_norm or r == url
        for r in rotas_flask
    )
    status = "✅" if match else "🔴"
    if not match:
        erros.append(f"Rota Flutter '{url}' NAO encontrada no Flask")
    print(f"  {status} {url}")

# Checa token
flutter_token = re.search(r"API_TOKEN\s*=\s*['\"]([^'\"]+)['\"]", flutter)
backend_token = re.search(r"API_TOKEN\s*=\s*os\.environ\.get\(['\"][^'\"]+['\"],\s*['\"]([^'\"]+)['\"]", backend)

print(f"\n🔑 Token de autenticacao:")
if flutter_token and backend_token:
    ft = flutter_token.group(1)
    bt = backend_token.group(1)
    if ft == bt:
        print(f"  ✅ Tokens iguais: '{ft}'")
    else:
        print(f"  🔴 INCONSISTENTE! Flutter='{ft}' | Flask='{bt}'")
        erros.append(f"Tokens diferentes: Flutter='{ft}' vs Flask='{bt}'")

# Checa BASE_URL
base_url = re.search(r"BASE_URL\s*=\s*['\"]([^'\"]+)['\"]", flutter)
if base_url:
    url = base_url.group(1)
    if 'https' not in url:
        avisos.append(f"BASE_URL usa HTTP (nao HTTPS): {url}")
    if '127.0.0.1' in url or 'localhost' in url:
        avisos.append(f"BASE_URL apontando para localhost - nao funcionara em producao: {url}")
    print(f"\n🌐 BASE_URL: {url}")

if erros:
    print(f"\n🔴 ERROS ({len(erros)}):")
    for e in erros:
        print(f"  {e}")
else:
    print("\n✅ Nenhum erro de consistencia encontrado")

if avisos:
    print(f"\n🟡 AVISOS ({len(avisos)}):")
    for a in avisos:
        print(f"  {a}")

print("=" * 60)
