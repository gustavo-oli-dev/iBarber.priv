"""
One-shot security patch for app.py:
- Adds auth checks to criar_pagamento and verificar_pagamento
- Adds @limiter.limit decorators to 9 high-risk routes
"""
import re, sys, os

APP = os.path.join(os.path.dirname(__file__), '..', 'app.py')

with open(APP, encoding='utf-8') as f:
    src = f.read()

original = src

# ── FIX 1 & rate-limit: POST /api/criar-pagamento ─────────────────────────
old1 = "@app.route('/api/criar-pagamento', methods=['POST'])\ndef criar_pagamento():"
new1 = ("@app.route('/api/criar-pagamento', methods=['POST'])\n"
        "@limiter.limit('10 per minute')\n"
        "def criar_pagamento():\n"
        "    if 'user_id' not in session:\n"
        "        return jsonify({'erro': 'não autenticado'}), 401")
if old1 not in src:
    print("ERROR: FIX 1 pattern not found"); sys.exit(1)
src = src.replace(old1, new1, 1)

# ── FIX 2 & rate-limit: GET /api/verificar-pagamento/<int:mp_payment_id> ──
old2 = "@app.route('/api/verificar-pagamento/<int:mp_payment_id>', methods=['GET'])\ndef verificar_pagamento(mp_payment_id):"
new2 = ("@app.route('/api/verificar-pagamento/<int:mp_payment_id>', methods=['GET'])\n"
        "@limiter.limit('30 per minute')\n"
        "def verificar_pagamento(mp_payment_id):\n"
        "    if 'user_id' not in session:\n"
        "        return jsonify({'erro': 'não autenticado'}), 401")
if old2 not in src:
    print("ERROR: FIX 2 pattern not found"); sys.exit(1)
src = src.replace(old2, new2, 1)

# ── FIX 3: rate-limit remaining routes ────────────────────────────────────

# POST /api/pagamento/criar
old3 = "@app.route('/api/pagamento/criar', methods=['POST'])\ndef api_pagamento_criar_v2():"
new3 = ("@app.route('/api/pagamento/criar', methods=['POST'])\n"
        "@limiter.limit('5 per minute')\n"
        "def api_pagamento_criar_v2():")
if old3 not in src:
    print("ERROR: FIX 3a pattern not found"); sys.exit(1)
src = src.replace(old3, new3, 1)

# POST /api/pagamento/cartao
old4 = "@app.route('/api/pagamento/cartao', methods=['POST'])\ndef api_pagamento_cartao():"
new4 = ("@app.route('/api/pagamento/cartao', methods=['POST'])\n"
        "@limiter.limit('5 per minute')\n"
        "def api_pagamento_cartao():")
if old4 not in src:
    print("ERROR: FIX 3b pattern not found"); sys.exit(1)
src = src.replace(old4, new4, 1)

# POST /api/cadastro-personalizar
old5 = "@app.route('/api/cadastro-personalizar', methods=['POST'])\ndef api_cadastro_personalizar():"
new5 = ("@app.route('/api/cadastro-personalizar', methods=['POST'])\n"
        "@limiter.limit('5 per hour')\n"
        "def api_cadastro_personalizar():")
if old5 not in src:
    print("ERROR: FIX 3c pattern not found"); sys.exit(1)
src = src.replace(old5, new5, 1)

# POST /confirmar-pedido
old6 = "@app.route('/confirmar-pedido', methods=['POST'])\ndef confirmar_pedido():"
new6 = ("@app.route('/confirmar-pedido', methods=['POST'])\n"
        "@limiter.limit('20 per minute')\n"
        "def confirmar_pedido():")
if old6 not in src:
    print("ERROR: FIX 3d pattern not found"); sys.exit(1)
src = src.replace(old6, new6, 1)

# POST /reagendar-agendamento
old7 = "@app.route('/reagendar-agendamento', methods=['POST'])\ndef reagendar_agendamento():"
new7 = ("@app.route('/reagendar-agendamento', methods=['POST'])\n"
        "@limiter.limit('10 per minute')\n"
        "def reagendar_agendamento():")
if old7 not in src:
    print("ERROR: FIX 3e pattern not found"); sys.exit(1)
src = src.replace(old7, new7, 1)

# GET /verificar-slug/<slug>
old8 = "@app.route('/verificar-slug/<slug>')\ndef verificar_slug(slug):"
new8 = ("@app.route('/verificar-slug/<slug>')\n"
        "@limiter.limit('30 per minute')\n"
        "def verificar_slug(slug):")
if old8 not in src:
    print("ERROR: FIX 3f pattern not found"); sys.exit(1)
src = src.replace(old8, new8, 1)

# GET /api/horarios-disponiveis
old9 = "@app.route('/api/horarios-disponiveis')\ndef api_horarios_disponiveis():"
new9 = ("@app.route('/api/horarios-disponiveis')\n"
        "@limiter.limit('60 per minute')\n"
        "def api_horarios_disponiveis():")
if old9 not in src:
    print("ERROR: FIX 3g pattern not found"); sys.exit(1)
src = src.replace(old9, new9, 1)

if src == original:
    print("No changes made – all patterns already applied or none matched.")
    sys.exit(0)

with open(APP, 'w', encoding='utf-8') as f:
    f.write(src)

print("SUCCESS: all 9 fixes applied to app.py")
