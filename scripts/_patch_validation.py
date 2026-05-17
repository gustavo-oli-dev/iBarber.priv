"""
Patch script: apply medium-severity input validation fixes to app.py
Run once from the Barbearia directory: python scripts/_patch_validation.py
"""
import re, sys, os

APP = os.path.join(os.path.dirname(__file__), '..', 'app.py')

with open(APP, encoding='utf-8') as f:
    src = f.read()

original = src
changes = []

# ── FIX 1A: POST /api/lista-espera — date format validation ──────────────────
old1a = (
    "    data    = request.get_json(silent=True) or {}\n"
    "    data_dh = data.get('data')\n"
    "    if not data_dh:\n"
    "        return jsonify({'erro': 'data obrigatória'}), 400\n"
    "    tid = _api_tid()\n"
    "    if not tid:\n"
    "        return jsonify({'erro': 'tenant inválido'}), 400\n"
    "    existente = ListaEspera.query.filter_by("
)
new1a = (
    "    data    = request.get_json(silent=True) or {}\n"
    "    data_dh = data.get('data')\n"
    "    if not data_dh:\n"
    "        return jsonify({'erro': 'data obrigatória'}), 400\n"
    "    data_val = (data.get('data') or '').strip()\n"
    "    if not re.match(r'^\\d{4}-\\d{2}-\\d{2}$', data_val):\n"
    "        return jsonify({'erro': 'data inválida'}), 400\n"
    "    tid = _api_tid()\n"
    "    if not tid:\n"
    "        return jsonify({'erro': 'tenant inválido'}), 400\n"
    "    existente = ListaEspera.query.filter_by("
)
if old1a in src:
    src = src.replace(old1a, new1a, 1)
    changes.append("FIX 1A: POST /api/lista-espera date validation added")
else:
    print("WARNING: FIX 1A pattern not found")

# ── FIX 1B: GET+POST /api/escala/<data_str> — date format validation ─────────
old1b = (
    "@app.route('/api/escala/<data_str>', methods=['GET', 'POST'])\n"
    "def api_escala(data_str):\n"
    "    \"\"\"GET: lista funcionários e se trabalham na data. POST: salva ausências.\"\"\"\n"
    "    tid = verificar_token(request)\n"
    "    if not tid: return jsonify({'erro': 'token inválido'}), 401"
)
new1b = (
    "@app.route('/api/escala/<data_str>', methods=['GET', 'POST'])\n"
    "def api_escala(data_str):\n"
    "    \"\"\"GET: lista funcionários e se trabalham na data. POST: salva ausências.\"\"\"\n"
    "    if not re.match(r'^\\d{4}-\\d{2}-\\d{2}$', data_str):\n"
    "        return jsonify({'erro': 'data inválida'}), 400\n"
    "    tid = verificar_token(request)\n"
    "    if not tid: return jsonify({'erro': 'token inválido'}), 401"
)
if old1b in src:
    src = src.replace(old1b, new1b, 1)
    changes.append("FIX 1B: /api/escala/<data_str> date validation added")
else:
    print("WARNING: FIX 1B pattern not found")

# ── FIX 1C: POST /api/dias-fechados — date format validation ─────────────────
old1c = (
    "    if request.method in ('POST', 'DELETE'):\n"
    "        body = request.get_json(silent=True) or {}\n"
    "        data_val = body.get('data', '')\n"
    "        if request.method == 'POST' and data_val and data_val not in dias:"
)
new1c = (
    "    if request.method in ('POST', 'DELETE'):\n"
    "        body = request.get_json(silent=True) or {}\n"
    "        data_val = body.get('data', '')\n"
    "        if request.method == 'POST' and data_val:\n"
    "            if not re.match(r'^\\d{4}-\\d{2}-\\d{2}$', data_val):\n"
    "                return jsonify({'erro': 'data inválida'}), 400\n"
    "        if request.method == 'POST' and data_val and data_val not in dias:"
)
if old1c in src:
    src = src.replace(old1c, new1c, 1)
    changes.append("FIX 1C: POST /api/dias-fechados date validation added")
else:
    print("WARNING: FIX 1C pattern not found")

