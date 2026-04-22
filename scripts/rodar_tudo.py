"""
Roda todas as auditorias e gera relatorio final
"""
import subprocess, sys, io, os, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

scripts_dir = os.path.dirname(os.path.abspath(__file__))
audits = [
    ('BACKEND',      'audit_backend.py'),
    ('FRONTEND',     'audit_frontend.py'),
    ('CONSISTENCIA', 'audit_consistencia.py'),
    ('DEPENDENCIAS', 'audit_deps.py'),
]

agora = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
print(f"\n{'='*60}")
print(f"  RELATORIO COMPLETO DE AUDITORIA — {agora}")
print(f"{'='*60}\n")

total_erros = 0
for nome, script in audits:
    resultado = subprocess.run(
        [sys.executable, os.path.join(scripts_dir, script)],
        capture_output=True, text=True, encoding='utf-8',
        cwd=scripts_dir
    )
    saida = resultado.stdout + resultado.stderr
    erros_encontrados = saida.count('🔴')
    total_erros += erros_encontrados
    print(saida)

print(f"\n{'='*60}")
print(f"  TOTAL DE ERROS ENCONTRADOS: {total_erros}")
print(f"{'='*60}\n")
