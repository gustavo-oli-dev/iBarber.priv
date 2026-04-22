"""
Auditoria de dependencias
Verifica: requirements.txt vs imports reais do app.py
"""
import re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

with open('../requirements.txt', encoding='utf-8') as f:
    reqs = [l.strip().split('==')[0].split('[')[0].lower() for l in f if l.strip() and not l.startswith('#')]

with open('../app.py', encoding='utf-8') as f:
    source = f.read()

# Mapeia pacote pip -> nome de import
mapa = {
    'flask': 'flask',
    'flask-sqlalchemy': 'flask_sqlalchemy',
    'werkzeug': 'werkzeug',
    'qrcode': 'qrcode',
    'pillow': 'PIL',
    'requests': 'requests',
    'apscheduler': 'apscheduler',
    'python-dotenv': 'dotenv',
}

imports_no_codigo = set(re.findall(r'^(?:import|from)\s+(\w+)', source, re.M))

print("=" * 60)
print("AUDIT DEPENDENCIAS - requirements.txt vs app.py")
print("=" * 60)
print()

for req in reqs:
    nome_import = mapa.get(req, req.replace('-', '_'))
    usado = nome_import in imports_no_codigo or req.replace('-','_') in imports_no_codigo
    status = "✅" if usado else "🔴 INUTEL - pode remover"
    print(f"  {status}  {req}")

print()
print("=" * 60)