# ── FIX 2a: POST /api/servicos — duracao upper bound ─────────────────────────
old2a = "        duracao=max(5, int(d.get('duracao', 30))),"
new2a = "        duracao=max(5, min(int(d.get('duracao', 30)), 480)),"
if old2a in src:
    src = src.replace(old2a, new2a, 1)
    changes.append("FIX 2a: POST /api/servicos duracao capped at 480")
else:
    print("WARNING: FIX 2a pattern not found")

# ── FIX 2b: PUT /api/servicos/<sid> — duracao upper bound ────────────────────
old2b = "    if 'duracao'      in d: sv.duracao      = max(5, int(d['duracao']))"
new2b = "    if 'duracao'      in d: sv.duracao      = max(5, min(int(d['duracao']), 480))"
if old2b in src:
    src = src.replace(old2b, new2b, 1)
    changes.append("FIX 2b: PUT /api/servicos/<sid> duracao capped at 480")
else:
    print("WARNING: FIX 2b pattern not found")

# ── FIX 3: POST /gestao/entradas — forma whitelist ───────────────────────────
old3 = (
    "        desc = request.form.get('descricao', '').strip()\n"
    "        valor = _sf(request.form.get('valor', 0))\n"
    "        forma = request.form.get('forma', 'dinheiro')\n"
    "        if desc and valor > 0:"
)
new3 = (
    "        desc = request.form.get('descricao', '').strip()\n"
    "        valor = _sf(request.form.get('valor', 0))\n"
    "        formas_validas = {'dinheiro', 'cartao', 'pix'}\n"
    "        forma = request.form.get('forma', 'dinheiro')\n"
    "        if forma not in formas_validas:\n"
    "            forma = 'dinheiro'\n"
    "        if desc and valor > 0:"
)
if old3 in src:
    src = src.replace(old3, new3, 1)
    changes.append("FIX 3: POST /gestao/entradas forma whitelist added")
else:
    print("WARNING: FIX 3 pattern not found")

# ── FIX 4: POST /api/config-horarios — intervalo and dias bounds ─────────────
old4 = (
    "        intervalo = data.pop('intervalo_minutos', None)\n"
    "        if intervalo is not None:\n"
    "            _upsert_setting('intervalo_minutos', str(int(intervalo)), _tid)\n"
    "        dias_agenda = data.pop('dias_agenda', None)\n"
    "        if dias_agenda is not None:\n"
    "            _upsert_setting('dias_agenda', str(int(dias_agenda)), _tid)"
)
new4 = (
    "        intervalo = data.pop('intervalo_minutos', None)\n"
    "        if intervalo is not None:\n"
    "            intervalo = max(10, min(int(intervalo), 120))\n"
    "            _upsert_setting('intervalo_minutos', str(intervalo), _tid)\n"
    "        dias_agenda = data.pop('dias_agenda', None)\n"
    "        if dias_agenda is not None:\n"
    "            dias_agenda = max(1, min(int(dias_agenda), 60))\n"
    "            _upsert_setting('dias_agenda', str(dias_agenda), _tid)"
)
if old4 in src:
    src = src.replace(old4, new4, 1)
    changes.append("FIX 4: POST /api/config-horarios intervalo/dias bounds added")
else:
    print("WARNING: FIX 4 pattern not found")

# ── FIX 5A: POST /api/auth/criar-telefone — nome[:100], email validation ──────
old5a = (
    "    data      = request.get_json(force=True) or {}\n"
    "    nome      = data.get('nome', '').strip()\n"
    "    tel       = ''.join(c for c in data.get('telefone', '') if c.isdigit())\n"
    "    email_opt = data.get('email', '').strip().lower() or None\n"
    "    lembretes = bool(data.get('lembretes')) and bool(email_opt)\n"
    "    if not nome or len(tel) < 10:"
)
new5a = (
    "    data      = request.get_json(force=True) or {}\n"
    "    nome      = data.get('nome', '').strip()[:100]\n"
    "    tel       = ''.join(c for c in data.get('telefone', '') if c.isdigit())\n"
    "    email_opt = data.get('email', '').strip().lower() or None\n"
    "    if email_opt and not re.match(r'^[^@]+@[^@]+\\.[^@]+$', email_opt):\n"
    "        return jsonify({'erro': 'e-mail inválido'}), 400\n"
    "    lembretes = bool(data.get('lembretes')) and bool(email_opt)\n"
    "    if not nome or len(tel) < 10:"
)
if old5a in src:
    src = src.replace(old5a, new5a, 1)
    changes.append("FIX 5A: /api/auth/criar-telefone nome[:100] and email validation added")
