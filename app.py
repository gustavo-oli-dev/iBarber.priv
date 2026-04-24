from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

import os, json, uuid, secrets, smtplib, requests as req_http
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

if not os.environ.get('SECRET_KEY'):
    raise RuntimeError('SECRET_KEY não definida no ambiente')

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
app.jinja_env.cache = {}
origens = os.environ.get('CORS_ORIGINS', 'http://localhost:5000,http://localhost:8888,http://localhost:9999,http://localhost:7777').split(',')
CORS(app, origins=origens, supports_credentials=True)
app.secret_key = os.environ.get('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///barbearia.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

MAIL_HOST     = 'smtp.gmail.com'
MAIL_PORT     = 587
MAIL_USER     = os.environ.get('MAIL_USER', 'ibarbeariaaa@gmail.com')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
MAIL_FROM     = 'Barbearia <ibarbeariaaa@gmail.com>'
API_TOKEN     = os.environ.get('API_TOKEN', '')
ADMIN_EMAIL   = os.environ.get('ADMIN_EMAIL', '')

db = SQLAlchemy(app)

# Dados demo usados quando o banco não existe / está vazio (preview independente de DB)
_PREVIEW_CATS = [
    {'id': 1, 'nome': 'Corte',       'icone': '✂'},
    {'id': 2, 'nome': 'Barba',       'icone': '🪒'},
    {'id': 3, 'nome': 'Combo',       'icone': '✦'},
    {'id': 4, 'nome': 'Hidratação',  'icone': '💧'},
]
_PREVIEW_SVCS = [
    {'id': 1, 'nome': 'Corte Simples',       'categoria_id': 1, 'categoria_nome': 'Corte',  'preco': 35.0},
    {'id': 2, 'nome': 'Corte + Degradê',     'categoria_id': 1, 'categoria_nome': 'Corte',  'preco': 45.0},
    {'id': 3, 'nome': 'Barba Completa',      'categoria_id': 2, 'categoria_nome': 'Barba',  'preco': 30.0},
    {'id': 4, 'nome': 'Combo Cabelo+Barba',  'categoria_id': 3, 'categoria_nome': 'Combo',  'preco': 70.0},
]

class _MockTenant:
    """Tenant substituto usado exclusivamente em rotas de preview quando não há banco."""
    id = None; slug = 'preview'; nome = 'Barbearia'; ativo = True
    whatsapp = ''; maps_url = ''; tema = None; fab_wpp = None; fab_maps = None

PLANOS = {
    'mensal':     {'meses': 1,  'mensal': 49.00, 'total':  49.00},
    'trimestral': {'meses': 3,  'mensal': 44.00, 'total': 132.00},
    'semestral':  {'meses': 6,  'mensal': 39.00, 'total': 234.00},
    'anual':      {'meses': 12, 'mensal': 34.00, 'total': 408.00},
}

class Tenant(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    slug             = db.Column(db.String(50),  unique=True, nullable=False)
    nome             = db.Column(db.String(100), nullable=False)
    email            = db.Column(db.String(120), unique=True, nullable=False)
    password         = db.Column(db.String(200), nullable=False)
    contato          = db.Column(db.String(20),  nullable=True)
    ativo            = db.Column(db.Boolean, default=True)
    criado_em        = db.Column(db.DateTime, default=datetime.utcnow)
    tema             = db.Column(db.Text, nullable=True)
    tema_editacoes   = db.Column(db.Integer, default=0)
    tema_pendente    = db.Column(db.Text,    nullable=True)
    assinatura_ativa = db.Column(db.Boolean, default=False)
    whatsapp         = db.Column(db.String(20),  nullable=True)
    maps_url         = db.Column(db.String(500), nullable=True)
    fab_wpp          = db.Column(db.Text, nullable=True)   # JSON
    fab_maps         = db.Column(db.Text, nullable=True)   # JSON
    assinaturas      = db.relationship('Assinatura', backref='tenant', lazy=True, order_by='Assinatura.id.desc()')

class Assinatura(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    tenant_id        = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False)
    plano            = db.Column(db.String(20), nullable=False)
    valor_total      = db.Column(db.Float, nullable=False)
    valor_mensal     = db.Column(db.Float, nullable=False)
    status           = db.Column(db.String(20), default='pendente')
    mp_payment_id    = db.Column(db.String(100), nullable=True)
    mp_preference_id = db.Column(db.String(100), nullable=True)
    inicio           = db.Column(db.DateTime, nullable=True)
    vencimento       = db.Column(db.DateTime, nullable=True)
    criado_em        = db.Column(db.DateTime, default=datetime.utcnow)

    def dias_restantes(self):
        if not self.vencimento: return 0
        return max(0, (self.vencimento - datetime.utcnow()).days)

    def esta_ativo(self):
        return self.status == 'ativo' and self.vencimento > datetime.utcnow()

class User(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    name           = db.Column(db.String(100), nullable=False)
    email          = db.Column(db.String(120), unique=True, nullable=False)
    password       = db.Column(db.String(200), nullable=False)
    contact            = db.Column(db.String(20),  nullable=True)
    observation        = db.Column(db.Text,        nullable=True)
    receber_lembretes  = db.Column(db.Boolean,     default=True)
    guest              = db.Column(db.Boolean,     default=False)
    criado_em          = db.Column(db.DateTime,    default=datetime.utcnow)
    pedidos        = db.relationship('Pedido', backref='usuario', lazy=True,
                                     cascade='all, delete-orphan')

class Pedido(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total     = db.Column(db.Float,   default=0)
    status    = db.Column(db.String(20), default='pendente')   # pendente | pago | cancelado
    criado_em = db.Column(db.DateTime,  default=datetime.utcnow)
    itens     = db.relationship('PedidoItem', backref='pedido', lazy=True,
                                cascade='all, delete-orphan')

class PedidoItem(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedido.id'), nullable=False)
    nome      = db.Column(db.String(100), nullable=False)
    categoria = db.Column(db.String(50))
    preco     = db.Column(db.Float, default=0)

class Setting(db.Model):
    key   = db.Column(db.String(100), primary_key=True)  # "global:KEY" ou "{tenant_id}:KEY"
    value = db.Column(db.String(500))

def _sk(key, tenant_id=None):
    """Monta a chave do Setting com prefixo de tenant."""
    return f"{tenant_id}:{key}" if tenant_id else f"global:{key}"

def _get_setting(key, tenant_id=None):
    return db.session.get(Setting, _sk(key, tenant_id))

def _upsert_setting(key, value, tenant_id=None):
    full = _sk(key, tenant_id)
    s = db.session.get(Setting, full)
    if s:
        s.value = value
    else:
        db.session.add(Setting(key=full, value=value))

def _gestao_tid():
    """Retorna tenant_id do gestor logado, ou None."""
    return session.get('gestao_tenant_id')

def _api_tid():
    """Retorna tenant_id a partir do contexto atual (gestão session ou path_tenant_id)."""
    tid = session.get('gestao_tenant_id')
    if tid:
        return tid
    t = get_tenant_atual()
    return t.id if t else None

class LembreteEnviado(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    agendamento_id = db.Column(db.Integer, db.ForeignKey('agendamento.id'), nullable=False, index=True)
    tipo           = db.Column(db.String(10), nullable=False)  # 3d | 1d | 12h | 1h
    enviado_em     = db.Column(db.DateTime, default=datetime.utcnow)

class PasswordResetToken(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token      = db.Column(db.String(100), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used       = db.Column(db.Boolean, default=False)

class EntradaMonetaria(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    descricao   = db.Column(db.String(200), nullable=False)
    valor       = db.Column(db.Float, nullable=False)
    forma       = db.Column(db.String(30), default='dinheiro')  # dinheiro|cartao|pix
    criado_em   = db.Column(db.DateTime, default=datetime.utcnow)
    pedido_id   = db.Column(db.Integer, db.ForeignKey('pedido.id'), nullable=True)

class FotoServico(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    categoria  = db.Column(db.String(50), nullable=False, index=True)   # corte | barba | outros
    servico    = db.Column(db.String(100), nullable=True, index=True)   # nome do serviço
    filename   = db.Column(db.String(200), nullable=False)
    criado_em  = db.Column(db.DateTime, default=datetime.utcnow)

class Agendamento(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    pedido_id       = db.Column(db.Integer, db.ForeignKey('pedido.id'), nullable=True)
    data_hora       = db.Column(db.DateTime, nullable=False, index=True)
    status          = db.Column(db.String(20), default='ativo', index=True)  # ativo | cancelado | concluido
    forma_pagamento = db.Column(db.String(20), nullable=True)  # dinheiro | pix | cartao
    funcionario_id  = db.Column(db.Integer, db.ForeignKey('funcionario.id'), nullable=True)
    criado_em       = db.Column(db.DateTime, default=datetime.utcnow)
    usuario         = db.relationship('User', lazy='select')
    funcionario     = db.relationship('Funcionario', lazy='select', foreign_keys=[funcionario_id])

class Categoria(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    nome      = db.Column(db.String(100), nullable=False)
    icone     = db.Column(db.String(10),  default='✦')
    ordem     = db.Column(db.Integer,     default=0)
    ativo     = db.Column(db.Boolean,     default=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True, default=1)
    servicos  = db.relationship('Servico', backref='cat_ref', lazy=True,
                                foreign_keys='Servico.categoria_id')

class Servico(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    nome         = db.Column(db.String(100), nullable=False)
    categoria    = db.Column(db.String(50),  nullable=True)   # legado
    categoria_id = db.Column(db.Integer, db.ForeignKey('categoria.id'), nullable=True)
    preco        = db.Column(db.Float,  default=0)
    ativo        = db.Column(db.Boolean, default=True)
    ordem        = db.Column(db.Integer, default=0)
    criado_em    = db.Column(db.DateTime, default=datetime.utcnow)
    tenant_id    = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True, default=1)

class HorarioEspecial(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    data       = db.Column(db.String(10), nullable=False, index=True)   # YYYY-MM-DD
    abertura   = db.Column(db.String(5),  nullable=False, default='08:00')
    fechamento = db.Column(db.String(5),  nullable=False, default='18:00')
    criado_em  = db.Column(db.DateTime, default=datetime.utcnow)

class Funcionario(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    nome          = db.Column(db.String(100), nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password      = db.Column(db.String(200), nullable=False)
    telefone      = db.Column(db.String(20),  nullable=True)
    foto          = db.Column(db.String(200),  nullable=True)
    ativo         = db.Column(db.Boolean, default=True)
    criado_em     = db.Column(db.DateTime, default=datetime.utcnow)
    perm_agendamentos = db.Column(db.Boolean, default=True)
    perm_calendario   = db.Column(db.Boolean, default=True)
    perm_marcar       = db.Column(db.Boolean, default=False)
    perm_clientes     = db.Column(db.Boolean, default=False)
    perm_servicos     = db.Column(db.Boolean, default=False)
    perm_precos       = db.Column(db.Boolean, default=False)
    perm_pedidos      = db.Column(db.Boolean, default=False)
    perm_entradas     = db.Column(db.Boolean, default=False)
    perm_fotos        = db.Column(db.Boolean, default=False)
    def to_dict(self):
        return {
            'id': self.id, 'nome': self.nome, 'email': self.email,
            'telefone': self.telefone, 'ativo': self.ativo,
            'foto_url': f'/static/uploads/{self.foto}' if self.foto else None,
            'permissoes': {
                'agendamentos': self.perm_agendamentos,
                'calendario':   self.perm_calendario,
                'marcar':       self.perm_marcar,
                'clientes':     self.perm_clientes,
                'servicos':     self.perm_servicos,
                'precos':       self.perm_precos,
                'pedidos':      self.perm_pedidos,
                'entradas':     self.perm_entradas,
                'fotos':        self.perm_fotos,
            }
        }

class FuncionarioAusencia(db.Model):
    """Marca funcionários ausentes em datas específicas."""
    __tablename__ = 'funcionario_ausencia'
    id             = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(db.Integer, db.ForeignKey('funcionario.id'), nullable=False)
    data           = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    __table_args__ = (db.UniqueConstraint('funcionario_id', 'data'),)


with app.app_context():
    from sqlalchemy import inspect as _inspect
    _inspector = _inspect(db.engine)
    _existing = set(_inspector.get_table_names())
    for _tbl in db.metadata.sorted_tables:
        if _tbl.name not in _existing:
            _tbl.create(db.engine)
    # auto-migrate: add new columns if missing
    _tenant_cols = {c['name'] for c in _inspector.get_columns('tenant')} if 'tenant' in _existing else set()
    for _col, _type in [
        ('fab_wpp',        'TEXT'),
        ('fab_maps',       'TEXT'),
        ('whatsapp',       'VARCHAR(20)'),
        ('maps_url',       'VARCHAR(500)'),
        ('tema_editacoes', 'INTEGER DEFAULT 0'),
        ('tema_pendente',  'TEXT'),
    ]:
        if _col not in _tenant_cols:
            with db.engine.connect() as _conn:
                _conn.execute(db.text(f'ALTER TABLE tenant ADD COLUMN {_col} {_type}'))
                _conn.commit()
    _user_cols = {c['name'] for c in _inspector.get_columns('user')} if 'user' in _existing else set()
    if 'guest' not in _user_cols:
        with db.engine.connect() as _conn:
            _conn.execute(db.text('ALTER TABLE user ADD COLUMN guest INTEGER DEFAULT 0'))
            _conn.commit()
    _serv_cols = {c['name'] for c in _inspector.get_columns('servico')} if 'servico' in _existing else set()
    if 'categoria_id' not in _serv_cols:
        with db.engine.connect() as _conn:
            _conn.execute(db.text('ALTER TABLE servico ADD COLUMN categoria_id INTEGER REFERENCES categoria(id)'))
            _conn.commit()
    _entrada_cols = {c['name'] for c in _inspector.get_columns('entrada_monetaria')} if 'entrada_monetaria' in _existing else set()
    if 'pedido_id' not in _entrada_cols:
        with db.engine.connect() as _conn:
            _conn.execute(db.text('ALTER TABLE entrada_monetaria ADD COLUMN pedido_id INTEGER REFERENCES pedido(id)'))
            _conn.commit()


def _sf(val, default=0.0):
    """Converte para float com segurança; retorna default se inválido."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def get_mp_token():
    """Retorna o MP Access Token: env var primeiro, depois Setting persistido."""
    t = os.environ.get('MP_ACCESS_TOKEN', '')
    if t:
        return t
    try:
        s = _get_setting('admin_mp_access_token')
        return s.value if s and s.value else ''
    except Exception:
        return ''

def user_dict(u):
    return {
        'id': u.id, 'nome': u.name, 'email': u.email,
        'contato': u.contact, 'observacao': u.observation,
        'criado_em': u.criado_em.isoformat() if u.criado_em else None,
    }

def pedido_dict(p):
    ag   = Agendamento.query.filter_by(pedido_id=p.id).first()
    user = db.session.get(User, p.user_id)
    return {
        'id': p.id, 'user_id': p.user_id, 'total': p.total,
        'status': p.status,
        'criado_em': p.criado_em.isoformat() if p.criado_em else None,
        'data_hora': ag.data_hora.isoformat() if ag else None,
        'user_nome':    user.name    if user else '',
        'user_contato': user.contact if user else '',
        'itens': [{'nome': i.nome, 'categoria': i.categoria, 'preco': i.preco} for i in p.itens],
    }

DIAS_PT  = ['Segunda-feira','Terça-feira','Quarta-feira',
            'Quinta-feira','Sexta-feira','Sábado','Domingo']
# Chaves nomeadas usadas no banco para horário_funcionamento (weekday() 0=seg … 6=dom)
_DIAS_KEYS = ['seg','ter','qua','qui','sex','sab','dom']
MESES_PT = ['janeiro','fevereiro','março','abril','maio','junho',
            'julho','agosto','setembro','outubro','novembro','dezembro']

DOMINIOS_VALIDOS = {
    'gmail.com', 'hotmail.com', 'outlook.com', 'yahoo.com',
    'icloud.com', 'live.com', 'msn.com', 'bol.com.br',
    'uol.com.br', 'terra.com.br', 'globo.com', 'protonmail.com',
}

_MANHA = ['08:00','08:40','09:20','10:00','10:40','11:20']
_TARDE = ['14:20','15:00','15:40','16:20','17:00','17:40','18:20','19:00']
SLOTS_PADRAO = {
    0: _MANHA,                 # Seg – só manhã
    **{i: _MANHA + _TARDE for i in range(1, 6)},
    6: [],                     # Dom – fechado
}

def _gestor_como_barbeiro(tenant_id=None):
    """Retorna (ativo: bool, nome: str) do gestor como barbeiro."""
    if tenant_id is None:
        tenant_id = _api_tid()
    s = _get_setting('gestor_e_barbeiro', tenant_id)
    ativo = s and s.value == '1'
    n = _get_setting('gestor_nome', tenant_id)
    nome = n.value if n and n.value else 'Proprietário'
    return ativo, nome

def _funcionarios_ativos_para_data(data_str, tenant_id=None):
    """Retorna lista de dicts {id, nome} dos barbeiros disponíveis na data.
    id=0 representa o gestor (proprietário) quando habilitado."""
    if tenant_id is None:
        tenant_id = _api_tid()
    todos = Funcionario.query.filter_by(ativo=True, tenant_id=tenant_id).order_by(Funcionario.nome).all()
    ausentes_ids = {
        a.funcionario_id
        for a in FuncionarioAusencia.query.filter_by(data=data_str).all()
    }
    lista = [
        {
            'id': f.id,
            'nome': f.nome,
            'foto_url': f'/static/uploads/{f.foto}' if f.foto else None
        }
        for f in todos if f.id not in ausentes_ids
    ]
    gestor_ativo, gestor_nome = _gestor_como_barbeiro(tenant_id)
    if gestor_ativo:
        gf = _get_setting('gestor_foto', tenant_id)
        gestor_foto = f'/static/uploads/{gf.value}' if gf and gf.value else None
        lista.insert(0, {'id': 0, 'nome': gestor_nome, 'foto_url': gestor_foto})
    return lista

def _gerar_slots(abertura='08:00', fechamento='18:00', duracao=40):
    h, m = map(int, abertura.split(':'))
    fh, fm = map(int, fechamento.split(':'))
    slots, cur = [], h * 60 + m
    end = fh * 60 + fm
    while cur + duracao <= end:
        slots.append(f'{cur // 60:02d}:{cur % 60:02d}')
        cur += duracao
    return slots

@app.route('/manifest.json')
def manifest():
    from flask import send_from_directory
    return send_from_directory('static', 'manifest.json', mimetype='application/manifest+json')

@app.route('/')
def index():
    user = None
    if 'user_id' in session:
        user = db.session.get(User, session['user_id'])
    return render_template('index.html', user=user, preview_mode=False, tema_override=None, hide_fabs=False)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        email       = request.form.get('email', '').strip().lower()
        password    = request.form.get('password', '')
        contact            = request.form.get('contact', '').strip()
        observation        = request.form.get('observation', '').strip()
        receber_lembretes  = request.form.get('receber_lembretes') == '1'

        if not name or not email or not password or not contact:
            flash('Preencha todos os campos obrigatórios.', 'error')
            return render_template('register.html', hide_fabs=True)

        digitos_contato = len([c for c in contact if c.isdigit()])
        if digitos_contato < 8:
            flash('Contato deve ter pelo menos 8 números.', 'error')
            return render_template('register.html', hide_fabs=True)

        dominio = email.split('@')[-1] if '@' in email else ''
        if dominio not in DOMINIOS_VALIDOS:
            flash('Use um e-mail com domínio válido (ex: @gmail.com, @hotmail.com).', 'error')
            return render_template('register.html', hide_fabs=True)

        if User.query.filter_by(email=email).first():
            flash('Este e-mail já está cadastrado.', 'error')
            return render_template('register.html', hide_fabs=True)

        hashed = generate_password_hash(password)
        user = User(name=name, email=email, password=hashed,
                    contact=contact or None, observation=observation or None,
                    receber_lembretes=receber_lembretes)
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        session['user_name'] = user.name
        session['user_email'] = user.email
        return redirect(url_for('servicos'))

    return render_template('register.html', hide_fabs=True)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['user_name'] = user.name
            session['user_email'] = user.email
            return redirect(url_for('servicos'))

        flash('E-mail ou senha incorretos.', 'error')

    return render_template('login.html', hide_fabs=True)

@app.route('/login-rapido', methods=['POST'])
def login_rapido():
    nome    = request.form.get('nome', '').strip()
    contato = request.form.get('contato', '').strip()
    alergia = request.form.get('alergia', '').strip()
    if not nome or not contato:
        flash('Nome e contato são obrigatórios.', 'error')
        return redirect(url_for('login'))
    email  = f"guest_{uuid.uuid4().hex[:8]}@temp.com"
    user   = User(
        name=nome,
        email=email,
        password=generate_password_hash(secrets.token_hex(16)),
        contact=contato or None,
        observation=alergia or None,
        receber_lembretes=False,
        guest=True,
    )
    db.session.add(user)
    db.session.commit()
    session['user_id']   = user.id
    session['user_name'] = nome
    session['user_email'] = email
    session['is_guest']  = True
    return redirect(url_for('servicos'))

@app.route('/servicos')
def servicos():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    agendamento_info = None
    ag = (Agendamento.query
          .filter_by(user_id=session['user_id'], status='ativo')
          .filter(Agendamento.data_hora > datetime.now())
          .order_by(Agendamento.data_hora.asc())
          .first())
    if ag:
        _forma_label = {'dinheiro': 'Pagar no local', 'pix': 'PIX', 'cartao': 'Cartão'}
        agendamento_info = {
            'dia': f"{DIAS_PT[ag.data_hora.weekday()]}, {ag.data_hora.day} de {MESES_PT[ag.data_hora.month-1]}",
            'hora': ag.data_hora.strftime('%H:%M'),
            'forma_pagamento': _forma_label.get(ag.forma_pagamento or '', ''),
        }
    sd = _get_setting('dias_agenda', _api_tid())
    dias_agenda = int(sd.value) if sd and sd.value else 20
    return render_template('servicos.html', agendamento_info=agendamento_info, dias_agenda=dias_agenda)

@app.route('/perfil')
def perfil():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    user = db.session.get(User, session['user_id'])
    if not user:
        return redirect(url_for('logout'))
    return render_template('perfil.html', user=user, hide_fabs=True)

@app.route('/perfil/observacao', methods=['POST'])
def salvar_observacao():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    user = db.session.get(User, session['user_id'])
    if not user:
        return redirect(url_for('logout'))
    user.observation = request.form.get('observation', '').strip() or None
    db.session.commit()
    return redirect(url_for('perfil'))

@app.route('/perfil/lembretes', methods=['POST'])
def salvar_lembretes():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    user = db.session.get(User, session['user_id'])
    if not user:
        return redirect(url_for('logout'))
    user.receber_lembretes = request.form.get('receber_lembretes') == '1'
    db.session.commit()
    return redirect(url_for('perfil'))

def _enviar_email(dest, assunto, html):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = assunto
        msg['From']    = MAIL_FROM
        msg['To']      = dest
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(MAIL_USER, MAIL_PASSWORD)
            smtp.sendmail(MAIL_USER, dest, msg.as_string())
        return True
    except Exception as e:
        print(f'[EMAIL ERROR] {e}')
        return False

def _enviar_confirmacao_agendamento(user, data_hora):
    data_fmt = f"{DIAS_PT[data_hora.weekday()]}, {data_hora.day} de {MESES_PT[data_hora.month-1]}"
    hora_fmt = data_hora.strftime('%H:%M')
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                background:#0f0f0f;color:#f0f0f0;padding:28px;border-radius:10px;">
      <h2 style="color:#C9A96E;margin-top:0;">✦ Agendamento Confirmado!</h2>
      <p>Olá, <strong>{user.name}</strong>! Seu horário foi reservado.</p>
      <div style="background:#1a1a1a;border-left:4px solid #C9A96E;
                  padding:16px 20px;border-radius:6px;margin:20px 0;">
        <p style="margin:0;font-size:15px;color:#888;">📅 {data_fmt}</p>
        <p style="margin:8px 0 0;font-size:32px;font-weight:bold;
                  color:#C9A96E;letter-spacing:2px;">⏰ {hora_fmt}</p>
      </div>
      <p style="color:#888;font-size:13px;">
        Para cancelar, acesse o site com pelo menos 1h de antecedência.
      </p>
    </div>
    """
    _enviar_email(user.email, 'Agendamento confirmado — Barbearia', html)

def _enviar_comprovante_pagamento(user, pedido):
    itens_html = ''.join([
        f'<tr><td style="padding:6px 0;color:#ccc;">{i.nome}</td>'
        f'<td style="padding:6px 0;color:#C9A96E;text-align:right;">'
        f'R$ {i.preco:.2f}</td></tr>'
        for i in pedido.itens
    ])
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                background:#0f0f0f;color:#f0f0f0;padding:28px;border-radius:10px;">
      <h2 style="color:#C9A96E;margin-top:0;">✦ Pagamento Confirmado!</h2>
      <p>Olá, <strong>{user.name}</strong>! Seu pagamento foi aprovado.</p>
      <table style="width:100%;border-collapse:collapse;margin:20px 0;">
        {itens_html}
        <tr style="border-top:1px solid #333;">
          <td style="padding:10px 0;font-weight:bold;color:#fff;">Total</td>
          <td style="padding:10px 0;font-weight:bold;color:#C9A96E;text-align:right;">
            R$ {pedido.total:.2f}</td>
        </tr>
      </table>
      <p style="color:#888;font-size:13px;">Obrigado por escolher nossa barbearia!</p>
    </div>
    """
    _enviar_email(user.email, 'Comprovante de pagamento — Barbearia', html)

@app.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user  = User.query.filter_by(email=email).first()
        flash('Confira seu e-mail!', 'success')
        if user:
            PasswordResetToken.query.filter_by(user_id=user.id, used=False).delete()
            token_str = secrets.token_urlsafe(32)
            token = PasswordResetToken(
                user_id    = user.id,
                token      = token_str,
                expires_at = datetime.utcnow() + timedelta(hours=1),
            )
            db.session.add(token)
            db.session.commit()
            host = os.environ.get('SERVER_HOST', request.host)
            link = f"http://{host}{url_for('redefinir_senha', token=token_str)}"
            html = f"""
            <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
              <h2 style="color:#C9A96E;">Redefinição de senha</h2>
              <p>Olá, <strong>{user.name}</strong>!</p>
              <p>Recebemos uma solicitação para redefinir a senha da sua conta.</p>
              <p style="margin:24px 0;">
                <a href="{link}"
                   style="background:#C9A96E;color:#000;padding:12px 24px;
                          border-radius:6px;text-decoration:none;font-weight:bold;">
                  Redefinir minha senha
                </a>
              </p>
              <p style="color:#888;font-size:13px;">
                Este link expira em <strong>1 hora</strong>.<br>
                Se não foi você, ignore este e-mail.
              </p>
            </div>
            """
            _enviar_email(user.email, 'Redefinição de senha — Barbearia', html)
        return redirect(url_for('esqueci_senha'))
    return render_template('esqueci_senha.html', hide_fabs=True)

@app.route('/redefinir-senha/<token>', methods=['GET', 'POST'])
def redefinir_senha(token):
    t = PasswordResetToken.query.filter_by(token=token, used=False).first()
    if not t or t.expires_at < datetime.utcnow():
        flash('Link inválido ou expirado. Solicite um novo.', 'error')
        return redirect(url_for('esqueci_senha'))
    if request.method == 'POST':
        nova = request.form.get('password', '')
        if len(nova) < 6:
            flash('A senha deve ter pelo menos 6 caracteres.', 'error')
            return render_template('redefinir_senha.html', token=token, hide_fabs=True)
        user = db.session.get(User, t.user_id)
        user.password = generate_password_hash(nova)

        t.used = True
        db.session.commit()
        flash('Senha redefinida com sucesso! Faça login.', 'success')
        return redirect(url_for('login'))
    return render_template('redefinir_senha.html', token=token, hide_fabs=True)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

def _build_ag_tema_override(args):
    bg      = args.get('fundo',      '#0c0c0c')
    surface = args.get('superficie', '#161616')
    gold    = args.get('destaque',   '#C9A96E')
    texto   = args.get('texto',      '#F0ECE4')
    borda   = args.get('borda',      '#2A2A2A')
    fonte_t = args.get('fonteTitulo','Playfair Display')
    fonte_c = args.get('fonteCorpo', 'Inter')
    radius  = args.get('cardRadius', '6')
    estilo  = args.get('btnEstilo',  'arredondado')
    hero    = args.get('heroUrl',    '')
    btn_r   = '999px' if estilo=='pilula' else ('0px' if estilo=='angular' else f'{radius}px')
    hero_css = (
        f'body{{background-image:url("{hero}");background-size:cover;background-position:center;background-attachment:fixed}}'
        f'.hero-central{{background-image:url("{hero}")!important}}'
    ) if hero else ''
    return (
        f'<style>:root{{--bg:{bg};--surface:{surface};--surface2:{surface};--border:{borda};'
        f'--gold:{gold};--gold-dim:{gold}cc;--gold-hover:{gold}dd;--text:{texto};'
        f'--text-muted:{texto}88;--placeholder:{texto}55;--radius:{radius}px;'
        f"--font-serif:'{fonte_t}',Georgia,serif;--font-sans:'{fonte_c}',system-ui,sans-serif}}"
        f'.btn,.btn-gold,[class*="btn"]{{border-radius:{btn_r}!important}}'
        f'{hero_css}</style>'
        f'<link href="https://fonts.googleapis.com/css2?family={fonte_t.replace(" ","+")}:wght@600;700'
        f'&family={fonte_c.replace(" ","+")}:wght@400;500&display=swap" rel="stylesheet">'
    )

def _gt_tema_override_css(args):
    fundo      = args.get('fundo',      '#080808')
    superficie = args.get('superficie', '#111111')
    borda      = args.get('borda',      '#222222')
    destaque   = args.get('destaque',   '#C8C8C8')
    texto      = args.get('texto',      '#F2F2F2')
    fonte_t    = args.get('fonteTitulo','Playfair Display')
    fonte_c    = args.get('fonteCorpo', 'Inter')
    return (
        f'<style>:root{{'
        f'--bg:{fundo};--surface:{superficie};--surface2:{superficie};'
        f'--border:{borda};--gold:{destaque};--gold-dim:{destaque}99;'
        f'--gold-hover:{destaque};--text:{texto};--text-muted:{texto}66;'
        f"--font-serif:'{fonte_t}',Georgia,serif;--font-sans:'{fonte_c}',system-ui,sans-serif;"
        f'}}</style>'
        f'<link href="https://fonts.googleapis.com/css2?family={fonte_t.replace(" ","+")}:wght@600;700'
        f'&family={fonte_c.replace(" ","+")}:wght@400;500&display=swap" rel="stylesheet">'
    )

@app.route('/preview/gt/<screen>')
def preview_gt(screen):
    from flask import make_response
    gt_tema_override = _gt_tema_override_css(request.args)
    gt_preview_nome  = 'João Silva'

    hoje_dt = datetime.now()
    hoje_str = hoje_dt.strftime('%Y-%m-%d')
    meses = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
             'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    dias_semana = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']
    proximos_dias = []
    for i in range(7):
        d = hoje_dt + timedelta(days=i)
        proximos_dias.append({
            'data_str': d.strftime('%Y-%m-%d'),
            'dia':      dias_semana[d.weekday()],
            'num':      d.day,
            'mes':      meses[d.month-1][:3],
        })

    ag_mock = json.dumps([
        {'id':1,'hora':'09:00','nome':'Carlos Silva','servico':'Corte Clássico','status':'ativo','funcionario':'Miguel'},
        {'id':2,'hora':'10:30','nome':'Pedro Alves','servico':'Barba','status':'ativo','funcionario':'Miguel'},
        {'id':3,'hora':'14:00','nome':'Marcos Lima','servico':'Corte + Barba','status':'ativo','funcionario':'Rafael'},
        {'id':4,'hora':'15:30','nome':'Lucas Souza','servico':'Corte Degradê','status':'ativo','funcionario':'Rafael'},
    ])
    clientes_mock = json.dumps([
        {'id':1,'nome':'Carlos Silva','email':'carlos@example.com','contact':'(11) 99999-1111','criado_em':'2024-01-10'},
        {'id':2,'nome':'Pedro Alves','email':'pedro@example.com','contact':'(11) 99999-2222','criado_em':'2024-01-12'},
        {'id':3,'nome':'Marcos Lima','email':'marcos@example.com','contact':'(11) 99999-3333','criado_em':'2024-01-14'},
    ])

    ctx = dict(
        gt_tema_override=gt_tema_override,
        gt_preview_nome=gt_preview_nome,
        token='preview',
    )
    screen_map = {
        'dashboard':    ('gestao/dashboard.html',    {**ctx, 'active':'dashboard',
            'hoje':hoje_str,'hoje_dia':str(hoje_dt.day),'hoje_mes':meses[hoje_dt.month-1],
            'proximos_dias':proximos_dias,'agendamentos_json':ag_mock}),
        'agendamentos': ('gestao/agendamentos.html', {**ctx, 'active':'agendamentos','clientes_json':clientes_mock}),
        'pedidos':      ('gestao/pedidos.html',      {**ctx, 'active':'pedidos'}),
        'clientes':     ('gestao/clientes.html',     {**ctx, 'active':'clientes'}),
        'financeiro':   ('gestao/entradas.html',     {**ctx, 'active':'entradas'}),
        'funcionarios': ('gestao/funcionarios.html', {**ctx, 'active':'funcionarios'}),
        'servicos':     ('gestao/servicos.html',     {**ctx, 'active':'servicos'}),
        'precos':       ('gestao/precos.html',       {**ctx, 'active':'precos'}),
        'fotos':        ('gestao/fotos.html',        {**ctx, 'active':'fotos'}),
        'graficos':     ('gestao/graficos.html',     {**ctx, 'active':'graficos'}),
        'horarios':     ('gestao/horarios.html',     {**ctx, 'active':'horarios',
            'config_geral':{'slot_minutos':40,'dias_agenda':20},
            'config_dias':{
                'seg':{'aberto':True,'abertura':'08:00','fechamento':'18:00'},
                'ter':{'aberto':True,'abertura':'08:00','fechamento':'18:00'},
                'qua':{'aberto':True,'abertura':'08:00','fechamento':'18:00'},
                'qui':{'aberto':True,'abertura':'08:00','fechamento':'18:00'},
                'sex':{'aberto':True,'abertura':'08:00','fechamento':'18:00'},
                'sab':{'aberto':True,'abertura':'09:00','fechamento':'16:00'},
                'dom':{'aberto':False,'abertura':'09:00','fechamento':'14:00'},
            },
            'dias_fechados':[]}),
        'contato':      ('gestao/contato.html',      {**ctx, 'active':'contato'}),
        'credenciais':  ('gestao/credenciais.html',  {**ctx, 'active':'credenciais'}),
        'calendario':   ('gestao/calendario.html',   {**ctx, 'active':'calendario'}),
    }
    template, tctx = screen_map.get(screen, screen_map['dashboard'])
    resp = make_response(render_template(template, **tctx))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/inicio')
def preview_ag_inicio():
    from flask import make_response
    tema_override = _build_ag_tema_override(request.args)
    resp = make_response(render_template('index.html',
        user=None, preview_mode=True, tema_override=tema_override, hide_fabs=True))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/sem-cadastro')
def preview_ag_sem_cadastro():
    from flask import make_response
    tema_override = _build_ag_tema_override(request.args)
    resp = make_response(render_template('index.html',
        user=None, preview_mode=True, tema_override=tema_override, hide_fabs=True, auto_rapido=True))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/perfil')
def preview_ag_perfil():
    from flask import make_response
    tema_override = _build_ag_tema_override(request.args)
    class MockUser:
        id=9991; name='Carlos Silva'; email='carlos@preview.com'
        contact='(11) 99999-0000'; observation=''; receber_lembretes=True; guest=False
    resp = make_response(render_template('perfil.html',
        user=MockUser(), hide_fabs=True, tema_override=tema_override, preview_mode=True))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/esqueci')
def preview_ag_esqueci():
    from flask import make_response
    tema_override = _build_ag_tema_override(request.args)
    resp = make_response(render_template('esqueci_senha.html',
        hide_fabs=True, tema_override=tema_override, preview_mode=True))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/register')
def preview_ag_register():
    from flask import make_response
    tema_override = _build_ag_tema_override(request.args)
    resp = make_response(render_template('register.html',
        hide_fabs=True, tema_override=tema_override, preview_mode=True))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/servicos')
def preview_ag_servicos():
    from flask import make_response
    tema_override = _build_ag_tema_override(request.args)
    session['user_id']    = 9991
    session['user_name']  = 'Carlos Silva'
    session['user_email'] = 'carlos@preview.com'
    session['is_preview'] = True
    session.pop('is_guest', None)
    resp = make_response(render_template('servicos.html',
        agendamento_info=None, dias_agenda=20, tema_override=tema_override,
        preview_mode=True, hide_fabs=True, preview_cats=_PREVIEW_CATS))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/demo/login')
def preview_demo_login():
    if request.args.get('origem') != 'editor':
        return '', 403
    session['user_id']   = 9991
    session['user_name'] = 'Cliente Demo'
    session['tenant_id'] = 999
    session['is_preview'] = True
    return redirect(request.args.get('next', '/servicos'))

@app.route('/preview/demo/logout')
def preview_demo_logout():
    session.clear()
    return '', 200

@app.route('/confirmar-pedido', methods=['POST'])
def confirmar_pedido():
    if 'user_id' not in session:
        return jsonify({'erro': 'não autenticado'}), 401

    data  = request.get_json()
    itens = data.get('itens', [])
    total = _sf(data.get('total', 0))

    pedido = Pedido(user_id=session['user_id'], total=total)
    db.session.add(pedido)
    db.session.flush()
    for item in itens:
        db.session.add(PedidoItem(
            pedido_id=pedido.id,
            nome=item.get('nome', ''),
            categoria=item.get('categoria', ''),
            preco=_sf(item.get('preco', 0)),
        ))
    db.session.commit()

    return jsonify({'total': total, 'pedido_id': pedido.id})

@app.route('/agendar', methods=['POST'])
def agendar():
    if 'user_id' not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    data = request.get_json() or {}
    pedido_id     = data.get('pedido_id')
    data_hora_str = data.get('data_hora', '')
    try:
        data_hora = datetime.fromisoformat(data_hora_str)
    except Exception:
        return jsonify({'erro': 'data inválida'}), 400
    if data_hora <= datetime.now():
        return jsonify({'erro': 'Data inválida. Escolha uma data futura.'}), 400
    existente = (Agendamento.query
                 .filter_by(user_id=session['user_id'], status='ativo')
                 .filter(Agendamento.data_hora > datetime.now())
                 .first())
    if existente:
        return jsonify({'erro': 'Você já possui um agendamento marcado.'}), 400

    data_str = data_hora.strftime('%Y-%m-%d')
    dias_s = _get_setting('dias_fechados', _api_tid())
    if dias_s and dias_s.value and data_str in json.loads(dias_s.value):
        return jsonify({'erro': 'Este dia não está disponível.'}), 400

    ativos = _funcionarios_ativos_para_data(data_str)
    capacidade = max(1, len(ativos))
    from collections import Counter
    ags_slot = (Agendamento.query
                .filter_by(status='ativo')
                .filter(Agendamento.data_hora == data_hora)
                .all())
    if len(ags_slot) >= capacidade:
        return jsonify({'erro': 'Este horário já está cheio. Escolha outro.'}), 400

    # Determinar funcionario_id
    funcionario_id = data.get('funcionario_id')
    if funcionario_id is not None:
        funcionario_id = int(funcionario_id)
    else:
        # Auto-atribuir funcionário livre
        ocupados_ids = {ag.funcionario_id for ag in ags_slot if ag.funcionario_id}
        livres = [f for f in ativos if f['id'] not in ocupados_ids]
        if livres:
            import random
            funcionario_id = random.choice(livres)['id']

    ag = Agendamento(user_id=session['user_id'], pedido_id=pedido_id,
                     data_hora=data_hora, funcionario_id=funcionario_id)
    db.session.add(ag)
    db.session.commit()

    user = db.session.get(User, session['user_id'])
    if user:
        _enviar_confirmacao_agendamento(user, data_hora)

    return jsonify({'ok': True, 'id': ag.id})

@app.route('/cancelar-agendamento/<int:ag_id>', methods=['POST'])
def cancelar_agendamento(ag_id):
    if 'user_id' not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    ag = db.session.get(Agendamento, ag_id)
    if not ag or ag.user_id != session['user_id']:
        return jsonify({'erro': 'não encontrado'}), 404
    diferenca = (ag.data_hora - datetime.utcnow()).total_seconds()
    if diferenca < 3600:  # menos de 1h
        return jsonify({'erro': 'Cancelamento não permitido com menos de 1h de antecedência.'}), 400
    ag.status = 'cancelado'
    if ag.pedido_id:
        pedido = db.session.get(Pedido, ag.pedido_id)
        if pedido:
            pedido.status = 'cancelado'
        # Remover entrada monetária automática vinculada a este pedido
        EntradaMonetaria.query.filter_by(pedido_id=ag.pedido_id).delete()
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/meu-agendamento')
def meu_agendamento():
    if 'user_id' not in session:
        return jsonify({'agendamento': None})
    ag = (Agendamento.query
          .filter_by(user_id=session['user_id'], status='ativo')
          .order_by(Agendamento.data_hora.desc())
          .first())
    if not ag:
        return jsonify({'agendamento': None})
    if ag.funcionario_id == 0:
        _, func_nome = _gestor_como_barbeiro()
    else:
        func_nome = ag.funcionario.nome if ag.funcionario else None
    return jsonify({'agendamento': {
        'id': ag.id,
        'data_hora': ag.data_hora.isoformat(),
        'pedido_id': ag.pedido_id,
        'forma_pagamento': ag.forma_pagamento,
        'funcionario_nome': func_nome,
    }})

@app.route('/atualizar-forma-pagamento', methods=['POST'])
def atualizar_forma_pagamento():
    if 'user_id' not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    data = request.get_json() or {}
    forma = data.get('forma', '')
    if forma not in ('dinheiro', 'pix', 'cartao'):
        return jsonify({'erro': 'forma inválida'}), 400
    ag = (Agendamento.query
          .filter_by(user_id=session['user_id'], status='ativo')
          .filter(Agendamento.data_hora > datetime.now())
          .first())
    if not ag:
        return jsonify({'erro': 'agendamento não encontrado'}), 404
    ag.forma_pagamento = forma
    # Remover entrada anterior deste pedido (troca de forma de pagamento)
    if ag.pedido_id:
        EntradaMonetaria.query.filter_by(pedido_id=ag.pedido_id).delete()
    # Criar entrada monetária automática
    if ag.pedido_id:
        pedido = db.session.get(Pedido, ag.pedido_id)
        user   = db.session.get(User, session['user_id'])
        if pedido:
            _forma_map = {'dinheiro': 'dinheiro', 'pix': 'pix', 'cartao': 'cartao_credito'}
            servicos = ', '.join(i.nome for i in pedido.itens) if pedido.itens else 'Serviço'
            nome_cliente = user.name if user else 'Cliente'
            entrada = EntradaMonetaria(
                descricao=f'{servicos} — {nome_cliente}',
                valor=pedido.total,
                forma=_forma_map.get(forma, 'dinheiro'),
                pedido_id=ag.pedido_id
            )
            db.session.add(entrada)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/meu-historico')
def meu_historico():
    if 'user_id' not in session:
        return jsonify({'historico': []})
    ags = (Agendamento.query
           .filter_by(user_id=session['user_id'])
           .order_by(Agendamento.data_hora.desc())
           .limit(20).all())
    pedido_ids = [ag.pedido_id for ag in ags if ag.pedido_id]
    pedidos = (
        {p.id: p for p in
         Pedido.query.filter(Pedido.id.in_(pedido_ids))
                     .options(joinedload(Pedido.itens)).all()}
        if pedido_ids else {}
    )
    resultado = []
    for ag in ags:
        p = pedidos.get(ag.pedido_id)
        resultado.append({
            'id': ag.id,
            'data_hora': ag.data_hora.isoformat(),
            'status': ag.status,
            'total': p.total if p else 0,
            'servicos': [i.nome for i in p.itens] if p else [],
        })
    return jsonify({'historico': resultado})

import hmac as _hmac, hashlib, base64 as _b64

_SECRET = os.environ.get('SECRET_KEY', 'dev-secret')

def _gerar_token(tenant_id: int, func_id: int = 0) -> str:
    msg = f"{tenant_id}:{func_id}"
    sig = _hmac.new(_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return _b64.b64encode(f"{msg}:{sig}".encode()).decode()

def _extrair_tenant_token(token: str):
    """Retorna (tenant_id, func_id) ou (None, None) se inválido."""
    try:
        decoded = _b64.b64decode(token.encode()).decode()
        parts = decoded.rsplit(':', 1)
        if len(parts) != 2: return None, None
        payload, sig = parts
        expected = _hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected): return None, None
        tid_str, fid_str = payload.split(':', 1)
        return int(tid_str), int(fid_str)
    except Exception:
        return None, None

def verificar_token(req):
    """Retorna tenant_id se válido, None caso contrário."""
    token = req.headers.get('Authorization', '').replace('Bearer ', '').strip()
    tid, _ = _extrair_tenant_token(token)
    return tid  # None = inválido, int = tenant_id válido

def get_tenant_by_slug(slug):
    return Tenant.query.filter_by(slug=slug, ativo=True).first()

def get_tenant_atual():
    # 1. Subdomínio (produção com *.dominio.com)
    host = request.host.split(':')[0]
    slug = host.split('.')[0]
    _reservados = ('www', 'localhost', '127', 'seuapp', 'ibarber', '0', '192', '10')
    if slug not in _reservados and '.' in request.host:
        tenant = get_tenant_by_slug(slug)
        if tenant and tenant.assinatura_ativa:
            return tenant

    # 2. Rota por caminho (dev / domínio único) via session
    tid = session.get('path_tenant_id')
    if tid:
        tenant = db.session.get(Tenant, tid)
        if tenant and tenant.assinatura_ativa:
            return tenant

    return None

@app.context_processor
def inject_tenant():
    try:
        t = get_tenant_atual()
    except Exception:
        t = None

    # Fallback: gestão logada
    if t is None and session.get('gestao_tenant_id'):
        try:
            t = db.session.get(Tenant, session['gestao_tenant_id'])
        except Exception:
            t = None

    # Fallback para preview sem banco
    if t is None and session.get('is_preview'):
        t = _MockTenant()

    tema_config = {}
    if t and getattr(t, 'tema', None):
        try:
            raw = json.loads(t.tema)
            if isinstance(raw, dict) and 'ag' in raw and isinstance(raw.get('ag'), dict):
                tema_config = raw['ag']
            elif isinstance(raw, dict):
                tema_config = raw
        except Exception:
            pass

    # Gera CSS de variáveis do tema em Python (mais seguro que Jinja2)
    tema_css = ''
    tema_font_link = ''
    if tema_config:
        css = [':root{']
        if tema_config.get('fundo'):       css.append(f"--bg:{tema_config['fundo']};")
        if tema_config.get('superficie'):  css.append(f"--surface:{tema_config['superficie']};--surface2:{tema_config['superficie']};")
        if tema_config.get('borda'):       css.append(f"--border:{tema_config['borda']};")
        if tema_config.get('destaque'):    d=tema_config['destaque']; css.append(f"--gold:{d};--gold-dim:{d}cc;--gold-hover:{d}dd;")
        if tema_config.get('texto'):       tx=tema_config['texto']; css.append(f"--text:{tx};--text-muted:{tx}88;--placeholder:{tx}55;")
        if tema_config.get('fonteTitulo'): css.append(f"--font-serif:'{tema_config['fonteTitulo']}',Georgia,serif;")
        if tema_config.get('fonteCorpo'):  css.append(f"--font-sans:'{tema_config['fonteCorpo']}',system-ui,sans-serif;")
        if tema_config.get('cardRadius') is not None: css.append(f"--radius:{tema_config['cardRadius']}px;")
        css.append('}')
        tema_css = '<style>' + ''.join(css) + '</style>'
        # Fontes customizadas do Google Fonts
        fonts_qs = []
        tf = tema_config.get('fonteTitulo', '')
        cf = tema_config.get('fonteCorpo', '')
        if tf: fonts_qs.append(f"family={tf.replace(' ','+')}:wght@400;600;700")
        if cf: fonts_qs.append(f"family={cf.replace(' ','+')}:wght@300;400;500")
        if fonts_qs:
            tema_font_link = f'<link href="https://fonts.googleapis.com/css2?{"&".join(fonts_qs)}&display=swap" rel="stylesheet">'
    # JS para btnEstilo e heroUrl (não podem ser feitos só em CSS)
    tema_js = ''
    if tema_config:
        js_parts = ['<script>(function(){']
        bs = tema_config.get('btnEstilo', '')
        cr = tema_config.get('cardRadius', 6)
        if bs:
            br = '999px' if bs == 'pilula' else '0px' if bs == 'angular' else f'{cr}px'
            js_parts.append(f"document.querySelectorAll('.btn,.btn-gold').forEach(function(el){{el.style.borderRadius='{br}';}});")
        hero = tema_config.get('heroUrl', '')
        if hero:
            js_parts.append(f"var h=document.querySelector('.hero-central');if(h){{h.style.backgroundImage=\"url('{hero}')\";h.style.backgroundSize='cover';h.style.backgroundPosition='center';}}")
        js_parts.append('})();</script>')
        if len(js_parts) > 2:
            tema_js = ''.join(js_parts)

    # Sobrescreve campos de identidade via URL params (modo preview)
    preview_identity = {}
    args = request.args
    if args.get('nomeDisplay') or args.get('logoUrl') or args.get('tagline') or args.get('ctaTexto') or args.get('heroUrl'):
        preview_identity = {
            'nomeDisplay': args.get('nomeDisplay', ''),
            'logoUrl':     args.get('logoUrl',     ''),
            'tagline':     args.get('tagline',     ''),
            'ctaTexto':    args.get('ctaTexto',    ''),
            'heroUrl':     args.get('heroUrl',     ''),
        }

    # FAB visibility — só mostra se o tenant configurou explicitamente mostrar=True
    fab_wpp_mostrar = False
    fab_maps_mostrar = False
    if t and not session.get('is_preview'):
        try:
            wpp_cfg = json.loads(t.fab_wpp) if t.fab_wpp else {}
            fab_wpp_mostrar = bool(wpp_cfg.get('mostrar', False))
        except Exception:
            pass
        try:
            maps_cfg = json.loads(t.fab_maps) if t.fab_maps else {}
            fab_maps_mostrar = bool(maps_cfg.get('mostrar', False))
        except Exception:
            pass

    return {'tenant': t, 'tema_config': tema_config, 'tema_css': tema_css,
            'tema_font_link': tema_font_link, 'tema_js': tema_js,
            'preview_identity': preview_identity,
            'fab_wpp_mostrar': fab_wpp_mostrar, 'fab_maps_mostrar': fab_maps_mostrar}

def _get_tenant_para_api():
    """Retorna o tenant a partir do token JWT-like do app Flutter."""
    token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    tid, _ = _extrair_tenant_token(token)
    if tid:
        return db.session.get(Tenant, tid)
    return None

@app.route('/api/tenant/config', methods=['GET'])
def api_tenant_config_get():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    tenant = _get_tenant_para_api()
    if not tenant: return jsonify({'erro': 'não encontrado'}), 404
    fab_wpp_default  = {'mostrar': False, 'texto': 'Fale conosco!',    'cor': '#25D366', 'corTexto': '#ffffff', 'posicaoH': 'right', 'bottom': 24}
    fab_maps_default = {'mostrar': False, 'texto': 'Estamos aqui!',   'cor': '#4285F4', 'corTexto': '#ffffff', 'posicaoH': 'right', 'bottom': 80}
    fab_wpp  = json.loads(tenant.fab_wpp)  if tenant.fab_wpp  else fab_wpp_default
    fab_maps = json.loads(tenant.fab_maps) if tenant.fab_maps else fab_maps_default
    return jsonify({'nome': tenant.nome, 'whatsapp': tenant.whatsapp or '', 'maps_url': tenant.maps_url or '',
                    'fab_wpp': fab_wpp, 'fab_maps': fab_maps})

@app.route('/api/tenant/config', methods=['PUT'])
def api_tenant_config_put():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    tenant = _get_tenant_para_api()
    if not tenant: return jsonify({'erro': 'não encontrado'}), 404
    d = request.get_json(force=True) or {}
    for campo in ('nome', 'whatsapp', 'maps_url'):
        if campo in d:
            setattr(tenant, campo, (d[campo] or '').strip() or None)
    if 'fab_wpp' in d and isinstance(d['fab_wpp'], dict):
        tenant.fab_wpp = json.dumps(d['fab_wpp'])
    if 'fab_maps' in d and isinstance(d['fab_maps'], dict):
        tenant.fab_maps = json.dumps(d['fab_maps'])
    db.session.commit()
    return jsonify({'ok': True})

# ── Categorias ────────────────────────────────────────────────────
@app.route('/api/categorias', methods=['GET'])
def api_categorias_listar():
    if session.get('is_preview'):
        return jsonify([{'id': c['id'], 'nome': c['nome'], 'icone': c['icone'], 'ordem': i} for i, c in enumerate(_PREVIEW_CATS)])
    try:
        tid = session.get('tenant_id') or session.get('path_tenant_id')
        if not tid:
            t = _get_tenant_para_api()
            tid = t.id if t else None
        if not tid:
            t = get_tenant_atual()
            tid = t.id if t else None
        q = Categoria.query.filter_by(ativo=True)
        if tid: q = q.filter_by(tenant_id=tid)
        cats = q.order_by(Categoria.ordem).all()
        return jsonify([{'id': c.id, 'nome': c.nome, 'icone': c.icone, 'ordem': c.ordem} for c in cats])
    except Exception:
        return jsonify([])

@app.route('/api/categorias', methods=['POST'])
def api_categorias_criar():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    d = request.get_json() or {}
    nome = d.get('nome', '').strip()
    if not nome: return jsonify({'erro': 'nome obrigatório'}), 400
    tenant = _get_tenant_para_api()
    cat = Categoria(
        nome=nome, icone=d.get('icone', '✦'),
        ordem=Categoria.query.count(),
        tenant_id=tenant.id if tenant else 1,
    )
    db.session.add(cat); db.session.commit()
    return jsonify({'ok': True, 'id': cat.id, 'nome': cat.nome, 'icone': cat.icone})

@app.route('/api/categorias/<int:cid>', methods=['PUT', 'DELETE'])
def api_categoria_detalhe(cid):
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    cat = db.session.get(Categoria, cid)
    if not cat: return jsonify({'erro': 'não encontrado'}), 404
    if request.method == 'DELETE':
        cat.ativo = False; db.session.commit(); return jsonify({'ok': True})
    d = request.get_json() or {}
    if 'nome'  in d: cat.nome  = d['nome'].strip()
    if 'icone' in d: cat.icone = d['icone']
    if 'ordem' in d: cat.ordem = d['ordem']
    db.session.commit()
    return jsonify({'ok': True})

# ── Serviços ───────────────────────────────────────────────────────
@app.route('/api/servicos', methods=['GET'])
def api_servicos_listar():
    if session.get('is_preview'):
        return jsonify(_PREVIEW_SVCS)
    try:
        q = Servico.query.filter_by(ativo=True)
        tid = session.get('tenant_id') or session.get('path_tenant_id')
        if not tid:
            t = _get_tenant_para_api()
            tid = t.id if t else None
        if not tid:
            t = get_tenant_atual()
            tid = t.id if t else None
        if tid: q = q.filter_by(tenant_id=tid)
        svs = q.order_by(Servico.categoria_id, Servico.ordem).all()
        return jsonify([{
            'id': s.id, 'nome': s.nome,
            'categoria_id': s.categoria_id,
            'categoria_nome': s.cat_ref.nome if s.cat_ref else (s.categoria or ''),
            'preco': s.preco
        } for s in svs])
    except Exception:
        return jsonify([])

@app.route('/api/servicos', methods=['POST'])
def api_servicos_criar():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    d    = request.get_json() or {}
    nome = d.get('nome', '').strip()
    if not nome: return jsonify({'erro': 'nome obrigatório'}), 400
    cat_id = d.get('categoria_id')
    sv = Servico(
        nome=nome,
        categoria='',
        categoria_id=cat_id,
        preco=_sf(d.get('preco', 0)),
        ordem=Servico.query.filter_by(categoria_id=cat_id, ativo=True).count()
    )
    db.session.add(sv); db.session.commit()
    return jsonify({'ok': True, 'id': sv.id})

@app.route('/api/servicos/<int:sid>', methods=['PUT', 'DELETE'])
def api_servico_detalhe(sid):
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    sv = db.session.get(Servico, sid)
    if not sv: return jsonify({'erro': 'não encontrado'}), 404
    if request.method == 'DELETE':
        sv.ativo = False; db.session.commit(); return jsonify({'ok': True})
    d = request.get_json() or {}
    if 'nome'         in d: sv.nome         = d['nome'].strip()
    if 'categoria_id' in d: sv.categoria_id = d['categoria_id']
    if 'preco'        in d: sv.preco        = _sf(d['preco'])
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/horarios-disponiveis')
def api_horarios_disponiveis():
    data_str = request.args.get('data', '')
    try:
        data_obj = datetime.strptime(data_str, '%Y-%m-%d').date()
    except Exception:
        return jsonify({'erro': 'data inválida'}), 400

    _tid = _api_tid()
    dias_s = _get_setting('dias_fechados', _tid)
    dias_fechados = json.loads(dias_s.value) if dias_s and dias_s.value else []
    if data_str in dias_fechados:
        return jsonify({'disponiveis': [], 'tomados': [], 'fechado': True})

    duracao_s = _get_setting('intervalo_minutos', _tid)
    duracao   = int(duracao_s.value) if duracao_s and duracao_s.value else 40

    # horário especial para esta data sobrepõe o semanal
    he = HorarioEspecial.query.filter_by(data=data_str).first()
    if he:
        todos_slots = _gerar_slots(he.abertura, he.fechamento, duracao)
    else:
        config_s = _get_setting('horario_funcionamento', _tid)
        if config_s and config_s.value:
            config  = json.loads(config_s.value)
            dia_key = _DIAS_KEYS[data_obj.weekday()]
            dia_cfg = config.get(dia_key, {})
            if not dia_cfg.get('aberto', True):
                return jsonify({'disponiveis': [], 'tomados': [], 'fechado': True})
            todos_slots = _gerar_slots(dia_cfg.get('abertura', '08:00'),
                                       dia_cfg.get('fechamento', '18:00'), duracao)
        else:
            if data_obj.weekday() == 6:
                return jsonify({'disponiveis': [], 'tomados': [], 'fechado': True})
            todos_slots = _gerar_slots('08:00', '19:00', duracao) if duracao != 40 \
                          else SLOTS_PADRAO.get(data_obj.weekday(), [])

    inicio = datetime.combine(data_obj, datetime.min.time())
    fim    = inicio + timedelta(days=1)
    agendados = (Agendamento.query
                 .filter(Agendamento.data_hora >= inicio,
                         Agendamento.data_hora < fim,
                         Agendamento.status == 'ativo')
                 .all())
    # Capacidade por slot = número de funcionários ativos nessa data (mínimo 1)
    ativos = _funcionarios_ativos_para_data(data_str)
    capacidade = max(1, len(ativos))

    from collections import Counter
    slot_counts = Counter(ag.data_hora.strftime('%H:%M') for ag in agendados)
    tomados = {s for s, c in slot_counts.items() if c >= capacidade}
    todos_tomados = list(slot_counts.keys())

    # Para hoje, remove slots cujo horário já passou
    agora = datetime.now()
    if data_obj == agora.date():
        disponiveis = [
            s for s in todos_slots
            if s not in tomados and
               datetime.combine(data_obj, datetime.strptime(s, '%H:%M').time()) > agora
        ]
    else:
        disponiveis = [s for s in todos_slots if s not in tomados]

    return jsonify({'disponiveis': disponiveis, 'tomados': todos_tomados, 'fechado': False})

@app.route('/api/barbeiros-disponiveis')
def api_barbeiros_disponiveis():
    """Retorna barbeiros disponíveis para um data_hora específico (sem autenticação)."""
    data_hora_str = request.args.get('data_hora', '')
    try:
        data_hora = datetime.fromisoformat(data_hora_str)
    except Exception:
        return jsonify({'erro': 'data_hora inválida'}), 400
    data_str = data_hora.strftime('%Y-%m-%d')
    ativos = _funcionarios_ativos_para_data(data_str)
    if not ativos:
        return jsonify({'funcionarios': []})
    # Quais já têm agendamento nesse slot
    ocupados_ids = {
        ag.funcionario_id
        for ag in Agendamento.query
            .filter_by(status='ativo')
            .filter(Agendamento.data_hora == data_hora)
            .filter(Agendamento.funcionario_id.isnot(None))
            .all()
    }
    disponiveis = [f for f in ativos if f['id'] not in ocupados_ids]
    return jsonify({'funcionarios': disponiveis})

@app.route('/api/gestor-barbeiro', methods=['GET', 'POST'])
def api_gestor_barbeiro():
    """Lê/salva se o gestor conta como barbeiro e seu nome."""
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    _tid = _api_tid()
    ativo, nome = _gestor_como_barbeiro(_tid)
    if request.method == 'GET':
        gf = _get_setting('gestor_foto', _tid)
        foto_url = f'/static/uploads/{gf.value}' if gf and gf.value else None
        return jsonify({'ativo': ativo, 'nome': nome, 'foto_url': foto_url})
    data = request.get_json(silent=True) or {}
    if 'ativo' in data:
        _upsert_setting('gestor_e_barbeiro', '1' if data['ativo'] else '0', _tid)
    if 'nome' in data:
        _upsert_setting('gestor_nome', data['nome'], _tid)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/escala/<data_str>', methods=['GET', 'POST'])
def api_escala(data_str):
    """GET: lista funcionários e se trabalham na data. POST: salva ausências."""
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    todos = Funcionario.query.filter_by(ativo=True).order_by(Funcionario.nome).all()
    ausentes_ids = {
        a.funcionario_id
        for a in FuncionarioAusencia.query.filter_by(data=data_str).all()
    }
    if request.method == 'GET':
        return jsonify([{
            'id': f.id,
            'nome': f.nome,
            'trabalhando': f.id not in ausentes_ids,
        } for f in todos])
    # POST: recebe lista de IDs que VÃO trabalhar
    data = request.get_json(silent=True) or {}
    trabalhando_ids = set(data.get('trabalhando', []))
    FuncionarioAusencia.query.filter_by(data=data_str).delete()
    for f in todos:
        if f.id not in trabalhando_ids:
            db.session.add(FuncionarioAusencia(funcionario_id=f.id, data=data_str))
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/config-horarios', methods=['GET', 'POST'])
def api_config_horarios():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    _tid = _api_tid()
    if request.method == 'POST':
        data     = request.get_json(silent=True) or {}
        intervalo = data.pop('intervalo_minutos', None)
        if intervalo is not None:
            _upsert_setting('intervalo_minutos', str(int(intervalo)), _tid)
        dias_agenda = data.pop('dias_agenda', None)
        if dias_agenda is not None:
            _upsert_setting('dias_agenda', str(int(dias_agenda)), _tid)
        _upsert_setting('horario_funcionamento', json.dumps(data, ensure_ascii=False), _tid)
        db.session.commit()
        return jsonify({'ok': True})
    s  = _get_setting('horario_funcionamento', _tid)
    si = _get_setting('intervalo_minutos', _tid)
    sd = _get_setting('dias_agenda', _tid)
    cfg = json.loads(s.value) if s and s.value else \
          {k: {'aberto': k != 'dom', 'abertura': '08:00', 'fechamento': '18:00'} for k in _DIAS_KEYS}
    cfg['intervalo_minutos'] = int(si.value) if si and si.value else 40
    cfg['dias_agenda'] = int(sd.value) if sd and sd.value else 20
    return jsonify(cfg)

@app.route('/api/config-publica', methods=['GET'])
def api_config_publica():
    """Configurações públicas lidas pelo site (sem autenticação)."""
    sd = _get_setting('dias_agenda', _api_tid())
    return jsonify({'dias_agenda': int(sd.value) if sd and sd.value else 20})

@app.route('/api/dias-fechados', methods=['GET', 'POST', 'DELETE'])
def api_dias_fechados():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    _tid = _api_tid()
    s = _get_setting('dias_fechados', _tid)
    dias = json.loads(s.value) if s and s.value else []
    if request.method in ('POST', 'DELETE'):
        data_val = (request.get_json(silent=True) or {}).get('data', '')
        if request.method == 'POST' and data_val and data_val not in dias:
            dias.append(data_val); dias.sort()
        elif request.method == 'DELETE' and data_val in dias:
            dias.remove(data_val)
        _upsert_setting('dias_fechados', json.dumps(dias), _tid)
        db.session.commit()
        return jsonify({'ok': True, 'dias': dias})
    return jsonify({'dias': dias})

@app.route('/api/horarios-especiais', methods=['GET', 'POST'])
def api_horarios_especiais():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    if request.method == 'POST':
        d = request.get_json(silent=True) or {}
        import re as _re
        data       = d.get('data', '').strip()
        abertura   = d.get('abertura', '08:00').strip()
        fechamento = d.get('fechamento', '18:00').strip()
        acao       = d.get('acao', '')  # '' | 'cancelar' | 'manter'

        if not data: return jsonify({'erro': 'data obrigatória'}), 400
        if not _re.match(r'^\d{4}-\d{2}-\d{2}$', data):
            return jsonify({'erro': 'data inválida, use YYYY-MM-DD'}), 400
        if not _re.match(r'^\d{2}:\d{2}$', abertura) or not _re.match(r'^\d{2}:\d{2}$', fechamento):
            return jsonify({'erro': 'horário inválido, use HH:MM'}), 400
        if abertura >= fechamento:
            return jsonify({'erro': 'abertura deve ser antes do fechamento'}), 400

        # Detecta agendamentos ativos que ficam fora do novo horário
        if not acao:
            data_obj   = datetime.strptime(data, '%Y-%m-%d').date()
            inicio_dia = datetime.combine(data_obj, datetime.min.time())
            fim_dia    = datetime.combine(data_obj, datetime.max.time())
            ags = (Agendamento.query
                   .filter(Agendamento.data_hora >= inicio_dia,
                           Agendamento.data_hora <= fim_dia,
                           Agendamento.status == 'ativo')
                   .all())
            conflitos = []
            for ag in ags:
                hora = ag.data_hora.strftime('%H:%M')
                if hora < abertura or hora >= fechamento:
                    conflitos.append({
                        'id':      ag.id,
                        'hora':    hora,
                        'usuario': ag.usuario.name if ag.usuario else 'Cliente',
                    })
            if conflitos:
                return jsonify({'conflitos': conflitos, 'total': len(conflitos)}), 409

        # Ação: cancelar os conflitantes
        if acao == 'cancelar':
            data_obj   = datetime.strptime(data, '%Y-%m-%d').date()
            inicio_dia = datetime.combine(data_obj, datetime.min.time())
            fim_dia    = datetime.combine(data_obj, datetime.max.time())
            ags = (Agendamento.query
                   .filter(Agendamento.data_hora >= inicio_dia,
                           Agendamento.data_hora <= fim_dia,
                           Agendamento.status == 'ativo')
                   .all())
            for ag in ags:
                hora = ag.data_hora.strftime('%H:%M')
                if hora < abertura or hora >= fechamento:
                    ag.status = 'cancelado'

        HorarioEspecial.query.filter_by(data=data).delete()
        db.session.add(HorarioEspecial(data=data, abertura=abertura, fechamento=fechamento))
        db.session.commit()
        return jsonify({'ok': True})
    hes = HorarioEspecial.query.order_by(HorarioEspecial.data).all()
    return jsonify([{'id': h.id, 'data': h.data, 'abertura': h.abertura, 'fechamento': h.fechamento} for h in hes])

@app.route('/api/horarios-especiais/<int:hid>', methods=['DELETE'])
def api_horario_especial_del(hid):
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    h = db.session.get(HorarioEspecial, hid)
    if not h: return jsonify({'erro': 'não encontrado'}), 404
    db.session.delete(h)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/agendamentos/<int:ag_id>/status', methods=['POST'])
def api_agendamento_status(ag_id):
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    ag = db.session.get(Agendamento, ag_id)
    if not ag:
        return jsonify({'erro': 'não encontrado'}), 404
    status = (request.get_json(silent=True) or {}).get('status', '').strip()
    if status not in ('ativo', 'cancelado', 'concluido', 'nao_compareceu'):
        return jsonify({'erro': 'status inválido'}), 400
    ag.status = status
    if status in ('cancelado', 'nao_compareceu') and ag.pedido_id:
        pedido = db.session.get(Pedido, ag.pedido_id)
        if pedido:
            pedido.status = status
    db.session.commit()
    return jsonify({'ok': True, 'status': ag.status})

@app.route('/api/credenciais', methods=['GET', 'POST'])
def api_credenciais():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    _keys = ['pix_chave', 'mp_token', 'mp_public_key', 'pix_ativo', 'cartao_ativo']
    _tid = _api_tid()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        for k in _keys:
            if k in data:
                _upsert_setting(k, str(data[k]), _tid)
        db.session.commit()
        return jsonify({'ok': True})
    result = {}
    for k in _keys:
        s = _get_setting(k, _tid)
        result[k] = s.value if s else ''
    return jsonify(result)

@app.route('/api/status-pagamentos', methods=['GET'])
def api_status_pagamentos():
    """Retorna quais métodos de pagamento estão configurados (sem expor credenciais)."""
    _tid = _api_tid()
    mp_token  = _get_setting('mp_token', _tid)
    pix_chave = _get_setting('pix_chave', _tid)
    mp_ok  = bool(mp_token  and mp_token.value  and mp_token.value.strip())
    pix_ok = bool(pix_chave and pix_chave.value and pix_chave.value.strip()) and mp_ok
    pix_ativo_s    = _get_setting('pix_ativo', _tid)
    cartao_ativo_s = _get_setting('cartao_ativo', _tid)
    pix_ligado    = pix_ativo_s.value    != '0' if pix_ativo_s    else False
    cartao_ligado = cartao_ativo_s.value != '0' if cartao_ativo_s else False
    return jsonify({'pix': pix_ok and pix_ligado, 'cartao': mp_ok and cartao_ligado})

@app.route('/api/criar-pagamento', methods=['POST'])
def criar_pagamento():
    data      = request.get_json(silent=True) or {}
    total     = _sf(data.get('total', 0))
    metodo    = data.get('metodo', 'pix')
    pedido_id = data.get('pedido_id')

    _tid = _api_tid()
    mp_token_s = _get_setting('mp_token', _tid)
    mp_token   = mp_token_s.value if mp_token_s else ''

    if not mp_token:
        return jsonify({'erro': 'Mercado Pago não configurado. Configure o Access Token em Credenciais.'}), 400

    if metodo == 'pix':
        email_pagador = data.get('payer_email') or session.get('user_email', 'cliente@barbearia.com')
        payload = {
            'transaction_amount': round(total, 2),
            'description': 'Barbearia – Serviços',
            'payment_method_id': 'pix',
            'payer': {'email': email_pagador},
        }
        r = req_http.post(
            'https://api.mercadopago.com/v1/payments',
            json=payload,
            headers={'Authorization': f'Bearer {mp_token}',
                     'Content-Type': 'application/json',
                     'X-Idempotency-Key': str(uuid.uuid4())},
            timeout=15,
        )
        if r.status_code not in (200, 201):
            return jsonify({'erro': 'Erro ao gerar QR Code', 'detalhe': r.text}), 502
        d  = r.json()
        td = d.get('point_of_interaction', {}).get('transaction_data', {})
        return jsonify({
            'mp_payment_id': d['id'],
            'pedido_id':     pedido_id,
            'qr_code':       td.get('qr_code', ''),
            'qr_code_base64': td.get('qr_code_base64', ''),
        })

    else:  # cartao – Checkout Pro
        payload = {
            'items': [{'title': 'Barbearia – Serviços', 'quantity': 1,
                       'unit_price': round(total, 2), 'currency_id': 'BRL'}],
        }
        r = req_http.post(
            'https://api.mercadopago.com/checkout/preferences',
            json=payload,
            headers={'Authorization': f'Bearer {mp_token}',
                     'Content-Type': 'application/json'},
            timeout=15,
        )
        if r.status_code not in (200, 201):
            return jsonify({'erro': 'Erro MP', 'detalhe': r.text}), 502
        d = r.json()
        return jsonify({'checkout_url': d.get('init_point', '')})

@app.route('/api/verificar-pagamento/<int:mp_payment_id>', methods=['GET'])
def verificar_pagamento(mp_payment_id):
    pedido_id  = request.args.get('pedido_id', type=int)
    mp_token_s = _get_setting('mp_token', _api_tid())
    if not mp_token_s or not mp_token_s.value:
        return jsonify({'status': 'unknown'}), 400
    r = req_http.get(
        f'https://api.mercadopago.com/v1/payments/{mp_payment_id}',
        headers={'Authorization': f'Bearer {mp_token_s.value}'},
        timeout=10,
    )
    if r.status_code != 200:
        return jsonify({'status': 'unknown'}), 404
    status = r.json().get('status', 'unknown')
    if status == 'approved' and pedido_id:
        pedido = db.session.get(Pedido, pedido_id)
        if pedido and pedido.status != 'pago':
            pedido.status = 'pago'
            db.session.add(EntradaMonetaria(
                descricao=f'Pedido #{pedido_id} — PIX',
                valor=pedido.total,
                forma='pix',
            ))
            db.session.commit()
            user = db.session.get(User, pedido.user_id)
            if user:
                _enviar_comprovante_pagamento(user, pedido)
    return jsonify({'status': status})

@app.route('/api/precos', methods=['GET', 'POST'])
def api_precos():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        for nome, valor in data.items():
            sv = Servico.query.filter_by(nome=nome).first()
            if sv:
                sv.preco = _sf(valor)
        db.session.commit()
        svs = Servico.query.filter_by(ativo=True).order_by(Servico.categoria, Servico.ordem).all()
        return jsonify({'ok': True, 'precos': {sv.nome: sv.preco for sv in svs}})
    svs = Servico.query.filter_by(ativo=True).order_by(Servico.categoria, Servico.ordem).all()
    return jsonify({sv.nome: sv.preco for sv in svs})

@app.route('/api/usuarios', methods=['GET', 'POST'])
def api_usuarios():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    if request.method == 'POST':
        data  = request.get_json(silent=True) or {}
        name  = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip().lower()
        contact     = (data.get('contact') or '').strip() or None
        observation = (data.get('observation') or '').strip() or None
        if not name:
            return jsonify({'erro': 'Nome obrigatório'}), 400
        if not contact:
            return jsonify({'erro': 'Contato obrigatório'}), 400
        # email opcional: gera placeholder único se não fornecido
        if not email:
            slug = name.split()[0].lower().replace(' ', '')
            import re; slug = re.sub(r'[^a-z0-9]', '', slug)
            base = f"{slug}.{contact.replace(' ','').replace('-','').replace('(','').replace(')','')}"
            email = f"{base}@admin.local"
            if User.query.filter_by(email=email).first():
                import time; email = f"{base}.{int(time.time())}@admin.local"
        elif User.query.filter_by(email=email).first():
            return jsonify({'erro': 'E-mail já cadastrado'}), 400
        senha_temp = generate_password_hash('barber@' + name.split()[0].lower())
        receber_lembretes = data.get('receber_lembretes', True)
        user = User(name=name, email=email, password=senha_temp,
                    contact=contact, observation=observation,
                    receber_lembretes=bool(receber_lembretes))
        db.session.add(user)
        db.session.commit()
        return jsonify({'ok': True, 'usuario': user_dict(user)})
    usuarios = User.query.order_by(User.criado_em.desc()).all()
    return jsonify([user_dict(u) for u in usuarios])

@app.route('/api/usuarios/<int:uid>', methods=['GET'])
def api_usuario(uid):
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    u = db.session.get(User, uid)
    if not u:
        return jsonify({'erro': 'não encontrado'}), 404
    data = user_dict(u)
    data['pedidos'] = [pedido_dict(p) for p in u.pedidos]
    return jsonify(data)

@app.route('/api/pedidos', methods=['GET'])
def api_pedidos():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    pedidos = Pedido.query.order_by(Pedido.criado_em.desc()).all()
    return jsonify([pedido_dict(p) for p in pedidos])

@app.route('/api/pedidos/<int:pid>', methods=['GET'])
def api_pedido(pid):
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    p = db.session.get(Pedido, pid)
    if not p:
        return jsonify({'erro': 'não encontrado'}), 404
    return jsonify(pedido_dict(p))

@app.route('/api/pedidos/<int:pid>/status', methods=['POST'])
def api_pedido_status(pid):
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    p = db.session.get(Pedido, pid)
    if not p:
        return jsonify({'erro': 'não encontrado'}), 404
    status = (request.get_json(silent=True) or {}).get('status', '').strip()
    if status not in ('pendente', 'pago', 'cancelado', 'nao_compareceu'):
        return jsonify({'erro': 'status inválido'}), 400
    p.status = status
    db.session.commit()
    return jsonify({'ok': True, 'status': p.status})

@app.route('/api/stats/servicos', methods=['GET'])
def api_stats_servicos():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    # Busca categorias reais para enriquecer a resposta
    cats = {c.nome: c.nome for c in Categoria.query.filter_by(ativo=True).all()}
    contagem = {}
    pedidos = Pedido.query.filter(Pedido.status != 'cancelado').all()
    for p in pedidos:
        for i in p.itens:
            nome = (i.nome or '').strip()
            cat  = (i.categoria or '').strip()
            if nome:
                if nome not in contagem:
                    contagem[nome] = {'total': 0, 'categoria': cat}
                contagem[nome]['total'] += 1
    resultado = sorted(
        [{'nome': k, 'total': v['total'], 'categoria': v['categoria']}
         for k, v in contagem.items()],
        key=lambda x: x['total'], reverse=True)
    return jsonify(resultado)

@app.route('/api/stats/barbeiros', methods=['GET'])
def api_stats_barbeiros():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    contagem = {}
    ags = (Agendamento.query
           .filter(Agendamento.status == 'ativo')
           .options(joinedload(Agendamento.funcionario))
           .all())
    _, gestor_nome = _gestor_como_barbeiro()
    for ag in ags:
        if ag.funcionario_id == 0:
            nome = gestor_nome or 'Gestor'
        elif ag.funcionario:
            nome = ag.funcionario.nome
        else:
            continue
        contagem[nome] = contagem.get(nome, 0) + 1
    resultado = sorted([{'nome': k, 'total': v} for k, v in contagem.items()],
                       key=lambda x: x['total'], reverse=True)
    return jsonify(resultado)

@app.route('/api/stats/agendamentos_por_mes', methods=['GET'])
def api_stats_ags_mes():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    ags = Agendamento.query.filter(Agendamento.status != 'cancelado').all()
    contagem = {}
    for ag in ags:
        chave = ag.data_hora.strftime('%Y-%m')
        contagem[chave] = contagem.get(chave, 0) + 1
    resultado = [{'mes': k, 'total': v} for k, v in sorted(contagem.items())]
    return jsonify(resultado)

@app.route('/api/stats/agendamentos_por_dia', methods=['GET'])
def api_stats_ags_dia():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    dias_pt = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    ags = Agendamento.query.filter(Agendamento.status != 'cancelado').all()
    contagem = {d: 0 for d in dias_pt}
    for ag in ags:
        contagem[dias_pt[ag.data_hora.weekday()]] += 1
    resultado = [{'dia': k, 'total': v} for k, v in contagem.items()]
    return jsonify(resultado)

@app.route('/api/stats/formas_pagamento', methods=['GET'])
def api_stats_formas():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    ags = Agendamento.query.filter(
        Agendamento.status != 'cancelado',
        Agendamento.forma_pagamento.isnot(None)
    ).all()
    contagem = {}
    for ag in ags:
        f = (ag.forma_pagamento or 'não informado').strip()
        if f:
            contagem[f] = contagem.get(f, 0) + 1
    resultado = sorted([{'forma': k, 'total': v} for k, v in contagem.items()],
                       key=lambda x: x['total'], reverse=True)
    return jsonify(resultado)

@app.route('/api/stats/receita_por_mes', methods=['GET'])
def api_stats_receita_mes():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    entradas = EntradaMonetaria.query.all()
    contagem = {}
    for e in entradas:
        chave = e.criado_em.strftime('%Y-%m')
        contagem[chave] = round(contagem.get(chave, 0.0) + (e.valor or 0), 2)
    resultado = [{'mes': k, 'total': v} for k, v in sorted(contagem.items())]
    return jsonify(resultado)

@app.route('/api/stats/receita_por_forma', methods=['GET'])
def api_stats_receita_forma():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    entradas = EntradaMonetaria.query.all()
    contagem = {}
    for e in entradas:
        f = (e.forma or 'dinheiro').strip()
        contagem[f] = round(contagem.get(f, 0.0) + (e.valor or 0), 2)
    resultado = sorted([{'forma': k, 'total': v} for k, v in contagem.items()],
                       key=lambda x: x['total'], reverse=True)
    return jsonify(resultado)

@app.route('/api/stats/pedidos_por_status', methods=['GET'])
def api_stats_pedidos_status():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    pedidos = Pedido.query.all()
    contagem = {}
    for p in pedidos:
        s = p.status or 'pendente'
        contagem[s] = contagem.get(s, 0) + 1
    resultado = sorted([{'status': k, 'total': v} for k, v in contagem.items()],
                       key=lambda x: x['total'], reverse=True)
    return jsonify(resultado)

@app.route('/api/stats/categorias', methods=['GET'])
def api_stats_categorias():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    pedidos = Pedido.query.filter(Pedido.status != 'cancelado').all()
    contagem = {}
    for p in pedidos:
        for i in p.itens:
            cat = (i.categoria or 'Sem categoria').strip()
            if cat:
                contagem[cat] = contagem.get(cat, 0) + 1
    resultado = sorted([{'categoria': k, 'total': v} for k, v in contagem.items()],
                       key=lambda x: x['total'], reverse=True)
    return jsonify(resultado)

@app.route('/api/agendamentos', methods=['GET', 'POST'])
def api_agendamentos():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        user_id      = data.get('user_id')
        data_hora_str = data.get('data_hora', '')
        try:
            data_hora = datetime.fromisoformat(data_hora_str)
        except Exception:
            return jsonify({'erro': 'data inválida'}), 400
        if not user_id:
            return jsonify({'erro': 'user_id obrigatório'}), 400
        user = db.session.get(User, user_id)
        if not user:
            return jsonify({'erro': 'usuário não encontrado'}), 404
        data_str = data_hora.strftime('%Y-%m-%d')
        dias_s = _get_setting('dias_fechados', _api_tid())
        if dias_s and dias_s.value and data_str in json.loads(dias_s.value):
            return jsonify({'erro': 'Este dia não está disponível.'}), 400
        slot_ocupado = (Agendamento.query
                        .filter_by(status='ativo')
                        .filter(Agendamento.data_hora == data_hora)
                        .first())
        if slot_ocupado:
            return jsonify({'erro': 'Este horário já foi reservado.'}), 400
        # Aceita servico_ids (lista) ou servico_id (legado, único)
        raw_ids = data.get('servico_ids') or (
            [data['servico_id']] if data.get('servico_id') else []
        )
        servico_ids   = [int(x) for x in raw_ids if x]
        forma_pag     = data.get('forma_pagamento', 'pagar_no_local')
        funcionario_id = data.get('funcionario_id', None)

        ag = Agendamento(user_id=user_id, data_hora=data_hora)
        if funcionario_id is not None:
            ag.funcionario_id = funcionario_id

        # Cria Pedido se houver serviços
        pedido = None
        servicos_validos = []
        if servico_ids:
            for sid in servico_ids:
                sv = db.session.get(Servico, sid)
                if sv:
                    servicos_validos.append(sv)
        if servicos_validos:
            total_pedido = sum(sv.preco or 0 for sv in servicos_validos)
            pedido = Pedido(
                user_id=user_id,
                status='pago' if forma_pag == 'pagar_no_local' else 'pendente',
                total=total_pedido,
            )
            db.session.add(pedido)
            db.session.flush()
            for sv in servicos_validos:
                db.session.add(PedidoItem(
                    pedido_id=pedido.id,
                    nome=sv.nome,
                    categoria=sv.cat_ref.nome if sv.cat_ref else (sv.categoria or ''),
                    preco=sv.preco or 0,
                ))
            ag.pedido_id = pedido.id

        db.session.add(ag)
        db.session.flush()

        # Auto-entrada monetária se "pagar no local"
        if pedido and forma_pag == 'pagar_no_local':
            nomes = ', '.join(sv.nome for sv in servicos_validos)
            db.session.add(EntradaMonetaria(
                descricao=f'{nomes} — {user.name}',
                valor=total_pedido,
                forma='dinheiro',
            ))

        db.session.commit()
        resp = {'ok': True, 'id': ag.id}
        if pedido:
            resp['pedido_id'] = pedido.id
            resp['total'] = float(pedido.total or 0)
        return jsonify(resp)

    ags = (Agendamento.query
           .options(joinedload(Agendamento.usuario), joinedload(Agendamento.funcionario))
           .order_by(Agendamento.data_hora.asc())
           .all())
    pedido_ids = [ag.pedido_id for ag in ags if ag.pedido_id]
    pedidos = (
        {p.id: p for p in
         Pedido.query.filter(Pedido.id.in_(pedido_ids))
                     .options(joinedload(Pedido.itens)).all()}
        if pedido_ids else {}
    )
    _, gestor_nome = _gestor_como_barbeiro()
    result = []
    for ag in ags:
        u = ag.usuario
        p = pedidos.get(ag.pedido_id)
        if ag.funcionario_id == 0:
            barbeiro = gestor_nome or 'Gestor'
        elif ag.funcionario:
            barbeiro = ag.funcionario.nome
        else:
            barbeiro = None
        result.append({
            'id': ag.id,
            'data_hora': ag.data_hora.isoformat(),
            'status': ag.status or 'ativo',
            'pedido_id': ag.pedido_id,
            'usuario': u.name if u else '—',
            'email': u.email if u else '—',
            'contato': u.contact if u else '—',
            'observacao': u.observation if u else None,
            'barbeiro': barbeiro,
            'pedido': pedido_dict(p) if p else None,
        })
    return jsonify(result)

@app.route('/api/entradas', methods=['GET'])
def api_entradas():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    entradas = EntradaMonetaria.query.order_by(EntradaMonetaria.criado_em.desc()).all()
    return jsonify([{
        'id': e.id, 'descricao': e.descricao, 'valor': e.valor,
        'forma': e.forma, 'criado_em': e.criado_em.isoformat(),
    } for e in entradas])

@app.route('/api/entradas', methods=['POST'])
def api_entrada_criar():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    d = request.get_json() or {}
    descricao = d.get('descricao', '').strip()
    valor     = _sf(d.get('valor', 0))
    forma     = d.get('forma', 'dinheiro').strip()
    formas_validas = {'dinheiro', 'pix', 'cartao_credito', 'cartao_debito', 'cartao'}
    if not descricao:
        return jsonify({'erro': 'descrição obrigatória'}), 400
    if valor <= 0:
        return jsonify({'erro': 'valor deve ser maior que zero'}), 400
    if forma not in formas_validas:
        forma = 'dinheiro'
    e = EntradaMonetaria(descricao=descricao, valor=valor, forma=forma)
    db.session.add(e)
    db.session.commit()
    return jsonify({'ok': True, 'id': e.id})

@app.route('/api/entradas/<int:eid>', methods=['DELETE'])
def api_entrada_deletar(eid):
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    e = db.session.get(EntradaMonetaria, eid)
    if not e:
        return jsonify({'erro': 'não encontrado'}), 404
    db.session.delete(e)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/fotos', methods=['GET'])
def api_fotos_listar():
    categoria = request.args.get('categoria')
    q = FotoServico.query
    if categoria:
        q = q.filter_by(categoria=categoria)
    fotos = q.order_by(FotoServico.criado_em.desc()).all()
    return jsonify([{
        'id': f.id,
        'categoria': f.categoria,
        'servico': f.servico,
        'url': f'/static/uploads/{f.filename}',
        'criado_em': f.criado_em.isoformat(),
    } for f in fotos])

@app.route('/api/fotos', methods=['POST'])
def api_fotos_upload():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    categoria = request.form.get('categoria', 'outros')
    servico   = request.form.get('servico', '').strip()
    arquivo   = request.files.get('foto')
    if not arquivo:
        return jsonify({'erro': 'nenhum arquivo enviado'}), 400
    ext = os.path.splitext(secure_filename(arquivo.filename))[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        return jsonify({'erro': 'formato inválido'}), 400
    if servico:
        existente = FotoServico.query.filter_by(servico=servico).first()
        if existente:
            old = os.path.join(UPLOAD_FOLDER, existente.filename)
            if os.path.exists(old):
                os.remove(old)
            db.session.delete(existente)
    filename = f"{uuid.uuid4().hex}{ext}"
    arquivo.save(os.path.join(UPLOAD_FOLDER, filename))
    foto = FotoServico(categoria=categoria, servico=servico or None, filename=filename)
    db.session.add(foto)
    db.session.commit()
    return jsonify({'ok': True, 'id': foto.id, 'url': f'/static/uploads/{filename}'})

@app.route('/api/fotos/<int:fid>', methods=['DELETE'])
def api_fotos_deletar(fid):
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    foto = db.session.get(FotoServico, fid)
    if not foto:
        return jsonify({'erro': 'não encontrado'}), 404
    caminho = os.path.join(UPLOAD_FOLDER, foto.filename)
    if os.path.exists(caminho):
        os.remove(caminho)
    db.session.delete(foto)
    db.session.commit()
    return jsonify({'ok': True})

LEMBRETES = [
    ('3d',  72),   # 3 dias  = 72h
    ('1d',  24),   # 1 dia   = 24h
    ('12h', 12),   # 12 horas
    ('1h',   1),   # 1 hora
]

def _corpo_lembrete(user_name, data_hora, tipo):
    dia_nome = DIAS_PT[data_hora.weekday()]
    data_fmt = f"{dia_nome}, {data_hora.day} de {MESES_PT[data_hora.month-1]}"
    hora_fmt = data_hora.strftime('%H:%M')
    aviso = {'3d': 'em 3 dias', '1d': 'amanhã', '12h': 'em 12 horas', '1h': 'em 1 hora'}[tipo]
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                background:#0f0f0f;color:#f0f0f0;padding:28px;border-radius:10px;">
      <h2 style="color:#C9A96E;margin-top:0;">✦ Lembrete de Agendamento</h2>
      <p>Olá, <strong>{user_name}</strong>!</p>
      <p>Seu agendamento está marcado para <strong>{aviso}</strong>:</p>
      <div style="background:#1a1a1a;border-left:4px solid #C9A96E;
                  padding:16px 20px;border-radius:6px;margin:20px 0;">
        <p style="margin:0;font-size:15px;color:#888;">📅 {data_fmt}</p>
        <p style="margin:8px 0 0;font-size:32px;font-weight:bold;
                  color:#C9A96E;letter-spacing:2px;">⏰ {hora_fmt}</p>
      </div>
      <p style="color:#888;font-size:13px;">
        Caso precise cancelar, acesse o site com até 1h de antecedência.
      </p>
    </div>
    """

def verificar_lembretes():
    with app.app_context():
        agora = datetime.utcnow()
        ags = (Agendamento.query
               .filter_by(status='ativo')
               .filter(Agendamento.data_hora > agora)
               .options(joinedload(Agendamento.usuario))
               .all())
        if not ags:
            return
        ag_ids = [ag.id for ag in ags]
        enviados = {
            (l.agendamento_id, l.tipo)
            for l in LembreteEnviado.query.filter(
                LembreteEnviado.agendamento_id.in_(ag_ids)).all()
        }
        for ag in ags:
            user = ag.usuario
            if not user or not user.receber_lembretes:
                continue
            diff_h = (ag.data_hora - agora).total_seconds() / 3600
            for tipo, horas in LEMBRETES:
                if abs(diff_h - horas) > 0.5:
                    continue
                if (ag.id, tipo) in enviados:
                    continue
                ok = _enviar_email(
                    user.email,
                    'Lembrete — Barbearia',
                    _corpo_lembrete(user.name, ag.data_hora, tipo)
                )
                if ok:
                    db.session.add(LembreteEnviado(agendamento_id=ag.id, tipo=tipo))
                    db.session.commit()
                    enviados.add((ag.id, tipo))
                    print(f'[LEMBRETE] {tipo} enviado para {user.email}')

@app.route('/api/testar-lembretes', methods=['POST'])
def testar_lembretes():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    with app.app_context():
        user = User.query.first()
        if not user:
            return jsonify({'erro': 'nenhum usuário cadastrado'}), 404
        ag = (Agendamento.query.filter_by(user_id=user.id, status='ativo')
              .order_by(Agendamento.data_hora.asc()).first())
        if not ag:
            return jsonify({'erro': 'nenhum agendamento ativo para este usuário'}), 404
        enviados = []
        for tipo, _ in LEMBRETES:
            ok = _enviar_email(
                user.email,
                'Lembrete — Barbearia',
                _corpo_lembrete(user.name, ag.data_hora, tipo)
            )
            if ok:
                enviados.append(tipo)
        return jsonify({'ok': True, 'enviados': enviados, 'email': user.email})

def verificar_assinaturas():
    with app.app_context():
        agora = datetime.utcnow()
        vencidas = Assinatura.query.filter(
            Assinatura.status == 'ativo',
            Assinatura.vencimento < agora
        ).all()
        for a in vencidas:
            a.status = 'suspenso'
            tenant = db.session.get(Tenant, a.tenant_id)
            if tenant:
                tenant.assinatura_ativa = False
                _enviar_email(tenant.email,
                    'Assinatura vencida — Barbearia Online',
                    f'''<div style="font-family:Arial,sans-serif;
                      background:#0f0f0f;color:#f0f0f0;padding:28px;
                      border-radius:10px;">
                      <h2 style="color:#C9A96E;">Assinatura vencida</h2>
                      <p>Olá {tenant.nome}, sua assinatura venceu.</p>
                      <p>Renove em
                        <a href="https://seuapp.com.br/renovar/{tenant.slug}"
                           style="color:#C9A96E;">seuapp.com.br</a>
                        para reativar seu site.
                      </p></div>''')
        db.session.commit()

def limpar_guests():
    with app.app_context():
        limite = datetime.utcnow() - timedelta(hours=24)
        guests = User.query.filter_by(guest=True).filter(User.criado_em < limite).all()
        for g in guests:
            tem_ag = Agendamento.query.filter_by(user_id=g.id, status='ativo').first()
            if not tem_ag:
                db.session.delete(g)
        db.session.commit()

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(verificar_lembretes, 'interval', minutes=30)
scheduler.add_job(verificar_assinaturas, 'interval', hours=12)
scheduler.add_job(limpar_guests, 'interval', hours=24)
scheduler.start()

@app.route('/admin/banco')
def admin_banco():
    from sqlalchemy import inspect, text
    senha = request.args.get('key', '')
    if senha != API_TOKEN:
        return '''
        <html><body style="background:#0c0c0c;color:#f0ece4;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <form method="get" style="text-align:center">
            <h2 style="color:#c9a96e">🔒 Banco de Dados</h2>
            <input name="key" type="password" placeholder="Token de acesso"
                style="padding:10px 16px;border-radius:6px;border:1px solid #333;background:#1e1e1e;color:#f0ece4;font-size:15px;width:260px">
            <br><br>
            <button type="submit"
                style="padding:10px 28px;background:#c9a96e;color:#000;border:none;border-radius:6px;font-weight:bold;cursor:pointer;font-size:15px">
                Entrar
            </button>
        </form></body></html>''', 401

    insp   = inspect(db.engine)
    tabelas = insp.get_table_names()
    html_tabelas = ''
    for tabela in tabelas:
        rows = db.session.execute(text(f'SELECT * FROM "{tabela}" LIMIT 200')).fetchall()
        cols = [c['name'] for c in insp.get_columns(tabela)]
        thead = ''.join(f'<th>{c}</th>' for c in cols)
        tbody = ''
        for row in rows:
            tbody += '<tr>' + ''.join(f'<td>{v}</td>' for v in row) + '</tr>'
        total = db.session.execute(text(f'SELECT COUNT(*) FROM "{tabela}"')).scalar()
        html_tabelas += f'''
        <div class="tabela-bloco">
            <h3>{tabela} <span class="badge">{total} registros</span></h3>
            <div class="table-wrap">
                <table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>
            </div>
        </div>'''

    return f'''<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<title>Banco de Dados — Barbearia</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0c0c0c; color: #f0ece4; font-family: sans-serif; padding: 24px; }}
  h1 {{ color: #c9a96e; margin-bottom: 24px; }}
  h3 {{ color: #c9a96e; margin-bottom: 8px; font-size: 15px; letter-spacing: 1px; }}
  .badge {{ background: #1e1e1e; border: 1px solid #333; border-radius: 12px;
            padding: 2px 10px; font-size: 12px; color: #888; margin-left: 8px; }}
  .tabela-bloco {{ margin-bottom: 36px; }}
  .table-wrap {{ overflow-x: auto; border-radius: 8px; border: 1px solid #2a2a2a; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  thead {{ background: #161616; }}
  th {{ padding: 10px 14px; text-align: left; color: #c9a96e; border-bottom: 1px solid #2a2a2a;
        white-space: nowrap; }}
  td {{ padding: 8px 14px; border-bottom: 1px solid #1a1a1a; color: #ccc; max-width: 300px;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  tr:hover td {{ background: #141414; }}
  nav {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 28px; }}
  nav a {{ background: #1e1e1e; border: 1px solid #2a2a2a; color: #c9a96e; padding: 6px 14px;
           border-radius: 6px; text-decoration: none; font-size: 13px; }}
  nav a:hover {{ background: #c9a96e; color: #000; }}
</style>
</head>
<body>
<h1>✦ Banco de Dados</h1>
<nav>{''.join(f'<a href="#{t}">{t}</a>' for t in tabelas)}</nav>
{html_tabelas}
</body></html>'''

@app.route('/api/ping', methods=['GET'])
def ping():
    return jsonify({'ok': True, 'app': 'barbearia'})

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data  = request.get_json(force=True) or {}
    email = data.get('email', '').strip().lower()
    senha = data.get('password', '')
    tenant = Tenant.query.filter_by(email=email, ativo=True).first()
    if not tenant or not check_password_hash(tenant.password, senha):
        return jsonify({'erro': 'credenciais inválidas'}), 401
    token = _gerar_token(tenant.id, 0)
    return jsonify({'ok': True, 'token': token, 'nome': tenant.nome, 'tipo': 'admin'})

@app.route('/api/funcionarios/login', methods=['POST'])
def api_funcionarios_login():
    data  = request.get_json(force=True) or {}
    email = data.get('email', '').strip().lower()
    senha = data.get('password', '')
    f = Funcionario.query.filter_by(email=email, ativo=True).first()
    if not f or not check_password_hash(f.password, senha):
        return jsonify({'erro': 'credenciais inválidas'}), 401
    token = _gerar_token(f.tenant_id, f.id)
    return jsonify({
        'ok': True, 'tipo': 'funcionario',
        'nome': f.nome, 'token': token,
        'permissoes': f.to_dict()['permissoes'],
    })

@app.route('/api/funcionarios', methods=['GET'])
def api_funcionarios_listar():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    funs = Funcionario.query.order_by(Funcionario.nome).all()
    return jsonify([f.to_dict() for f in funs])

@app.route('/api/funcionarios', methods=['POST'])
def api_funcionarios_criar():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    d = request.get_json() or {}
    email_func = d.get('email', '').strip().lower()
    if email_func and Funcionario.query.filter_by(email=email_func).first():
        return jsonify({'erro': 'E-mail já cadastrado'}), 400
    perms = d.get('permissoes', {})
    f = Funcionario(
        nome=d.get('nome', '').strip(),
        email=email_func,
        password=generate_password_hash(d.get('senha', '')),
        telefone=d.get('telefone', '').strip() or None,
        perm_agendamentos=perms.get('agendamentos', True),
        perm_calendario=perms.get('calendario', True),
        perm_marcar=perms.get('marcar', False),
        perm_clientes=perms.get('clientes', False),
        perm_servicos=perms.get('servicos', False),
        perm_precos=perms.get('precos', False),
        perm_pedidos=perms.get('pedidos', False),
        perm_entradas=perms.get('entradas', False),
        perm_fotos=perms.get('fotos', False),
    )
    db.session.add(f)
    db.session.commit()
    return jsonify({'ok': True, 'id': f.id})

@app.route('/api/funcionarios/<int:fid>', methods=['PUT', 'DELETE'])
def api_funcionario_detalhe(fid):
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    f = db.session.get(Funcionario, fid)
    if not f:
        return jsonify({'erro': 'não encontrado'}), 404
    if request.method == 'DELETE':
        f.ativo = False
        db.session.commit()
        return jsonify({'ok': True})
    d = request.get_json() or {}
    if 'nome' in d:
        f.nome = d['nome'].strip()
    if 'telefone' in d:
        f.telefone = d['telefone'].strip() or None
    if 'ativo' in d:
        f.ativo = bool(d['ativo'])
    if 'nova_senha' in d and d['nova_senha']:
        f.password = generate_password_hash(d['nova_senha'])
    perms = d.get('permissoes', {})
    if perms:
        f.perm_agendamentos = perms.get('agendamentos', f.perm_agendamentos)
        f.perm_calendario   = perms.get('calendario',   f.perm_calendario)
        f.perm_marcar       = perms.get('marcar',       f.perm_marcar)
        f.perm_clientes     = perms.get('clientes',     f.perm_clientes)
        f.perm_servicos     = perms.get('servicos',     f.perm_servicos)
        f.perm_precos       = perms.get('precos',       f.perm_precos)
        f.perm_pedidos      = perms.get('pedidos',      f.perm_pedidos)
        f.perm_entradas     = perms.get('entradas',     f.perm_entradas)
        f.perm_fotos        = perms.get('fotos',        f.perm_fotos)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/funcionarios/<int:fid>/foto', methods=['POST', 'DELETE'])
def api_funcionario_foto(fid):
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    f = db.session.get(Funcionario, fid)
    if not f:
        return jsonify({'erro': 'não encontrado'}), 404
    if request.method == 'DELETE':
        if f.foto:
            p = os.path.join(UPLOAD_FOLDER, f.foto)
            if os.path.exists(p): os.remove(p)
            f.foto = None
            db.session.commit()
        return jsonify({'ok': True})
    arquivo = request.files.get('foto')
    if not arquivo:
        return jsonify({'erro': 'nenhum arquivo'}), 400
    ext = os.path.splitext(secure_filename(arquivo.filename))[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
        return jsonify({'erro': 'formato inválido'}), 400
    if f.foto:
        old = os.path.join(UPLOAD_FOLDER, f.foto)
        if os.path.exists(old): os.remove(old)
    filename = f"func_{fid}_{uuid.uuid4().hex}{ext}"
    arquivo.save(os.path.join(UPLOAD_FOLDER, filename))
    f.foto = filename
    db.session.commit()
    return jsonify({'ok': True, 'foto_url': f'/static/uploads/{filename}'})

@app.route('/api/gestor-foto', methods=['POST', 'DELETE'])
def api_gestor_foto():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    _tid = _api_tid()
    if request.method == 'DELETE':
        s = _get_setting('gestor_foto', _tid)
        if s and s.value:
            p = os.path.join(UPLOAD_FOLDER, s.value)
            if os.path.exists(p): os.remove(p)
            s.value = ''
            db.session.commit()
        return jsonify({'ok': True})
    arquivo = request.files.get('foto')
    if not arquivo:
        return jsonify({'erro': 'nenhum arquivo'}), 400
    ext = os.path.splitext(secure_filename(arquivo.filename))[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
        return jsonify({'erro': 'formato inválido'}), 400
    s = _get_setting('gestor_foto', _tid)
    if s and s.value:
        old = os.path.join(UPLOAD_FOLDER, s.value)
        if os.path.exists(old): os.remove(old)
    filename = f"gestor_{uuid.uuid4().hex}{ext}"
    arquivo.save(os.path.join(UPLOAD_FOLDER, filename))
    _upsert_setting('gestor_foto', filename, _tid)
    db.session.commit()
    return jsonify({'ok': True, 'foto_url': f'/static/uploads/{filename}'})

def _enviar_boas_vindas(tenant):
    base = os.environ.get('APP_BASE_URL', 'https://ibarber.com.br')
    site_url  = f'{base}/{tenant.slug}'
    painel_url = f'{base}/gestao/login'
    corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;
      margin:0 auto;background:#0f0f0f;color:#f0f0f0;
      padding:32px;border-radius:12px;">
      <h2 style="color:#C9A96E;margin-bottom:4px;">✦ iBarber</h2>
      <h3 style="font-weight:400;color:#aaa;margin-bottom:24px;">Sua barbearia está no ar!</h3>
      <p style="margin-bottom:16px;">Olá, <strong>{tenant.nome}</strong>! Tudo pronto. 🎉</p>
      <p style="color:#888;margin-bottom:8px;">Site dos seus clientes:</p>
      <a href="{site_url}" style="display:block;background:#1a1a1a;color:#C9A96E;
         padding:12px 16px;border-radius:8px;margin-bottom:16px;word-break:break-all;">
         {site_url}
      </a>
      <p style="color:#888;margin-bottom:8px;">Seu painel de gestão:</p>
      <a href="{painel_url}" style="display:block;background:#1a1a1a;color:#C9A96E;
         padding:12px 16px;border-radius:8px;margin-bottom:24px;">
         {painel_url}
      </a>
      <p style="color:#666;font-size:13px;border-top:1px solid #222;padding-top:16px;">
        Baixe o app <strong style="color:#C9A96E;">iBarber Admin</strong> e faça login
        com seu e-mail para gerenciar agendamentos, funcionários e preços pelo celular.
      </p>
    </div>
    """
    _enviar_email(tenant.email, '✦ Sua barbearia está no ar — iBarber', corpo)

@app.route('/landing')
def landing():
    return render_template('landing.html', hide_fabs=True)

# ─── Site de Gestão (HTML/Flask) ──────────────────────────────────────────────

def _gestao_login_required():
    """Retorna None se ok, ou um redirect se não autenticado."""
    if not session.get('gestao_tenant_id'):
        return redirect(url_for('gestao_login'))
    return None

def _gestao_tenant():
    return db.session.get(Tenant, session.get('gestao_tenant_id'))

@app.route('/gestao/login', methods=['GET', 'POST'])
def gestao_login():
    if session.get('gestao_tenant_id'):
        return redirect(url_for('gestao_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        tenant = Tenant.query.filter_by(email=email, ativo=True).first()
        if tenant and check_password_hash(tenant.password, senha):
            session['gestao_tenant_id'] = tenant.id
            session['gestao_nome'] = tenant.nome
            return redirect(url_for('gestao_dashboard'))
        flash('E-mail ou senha incorretos.', 'error')
    return render_template('gestao/login.html')

@app.route('/gestao/logout')
def gestao_logout():
    session.pop('gestao_tenant_id', None)
    session.pop('gestao_nome', None)
    return redirect(url_for('gestao_login'))

@app.route('/gestao')
@app.route('/gestao/')
def gestao_dashboard():
    redir = _gestao_login_required()
    if redir: return redir
    _, gestor_nome = _gestor_como_barbeiro()
    hoje = datetime.utcnow().date()
    MESES = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    DIAS_PT = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']
    # Todos agendamentos (últimos 60 dias + próximos 30) para o JS filtrar por dia
    janela_ini = datetime.utcnow() - timedelta(days=60)
    janela_fim = datetime.utcnow() + timedelta(days=30)
    ags = (Agendamento.query
           .options(joinedload(Agendamento.usuario), joinedload(Agendamento.funcionario))
           .filter(Agendamento.data_hora >= janela_ini, Agendamento.data_hora <= janela_fim)
           .order_by(Agendamento.data_hora.asc()).all())
    pedido_ids = [ag.pedido_id for ag in ags if ag.pedido_id]
    pedidos_map = {p.id: p for p in Pedido.query.filter(Pedido.id.in_(pedido_ids)).options(joinedload(Pedido.itens)).all()} if pedido_ids else {}
    # Serializar para JSON
    ags_json = []
    for ag in ags:
        u = ag.usuario
        p = pedidos_map.get(ag.pedido_id)
        if ag.funcionario_id == 0:
            barbeiro = gestor_nome or 'Gestor'
        elif ag.funcionario:
            barbeiro = ag.funcionario.nome
        else:
            barbeiro = None
        servico_nome = p.itens[0].nome if p and p.itens else None
        ags_json.append({
            'id': ag.id,
            'data_hora': ag.data_hora.strftime('%Y-%m-%dT%H:%M:%S'),
            'status': ag.status or 'ativo',
            'usuario': u.name if u else '—',
            'email': u.email if u else '—',
            'contato': u.contact if u else '—',
            'observacao': u.observation if u else None,
            'barbeiro': barbeiro,
            'servico': servico_nome,
        })
    # Datas com agendamentos (para ponto no strip)
    datas_com_ag = {ag['data_hora'][:10] for ag in ags_json if ag['status'] != 'cancelado'}
    # Próximos dias para o strip (30 dias, sem domingo)
    proximos_dias = []
    cursor = hoje + timedelta(days=1)
    while len(proximos_dias) < 30:
        if cursor.weekday() != 6:  # 6 = domingo
            proximos_dias.append({
                'data': cursor.strftime('%Y-%m-%d'),
                'dia': cursor.day,
                'dia_semana': DIAS_PT[cursor.weekday()],
                'mes': MESES[cursor.month - 1],
                'tem_ag': cursor.strftime('%Y-%m-%d') in datas_com_ag,
            })
        cursor += timedelta(days=1)
    tenant = _gestao_tenant()
    return render_template('gestao/dashboard.html',
        active='dashboard',
        hoje=hoje.strftime('%Y-%m-%d'),
        hoje_dia=hoje.day,
        hoje_mes=MESES[hoje.month - 1],
        proximos_dias=proximos_dias,
        agendamentos_json=json.dumps(ags_json, ensure_ascii=False),
        token=_gerar_token(tenant.id, 0),
    )

@app.route('/gestao/agendamentos', methods=['GET'])
def gestao_agendamentos():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    # Gera token de admin para o JS usar na API
    token = _gerar_token(tenant.id, 0)
    clientes = User.query.order_by(User.name).all()
    clientes_json = json.dumps([
        {'id': c.id, 'name': c.name, 'email': c.email, 'contact': c.contact or ''}
        for c in clientes
    ], ensure_ascii=False)
    return render_template('gestao/agendamentos.html', active='agendamentos',
                           token=token, clientes_json=clientes_json)

@app.route('/gestao/agendamentos/<int:ag_id>/status', methods=['POST'])
def gestao_agendamento_status(ag_id):
    redir = _gestao_login_required()
    if redir: return redir
    ag = db.session.get(Agendamento, ag_id)
    if ag:
        ag.status = request.form.get('status', 'ativo')
        db.session.commit()
    return redirect(url_for('gestao_agendamentos'))

@app.route('/gestao/pedidos')
def gestao_pedidos():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    token = _gerar_token(tenant.id, 0)
    pedidos = (Pedido.query
               .options(joinedload(Pedido.usuario), joinedload(Pedido.itens))
               .order_by(Pedido.criado_em.desc()).limit(500).all())
    pedidos_json = json.dumps([{
        'id': p.id, 'status': p.status, 'total': p.total,
        'criado_em': p.criado_em.strftime('%Y-%m-%dT%H:%M:%S'),
        'usuario': p.usuario.name if p.usuario else '—',
        'itens': [{'nome': i.nome, 'categoria': i.categoria or '', 'preco': i.preco} for i in p.itens],
    } for p in pedidos], ensure_ascii=False)
    return render_template('gestao/pedidos.html', active='pedidos', pedidos_json=pedidos_json, token=token)

@app.route('/gestao/clientes')
def gestao_clientes():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    token = _gerar_token(tenant.id, 0)
    clientes = User.query.order_by(User.name).all()
    clientes_json = json.dumps([{
        'id': c.id, 'name': c.name, 'email': c.email,
        'contact': c.contact or '', 'criado_em': c.criado_em.strftime('%Y-%m-%d'),
    } for c in clientes], ensure_ascii=False)
    return render_template('gestao/clientes.html', active='clientes', clientes_json=clientes_json, token=token)

@app.route('/gestao/clientes/<int:uid>')
def gestao_cliente_detalhe(uid):
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    token = _gerar_token(tenant.id, 0)
    return render_template('gestao/cliente_detalhe.html', active='clientes', uid=uid, token=token)

@app.route('/gestao/entradas', methods=['GET', 'POST'])
def gestao_entradas():
    redir = _gestao_login_required()
    if redir: return redir
    if request.method == 'POST':
        desc = request.form.get('descricao', '').strip()
        valor = float(request.form.get('valor', 0) or 0)
        forma = request.form.get('forma', 'dinheiro')
        if desc and valor > 0:
            db.session.add(EntradaMonetaria(descricao=desc, valor=valor, forma=forma))
            db.session.commit()
        return redirect(url_for('gestao_entradas'))
    ags = (Agendamento.query.options(joinedload(Agendamento.usuario))
           .order_by(Agendamento.data_hora.desc()).all())
    pedido_ids = [ag.pedido_id for ag in ags if ag.pedido_id]
    pedidos_map = {p.id: p for p in Pedido.query.filter(Pedido.id.in_(pedido_ids))
                   .options(joinedload(Pedido.itens)).all()} if pedido_ids else {}
    ags_json = []
    for ag in ags:
        p = pedidos_map.get(ag.pedido_id)
        if p and p.total and p.total > 0:
            ags_json.append({
                'data_hora': ag.data_hora.strftime('%Y-%m-%dT%H:%M:%S'),
                'total': p.total,
                'usuario': ag.usuario.name if ag.usuario else '—',
            })
    entradas = EntradaMonetaria.query.order_by(EntradaMonetaria.criado_em.desc()).all()
    entradas_json = json.dumps([{
        'id': e.id, 'descricao': e.descricao, 'valor': e.valor,
        'forma': e.forma or 'dinheiro', 'criado_em': e.criado_em.strftime('%Y-%m-%dT%H:%M:%S'),
    } for e in entradas], ensure_ascii=False)
    return render_template('gestao/entradas.html', active='entradas',
                           entradas_json=entradas_json,
                           agendamentos_json=json.dumps(ags_json, ensure_ascii=False))

@app.route('/gestao/entradas/<int:eid>/deletar', methods=['POST'])
def gestao_entrada_deletar(eid):
    redir = _gestao_login_required()
    if redir: return redir
    e = db.session.get(EntradaMonetaria, eid)
    if e:
        db.session.delete(e)
        db.session.commit()
    return redirect(url_for('gestao_entradas'))

@app.route('/gestao/funcionarios')
def gestao_funcionarios():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    token = _gerar_token(tenant.id, 0)
    return render_template('gestao/funcionarios.html', active='funcionarios', token=token)

@app.route('/gestao/servicos')
def gestao_servicos():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    token = _gerar_token(tenant.id, 0)
    return render_template('gestao/servicos.html', active='servicos', token=token)

@app.route('/gestao/precos', methods=['GET', 'POST'])
def gestao_precos():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    token = _gerar_token(tenant.id, 0)
    servicos = Servico.query.filter_by(ativo=True).order_by(Servico.nome).all()
    if request.method == 'POST':
        for s in servicos:
            val = request.form.get(f'preco_{s.id}', '').strip()
            if val:
                try: s.preco = float(val.replace(',', '.'))
                except ValueError: pass
        db.session.commit()
        flash('Preços atualizados.', 'success')
        return redirect(url_for('gestao_precos'))
    return render_template('gestao/precos.html', active='precos', servicos=servicos, token=token)

@app.route('/gestao/fotos')
def gestao_fotos():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    token = _gerar_token(tenant.id, 0)
    return render_template('gestao/fotos.html', active='fotos', token=token)

@app.route('/gestao/horarios', methods=['GET', 'POST'])
def gestao_horarios():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    token = _gerar_token(tenant.id, 0)
    if request.method == 'POST':
        DIAS_KEYS = ['seg','ter','qua','qui','sex','sab','dom']
        horarios = {}
        for k in DIAS_KEYS:
            horarios[k] = {
                'aberto': bool(request.form.get(f'aberto_{k}')),
                'abertura': request.form.get(f'abertura_{k}', '08:00'),
                'fechamento': request.form.get(f'fechamento_{k}', '18:00'),
            }
        _tid = tenant.id
        _upsert_setting('horario_funcionamento', json.dumps(horarios, ensure_ascii=False), _tid)
        _upsert_setting('intervalo_minutos', request.form.get('slot_minutos', '40'), _tid)
        _upsert_setting('dias_agenda', request.form.get('dias_agenda', '20'), _tid)
        db.session.commit()
        flash('Horários salvos.', 'success')
        return redirect(url_for('gestao_horarios'))
    _tid = tenant.id
    config_s = _get_setting('horario_funcionamento', _tid)
    config_dias = json.loads(config_s.value) if config_s and config_s.value else {}
    slot_s = _get_setting('intervalo_minutos', _tid)
    dias_ag_s = _get_setting('dias_agenda', _tid)
    dias_fechados_s = _get_setting('dias_fechados', _tid)
    dias_fechados = json.loads(dias_fechados_s.value) if dias_fechados_s and dias_fechados_s.value else []
    especiais = HorarioEspecial.query.order_by(HorarioEspecial.data).all()
    config_geral = {
        'slot_minutos': int(slot_s.value) if slot_s and slot_s.value else 40,
        'dias_agenda': int(dias_ag_s.value) if dias_ag_s and dias_ag_s.value else 20,
    }
    especiais_json = json.dumps([{'id': e.id, 'data': e.data,
        'abertura': e.abertura, 'fechamento': e.fechamento} for e in especiais])
    return render_template('gestao/horarios.html', active='horarios',
                           config_dias=config_dias, config_geral=config_geral,
                           dias_fechados=dias_fechados, especiais_json=especiais_json, token=token)

@app.route('/gestao/contato', methods=['GET', 'POST'])
def gestao_contato():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    if request.method == 'POST':
        tenant.whatsapp = request.form.get('whatsapp', '').strip() or None
        tenant.maps_url = request.form.get('maps_url', '').strip() or None
        tenant.contato  = request.form.get('contato', '').strip() or None
        db.session.commit()
        flash('Contato atualizado.', 'success')
        return redirect(url_for('gestao_contato'))
    return render_template('gestao/contato.html', active='contato', tenant=tenant)

@app.route('/gestao/credenciais', methods=['GET', 'POST'])
def gestao_credenciais():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip().lower()
        senha_atual = request.form.get('senha_atual', '')
        nova_senha  = request.form.get('nova_senha', '')
        confirmar   = request.form.get('confirmar_senha', '')
        if nome: tenant.nome = nome
        if email: tenant.email = email
        if nova_senha:
            if not check_password_hash(tenant.password, senha_atual):
                flash('Senha atual incorreta.', 'error')
                return redirect(url_for('gestao_credenciais'))
            if nova_senha != confirmar:
                flash('As senhas não coincidem.', 'error')
                return redirect(url_for('gestao_credenciais'))
            tenant.password = generate_password_hash(nova_senha)
        db.session.commit()
        session['gestao_nome'] = tenant.nome
        flash('Dados atualizados.', 'success')
        return redirect(url_for('gestao_credenciais'))
    def _sv(key): s = _get_setting(key, tenant.id); return s.value if s else ''
    return render_template('gestao/credenciais.html',
        active='credenciais', tenant=tenant,
        token=_gerar_token(tenant.id, 0),
        pix_chave=_sv('pix_chave'),
        mp_token=_sv('mp_token'),
        mp_public_key=_sv('mp_public_key'),
        pix_ativo=(_sv('pix_ativo') == '1'),
        cartao_ativo=(_sv('cartao_ativo') == '1'),
    )

@app.route('/gestao/graficos')
def gestao_graficos():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    return render_template('gestao/graficos.html',
        active='graficos',
        token=_gerar_token(tenant.id, 0))

@app.route('/gestao/calendario')
def gestao_calendario():
    redir = _gestao_login_required()
    if redir: return redir
    tenant = _gestao_tenant()
    return render_template('gestao/calendario.html',
        active='calendario',
        token=_gerar_token(tenant.id, 0))

@app.route('/personalizar')
def personalizar():
    from flask import make_response
    resp = make_response(render_template('personalizar.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp

@app.route('/planos')
def planos():
    return render_template('planos.html')

@app.route('/cadastro')
def cadastro():
    return render_template('cadastro.html')

@app.route('/pagamento')
def pagamento():
    return render_template('pagamento.html')

@app.route('/api/personalizar/upload', methods=['POST'])
def api_personalizar_upload():
    arquivo = request.files.get('imagem')
    if not arquivo:
        return jsonify({'erro': 'nenhum arquivo'}), 400
    ext = os.path.splitext(secure_filename(arquivo.filename))[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        return jsonify({'erro': 'formato inválido'}), 400
    filename = f"pers_{uuid.uuid4().hex}{ext}"
    arquivo.save(os.path.join(UPLOAD_FOLDER, filename))
    url = request.host_url.rstrip('/') + f'/static/uploads/{filename}'
    return jsonify({'ok': True, 'url': url})

@app.route('/api/cadastro-personalizar', methods=['POST'])
def api_cadastro_personalizar():
    import re
    d = request.get_json(force=True) or {}
    slug = d.get('slug', '').lower().strip()
    if not re.match(r'^[a-z0-9-]{3,30}$', slug):
        return jsonify({'erro': 'slug inválido'}), 400
    if Tenant.query.filter_by(slug=slug).first():
        return jsonify({'erro': 'slug já em uso'}), 400
    email = d.get('email', '').strip().lower()
    if not email:
        return jsonify({'erro': 'e-mail obrigatório'}), 400
    if Tenant.query.filter_by(email=email).first():
        return jsonify({'erro': 'e-mail já cadastrado'}), 400
    senha = d.get('senha', '').strip()
    if len(senha) < 6:
        return jsonify({'erro': 'senha deve ter ao menos 6 caracteres'}), 400
    tenant = Tenant(
        slug=slug,
        nome=d.get('nome', '').strip(),
        email=email,
        password=generate_password_hash(senha),
        whatsapp=d.get('whatsapp', '').strip() or None,
        tema=json.dumps(d.get('tema', {})),
        ativo=True,
        assinatura_ativa=False,
    )
    db.session.add(tenant)
    db.session.commit()
    return jsonify({'ok': True, 'slug': slug, 'tenant_id': tenant.id})

@app.route('/api/pagamento/status')
def api_pagamento_status():
    tenant_id = request.args.get('tenant_id', type=int)
    if not tenant_id:
        return jsonify({'ativo': False}), 400
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return jsonify({'ativo': False}), 404
    return jsonify({'ativo': bool(tenant.assinatura_ativa)})

@app.route('/repersonalizar')
def repersonalizar():
    return render_template('repersonalizar.html')

@app.route('/api/repersonalizar/auth', methods=['POST'])
def api_repersonalizar_auth():
    d = request.get_json(force=True) or {}
    email = d.get('email', '').strip().lower()
    senha = d.get('senha', '')
    tenant = Tenant.query.filter_by(email=email).first()
    if not tenant or not check_password_hash(tenant.password, senha):
        return jsonify({'erro': 'E-mail ou senha incorretos'}), 401
    if not tenant.assinatura_ativa:
        return jsonify({'erro': 'Conta sem assinatura ativa'}), 403
    tema = json.loads(tenant.tema) if tenant.tema else {}
    return jsonify({
        'ok': True,
        'tenant_id': tenant.id,
        'slug': tenant.slug,
        'nome': tenant.nome,
        'tema': tema,
        'editacoes': tenant.tema_editacoes or 0,
    })

@app.route('/api/repersonalizar/salvar', methods=['POST'])
def api_repersonalizar_salvar():
    d = request.get_json(force=True) or {}
    tenant = db.session.get(Tenant, d.get('tenant_id'))
    if not tenant:
        return jsonify({'erro': 'Tenant não encontrado'}), 404
    if not tenant.assinatura_ativa:
        return jsonify({'erro': 'Assinatura inativa'}), 403
    tema_json = json.dumps(d.get('tema', {}))
    if (tenant.tema_editacoes or 0) >= 1:
        tenant.tema_pendente = tema_json
        db.session.commit()
        return jsonify({'ok': False, 'precisa_pagar': True, 'preco': 5.00})
    tenant.tema = tema_json
    tenant.tema_editacoes = 1
    db.session.commit()
    return jsonify({'ok': True, 'gratis': True})

@app.route('/api/repersonalizar/status')
def api_repersonalizar_status():
    tenant_id = request.args.get('tenant_id', type=int)
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return jsonify({'editacoes': 0}), 404
    return jsonify({'editacoes': tenant.tema_editacoes or 0})

@app.route('/api/pagamento/criar-tema', methods=['POST'])
def api_pagamento_criar_tema():
    d = request.get_json(force=True) or {}
    tenant = db.session.get(Tenant, d.get('tenant_id'))
    if not tenant:
        return jsonify({'erro': 'tenant não encontrado'}), 404
    mp_token = get_mp_token()
    preference = {
        'items': [{'title': 'BarberOS — Edição de Tema', 'quantity': 1,
                   'currency_id': 'BRL', 'unit_price': 5.00}],
        'back_urls': {
            'success': f'{request.host_url}repersonalizar?pago={tenant.slug}',
            'failure': f'{request.host_url}repersonalizar',
            'pending': f'{request.host_url}repersonalizar',
        },
        'auto_return': 'approved',
        'notification_url': f'{request.host_url}api/pagamento/webhook',
        'metadata': {'tenant_id': tenant.id, 'tipo': 'tema'},
    }
    res = req_http.post(
        'https://api.mercadopago.com/checkout/preferences',
        headers={'Authorization': f'Bearer {mp_token}', 'Content-Type': 'application/json'},
        json=preference,
    )
    data = res.json()
    return jsonify({
        'ok': True,
        'preference_id': data.get('id'),
        'init_point': data.get('init_point'),
        'pix_url': data.get('point_of_interaction', {})
                       .get('transaction_data', {}).get('qr_code_base64', None),
        'pix_code': data.get('point_of_interaction', {})
                        .get('transaction_data', {}).get('qr_code', None),
    })

@app.route('/sucesso/<slug>')
def sucesso(slug):
    return render_template('sucesso.html', hide_fabs=True)

@app.route('/falha')
def falha():
    return render_template('falha.html', hide_fabs=True)

@app.route('/pendente')
def pendente():
    return render_template('falha.html', hide_fabs=True)

@app.route('/api/cadastro', methods=['POST'])
def api_cadastro():
    import re
    d = request.get_json(force=True) or {}
    slug = d.get('slug', '').lower().strip()
    if not re.match(r'^[a-z0-9-]{3,30}$', slug):
        return jsonify({'erro': 'slug inválido'}), 400
    if Tenant.query.filter_by(slug=slug).first():
        return jsonify({'erro': 'slug já em uso'}), 400
    email = d.get('email', '').strip().lower()
    if Tenant.query.filter_by(email=email).first():
        return jsonify({'erro': 'e-mail já cadastrado'}), 400
    tenant = Tenant(
        slug=slug,
        nome=d.get('nome', '').strip(),
        email=email,
        password=generate_password_hash(d.get('senha', '')),
        contato=d.get('contato', '').strip() or None,
        tema=json.dumps(d.get('tema', {})),
        mail_user=(d.get('mail_user') or '').strip() or None,
        mail_password=(d.get('mail_password') or '').strip() or None,
        ativo=True,
        assinatura_ativa=True,
    )
    db.session.add(tenant)
    db.session.commit()
    return jsonify({'ok': True, 'tenant_id': tenant.id, 'slug': slug})


@app.route('/api/pagamento/cartao', methods=['POST'])
def api_pagamento_cartao():
    d = request.get_json(force=True) or {}
    token        = d.get('token')
    installments = int(d.get('installments', 1))
    pm_id        = d.get('payment_method_id', '')
    issuer_id    = d.get('issuer_id')
    payer        = d.get('payer', {})
    tenant_id    = d.get('tenant_id')
    plano        = d.get('plano', 'mensal')
    if not token or not tenant_id:
        return jsonify({'erro': 'dados incompletos'}), 400
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return jsonify({'erro': 'tenant não encontrado'}), 404
    mp_token = get_mp_token()
    if not mp_token:
        return jsonify({'erro': 'credenciais não configuradas'}), 400
    amount = PLANOS.get(plano, {}).get('total', 49.0)
    payload = {
        'transaction_amount': float(amount),
        'token':              token,
        'description':        f'BarberOS — Plano {plano.capitalize()}',
        'installments':       installments,
        'payment_method_id':  pm_id,
        'payer':              {'email': payer.get('email', tenant.email)},
        'metadata':           {'tenant_id': tenant.id, 'plano': plano},
    }
    if issuer_id:
        payload['issuer_id'] = issuer_id
    res = req_http.post(
        'https://api.mercadopago.com/v1/payments',
        headers={'Authorization': f'Bearer {mp_token}',
                 'Content-Type':  'application/json',
                 'X-Idempotency-Key': str(uuid.uuid4())},
        json=payload, timeout=20,
    )
    data = res.json()
    status = data.get('status', '')
    if status in ('approved', 'in_process', 'pending'):
        if status == 'approved':
            assinatura = Assinatura.query.filter_by(tenant_id=tenant.id, status='pendente').first()
            if not assinatura:
                meses = PLANOS.get(plano, {}).get('meses', 1)
                assinatura = Assinatura(
                    tenant_id=tenant.id, plano=plano,
                    valor_total=PLANOS[plano]['total'],
                    valor_mensal=PLANOS[plano]['mensal'],
                    status='ativo',
                    mp_payment_id=str(data.get('id', '')),
                    inicio=datetime.utcnow(),
                    vencimento=datetime.utcnow() + timedelta(days=30 * meses),
                )
                db.session.add(assinatura)
            else:
                meses = PLANOS.get(plano, {}).get('meses', 1)
                assinatura.status      = 'ativo'
                assinatura.mp_payment_id = str(data.get('id', ''))
                assinatura.inicio      = datetime.utcnow()
                assinatura.vencimento  = datetime.utcnow() + timedelta(days=30 * meses)
            tenant.ativo = True
            tenant.assinatura_ativa = True
            db.session.commit()
            _enviar_boas_vindas(tenant)
        return jsonify({'ok': True, 'status': status, 'slug': tenant.slug})
    return jsonify({'erro': data.get('message', 'Pagamento recusado'), 'status': status}), 402

@app.route('/admin/painel')
def admin_painel():
    senha = request.args.get('key', '')
    if senha != API_TOKEN:
        return '''<html><body style="background:#0a0a0a;color:#f0ece4;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <form method="get" style="text-align:center">
          <h2 style="color:#C9A96E;margin-bottom:1.5rem">✦ Admin — iBarber</h2>
          <input name="key" type="password" placeholder="Token de acesso"
            style="padding:10px 16px;border-radius:6px;border:1px solid #333;background:#1e1e1e;color:#f0ece4;font-size:15px;width:260px">
          <br><br>
          <button type="submit" style="padding:10px 28px;background:#C9A96E;color:#000;border:none;border-radius:6px;font-weight:bold;cursor:pointer">Entrar</button>
        </form></body></html>''', 401
    tenants = Tenant.query.order_by(Tenant.id.desc()).all()
    mp_pub  = _get_setting('admin_mp_public_key')
    return render_template('admin_painel.html',
        tenants=tenants, key=senha,
        mp_public_key=mp_pub.value if mp_pub else '',
        mp_token_set=bool(get_mp_token()))

@app.route('/api/admin/credenciais', methods=['POST'])
def api_admin_credenciais():
    data = request.get_json(force=True) or {}
    if data.get('key') != API_TOKEN:
        return jsonify({'erro': 'não autorizado'}), 401
    for k, env_k in [('mp_token', 'MP_ACCESS_TOKEN'), ('mp_public_key', 'admin_mp_public_key')]:
        v = (data.get(k) or '').strip()
        if not v:
            continue
        if k == 'mp_token':
            os.environ['MP_ACCESS_TOKEN'] = v
            _upsert_setting('admin_mp_access_token', v)
            _upsert_setting('admin_mp_token_set', '1')
        else:
            _upsert_setting('admin_mp_public_key', v)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/admin/ativar/<int:tid>', methods=['POST'])
def api_admin_ativar(tid):
    data = request.get_json(force=True) or {}
    if data.get('key') != API_TOKEN:
        return jsonify({'erro': 'não autorizado'}), 401
    t = db.session.get(Tenant, tid)
    if not t:
        return jsonify({'erro': 'não encontrado'}), 404
    asn = Assinatura.query.filter_by(tenant_id=tid).order_by(Assinatura.id.desc()).first()
    if asn:
        asn.status = 'ativo'
        if not asn.inicio: asn.inicio = datetime.utcnow()
        if not asn.vencimento: asn.vencimento = datetime.utcnow() + timedelta(days=30)
    else:
        db.session.add(Assinatura(tenant_id=tid, plano='mensal', valor_total=49.0,
            valor_mensal=49.0, status='ativo', inicio=datetime.utcnow(),
            vencimento=datetime.utcnow() + timedelta(days=30)))
    t.ativo = True
    t.assinatura_ativa = True
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/admin/tenant/<int:tid>', methods=['DELETE'])
def api_admin_tenant_delete(tid):
    if request.args.get('key') != API_TOKEN:
        return jsonify({'erro': 'não autorizado'}), 401
    t = db.session.get(Tenant, tid)
    if not t:
        return jsonify({'erro': 'não encontrado'}), 404
    t.ativo = False
    t.assinatura_ativa = False
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/admin/desativar/<int:tid>', methods=['POST'])
def api_admin_desativar(tid):
    data = request.get_json(force=True) or {}
    if data.get('key') != API_TOKEN:
        return jsonify({'erro': 'não autorizado'}), 401
    t = db.session.get(Tenant, tid)
    if not t:
        return jsonify({'erro': 'não encontrado'}), 404
    t.assinatura_ativa = False
    asn = Assinatura.query.filter_by(tenant_id=tid, status='ativo').first()
    if asn:
        asn.status = 'inativo'
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/admin/excluir/<int:tid>', methods=['DELETE'])
def api_admin_excluir(tid):
    data = request.get_json(force=True) or {}
    if data.get('key') != API_TOKEN:
        return jsonify({'erro': 'não autorizado'}), 401
    t = db.session.get(Tenant, tid)
    if not t:
        return jsonify({'erro': 'não encontrado'}), 404
    # Remove assinaturas, serviços e categorias do tenant
    Assinatura.query.filter_by(tenant_id=tid).delete()
    Servico.query.filter_by(tenant_id=tid).delete()
    Categoria.query.filter_by(tenant_id=tid).delete()
    db.session.delete(t)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/admin/vencimento/<int:tid>', methods=['POST'])
def api_admin_vencimento(tid):
    data = request.get_json(force=True) or {}
    if data.get('key') != API_TOKEN:
        return jsonify({'erro': 'não autorizado'}), 401
    t = db.session.get(Tenant, tid)
    if not t:
        return jsonify({'erro': 'não encontrado'}), 404
    try:
        nova_data = datetime.strptime(data['vencimento'], '%Y-%m-%d')
    except (KeyError, ValueError):
        return jsonify({'erro': 'data inválida'}), 400
    asn = Assinatura.query.filter_by(tenant_id=tid).order_by(Assinatura.id.desc()).first()
    if not asn:
        asn = Assinatura(tenant_id=tid, plano='manual', valor_total=0, valor_mensal=0,
                         status='ativo', inicio=datetime.utcnow())
        db.session.add(asn)
    asn.vencimento = nova_data
    asn.status = 'ativo'
    t.assinatura_ativa = True
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/pagamento/criar', methods=['POST'])
def api_pagamento_criar_v2():
    d = request.get_json(force=True) or {}
    tenant = db.session.get(Tenant, d.get('tenant_id'))
    if not tenant:
        return jsonify({'erro': 'tenant não encontrado'}), 404
    plano = d.get('plano', '')
    if plano not in PLANOS:
        return jsonify({'erro': 'plano inválido'}), 400
    mp_token     = get_mp_token()
    mp_pub_key_s = _get_setting('admin_mp_public_key')
    mp_pub_key = mp_pub_key_s.value if mp_pub_key_s else ''
    amount = PLANOS[plano]['total']
    preference = {
        'items': [{'title': f'BarberOS — Plano {plano.capitalize()}',
                   'quantity': 1, 'currency_id': 'BRL', 'unit_price': amount}],
        'back_urls': {'success': f'{request.host_url}sucesso/{tenant.slug}',
                      'failure':  f'{request.host_url}falha',
                      'pending':  f'{request.host_url}pendente'},
        'auto_return': 'approved',
        'notification_url': f'{request.host_url}api/pagamento/webhook',
        'metadata': {'tenant_id': tenant.id, 'plano': plano},
    }
    pref_id = None
    if mp_token:
        res = req_http.post(
            'https://api.mercadopago.com/checkout/preferences',
            headers={'Authorization': f'Bearer {mp_token}', 'Content-Type': 'application/json'},
            json=preference, timeout=15,
        )
        pref_data = res.json()
        pref_id = pref_data.get('id')
    assinatura = Assinatura(
        tenant_id=tenant.id, plano=plano,
        valor_total=PLANOS[plano]['total'], valor_mensal=PLANOS[plano]['mensal'],
        status='pendente', mp_preference_id=pref_id,
    )
    db.session.add(assinatura)
    db.session.commit()
    return jsonify({'ok': True, 'preference_id': pref_id,
                    'public_key': mp_pub_key, 'amount': amount,
                    'tenant_id': tenant.id, 'plano': plano})

@app.route('/api/pagamento/webhook', methods=['POST'])
def api_pagamento_webhook():
    import hmac, hashlib

    # Verificação de assinatura do Mercado Pago
    mp_secret = os.environ.get('MP_WEBHOOK_SECRET', '')
    if mp_secret:
        sig_header = request.headers.get('X-Signature', '')
        ts = ''
        received = ''
        for part in sig_header.split(','):
            part = part.strip()
            if part.startswith('ts='):
                ts = part[3:]
            elif part.startswith('v1='):
                received = part[3:]
        data_id = request.args.get('data.id', '') or (request.get_json(force=True) or {}).get('data', {}).get('id', '')
        manifest = f'id:{data_id};request-id:{request.headers.get("X-Request-Id","")};ts:{ts};'
        expected = hmac.new(mp_secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        if received and not hmac.compare_digest(expected, received):
            return '', 401

    data = request.get_json(force=True) or {}
    if data.get('type') != 'payment':
        return '', 200

    mp_token = get_mp_token()
    payment_id = str(data.get('data', {}).get('id', ''))
    if not payment_id:
        return '', 200

    # Idempotência — ignora se já processado
    ja_processado = Assinatura.query.filter_by(mp_payment_id=payment_id, status='ativo').first()
    if ja_processado:
        return '', 200

    res = req_http.get(
        f'https://api.mercadopago.com/v1/payments/{payment_id}',
        headers={'Authorization': f'Bearer {mp_token}'}, timeout=15,
    )
    payment = res.json()
    if payment.get('status') == 'approved':
        meta = payment.get('metadata', {})
        tenant_id = meta.get('tenant_id')
        tipo = meta.get('tipo', 'assinatura')
        tenant = db.session.get(Tenant, tenant_id)
        if tenant:
            if tipo == 'tema':
                if tenant.tema_pendente:
                    tenant.tema = tenant.tema_pendente
                    tenant.tema_pendente = None
                tenant.tema_editacoes = (tenant.tema_editacoes or 0) + 1
                db.session.commit()
            else:
                plano = meta.get('plano', '')
                meses = PLANOS.get(plano, {}).get('meses', 1)
                assinatura = Assinatura.query.filter_by(
                    tenant_id=tenant_id, status='pendente').first()
                if not assinatura:
                    assinatura = Assinatura(
                        tenant_id=tenant_id, plano=plano,
                        valor_total=PLANOS.get(plano, {}).get('total', 0),
                        valor_mensal=PLANOS.get(plano, {}).get('mensal', 0),
                    )
                    db.session.add(assinatura)
                assinatura.status = 'ativo'
                assinatura.mp_payment_id = payment_id
                assinatura.inicio = datetime.utcnow()
                assinatura.vencimento = datetime.utcnow() + timedelta(days=30 * meses)
                tenant.ativo = True
                tenant.assinatura_ativa = True
                db.session.commit()
                _enviar_boas_vindas(tenant)
    return '', 200

@app.route('/verificar-slug/<slug>')
def verificar_slug(slug):
    import re
    slug = slug.lower().strip()
    valido = bool(re.match(r'^[a-z0-9-]{3,30}$', slug))
    existe = Tenant.query.filter_by(slug=slug).first() is not None
    return jsonify({'disponivel': valido and not existe})

@app.route('/api/minha-assinatura')
def api_minha_assinatura():
    if not verificar_token(request): return jsonify({'erro': 'token inválido'}), 401
    tenant_id = request.args.get('tenant_id', type=int)
    if not tenant_id:
        return jsonify({'erro': 'tenant_id obrigatório'}), 400
    assinatura = Assinatura.query.filter_by(
        tenant_id=tenant_id).order_by(Assinatura.criado_em.desc()).first()
    if not assinatura:
        return jsonify({'status': 'sem_assinatura'})
    return jsonify({
        'status': assinatura.status,
        'plano': assinatura.plano,
        'vencimento': assinatura.vencimento.isoformat() if assinatura.vencimento else None,
        'dias_restantes': assinatura.dias_restantes(),
        'valor_mensal': assinatura.valor_mensal,
    })

def _udp_broadcast():
    import socket, time
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            sock.sendto(b'BARBEARIA_SERVER:5000', ('255.255.255.255', 5001))
        except Exception:
            pass
        time.sleep(2)

@app.route('/<slug>')
def tenant_site(slug):
    """Rota pública do site de agendamento de cada barbearia."""
    tenant = get_tenant_by_slug(slug)
    if not tenant:
        return ('<html><body style="background:#0a0a0a;color:#f0ece4;font-family:sans-serif;'
                'display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center">'
                '<div><h2 style="color:#C9A96E">✦ iBarber</h2>'
                '<p style="color:#888;margin-top:.5rem">Barbearia não encontrada.</p>'
                '<a href="/landing" style="color:#C9A96E;margin-top:1rem;display:block">Criar meu site →</a>'
                '</div></body></html>', 404)
    if not tenant.assinatura_ativa:
        return ('<html><body style="background:#0a0a0a;color:#f0ece4;font-family:sans-serif;'
                'display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center">'
                f'<div><h2 style="color:#C9A96E">✦ {tenant.nome}</h2>'
                '<p style="color:#888;margin-top:.5rem">Site em ativação — aguardando confirmação do pagamento.</p>'
                '</div></body></html>', 402)
    session['path_tenant_id'] = tenant.id
    session.pop('is_preview', None)
    session.pop('tenant_id', None)
    user = None
    if 'user_id' in session:
        user = db.session.get(User, session['user_id'])
    return render_template('index.html', user=user, preview_mode=False, tema_override=None, hide_fabs=False)

if __name__ == '__main__':
    import threading
    threading.Thread(target=_udp_broadcast, daemon=True).start()
    app.run(host='0.0.0.0', debug=True)
