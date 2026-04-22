"""
Auditoria automatica do backend (app.py)
Verifica: imports inuteis, rotas mortas, imports nao usados, debug mode
"""
import ast, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ARQUIVO = '../app.py'

with open(ARQUIVO, encoding='utf-8') as f:
    source = f.read()
    lines  = source.splitlines()

try:
    tree = ast.parse(source)
except SyntaxError as e:
    print(f"[ERRO CRITICO] Syntax error em app.py: {e}")
    sys.exit(1)

erros   = []
avisos  = []
ok      = []

# 1. Imports não utilizados
imports_usados = set()
imports_nomes  = {}

for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            nome = alias.asname or alias.name.split('.')[0]
            imports_nomes[nome] = node.lineno
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            nome = alias.asname or alias.name
            imports_nomes[nome] = node.lineno

for node in ast.walk(tree):
    if isinstance(node, ast.Name):
        imports_usados.add(node.id)
    elif isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name):
            imports_usados.add(node.value.id)

inuteis = {n: l for n, l in imports_nomes.items() if n not in imports_usados}
for nome, linha in sorted(inuteis.items(), key=lambda x: x[1]):
    avisos.append(f"[IMPORT INUTEL] linha {linha}: '{nome}' importado mas nunca usado")

if not inuteis:
    ok.append("[OK] Todos os imports sao utilizados")

# 2. Debug mode ativo
for i, line in enumerate(lines, 1):
    if 'debug=True' in line and 'app.run' in lines[i-2:i+1][-1] if i > 1 else False:
        erros.append(f"[ERRO] linha {i}: debug=True ativo - risco em producao")
    if re.search(r'app\.run.*debug\s*=\s*True', line):
        erros.append(f"[ERRO] linha {i}: debug=True ativo - desativar em producao")

# 3. Senha hardcoded
for i, line in enumerate(lines, 1):
    if re.search(r"(password|senha|token|secret)\s*=\s*['\"][^'\"]{6,}['\"]", line, re.I):
        if 'os.environ' not in line and 'hash' not in line.lower() and 'check' not in line.lower():
            avisos.append(f"[SEGURANCA] linha {i}: possivel credencial hardcoded: {line.strip()[:60]}")

# 4. Rotas definidas vs chamadas no frontend
rotas_flask = re.findall(r"@app\.route\(['\"]([^'\"]+)['\"]", source)
for r in rotas_flask:
    ok.append(f"[ROTA] {r}")

# 5. Funcoes definidas mas não chamadas
funcoes_def   = {n.name: n.lineno for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
funcoes_calls = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            funcoes_calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            funcoes_calls.add(node.func.attr)

privadas_mortas = {
    n: l for n, l in funcoes_def.items()
    if n.startswith('_') and n not in funcoes_calls
    and not n.startswith('__')
}
for nome, linha in sorted(privadas_mortas.items(), key=lambda x: x[1]):
    avisos.append(f"[FUNCAO MORTA] linha {linha}: '{nome}' definida mas aparentemente nao chamada")

# OUTPUT
print("=" * 60)
print("AUDIT BACKEND - app.py")
print("=" * 60)
if erros:
    print(f"\n🔴 ERROS ({len(erros)}):")
    for e in erros:
        print(f"  {e}")
if avisos:
    print(f"\n🟡 AVISOS ({len(avisos)}):")
    for a in avisos:
        print(f"  {a}")
print(f"\n✅ OK ({len(ok)} rotas/checks passaram)")
print("=" * 60)
