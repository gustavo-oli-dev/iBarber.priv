"""
Substitui alert() e confirm() por sistema customizado em todos os templates.
- alert(X)   -> _notify(X, tipo)
- confirm(X) -> await _confirmAsync(X)  (torna a função async se necessário)
"""
import re, glob, os

def tipo_alert(inner):
    lo = inner.lower()
    if any(x in lo for x in ['erro','error','falha','inválid','obrigat','selecione','preencha','informe','já adiciona']):
        return "'err'"
    if any(x in lo for x in ['sucesso','salvo','criado','removido','enviado','atualizado','ok']):
        return "'ok'"
    return "''"

def fix_alerts(src):
    def repl(m):
        inner = m.group(1)
        return f'_notify({inner},{tipo_alert(inner)})'
    return re.sub(r'\balert\(([^)]+)\)', repl, src)

def fix_confirms(src):
    """
    Troca confirm(X) por await _confirmAsync(X) e torna a função async.
    """
    if 'confirm(' not in src:
        return src

    # Substitui confirm(X) -> await _confirmAsync(X)
    src = re.sub(r'\bconfirm\(([^)]+)\)', r'await _confirmAsync(\1)', src)

    # Torna async as funções que agora têm await _confirmAsync
    # Padrão: function nome(...) {  ou  async function nome(...) {
    def make_async(m):
        if 'async' in m.group(0):
            return m.group(0)
        return 'async ' + m.group(0)

    # Verifica quais funções contêm await _confirmAsync e as torna async
    lines = src.split('\n')
    # Encontra índices de linhas com await _confirmAsync
    confirm_lines = {i for i, l in enumerate(lines) if 'await _confirmAsync' in l}
    if not confirm_lines:
        return src

    # Para cada função que contém uma dessas linhas, torna async
    result = []
    func_stack = []  # (start_line, brace_depth)
    depth = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        # Detecta início de função
        fn_match = re.match(r'^(\s*)((?:async\s+)?function\s+\w+|(?:async\s+)?\w+\s*=\s*function|\w+\s*:\s*(?:async\s+)?function)\s*\(', line)
        if fn_match:
            func_stack.append((i, depth))

        open_b = line.count('{')
        close_b = line.count('}')
        depth += open_b - close_b

        result.append(line)
        i += 1

    # Abordagem simples: adicionar async a function declarations que contêm await _confirmAsync
    # Encontra blocos de função e verifica se têm await _confirmAsync dentro
    src2 = '\n'.join(result)

    # Regex para encontrar funções sem async que contêm await _confirmAsync no corpo
    def add_async_to_fn(m):
        full = m.group(0)
        if 'await _confirmAsync' in full and not full.startswith('async'):
            return 'async ' + full
        return full

    # Match de function declarations
    src2 = re.sub(
        r'(?<!\w)function\s+(\w+)\s*\([^)]*\)\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',
        add_async_to_fn,
        src2,
        flags=re.DOTALL
    )

    return src2

templates = glob.glob('templates/**/*.html', recursive=True)
changed = []

for f in sorted(templates):
    src = open(f, encoding='utf-8').read()
    new = fix_alerts(src)
    new = fix_confirms(new)
    if new != src:
        open(f, 'w', encoding='utf-8').write(new)
        a = src.count('alert(') - new.count('alert(')
        c = src.count('confirm(') - new.count('confirm(')
        changed.append((f, a, c))
        print(f"  {f}: -{a} alert, -{c} confirm")

print(f"\nTotal: {len(changed)} arquivos alterados")