else:
    print("WARNING: FIX 5A pattern not found")

# ── FIX 5B: POST /api/funcionarios — nome[:100] required, telefone[:20] ───────
old5b = (
    "    f = Funcionario(\n"
    "        nome=d.get('nome', '').strip(),\n"
    "        email=email_func,\n"
    "        password=generate_password_hash(senha),\n"
    "        telefone=d.get('telefone', '').strip() or None,"
)
new5b = (
    "    nome_func = d.get('nome', '').strip()[:100]\n"
    "    if not nome_func:\n"
    "        return jsonify({'erro': 'nome obrigatório'}), 400\n"
    "    tel_func = (d.get('telefone', '').strip() or '')[:20] or None\n"
    "    f = Funcionario(\n"
    "        nome=nome_func,\n"
    "        email=email_func,\n"
    "        password=generate_password_hash(senha),\n"
    "        telefone=tel_func,"
)
if old5b in src:
    src = src.replace(old5b, new5b, 1)
    changes.append("FIX 5B: POST /api/funcionarios nome[:100] required, telefone[:20]")
else:
    print("WARNING: FIX 5B pattern not found")

# ── FIX 5C: POST /api/entradas — descricao[:200], valor upper bound ───────────
old5c = (
    "    descricao = d.get('descricao', '').strip()\n"
    "    valor     = _sf(d.get('valor', 0))\n"
    "    forma     = d.get('forma', 'dinheiro').strip()\n"
    "    formas_validas = {'dinheiro', 'pix', 'cartao_credito', 'cartao_debito', 'cartao'}\n"
    "    if not descricao:\n"
    "        return jsonify({'erro': 'descrição obrigatória'}), 400\n"
    "    if valor <= 0:\n"
    "        return jsonify({'erro': 'valor deve ser maior que zero'}), 400"
)
new5c = (
    "    descricao = d.get('descricao', '').strip()[:200]\n"
    "    valor     = _sf(d.get('valor', 0))\n"
    "    forma     = d.get('forma', 'dinheiro').strip()\n"
    "    formas_validas = {'dinheiro', 'pix', 'cartao_credito', 'cartao_debito', 'cartao'}\n"
    "    if not descricao:\n"
    "        return jsonify({'erro': 'descrição obrigatória'}), 400\n"
    "    if valor <= 0:\n"
    "        return jsonify({'erro': 'valor deve ser maior que zero'}), 400\n"
    "    if valor > 99999:\n"
    "        return jsonify({'erro': 'valor inválido'}), 400"
)
if old5c in src:
    src = src.replace(old5c, new5c, 1)
    changes.append("FIX 5C: POST /api/entradas descricao[:200] and valor upper bound added")
else:
    print("WARNING: FIX 5C pattern not found")

# ── FIX 5D: POST /api/categorias — nome[:100], icone[:10] ─────────────────────
old5d = (
    "    d = request.get_json() or {}\n"
    "    nome = d.get('nome', '').strip()\n"
    "    if not nome: return jsonify({'erro': 'nome obrigatório'}), 400\n"
    "    cat = Categoria(\n"
    "        nome=nome, icone=d.get('icone', '✦'),"
)
new5d = (
    "    d = request.get_json() or {}\n"
    "    nome = d.get('nome', '').strip()[:100]\n"
    "    if not nome: return jsonify({'erro': 'nome obrigatório'}), 400\n"
    "    cat = Categoria(\n"
    "        nome=nome, icone=(d.get('icone', '✦') or '✦')[:10],"
)
if old5d in src:
    src = src.replace(old5d, new5d, 1)
    changes.append("FIX 5D: POST /api/categorias nome[:100] and icone[:10] added")
else:
    print("WARNING: FIX 5D pattern not found")

if src == original:
    print("No changes applied — all patterns may already be present or none matched.")
    sys.exit(1)

with open(APP, 'w', encoding='utf-8') as f:
    f.write(src)

print(f"\nSUCCESS: {len(changes)} fix(es) applied:")
for c in changes:
    print(f"  - {c}")
