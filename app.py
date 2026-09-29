# Adia a avaliação das anotações: permite sintaxe como `str | None` (PEP 604)
# em Python 3.9, onde ela só seria válida a partir do 3.10.
from __future__ import annotations

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response, make_response, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.orm import joinedload
from sqlalchemy import inspect as _sa_inspect, text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

import os, json, uuid, secrets, smtplib, requests as req_http
import subprocess, socket, threading, time
import re, random, html, csv, io, hmac, hashlib, base64
from functools import wraps
from cryptography.fernet import Fernet, InvalidToken
from calendar import monthrange
from urllib.parse import urlencode
from collections import defaultdict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from PIL import Image

load_dotenv()

APP_VERSION = '1.2.0'

_SESSION_USER_ID = 'user_id'
_SESSION_USER_NAME = 'user_name'
_SESSION_USER_EMAIL = 'user_email'
_SESSION_PATH_TENANT_ID = 'path_tenant_id'
_SESSION_GESTAO_TENANT_ID = 'gestao_tenant_id'
_SESSION_REPERSON_TID = 'reperson_tid'
_SESSION_TENANT_ID = 'tenant_id'
_SESSION_IS_GUEST = 'is_guest'
_SESSION_IS_PREVIEW = 'is_preview'

_SESSION_GESTAO_NOME = 'gestao_nome'
_SESSION_GESTAO_FUNC_ID = 'gestao_func_id'
_SESSION_ONB_TENANT_ID = 'onb_tenant_id'
_SESSION_ADMIN_OK = 'admin_ok'
if not os.environ.get('SECRET_KEY'):
    raise RuntimeError('SECRET_KEY não definida no ambiente')

app = Flask(__name__)
# Atrás do nginx, sem isto request.remote_addr é sempre 127.0.0.1 e todos os
# usuários dividem o mesmo balde de rate limit. x_for=1 = confia em exatamente
# um proxy (o nosso); valores maiores permitiriam o cliente forjar o IP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
app.jinja_env.cache = {}
app.jinja_env.globals['APP_VERSION'] = APP_VERSION

@app.context_processor
def _gestao_trial_ctx():
    tid = session.get(_SESSION_GESTAO_TENANT_ID) or session.get(_SESSION_REPERSON_TID)
    if not tid:
        return {}
    tenant = db.session.get(Tenant, tid)
    if not tenant:
        return {}
    return {
        'trial_ativo': tenant.em_trial(),
        'trial_dias': tenant.trial_dias_restantes(),
    }
_cors_raw = os.environ.get('CORS_ORIGINS', '')
_cors_origins = [o.strip() for o in _cors_raw.split(',') if o.strip()] if _cors_raw else None
_is_debug = os.environ.get('FLASK_DEBUG', '0') == '1'
if _cors_origins:
    CORS(app, origins=_cors_origins, supports_credentials=True)
elif _is_debug:
    CORS(app, origins='*', supports_credentials=False)
else:
    CORS(app, origins=['https://ibarber.shop', 'https://ibarber.app.br'], supports_credentials=True)
app.secret_key = os.environ.get('SECRET_KEY')
_DB_URL = os.environ.get('DATABASE_URL')
if not _DB_URL:
    raise RuntimeError('DATABASE_URL não definida no ambiente')
app.config['SQLALCHEMY_DATABASE_URI'] = _DB_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,    # reconecta automaticamente se a conexão cair
    'pool_recycle':  300,     # recicla conexões a cada 5 min (evita timeout MySQL)
    'pool_size':     10,
    'max_overflow':  20,
}
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB upload limit
app.config['SESSION_COOKIE_HTTPONLY'] = True
# O cookie tem Domain=.APP_DOMAIN, ou seja é compartilhado com TODOS os
# subdomínios de tenant. Como cada barbearia controla o conteúdo do próprio
# subdomínio, 'Lax' não separa um tenant do outro — a defesa efetiva é o token
# CSRF em _csrf_protect(). 'Lax' fica só para não quebrar o retorno do OAuth.
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
_is_dev = os.environ.get('FLASK_DEBUG', '0') == '1' or os.environ.get('FLASK_ENV') == 'development'
app.config['SESSION_COOKIE_SECURE'] = not _is_dev
_cookie_domain = os.environ.get('APP_DOMAIN', 'ibarber.shop')
app.config['SESSION_COOKIE_DOMAIN'] = f".{_cookie_domain}" if not _is_dev else None
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.after_request
def _security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if not _is_dev:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://sdk.mercadopago.com https://accounts.google.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self' https://api.mercadopago.com https://accounts.google.com; "
        "frame-ancestors 'self';"
    )
    return response

_MAGIC_IMAGES = {
    b'\xff\xd8\xff': 'jpg',
    b'\x89PNG\r\n\x1a\n': 'png',
    b'GIF87a': 'gif',
    b'GIF89a': 'gif',
    b'RIFF': 'webp',
    b'#define ': 'xpm',
    b'BM': 'bmp',
}

def _magic_image_type(buf: bytes) -> str | None:
    head = buf[:12]
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'webp'
    for sig, ext in _MAGIC_IMAGES.items():
        if head.startswith(sig):
            return ext
    return None

MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB por imagem
# Teto de pixels após descompressão: um PNG pequeno pode expandir para GBs.
Image.MAX_IMAGE_PIXELS = 40_000_000

def _ler_limitado(arquivo, limite=MAX_UPLOAD_BYTES):
    """Lê no máximo `limite`+1 bytes e devolve (BytesIO, erro).
    Confiar em request.content_length não funciona: o header é controlado pelo
    cliente e some por completo em Transfer-Encoding: chunked."""
    dados = arquivo.read(limite + 1)
    if len(dados) > limite:
        return None, f'Imagem muito grande. Máximo {limite // (1024 * 1024)} MB.'
    if not dados:
        return None, 'arquivo vazio'
    return io.BytesIO(dados), None

def _processar_imagem(arquivo, max_px=1400, quality=82):
    """Validate, strip EXIF, resize, return (BytesIO, error_str, ext)."""
    origem, _erro_tamanho = _ler_limitado(arquivo)
    if _erro_tamanho:
        return None, _erro_tamanho, None
    try:
        head = origem.read(16)
        if not head or _magic_image_type(head) is None:
            return None, 'arquivo de imagem inválido', None
        origem.seek(0)
        img = Image.open(origem)
        img.verify()
        origem.seek(0)
        img = Image.open(origem)
    except Exception:
        return None, 'arquivo de imagem inválido', None
    has_alpha = img.mode in ('RGBA', 'LA', 'PA') or (img.mode == 'P' and 'transparency' in img.info)
    buf = io.BytesIO()
    if has_alpha:
        img = img.convert('RGBA')
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        img.save(buf, format='PNG', optimize=True)
        ext = 'png'
    else:
        img = img.convert('RGB')
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        img.save(buf, format='JPEG', quality=quality, optimize=True)
        ext = 'jpg'
    buf.seek(0)
    return buf, None, ext

MAIL_HOST     = 'smtp.gmail.com'
MAIL_PORT     = 587
MAIL_USER     = os.environ.get('MAIL_USER')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
MAIL_FROM     = os.environ.get('MAIL_FROM', '')
if not MAIL_USER or not MAIL_PASSWORD or not MAIL_FROM:
    raise RuntimeError('MAIL_USER, MAIL_PASSWORD e MAIL_FROM devem estar definidos no ambiente')
API_TOKEN            = os.environ.get('API_TOKEN', '')
if not API_TOKEN or len(API_TOKEN) < 32:
    raise RuntimeError('API_TOKEN ausente ou fraco — defina no ambiente com no mínimo 32 caracteres')
GOOGLE_CLIENT_ID     = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI  = os.environ.get('GOOGLE_REDIRECT_URI', 'https://ibarber.shop/auth/google/callback')
APP_DOMAIN    = os.environ.get('APP_DOMAIN', 'ibarber.shop')
CF_TOKEN      = os.environ.get('CF_TOKEN', '')
CF_ZONE_ID    = os.environ.get('CF_ZONE_ID', '')
# Sem default: o IP do servidor é infraestrutura e não pertence ao código.
# Vazio faz _criar_dns_cloudflare() sair sem criar registro algum.
VPS_IP        = os.environ.get('VPS_IP', '')

def _tenant_url(slug):
    return f'https://{slug}.{APP_DOMAIN}'

def admin_key_required(fn):
    """Protege as rotas /api/admin/* (chave mestra do SaaS).
    Autoriza pela sessão do painel — assim a chave não precisa ir para o HTML —
    ou pela chave enviada em body/query/header, em comparação de tempo
    constante. Chave vazia nunca autoriza."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get(_SESSION_ADMIN_OK):
            return fn(*args, **kwargs)
        key = ((request.get_json(silent=True) or {}).get('key')
               or request.args.get('key', '')
               or request.headers.get('X-Admin-Key', ''))
        if not key or not hmac.compare_digest(str(key), API_TOKEN):
            return jsonify({'erro': 'não autorizado'}), 401
        return fn(*args, **kwargs)
    return wrapper

db = SQLAlchemy(app)
# memory:// dá um contador por worker do gunicorn — o limite real vira N vezes
# o declarado e zera a cada deploy. Com REDIS_URL o estado é compartilhado.
_LIMITER_STORAGE = os.environ.get('REDIS_URL', 'memory://')
limiter = Limiter(get_remote_address, app=app,
                  default_limits=['600 per hour'],
                  storage_uri=_LIMITER_STORAGE)
if _LIMITER_STORAGE == 'memory://' and not _is_debug:
    app.logger.warning(
        '[LIMITER] usando memory:// em produção — os limites são por worker. '
        'Defina REDIS_URL para contagem compartilhada.')

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

class SenhaMixin:
    """Campo de senha compartilhado por Tenant, User e Funcionario.

    A coluna continua se chamando 'password' no banco (sem precisar de
    migration); o que muda é que ninguém mais grava ou lê o hash na mão.
    Escrever em `.senha` sempre passa por generate_password_hash — não tem
    como salvar senha em texto puro por esquecimento. Ler `.senha` não é
    permitido; a comparação é feita por `conferir_senha()`.
    """
    password = db.Column(db.String(200), nullable=False)

    @property
    def senha(self):
        raise AttributeError('senha é escreve-só — use conferir_senha() para validar')

    @senha.setter
    def senha(self, texto):
        if not texto or len(texto) < 8:
            raise ValueError('senha precisa ter 8 caracteres ou mais')
        self.password = generate_password_hash(texto)

    def conferir_senha(self, texto):
        return bool(texto) and check_password_hash(self.password, texto)

class Tenant(SenhaMixin, db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    slug             = db.Column(db.String(50),  unique=True, nullable=False)
    nome             = db.Column(db.String(100), nullable=False)
    email            = db.Column(db.String(120), unique=True, nullable=False)
    contato          = db.Column(db.String(20),  nullable=True)
    ativo            = db.Column(db.Boolean, default=True)
    criado_em        = db.Column(db.DateTime, default=datetime.utcnow)
    tema             = db.Column(db.Text, nullable=True)
    tema_editacoes   = db.Column(db.Integer, default=0)
    tema_pendente    = db.Column(db.Text,    nullable=True)
    assinatura_ativa = db.Column(db.Boolean, default=False)
    trial_expira     = db.Column(db.DateTime, nullable=True)
    whatsapp         = db.Column(db.String(20),  nullable=True)
    maps_url         = db.Column(db.String(500), nullable=True)
    fab_wpp          = db.Column(db.Text, nullable=True)   # JSON
    fab_maps         = db.Column(db.Text, nullable=True)   # JSON
    loja_ativa       = db.Column(db.Boolean, default=False)
    token_version    = db.Column(db.Integer, default=0, nullable=False, server_default='0')
    assinaturas      = db.relationship('Assinatura', backref='tenant', lazy=True, order_by='Assinatura.id.desc()')

    def em_trial(self):
        return self.trial_expira is not None and self.trial_expira > datetime.utcnow()

    def trial_dias_restantes(self):
        if not self.em_trial():
            return 0
        return max(0, (self.trial_expira - datetime.utcnow()).days)

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
        return self.status == 'ativo' and bool(self.vencimento) and self.vencimento > datetime.utcnow()

class User(SenhaMixin, db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    name           = db.Column(db.String(100), nullable=False)
    email          = db.Column(db.String(120), nullable=False)
    contact            = db.Column(db.String(20),  nullable=True)
    observation        = db.Column(db.Text,        nullable=True)
    receber_lembretes  = db.Column(db.Boolean,     default=True)
    guest              = db.Column(db.Boolean,     default=False)
    google_id          = db.Column(db.String(200), nullable=True, index=True)
    criado_em          = db.Column(db.DateTime,    default=datetime.utcnow)
    tenant_id          = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)
    pedidos        = db.relationship('Pedido', backref='usuario', lazy=True,
                                     cascade='all, delete-orphan')
    __table_args__ = (db.UniqueConstraint('email', 'tenant_id', name='uq_user_email_tenant'),)

class Pedido(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total     = db.Column(db.Float,   default=0)
    status    = db.Column(db.String(20), default='pendente')   # pendente | pago | cancelado
    criado_em = db.Column(db.DateTime,  default=datetime.utcnow)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)
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
    """Retorna tenant_id do gestor ou repersonalizador logado, ou None."""
    return session.get(_SESSION_GESTAO_TENANT_ID) or session.get(_SESSION_REPERSON_TID)

def _api_tid():
    """Retorna tenant_id a partir do contexto atual (gestão session, Bearer token ou path)."""
    tid = session.get(_SESSION_GESTAO_TENANT_ID)
    if tid:
        return tid
    token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    if token:
        tid_tok, *_ = _extrair_tenant_token(token)
        if tid_tok:
            return tid_tok
    t = get_tenant_atual()
    return t.id if t else None

class LembreteEnviado(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    agendamento_id = db.Column(db.Integer, db.ForeignKey('agendamento.id'), nullable=False, index=True)
    tipo           = db.Column(db.String(10), nullable=False)  # 3d | 1d | 12h | 1h
    enviado_em     = db.Column(db.DateTime, default=datetime.utcnow)
    # A checagem "já enviei?" e o INSERT não são atômicos; a constraint impede
    # que duas execuções concorrentes mandem o mesmo lembrete duas vezes.
    __table_args__ = (db.UniqueConstraint('agendamento_id', 'tipo', name='uq_lembrete_ag_tipo'),)

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
    tenant_id   = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)

class FotoServico(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    categoria  = db.Column(db.String(50), nullable=False, index=True)   # corte | barba | outros
    servico    = db.Column(db.String(100), nullable=True, index=True)   # nome do serviço
    filename   = db.Column(db.String(200), nullable=False)
    criado_em  = db.Column(db.DateTime, default=datetime.utcnow)
    tenant_id  = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)

class Agendamento(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    pedido_id       = db.Column(db.Integer, db.ForeignKey('pedido.id'), nullable=True)
    data_hora       = db.Column(db.DateTime, nullable=False, index=True)
    status          = db.Column(db.String(20), default='ativo', index=True)  # ativo | cancelado | concluido
    forma_pagamento = db.Column(db.String(20), nullable=True)  # dinheiro | pix | cartao
    duracao_total   = db.Column(db.Integer, nullable=True)     # minutos (soma dos serviços)
    funcionario_id  = db.Column(db.Integer, db.ForeignKey('funcionario.id'), nullable=True)
    criado_em       = db.Column(db.DateTime, default=datetime.utcnow)
    tenant_id       = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)
    usuario         = db.relationship('User', lazy='select')
    funcionario     = db.relationship('Funcionario', lazy='select', foreign_keys=[funcionario_id])
    pedido          = db.relationship('Pedido', lazy='select', foreign_keys=[pedido_id])

class Categoria(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    nome      = db.Column(db.String(100), nullable=False)
    icone     = db.Column(db.String(10),  default='✦')
    ordem     = db.Column(db.Integer,     default=0)
    ativo     = db.Column(db.Boolean,     default=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)
    servicos  = db.relationship('Servico', backref='cat_ref', lazy=True,
                                foreign_keys='Servico.categoria_id')

class Servico(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    nome         = db.Column(db.String(100), nullable=False)
    categoria    = db.Column(db.String(50),  nullable=True)   # legado
    categoria_id = db.Column(db.Integer, db.ForeignKey('categoria.id'), nullable=True)
    preco        = db.Column(db.Float,  default=0)
    duracao      = db.Column(db.Integer, default=30)          # minutos
    ativo        = db.Column(db.Boolean, default=True)
    ordem        = db.Column(db.Integer, default=0)
    criado_em    = db.Column(db.DateTime, default=datetime.utcnow)
    tenant_id    = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)

class HorarioEspecial(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    data       = db.Column(db.String(10), nullable=False, index=True)   # YYYY-MM-DD
    abertura   = db.Column(db.String(5),  nullable=False, default='08:00')
    fechamento = db.Column(db.String(5),  nullable=False, default='18:00')
    criado_em  = db.Column(db.DateTime, default=datetime.utcnow)
    tenant_id  = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)

class Funcionario(SenhaMixin, db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    nome          = db.Column(db.String(100), nullable=False)
    email         = db.Column(db.String(120), nullable=True)
    telefone      = db.Column(db.String(20),  nullable=True)
    foto          = db.Column(db.String(200),  nullable=True)
    ativo         = db.Column(db.Boolean, default=True)
    criado_em     = db.Column(db.DateTime, default=datetime.utcnow)
    tenant_id     = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=True)
    __table_args__ = (db.UniqueConstraint('email', 'tenant_id', name='uq_func_email_tenant'),)
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
        email = self.email or ''
        if email.startswith('_deleted_'):
            email = ''
        return {
            'id': self.id, 'nome': self.nome, 'email': email,
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

class ListaEspera(db.Model):
    """Clientes que querem ser notificados quando abre vaga num dia cheio."""
    __tablename__ = 'lista_espera'
    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'),   nullable=False)
    data          = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    notificado_em = db.Column(db.DateTime, nullable=True)
    criado_em     = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('tenant_id', 'user_id', 'data'),)

class Produto(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    tenant_id  = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    nome       = db.Column(db.String(100), nullable=False)
    descricao  = db.Column(db.Text, nullable=True)
    foto       = db.Column(db.String(200), nullable=True)
    valor      = db.Column(db.Float, default=0)
    em_estoque = db.Column(db.Boolean, default=True)
    criado_em  = db.Column(db.DateTime, default=datetime.utcnow)


with app.app_context():
    _inspector = _sa_inspect(db.engine)
    _existing = set(_inspector.get_table_names())
    for _tbl in db.metadata.sorted_tables:
        if _tbl.name not in _existing:
            _tbl.create(db.engine)
    _inspector = _sa_inspect(db.engine)
    _existing = set(_inspector.get_table_names())
    _ALLOWED_TENANT_COLS = {
        'fab_wpp', 'fab_maps', 'whatsapp', 'maps_url',
        'tema_editacoes', 'tema_pendente', 'trial_expira', 'loja_ativa',
    }
    _ALLOWED_USER_COLS = {'guest', 'tenant_id'}
    _ALLOWED_SERV_COLS = {'categoria_id'}
    _ALLOWED_ENTRADA_COLS = {'pedido_id'}
    _ALLOWED_PEDIDO_COLS = {'tenant_id'}
    _ALLOWED_AG_COLS = {'tenant_id', 'duracao_total', 'funcionario_id', 'criado_em'}
    _TENANT_COL_TYPES = {
        'fab_wpp': 'TEXT', 'fab_maps': 'TEXT', 'whatsapp': 'VARCHAR(20)',
        'maps_url': 'VARCHAR(500)', 'tema_editacoes': 'INTEGER DEFAULT 0',
        'tema_pendente': 'TEXT', 'trial_expira': 'DATETIME', 'loja_ativa': 'BOOLEAN DEFAULT 0',
    }
    _USER_COL_TYPES = {'guest': 'INTEGER DEFAULT 0', 'tenant_id': 'INTEGER'}
    _SERV_COL_TYPES = {'categoria_id': 'INTEGER'}
    _ENTRADA_COL_TYPES = {'pedido_id': 'INTEGER'}
    _PEDIDO_COL_TYPES = {'tenant_id': 'INTEGER'}
    _AG_COL_TYPES = {'tenant_id': 'INTEGER', 'duracao_total': 'INTEGER', 'funcionario_id': 'INTEGER', 'criado_em': 'DATETIME'}
    _tenant_cols = {c['name'] for c in _inspector.get_columns('tenant')} if 'tenant' in _existing else set()
    for _col, _type in [(k, _TENANT_COL_TYPES[k]) for k in _ALLOWED_TENANT_COLS]:
        if _col not in _tenant_cols:
            with db.engine.connect() as _conn:
                _conn.execute(db.text(f'ALTER TABLE tenant ADD COLUMN {_col} {_type}'))
                _conn.commit()
    _user_cols = {c['name'] for c in _inspector.get_columns('user')} if 'user' in _existing else set()
    for _col, _type in [(k, _USER_COL_TYPES[k]) for k in _ALLOWED_USER_COLS]:
        if _col not in _user_cols:
            with db.engine.connect() as _conn:
                _conn.execute(db.text(f'ALTER TABLE `user` ADD COLUMN {_col} {_type}'))
                _conn.commit()
    _serv_cols = {c['name'] for c in _inspector.get_columns('servico')} if 'servico' in _existing else set()
    for _col, _type in [(k, _SERV_COL_TYPES[k]) for k in _ALLOWED_SERV_COLS]:
        if _col not in _serv_cols:
            with db.engine.connect() as _conn:
                _conn.execute(db.text(f'ALTER TABLE servico ADD COLUMN {_col} {_type}'))
                _conn.commit()
    _entrada_cols = {c['name'] for c in _inspector.get_columns('entrada_monetaria')} if 'entrada_monetaria' in _existing else set()
    for _col, _type in [(k, _ENTRADA_COL_TYPES[k]) for k in _ALLOWED_ENTRADA_COLS]:
        if _col not in _entrada_cols:
            with db.engine.connect() as _conn:
                _conn.execute(db.text(f'ALTER TABLE entrada_monetaria ADD COLUMN {_col} {_type}'))
                _conn.commit()
    _pedido_cols = {c['name'] for c in _inspector.get_columns('pedido')} if 'pedido' in _existing else set()
    for _col, _type in [(k, _PEDIDO_COL_TYPES[k]) for k in _ALLOWED_PEDIDO_COLS]:
        if _col not in _pedido_cols:
            with db.engine.connect() as _conn:
                _conn.execute(db.text(f'ALTER TABLE pedido ADD COLUMN {_col} {_type}'))
                _conn.commit()
    _ag_cols = {c['name'] for c in _inspector.get_columns('agendamento')} if 'agendamento' in _existing else set()
    for _col, _type in [(k, _AG_COL_TYPES[k]) for k in _ALLOWED_AG_COLS]:
        if _col not in _ag_cols:
            with db.engine.connect() as _conn:
                _conn.execute(db.text(f'ALTER TABLE agendamento ADD COLUMN {_col} {_type}'))
                _conn.commit()
    # Torna funcionario.email nullable para permitir múltiplos barbeiros sem email
    try:
        with db.engine.connect() as _conn:
            _conn.execute(db.text('ALTER TABLE funcionario MODIFY COLUMN email VARCHAR(120) NULL'))
            _conn.commit()
    except Exception:
        pass  # SQLite (dev) não suporta MODIFY COLUMN — sem problema
    # Mangle emails de funcionários inativos legados que ainda bloqueiam o unique constraint
    try:
        inativos = Funcionario.query.filter_by(ativo=False).all()
        changed = False
        for f in inativos:
            if f.email and not f.email.startswith('_deleted_'):
                f.email = f'_deleted_{f.id}_{f.email}'
                changed = True
        if changed:
            db.session.commit()
    except Exception:
        db.session.rollback()


def _agora_brt():
    """Retorna datetime atual em BRT (UTC-3). Brasil sem horário de verão desde 2019."""
    return datetime.utcnow() - timedelta(hours=3)

def _sf(val, default=0.0):
    """Converte para float com segurança; aceita vírgula como decimal (padrão BR)."""
    try:
        return float(str(val).replace(',', '.'))
    except (TypeError, ValueError):
        return default

def _fernet():
    """Fernet instance keyed from SECRET_KEY via SHA-256."""
    raw = (os.environ.get('SECRET_KEY') or 'fallback').encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)

def _enc(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()

def _dec(value: str) -> str:
    """Decrypt Fernet-encrypted value; returns plaintext on legacy/error (migration grace)."""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, Exception):
        return value

def get_mp_token():
    """Retorna o MP Access Token: env var primeiro, depois Setting persistido."""
    t = os.environ.get('MP_ACCESS_TOKEN', '')
    if t:
        return t
    try:
        s = _get_setting('admin_mp_access_token')
        return _dec(s.value) if s and s.value else ''
    except Exception:
        return ''

_CSS_COLOR_RE = re.compile(
    r'^(#[0-9a-fA-F]{3,8}|rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)|rgba\(\s*[\d,\s.]+\)|transparent|inherit)$'
)

def _safe_color(value: str, default: str) -> str:
    v = (value or '').strip()
    return v if _CSS_COLOR_RE.match(v) else default

def _safe_radius(value, default='6') -> str:
    try:
        r = int(float(str(value)))
        return str(max(0, min(r, 50)))
    except (TypeError, ValueError):
        return default

_NOME_RE = re.compile(r"^[0-9A-Za-zÀ-ÿ\s.'\-]{2,80}$")

def _nome_seguro(valor, tamanho=80):
    """Valida nome informado por visitante anônimo. Retorna None se inválido.
    Barra o vetor de XSS armazenado no painel de gestão (nome vira HTML lá)."""
    v = (valor or '').strip()[:tamanho]
    return v if _NOME_RE.match(v) else None

def user_dict(u):
    return {
        'id': u.id, 'nome': u.name, 'email': u.email,
        'contato': u.contact, 'observacao': u.observation,
        'criado_em': u.criado_em.isoformat() if u.criado_em else None,
    }

def pedido_dict(p):
    ag   = Agendamento.query.filter_by(pedido_id=p.id, tenant_id=p.tenant_id).first()
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



def _safe_json(data):
    """json.dumps seguro para embedding em <script>: escapa <, > e & para evitar XSS."""
    return json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')

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
    emp_ids = [f.id for f in todos]
    ausentes_ids = set()
    if emp_ids:
        ausentes_ids = {
            a.funcionario_id
            for a in FuncionarioAusencia.query.filter(
                FuncionarioAusencia.funcionario_id.in_(emp_ids),
                FuncionarioAusencia.data == data_str
            ).all()
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
        gestor_ausente_s = _get_setting(f'gestor_ausente_{data_str}', tenant_id)
        if not (gestor_ausente_s and gestor_ausente_s.value == '1'):
            gf = _get_setting('gestor_foto', tenant_id)
            gestor_foto = f'/static/uploads/{gf.value}' if gf and gf.value else None
            lista.insert(0, {'id': 0, 'nome': gestor_nome, 'foto_url': gestor_foto})
    return lista

DURACAO_MIN = 5
DURACAO_MAX = 480   # 8h — teto para não permitir bloqueio da agenda inteira

def _int_seguro(valor, default, minimo, maximo) -> int:
    """Converte para int e prende no intervalo [minimo, maximo]."""
    try:
        v = int(valor)
    except (TypeError, ValueError):
        v = default
    return max(minimo, min(v, maximo))

def _duracao_segura(valor, default=40) -> int:
    """Converte para int e prende no intervalo permitido.
    Duração 0/negativa causaria laço infinito nos geradores de slot."""
    try:
        d = int(valor)
    except (TypeError, ValueError):
        d = default
    return max(DURACAO_MIN, min(d, DURACAO_MAX))

def _gerar_slots(abertura='08:00', fechamento='18:00', duracao=40):
    duracao = _duracao_segura(duracao)
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
    return send_from_directory('static', 'manifest.json', mimetype='application/manifest+json')

_ADMIN_WEB = os.path.join(os.path.dirname(__file__), 'admin_web')

@app.route('/admin', defaults={'path': ''})
@app.route('/admin/<path:path>')
def serve_admin_web(path):
    target = os.path.join(_ADMIN_WEB, path)
    if path and os.path.isfile(target):
        return send_from_directory(_ADMIN_WEB, path)
    return send_from_directory(_ADMIN_WEB, 'index.html')

@app.route('/')
def index():
    tenant = get_tenant_atual()
    if tenant:
        session[_SESSION_PATH_TENANT_ID] = tenant.id
        session.pop('is_preview', None)
        user = db.session.get(User, session[_SESSION_USER_ID]) if 'user_id' in session else None
        auto_rapido = bool(not user and request.args.get('agendar'))
        return render_template('index.html', user=user, auto_rapido=auto_rapido, auto_criar=False)
    return redirect(url_for('landing'))

@app.route('/register')
def register():
    return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    return redirect(url_for('index'))

@app.route('/login-rapido', methods=['POST'])
@limiter.limit('15 per minute')
def login_rapido():
    nome    = _nome_seguro(request.form.get('nome'))
    contato = request.form.get('contato', '').strip()
    observation = (request.form.get('alergia') or request.form.get('observation') or '').strip()[:500]
    alergia = observation
    if not nome or not contato:
        flash('Informe um nome válido (apenas letras, espaços, apóstrofo e hífen) e o contato.', 'error')
        return redirect(url_for('login'))
    email  = f"guest_{uuid.uuid4().hex[:8]}@temp.com"
    user   = User(
        name=nome,
        email=email,
        senha=secrets.token_hex(16),
        contact=contato or None,
        observation=alergia or None,
        receber_lembretes=False,
        guest=True,
        tenant_id=session.get(_SESSION_PATH_TENANT_ID) or _api_tid(),
    )
    db.session.add(user)
    db.session.commit()
    session[_SESSION_USER_ID]   = user.id
    session[_SESSION_USER_NAME] = nome
    session[_SESSION_USER_EMAIL] = email
    session[_SESSION_IS_GUEST]  = True
    return redirect(url_for('servicos'))

@app.route('/api/auth/telefone', methods=['POST'])
@limiter.limit('10 per minute')
def api_auth_telefone():
    data = request.get_json(force=True) or {}
    tel  = ''.join(c for c in data.get('telefone', '') if c.isdigit())
    if len(tel) < 10:
        return jsonify({'erro': 'Telefone inválido'}), 400
    tid  = session.get(_SESSION_PATH_TENANT_ID) or _api_tid()
    user = User.query.filter_by(contact=tel, guest=False, tenant_id=tid).first()
    if user:
        _ptid = session.get(_SESSION_PATH_TENANT_ID)
        session.clear()
        session[_SESSION_USER_ID]    = user.id
        session[_SESSION_USER_NAME]  = user.name
        session[_SESSION_USER_EMAIL] = user.email
        if _ptid: session[_SESSION_PATH_TENANT_ID] = _ptid
        return jsonify({'ok': True, 'nome': user.name})
    return jsonify({'novo': True})

@app.route('/api/auth/criar-telefone', methods=['POST'])
@limiter.limit('10 per minute')
def api_auth_criar_telefone():
    data      = request.get_json(force=True) or {}
    nome      = _nome_seguro(data.get('nome'))
    tel       = ''.join(c for c in data.get('telefone', '') if c.isdigit())
    email_opt = data.get('email', '').strip().lower() or None
    if email_opt and not re.match(r'^[^@]+@[^@]+\.[^@]+$', email_opt):
        return jsonify({'erro': 'e-mail inválido'}), 400
    lembretes = bool(data.get('lembretes')) and bool(email_opt)
    if not nome:
        return jsonify({'erro': 'Nome inválido — use apenas letras, espaços, apóstrofo e hífen'}), 400
    if len(tel) < 10:
        return jsonify({'erro': 'Nome e telefone são obrigatórios'}), 400
    tid = session.get(_SESSION_PATH_TENANT_ID) or _api_tid()
    existing = User.query.filter_by(contact=tel, guest=False, tenant_id=tid).first()
    if existing:
        _ptid = session.get(_SESSION_PATH_TENANT_ID)
        session.clear()
        session[_SESSION_USER_ID]    = existing.id
        session[_SESSION_USER_NAME]  = existing.name
        session[_SESSION_USER_EMAIL] = existing.email
        if _ptid: session[_SESSION_PATH_TENANT_ID] = _ptid
        return jsonify({'ok': True, 'nome': existing.name})
    if email_opt and User.query.filter_by(email=email_opt, tenant_id=tid).first():
        return jsonify({'erro': 'Este e-mail já está em uso'}), 400
    email = email_opt or f"tel_{tel}_{tid or 0}@ibarber.local"
    user  = User(
        name=nome,
        email=email,
        senha=secrets.token_hex(16),
        contact=tel,
        receber_lembretes=lembretes,
        guest=False,
        tenant_id=tid,
    )
    db.session.add(user)
    db.session.commit()
    _ptid = session.get(_SESSION_PATH_TENANT_ID)
    session.clear()
    session[_SESSION_USER_ID]    = user.id
    session[_SESSION_USER_NAME]  = nome
    session[_SESSION_USER_EMAIL] = email
    if _ptid: session[_SESSION_PATH_TENANT_ID] = _ptid
    return jsonify({'ok': True, 'nome': nome})

@app.route('/api/auth/lembretes', methods=['POST'])
def api_auth_lembretes():
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    data      = request.get_json(force=True) or {}
    ativo     = bool(data.get('ativo'))
    email_opt = data.get('email', '').strip().lower() or None
    user      = db.session.get(User, session[_SESSION_USER_ID])
    if not user:
        return jsonify({'erro': 'usuário não encontrado'}), 404
    if ativo and not email_opt and user.email.endswith('@ibarber.local'):
        return jsonify({'erro': 'Informe um e-mail para receber lembretes'}), 400
    if email_opt:
        conflito = User.query.filter(User.email == email_opt, User.id != user.id, User.tenant_id == user.tenant_id).first()
        if conflito:
            return jsonify({'erro': 'E-mail já em uso'}), 400
        user.email = email_opt
        session[_SESSION_USER_EMAIL] = email_opt
    user.receber_lembretes = ativo
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/servicos')
def servicos():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    agendamento_info = None
    _tenant_servicos = get_tenant_atual()
    _tid_servicos = _tenant_servicos.id if _tenant_servicos else None
    ag = (Agendamento.query
          .filter_by(user_id=session[_SESSION_USER_ID], status='ativo', tenant_id=_tid_servicos)
          .filter(Agendamento.data_hora > _agora_brt())
          .order_by(Agendamento.data_hora.asc())
          .first()) if _tid_servicos else None
    if ag:
        _forma_label = {'dinheiro': 'Pagar no local', 'pix': 'PIX', 'cartao': 'Cartão'}
        _tem_func = Funcionario.query.filter_by(tenant_id=_tid_servicos, ativo=True).count() > 0
        if not _tem_func:
            _barb_nome = None
        else:
            _, gestor_nome = _gestor_como_barbeiro(_api_tid())
            if ag.funcionario_id in (0, None):
                _barb_nome = gestor_nome or 'Gestor'
            elif ag.funcionario:
                _barb_nome = ag.funcionario.nome
            else:
                _barb_nome = None
        agendamento_info = {
            'id': ag.id,
            'dia': f"{DIAS_PT[ag.data_hora.weekday()]}, {ag.data_hora.day} de {MESES_PT[ag.data_hora.month-1]}",
            'hora': ag.data_hora.strftime('%H:%M'),
            'forma_pagamento': _forma_label.get(ag.forma_pagamento or '', ''),
            'barbeiro': _barb_nome,
        }
    sd = _get_setting('dias_agenda', _api_tid())
    dias_agenda = _int_seguro(sd.value if sd and sd.value else 20, 20, 1, 60)
    tenant = get_tenant_atual()
    tenant_slug = tenant.slug if tenant else None
    return render_template('servicos.html', agendamento_info=agendamento_info, dias_agenda=dias_agenda, tenant_slug=tenant_slug)

# ── Google OAuth ──────────────────────────────────────────────────────────────

@app.route('/auth/google')
def auth_google():
    if not GOOGLE_CLIENT_ID:
        flash('Login com Google não está configurado.', 'error')
        return redirect(url_for('index'))
    tenant = get_tenant_atual()
    tid    = tenant.id if tenant else 0
    host   = request.host
    nonce  = secrets.token_hex(16)
    # state = "nonce-tid-host-sig" (sem base64, sem padding issues)
    payload = f"{nonce}-{tid}-{host}"
    sig     = hmac.new(app.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]
    state   = f"{payload}-{sig}"
    params = urlencode({
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account',
    })
    return redirect(f'https://accounts.google.com/o/oauth2/v2/auth?{params}')

@app.route('/auth/google/callback')
def auth_google_callback():
    state = request.args.get('state', '')
    tid_from_state = 0
    oauth_host = ''
    try:
        # format: "nonce-tid-host-sig" onde sig tem 20 chars hex
        sig      = state[-20:]
        payload  = state[:-21]  # remove "-sig"
        expected = hmac.new(app.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]
        if not hmac.compare_digest(sig, expected):
            raise ValueError('sig inválida')
        parts = payload.split('-', 2)   # nonce(32 hex) - tid - host
        tid_from_state = int(parts[1])
        oauth_host     = parts[2]
    except Exception:
        flash('Erro de segurança no login. Tente novamente.', 'error')
        return redirect(url_for('index'))
    code = request.args.get('code')
    if not code:
        flash('Login cancelado.', 'error')
        return redirect(url_for('index'))
    token_res = req_http.post('https://oauth2.googleapis.com/token', data={
        'code': code,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'grant_type': 'authorization_code',
    }, timeout=10)
    token_data = token_res.json()
    access_token = token_data.get('access_token')
    if not access_token:
        flash('Falha ao autenticar com Google.', 'error')
        return redirect(url_for('index'))
    user_info = req_http.get('https://www.googleapis.com/oauth2/v3/userinfo',
                              headers={'Authorization': f'Bearer {access_token}'}, timeout=10).json()
    google_id = user_info.get('sub')
    email     = user_info.get('email', '').strip().lower()
    nome      = user_info.get('name', 'Usuário')
    if not google_id or not email:
        flash('Não foi possível obter dados do Google.', 'error')
        return redirect(url_for('index'))
    tid = tid_from_state or session.get(_SESSION_PATH_TENANT_ID) or _api_tid()
    user = User.query.filter_by(google_id=google_id, tenant_id=tid).first()
    if not user:
        user = User.query.filter_by(email=email, tenant_id=tid).first()
        if user:
            if not user.google_id:
                user.google_id = google_id
                db.session.commit()
        else:
            user = User(
                name=nome, email=email,
                senha=secrets.token_hex(32),
                google_id=google_id, contact=None,
                receber_lembretes=True, guest=False, tenant_id=tid,
            )
            db.session.add(user)
            try:
                db.session.commit()
            except Exception:
                # C5: race condition — outro request criou o mesmo usuário simultaneamente
                db.session.rollback()
                user = (User.query.filter_by(google_id=google_id, tenant_id=tid).first() or
                        User.query.filter_by(email=email, tenant_id=tid).first())
                if not user:
                    flash('Erro ao criar conta. Tente novamente.', 'error')
                    return redirect(url_for('index'))
    # M1: atribuição direta em vez de session.clear() para preservar path_tenant_id
    # e outros dados úteis já presentes na sessão antes do callback OAuth
    session[_SESSION_USER_ID]       = user.id
    session[_SESSION_USER_NAME]     = user.name
    session[_SESSION_USER_EMAIL]    = user.email
    session[_SESSION_PATH_TENANT_ID] = tid
    # C4: só redirecionar para subdomínios conhecidos do APP_DOMAIN (evita open redirect)
    _base = ''
    if oauth_host and oauth_host != request.host:
        _allowed_suffix = f".{APP_DOMAIN}"
        if oauth_host == APP_DOMAIN or oauth_host.endswith(_allowed_suffix):
            _base = f"https://{oauth_host}"
    if not user.contact:
        return redirect(f"{_base}{url_for('google_contato')}")
    return redirect(f"{_base}{url_for('servicos')}")

@app.route('/auth/google/contato', methods=['GET', 'POST'])
def google_contato():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    erro = None
    if request.method == 'POST':
        tel = ''.join(c for c in request.form.get('telefone', '') if c.isdigit())
        if len(tel) < 10:
            erro = 'Telefone inválido — mínimo 10 dígitos.'
        else:
            user = db.session.get(User, session[_SESSION_USER_ID])
            # H3: garantir que o user pertence ao tenant da sessão atual
            if user and user.tenant_id != session.get(_SESSION_PATH_TENANT_ID):
                return redirect(url_for('index'))
            if user:
                user.contact = tel
                user.receber_lembretes = request.form.get('lembretes', '1') != '0'
                db.session.commit()
            return redirect(url_for('servicos'))
    user = db.session.get(User, session[_SESSION_USER_ID])
    return render_template('google_contato.html', user=user, erro=erro)

# ── Perfil — atualização de campo individual ──────────────────────────────────

@app.route('/api/perfil/update', methods=['POST'])
def api_perfil_update():
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    data  = request.get_json(force=True) or {}
    campo = data.get('campo', '').strip()
    valor = data.get('valor', '').strip()
    _CAMPOS_PERMITIDOS = {'telefone', 'email', 'nome'}  # H1: whitelist — name/contact/receber_lembretes
    if campo not in _CAMPOS_PERMITIDOS:
        return jsonify({'erro': 'Campo inválido'}), 400
    user  = db.session.get(User, session[_SESSION_USER_ID])
    if not user:
        return jsonify({'erro': 'usuário não encontrado'}), 404
    if campo == 'telefone':
        tel = ''.join(c for c in valor if c.isdigit())
        if len(tel) < 10:
            return jsonify({'erro': 'Telefone inválido — mínimo 10 dígitos'}), 400
        user.contact = tel
    elif campo == 'email':
        email = valor.strip().lower()
        if not email or not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
            return jsonify({'erro': 'E-mail inválido'}), 400
        conflito = User.query.filter(User.email == email, User.id != user.id, User.tenant_id == user.tenant_id).first()
        if conflito:
            return jsonify({'erro': 'E-mail já está em uso'}), 400
        user.email = email
        session[_SESSION_USER_EMAIL] = email
    elif campo == 'nome':
        if len(valor) < 2:
            return jsonify({'erro': 'Nome muito curto'}), 400
        user.name = valor
        session[_SESSION_USER_NAME] = valor
    else:
        return jsonify({'erro': 'Campo inválido'}), 400
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/perfil')
def perfil():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    user = db.session.get(User, session[_SESSION_USER_ID])
    if not user:
        return redirect(url_for('logout'))
    return render_template('perfil.html', user=user, hide_fabs=True)

@app.route('/perfil/observacao', methods=['POST'])
def salvar_observacao():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    user = db.session.get(User, session[_SESSION_USER_ID])
    if not user:
        return redirect(url_for('logout'))
    user.observation = request.form.get('observation', '').strip() or None
    db.session.commit()
    return redirect(url_for('perfil'))

@app.route('/perfil/lembretes', methods=['POST'])
def salvar_lembretes():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    user = db.session.get(User, session[_SESSION_USER_ID])
    if not user:
        return redirect(url_for('logout'))
    user.receber_lembretes = request.form.get('receber_lembretes') == '1'
    db.session.commit()
    return redirect(url_for('perfil'))

def _enviar_email(dest, assunto, corpo):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = assunto
        msg['From']    = MAIL_FROM
        msg['To']      = dest
        msg.attach(MIMEText(corpo, 'html', 'utf-8'))
        with smtplib.SMTP(MAIL_HOST, MAIL_PORT, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(MAIL_USER, MAIL_PASSWORD)
            smtp.sendmail(MAIL_FROM, [dest], msg.as_string())
        return True
    except Exception as e:
        app.logger.error('[EMAIL] falha ao enviar para %s: %s', dest, e)
        return False

def _enviar_confirmacao_agendamento(user, data_hora):
    if not user or not user.email or user.email.endswith('@ibarber.local'):
        return
    data_fmt = f"{DIAS_PT[data_hora.weekday()]}, {data_hora.day} de {MESES_PT[data_hora.month-1]}"
    hora_fmt = data_hora.strftime('%H:%M')
    _corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                background:#0f0f0f;color:#f0f0f0;padding:28px;border-radius:10px;">
      <h2 style="color:#C9A96E;margin-top:0;">✦ Agendamento Confirmado!</h2>
      <p>Olá, <strong>{html.escape(user.name)}</strong>! Seu horário foi reservado.</p>
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
    _enviar_email(user.email, 'Agendamento confirmado — Barbearia', _corpo)

def _enviar_cancelamento_por_fechamento(user, tenant_nome, data_hora, motivo):
    if not user or not getattr(user, 'email', None):
        return
    data_fmt = f"{DIAS_PT[data_hora.weekday()]}, {data_hora.day} de {MESES_PT[data_hora.month-1]}"
    hora_fmt = data_hora.strftime('%H:%M')
    _corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                background:#0f0f0f;color:#f0f0f0;padding:28px;border-radius:10px;">
      <h2 style="color:#f87171;margin-top:0;">⚠️ Agendamento Cancelado</h2>
      <p>Olá, <strong>{html.escape(user.name)}</strong>!</p>
      <p style="color:#ccc;">Seu agendamento foi cancelado:</p>
      <p style="color:#f87171;font-weight:600;">{motivo}</p>
      <div style="background:#1a1a1a;border-left:4px solid #f87171;
                  padding:16px 20px;border-radius:6px;margin:20px 0;">
        <p style="margin:0;font-size:15px;color:#888;">📅 {data_fmt}</p>
        <p style="margin:8px 0 0;font-size:32px;font-weight:bold;
                  color:#f87171;letter-spacing:2px;">⏰ {hora_fmt}</p>
      </div>
      <p style="color:#888;font-size:13px;">
        Pedimos desculpas pelo transtorno. Entre em contato com a barbearia para reagendar.
      </p>
      <p style="color:#888;font-size:13px;">— {tenant_nome}</p>
    </div>
    """
    _enviar_email(user.email, f'Agendamento cancelado — {tenant_nome}', _corpo)

def _conflitos_agendamentos_futuros(tenant_id, data=None, funcionario_id=None):
    """Retorna agendamentos futuros ativos filtrados por data e/ou funcionario_id."""
    q = Agendamento.query.filter(
        Agendamento.tenant_id == tenant_id,
        Agendamento.status == 'ativo',
        Agendamento.data_hora > _agora_brt()
    )
    if data:
        q = q.filter(db.func.date(Agendamento.data_hora) == data)
    if funcionario_id is not None:
        q = q.filter(Agendamento.funcionario_id == funcionario_id)
    result = []
    for ag in q.order_by(Agendamento.data_hora).all():
        user = db.session.get(User, ag.user_id)
        result.append({
            'ag': ag, 'user': user,
            'nome': user.name if user else 'Cliente',
            'data_hora_fmt': ag.data_hora.strftime('%d/%m/%Y %H:%M'),
        })
    return result

def _cancelar_conflitos(conflitos, tenant_nome, motivo):
    for c in conflitos:
        c['ag'].status = 'cancelado'
        if c['user']:
            _enviar_cancelamento_por_fechamento(c['user'], tenant_nome, c['ag'].data_hora, motivo)

def _enviar_comprovante_pagamento(user, pedido):
    itens_html = ''.join([
        f'<tr><td style="padding:6px 0;color:#ccc;">{i.nome}</td>'
        f'<td style="padding:6px 0;color:#C9A96E;text-align:right;">'
        f'R$ {i.preco:.2f}</td></tr>'
        for i in pedido.itens
    ])
    _corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                background:#0f0f0f;color:#f0f0f0;padding:28px;border-radius:10px;">
      <h2 style="color:#C9A96E;margin-top:0;">✦ Pagamento Confirmado!</h2>
      <p>Olá, <strong>{html.escape(user.name)}</strong>! Seu pagamento foi aprovado.</p>
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
    _enviar_email(user.email, 'Comprovante de pagamento — Barbearia', _corpo)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

def _build_ag_tema_override(args):
    bg      = _safe_color(args.get('fundo',      ''), '#0c0c0c')
    surface = _safe_color(args.get('superficie', ''), '#161616')
    gold    = _safe_color(args.get('destaque',   ''), '#C9A96E')
    texto   = _safe_color(args.get('texto',      ''), '#F0ECE4')
    borda   = _safe_color(args.get('borda',      ''), '#2A2A2A')
    fonte_t = re.sub(r'[^A-Za-z0-9 ]', '', args.get('fonteTitulo','Playfair Display'))[:50]
    fonte_c = re.sub(r'[^A-Za-z0-9 ]', '', args.get('fonteCorpo', 'Inter'))[:50]
    radius  = _safe_radius(args.get('cardRadius', '6'))
    estilo  = args.get('btnEstilo',  'arredondado')
    hero    = re.sub(r'["\'\\\r\n<>]', '', args.get('heroUrl', ''))
    btn_r   = '999px' if estilo=='pilula' else ('0px' if estilo=='angular' else f'{radius}px')
    hero_css = (
        f'.hero-central{{background-image:url("{hero}");background-size:cover;background-position:center}}'
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
    fundo      = _safe_color(args.get('fundo',      ''), '#080808')
    superficie = _safe_color(args.get('superficie', ''), '#111111')
    borda      = _safe_color(args.get('borda',      ''), '#222222')
    destaque   = _safe_color(args.get('destaque',   ''), '#C8C8C8')
    texto      = _safe_color(args.get('texto',      ''), '#F2F2F2')
    fonte_t    = re.sub(r'[^A-Za-z0-9 ]', '', args.get('fonteTitulo','Playfair Display'))[:50]
    fonte_c    = re.sub(r'[^A-Za-z0-9 ]', '', args.get('fonteCorpo', 'Inter'))[:50]
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
    gt_tema_override = _gt_tema_override_css(request.args)
    gt_preview_nome  = 'João Silva'
    gt_logo_url      = request.args.get('logoUrl', '')[:300]
    gt_nome_display  = request.args.get('nomeDisplay', '')[:60]

    hoje_dt = datetime.utcnow()
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

    _all_perms = {k: True for k in ['agendamentos','calendario','marcar','clientes',
                                    'servicos','precos','pedidos','entradas','fotos']}
    ctx = dict(
        gt_tema_override=gt_tema_override,
        gt_preview_nome=gt_preview_nome,
        gt_logo_url=gt_logo_url,
        gt_nome_display=gt_nome_display,
        token='preview',
        gestao_is_owner=True,
        gestao_perms=_all_perms,
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
    tema_override = _build_ag_tema_override(request.args)
    resp = make_response(render_template('index.html',
        user=None, preview_mode=True, tema_override=tema_override, hide_fabs=True))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/sem-cadastro')
def preview_ag_sem_cadastro():
    tema_override = _build_ag_tema_override(request.args)
    resp = make_response(render_template('index.html',
        user=None, preview_mode=True, tema_override=tema_override, hide_fabs=True,
        auto_rapido=True, auto_criar=True))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/google-contato')
def preview_ag_google_contato():
    tema_override = _build_ag_tema_override(request.args)
    class MockUser:
        email = 'carlos@gmail.com'; name = 'Carlos Silva'
    resp = make_response(render_template('google_contato.html',
        user=MockUser(), erro=None, hide_fabs=True, tema_override=tema_override, preview_mode=True))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/perfil')
def preview_ag_perfil():
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
    tema_override = _build_ag_tema_override(request.args)
    resp = make_response(render_template('esqueci_senha.html',
        hide_fabs=True, tema_override=tema_override, preview_mode=True))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/register')
def preview_ag_register():
    tema_override = _build_ag_tema_override(request.args)
    resp = make_response(render_template('register.html',
        hide_fabs=True, tema_override=tema_override, preview_mode=True))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/ag/servicos')
def preview_ag_servicos():
    tema_override = _build_ag_tema_override(request.args)
    session[_SESSION_USER_ID]    = 9991
    session[_SESSION_USER_NAME]  = 'Carlos Silva'
    session[_SESSION_USER_EMAIL] = 'carlos@preview.com'
    session[_SESSION_IS_PREVIEW] = True
    session.pop(_SESSION_IS_GUEST, None)
    resp = make_response(render_template('servicos.html',
        agendamento_info=None, dias_agenda=20, tema_override=tema_override,
        preview_mode=True, hide_fabs=True, preview_cats=_PREVIEW_CATS))
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return resp

@app.route('/preview/demo/login')
def preview_demo_login():
    if request.args.get('origem') != 'editor':
        return '', 403
    session[_SESSION_USER_ID]   = 9991
    session[_SESSION_USER_NAME] = 'Cliente Demo'
    session[_SESSION_TENANT_ID] = 999
    session[_SESSION_IS_PREVIEW] = True
    _next = request.args.get('next', '/servicos')
    if not _next.startswith('/') or _next.startswith('//'):
        _next = '/servicos'
    return redirect(_next)

@app.route('/preview/demo/logout')
def preview_demo_logout():
    session.clear()
    return '', 200

@app.route('/confirmar-pedido', methods=['POST'])
@limiter.limit('20 per minute')
def confirmar_pedido():
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401

    data  = request.get_json(silent=True) or {}
    itens = data.get('itens', [])
    _tid  = session.get(_SESSION_PATH_TENANT_ID) or session.get(_SESSION_TENANT_ID) or _api_tid()

    if not isinstance(itens, list) or not itens or len(itens) > 20:
        return jsonify({'erro': 'itens inválido'}), 400

    # Todo serviço precisa existir no catálogo do tenant. Aceitar itens
    # desconhecidos deixaria nome e preço nas mãos do cliente.
    nomes = [str(i.get('nome', '')).strip() for i in itens if isinstance(i, dict)]
    if len(nomes) != len(itens):
        return jsonify({'erro': 'itens inválido'}), 400
    catalogo = {s.nome: s for s in Servico.query.filter(
        Servico.tenant_id == _tid,
        Servico.ativo.is_(True),
        Servico.nome.in_(nomes)).all()}
    ausente = next((n for n in nomes if n not in catalogo), None)
    if ausente is not None:
        return jsonify({'erro': f'serviço indisponível: {ausente[:60]}'}), 400

    total = round(sum(catalogo[n].preco or 0 for n in nomes), 2)
    pedido = Pedido(user_id=session[_SESSION_USER_ID], total=total,
                    tenant_id=_tid)
    db.session.add(pedido)
    db.session.flush()
    for n in nomes:
        sv = catalogo[n]
        db.session.add(PedidoItem(
            pedido_id=pedido.id,
            nome=sv.nome,                                            # do catálogo, não do cliente
            categoria=sv.cat_ref.nome if sv.cat_ref else (sv.categoria or ''),
            preco=sv.preco or 0,
        ))
    db.session.commit()

    return jsonify({'total': total, 'pedido_id': pedido.id})

@app.route('/agendar', methods=['POST'])
@limiter.limit('5 per minute')
def agendar():
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    data = request.get_json() or {}
    pedido_id     = data.get('pedido_id')
    data_hora_str = data.get('data_hora', '')
    _tid_ag = _api_tid()
    # Garantir que o pedido pertence ao usuário e ao tenant correto
    if pedido_id:
        _ped = db.session.get(Pedido, pedido_id)
        if not _ped or _ped.user_id != session[_SESSION_USER_ID] or _ped.tenant_id != _tid_ag:
            return jsonify({'erro': 'pedido inválido'}), 403
    try:
        data_hora = datetime.fromisoformat(data_hora_str)
    except Exception:
        return jsonify({'erro': 'data inválida'}), 400
    if data_hora <= _agora_brt():
        return jsonify({'erro': 'Data inválida. Escolha uma data futura.'}), 400
    existente = (Agendamento.query
                 .filter_by(user_id=session[_SESSION_USER_ID], status='ativo', tenant_id=_tid_ag)
                 .filter(Agendamento.data_hora > _agora_brt())
                 .first())
    if existente:
        return jsonify({'erro': 'Você já possui um agendamento marcado.'}), 400

    data_str = data_hora.strftime('%Y-%m-%d')
    dias_s = _get_setting('dias_fechados', _api_tid())
    if dias_s and dias_s.value and data_str in json.loads(dias_s.value):
        return jsonify({'erro': 'Este dia não está disponível.'}), 400

    ativos = _funcionarios_ativos_para_data(data_str)
    capacidade = max(1, len(ativos))

    duracao_total = data.get('duracao_total')
    _dur_s = _get_setting('intervalo_minutos', _api_tid())
    _dur_default = _duracao_segura(_dur_s.value if _dur_s and _dur_s.value else 40)
    # H7: duração precisa ser inteiro dentro do intervalo permitido — 0/negativa
    # travaria os geradores de slot e valores altos bloqueariam a agenda inteira
    if duracao_total is None or duracao_total == '':
        _dur_req = _dur_default
    else:
        try:
            _dur_req = int(duracao_total)
        except (TypeError, ValueError):
            return jsonify({'erro': 'duração inválida'}), 400
        if not (DURACAO_MIN <= _dur_req <= DURACAO_MAX):
            return jsonify({'erro': f'duração deve estar entre {DURACAO_MIN} e {DURACAO_MAX} minutos'}), 400
    _data_hora_fim = data_hora + timedelta(minutes=_dur_req)

    # C6: with_for_update() serializa requisições concorrentes para o mesmo dia/tenant
    inicio_dia = datetime.combine(data_hora.date(), datetime.min.time())
    fim_dia    = inicio_dia + timedelta(days=1)
    ags_dia = (Agendamento.query
               .filter_by(status='ativo', tenant_id=_api_tid())
               .filter(Agendamento.data_hora >= inicio_dia,
                       Agendamento.data_hora < fim_dia)
               .with_for_update()
               .all())
    ags_slot = [
        ag for ag in ags_dia
        if ag.data_hora < _data_hora_fim and
           ag.data_hora + timedelta(minutes=ag.duracao_total or _dur_default) > data_hora
    ]
    if len(ags_slot) >= capacidade:
        return jsonify({'erro': 'Este horário já está cheio. Escolha outro.'}), 400

    # Determinar funcionario_id — validar que é um funcionário ativo do tenant
    funcionario_id = data.get('funcionario_id')
    _ativos_ids = {f['id'] for f in ativos} | {0}  # 0 = proprietário
    if funcionario_id is not None:
        funcionario_id = int(funcionario_id)
        if funcionario_id not in _ativos_ids:
            return jsonify({'erro': 'funcionário inválido'}), 400
        if funcionario_id == 0:
            funcionario_id = None  # proprietário não tem registro em funcionario
    else:
        # Auto-atribuir funcionário livre
        ocupados_ids = {ag.funcionario_id for ag in ags_slot if ag.funcionario_id}
        livres = [f for f in ativos if f['id'] not in ocupados_ids]
        if livres:
            funcionario_id = random.choice(livres)['id']
    ag = Agendamento(user_id=session[_SESSION_USER_ID], pedido_id=pedido_id,
                     data_hora=data_hora, funcionario_id=funcionario_id,
                     duracao_total=_dur_req if duracao_total else None,
                     tenant_id=session.get(_SESSION_PATH_TENANT_ID) or session.get(_SESSION_TENANT_ID) or _api_tid())
    db.session.add(ag)
    db.session.commit()

    user = db.session.get(User, session[_SESSION_USER_ID])
    if user:
        _enviar_confirmacao_agendamento(user, data_hora)

    return jsonify({'ok': True, 'id': ag.id})

@app.route('/cancelar-agendamento/<int:ag_id>', methods=['POST'])
def cancelar_agendamento(ag_id):
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    tid_cliente = session.get(_SESSION_PATH_TENANT_ID)
    ag = db.session.get(Agendamento, ag_id)
    if not ag or ag.user_id != session[_SESSION_USER_ID]:
        return jsonify({'erro': 'não encontrado'}), 404
    if tid_cliente and ag.tenant_id != tid_cliente:
        return jsonify({'erro': 'não encontrado'}), 404
    # H2: rejeitar agendamentos já passados (diferença negativa enganaria a checagem < 3600)
    if ag.data_hora <= _agora_brt():
        return jsonify({'erro': 'Agendamento já ocorreu e não pode ser cancelado.'}), 400
    diferenca = (ag.data_hora - _agora_brt()).total_seconds()
    if diferenca < 3600:  # menos de 1h
        return jsonify({'erro': 'Cancelamento não permitido com menos de 1h de antecedência.'}), 400
    ag.status = 'cancelado'
    if ag.pedido_id:
        pedido = db.session.get(Pedido, ag.pedido_id)
        # M5: registrar status original antes de cancelar para preservar entradas monetárias de pedidos pagos
        pedido_ja_pago = pedido and pedido.status == 'pago'
        if pedido:
            pedido.status = 'cancelado'
        if not pedido_ja_pago:
            EntradaMonetaria.query.filter_by(pedido_id=ag.pedido_id).delete()
    db.session.commit()
    # Email de cancelamento ao cliente
    user = db.session.get(User, ag.user_id)
    if user and user.email and not user.email.endswith('@ibarber.local'):
        data_fmt = f"{DIAS_PT[ag.data_hora.weekday()]}, {ag.data_hora.day} de {MESES_PT[ag.data_hora.month-1]}"
        hora_fmt = ag.data_hora.strftime('%H:%M')
        html_cancel = f"""
        <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                    background:#0f0f0f;color:#f0f0f0;padding:28px;border-radius:10px;">
          <h2 style="color:#c0392b;margin-top:0;">Agendamento Cancelado</h2>
          <p>Olá, <strong>{html.escape(user.name)}</strong>! Seu agendamento foi cancelado.</p>
          <div style="background:#1a1a1a;border-left:4px solid #c0392b;
                      padding:16px 20px;border-radius:6px;margin:20px 0;">
            <p style="margin:0;font-size:15px;color:#888;">📅 {data_fmt}</p>
            <p style="margin:8px 0 0;font-size:28px;font-weight:bold;
                      color:#c0392b;letter-spacing:2px;">⏰ {hora_fmt}</p>
          </div>
          <p style="color:#888;font-size:13px;">Acesse o site para agendar um novo horário.</p>
        </div>
        """
        _enviar_email(user.email, 'Agendamento cancelado — Barbearia', html_cancel)
    # Notificar lista de espera para esse dia
    _notificar_lista_espera(ag.tenant_id, ag.data_hora.strftime('%Y-%m-%d'))
    return jsonify({'ok': True})

@app.route('/reagendar-agendamento', methods=['POST'])
@limiter.limit('10 per minute')
def reagendar_agendamento():
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    data = request.get_json(silent=True) or {}
    ag_id        = data.get('ag_id')
    nova_dh_str  = data.get('data_hora')
    barbeiro_id  = data.get('barbeiro_id')
    if not ag_id or not nova_dh_str:
        return jsonify({'erro': 'dados inválidos'}), 400
    tid_cliente = session.get(_SESSION_PATH_TENANT_ID)
    ag = db.session.get(Agendamento, ag_id)
    if not ag or ag.user_id != session[_SESSION_USER_ID]:
        return jsonify({'erro': 'não encontrado'}), 404
    if tid_cliente and ag.tenant_id != tid_cliente:
        return jsonify({'erro': 'não encontrado'}), 404
    if ag.status != 'ativo':
        return jsonify({'erro': 'agendamento não está ativo'}), 400
    if (ag.data_hora - _agora_brt()).total_seconds() < 3600:
        return jsonify({'erro': 'Reagendamento não permitido com menos de 1h de antecedência.'}), 400
    try:
        nova_dt = datetime.fromisoformat(nova_dh_str)
    except Exception:
        return jsonify({'erro': 'data inválida'}), 400
    # C7: verificar sobreposição com duração, não só horário exato
    _dur_s = _get_setting('intervalo_minutos', ag.tenant_id)
    _dur_default_r = _duracao_segura(_dur_s.value if _dur_s and _dur_s.value else 40)
    _nova_fim = nova_dt + timedelta(minutes=ag.duracao_total or _dur_default_r)
    ativos = _funcionarios_ativos_para_data(nova_dt.strftime('%Y-%m-%d'), ag.tenant_id)
    capacidade = max(1, len(ativos))
    _inicio_dia_r = datetime.combine(nova_dt.date(), datetime.min.time())
    _fim_dia_r    = _inicio_dia_r + timedelta(days=1)
    ags_dia_r = (Agendamento.query
                 .filter_by(status='ativo', tenant_id=ag.tenant_id)
                 .filter(Agendamento.data_hora >= _inicio_dia_r,
                         Agendamento.data_hora < _fim_dia_r,
                         Agendamento.id != ag.id)
                 .all())
    ags_slot_r = [
        x for x in ags_dia_r
        if x.data_hora < _nova_fim and
           x.data_hora + timedelta(minutes=x.duracao_total or _dur_default_r) > nova_dt
    ]
    if len(ags_slot_r) >= capacidade:
        return jsonify({'erro': 'Horário não disponível'}), 409
    ag.data_hora = nova_dt
    if barbeiro_id is not None:
        _ativos_ids_r = {f['id'] for f in ativos} | {0}
        if int(barbeiro_id) not in _ativos_ids_r:
            return jsonify({'erro': 'funcionário inválido'}), 400
        ag.funcionario_id = None if int(barbeiro_id) == 0 else int(barbeiro_id)
    db.session.commit()
    # M4: enviar email de confirmação ao cliente após reagendamento
    user = db.session.get(User, ag.user_id)
    if user:
        _enviar_confirmacao_agendamento(user, nova_dt)
    return jsonify({'ok': True, 'data_hora': nova_dt.isoformat()})

@app.route('/api/gestao/reagendar', methods=['POST'])
def api_gestao_reagendar():
    tid = verificar_token_perm(request, 'agendamentos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    data = request.get_json(silent=True) or {}
    ag_id       = data.get('ag_id')
    nova_dh_str = data.get('data_hora')
    barbeiro_id = data.get('barbeiro_id')
    if not ag_id or not nova_dh_str:
        return jsonify({'erro': 'dados inválidos'}), 400
    ag = db.session.get(Agendamento, ag_id)
    if not ag or ag.tenant_id != tid:
        return jsonify({'erro': 'não encontrado'}), 404
    if ag.status not in ('ativo',):
        return jsonify({'erro': 'agendamento não está ativo'}), 400
    try:
        nova_dt = datetime.fromisoformat(nova_dh_str)
    except Exception:
        return jsonify({'erro': 'data inválida'}), 400
    # C7: verificar sobreposição com duração, não só horário exato
    _dur_sg = _get_setting('intervalo_minutos', tid)
    _dur_default_g = _duracao_segura(_dur_sg.value if _dur_sg and _dur_sg.value else 40)
    _nova_fim_g = nova_dt + timedelta(minutes=ag.duracao_total or _dur_default_g)
    ativos = _funcionarios_ativos_para_data(nova_dt.strftime('%Y-%m-%d'), tid)
    capacidade = max(1, len(ativos))
    _inicio_dia_g = datetime.combine(nova_dt.date(), datetime.min.time())
    _fim_dia_g    = _inicio_dia_g + timedelta(days=1)
    ags_dia_g = (Agendamento.query
                 .filter_by(status='ativo', tenant_id=tid)
                 .filter(Agendamento.data_hora >= _inicio_dia_g,
                         Agendamento.data_hora < _fim_dia_g,
                         Agendamento.id != ag.id)
                 .all())
    ags_slot_g = [
        x for x in ags_dia_g
        if x.data_hora < _nova_fim_g and
           x.data_hora + timedelta(minutes=x.duracao_total or _dur_default_g) > nova_dt
    ]
    if len(ags_slot_g) >= capacidade:
        return jsonify({'erro': 'Horário não disponível'}), 409
    ag.data_hora = nova_dt
    if barbeiro_id is not None:
        _ativos_ids_g = {f['id'] for f in ativos} | {0}
        if int(barbeiro_id) not in _ativos_ids_g:
            return jsonify({'erro': 'funcionário inválido'}), 400
        ag.funcionario_id = None if int(barbeiro_id) == 0 else int(barbeiro_id)
    db.session.commit()
    # M4: enviar email de confirmação ao cliente após reagendamento pela gestão
    user = db.session.get(User, ag.user_id)
    if user:
        _enviar_confirmacao_agendamento(user, nova_dt)
    return jsonify({'ok': True, 'data_hora': nova_dt.isoformat()})

@app.route('/api/lista-espera', methods=['POST'])
def api_lista_espera_entrar():
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    data    = request.get_json(silent=True) or {}
    data_dh = data.get('data')
    if not data_dh:
        return jsonify({'erro': 'data obrigatória'}), 400
    data_val = (data.get('data') or '').strip()
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', data_val):
        return jsonify({'erro': 'data inválida'}), 400
    tid = _api_tid()
    if not tid:
        return jsonify({'erro': 'tenant inválido'}), 400
    existente = ListaEspera.query.filter_by(
        tenant_id=tid, user_id=session[_SESSION_USER_ID], data=data_dh).first()
    if existente:
        return jsonify({'ok': True, 'ja_inscrito': True})
    db.session.add(ListaEspera(tenant_id=tid, user_id=session[_SESSION_USER_ID], data=data_dh))
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/lista-espera', methods=['DELETE'])
def api_lista_espera_sair():
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    data_dh = request.args.get('data')
    tid = _api_tid()
    le = ListaEspera.query.filter_by(
        tenant_id=tid, user_id=session[_SESSION_USER_ID], data=data_dh).first()
    if le:
        db.session.delete(le)
        db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/lista-espera', methods=['GET'])
def api_lista_espera_status():
    if 'user_id' not in session:
        return jsonify({'inscrito': False})
    data_dh = request.args.get('data')
    tid = _api_tid()
    inscrito = ListaEspera.query.filter_by(
        tenant_id=tid, user_id=session[_SESSION_USER_ID], data=data_dh).first() is not None
    return jsonify({'inscrito': inscrito})

def _notificar_lista_espera(tenant_id, data_str):
    """Envia email para quem está na lista de espera quando abre uma vaga."""
    pendentes = ListaEspera.query.filter_by(
        tenant_id=tenant_id, data=data_str, notificado_em=None).all()
    if not pendentes:
        return
    tenant = db.session.get(Tenant, tenant_id)
    nome_barbearia = tenant.nome if tenant else 'Barbearia'
    slug = tenant.slug if tenant else ''
    for le in pendentes:
        user = db.session.get(User, le.user_id)
        if not user or not user.email or user.email.endswith('@ibarber.local'):
            continue
        try:
            data_obj = datetime.strptime(data_str, '%Y-%m-%d')
            data_fmt = f"{DIAS_PT[data_obj.weekday()]}, {data_obj.day} de {MESES_PT[data_obj.month-1]}"
        except Exception:
            data_fmt = data_str
        _corpo = f"""
        <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                    background:#0f0f0f;color:#f0f0f0;padding:28px;border-radius:10px;">
          <h2 style="color:#C9A96E;margin-top:0;">Abriu uma vaga!</h2>
          <p>Olá, <strong>{html.escape(user.name)}</strong>!</p>
          <p>Uma vaga abriu em <strong>{nome_barbearia}</strong> para <strong>{data_fmt}</strong>.</p>
          <div style="text-align:center;margin:24px 0;">
            <a href="{_tenant_url(slug)}"
               style="background:#C9A96E;color:#000;padding:12px 28px;border-radius:8px;
                      text-decoration:none;font-weight:bold;font-size:15px;">
              Agendar agora
            </a>
          </div>
          <p style="color:#888;font-size:12px;">Corra, as vagas são limitadas!</p>
        </div>
        """
        ok = _enviar_email(user.email, f'Vaga disponível — {nome_barbearia}', _corpo)
        if ok:
            le.notificado_em = datetime.utcnow()
    db.session.commit()

@app.route('/meu-agendamento')
def meu_agendamento():
    if 'user_id' not in session:
        return jsonify({'agendamento': None})
    _tid = _api_tid()
    if not _tid:
        return jsonify({'agendamento': None})
    ag = (Agendamento.query
          .filter_by(user_id=session[_SESSION_USER_ID], status='ativo', tenant_id=_tid)
          .order_by(Agendamento.data_hora.desc()).first())
    if not ag:
        return jsonify({'agendamento': None})
    # Só mostra barbeiro se houver 2+ pessoas (proprietário + funcionários)
    _tem_funcionarios = Funcionario.query.filter_by(tenant_id=ag.tenant_id, ativo=True).count() > 0
    if not _tem_funcionarios:
        func_nome = None
    elif ag.funcionario_id in (0, None):
        _, func_nome = _gestor_como_barbeiro(ag.tenant_id)
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
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    data = request.get_json() or {}
    forma = data.get('forma', '')
    if forma not in ('dinheiro', 'pix', 'cartao'):
        return jsonify({'erro': 'forma inválida'}), 400
    _tid = _api_tid()
    if not _tid:
        return jsonify({'erro': 'tenant não identificado'}), 400
    ag = (Agendamento.query
          .filter_by(user_id=session[_SESSION_USER_ID], status='ativo', tenant_id=_tid)
          .filter(Agendamento.data_hora > _agora_brt()).first())
    if not ag:
        return jsonify({'erro': 'agendamento não encontrado'}), 404
    ag.forma_pagamento = forma
    # Cartão: não criar EntradaMonetaria agora — retorno_pagamento fará isso após confirmação
    if ag.pedido_id and forma != 'cartao':
        EntradaMonetaria.query.filter_by(pedido_id=ag.pedido_id).delete()
        pedido = db.session.get(Pedido, ag.pedido_id)
        user   = db.session.get(User, session[_SESSION_USER_ID])
        if pedido:
            _forma_map = {'dinheiro': 'dinheiro', 'pix': 'pix'}
            servicos = ', '.join(i.nome for i in pedido.itens) if pedido.itens else 'Serviço'
            nome_cliente = user.name if user else 'Cliente'
            entrada = EntradaMonetaria(
                descricao=f'{servicos} — {nome_cliente}',
                valor=pedido.total,
                forma=_forma_map.get(forma, 'dinheiro'),
                pedido_id=ag.pedido_id,
                tenant_id=ag.tenant_id,
            )
            db.session.add(entrada)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/meu-historico')
def meu_historico():
    if 'user_id' not in session:
        return jsonify({'historico': []})
    _tid = _api_tid()
    if not _tid:
        return jsonify({'historico': []})
    ags = (Agendamento.query
           .filter_by(user_id=session[_SESSION_USER_ID], tenant_id=_tid)
           .options(joinedload(Agendamento.funcionario))
           .order_by(Agendamento.data_hora.desc()).limit(20).all())
    pedido_ids = [ag.pedido_id for ag in ags if ag.pedido_id]
    pedidos = (
        {p.id: p for p in
         Pedido.query.filter(Pedido.tenant_id == _tid, Pedido.id.in_(pedido_ids))
                     .options(joinedload(Pedido.itens)).all()}
        if pedido_ids else {}
    )
    _, gestor_nome = _gestor_como_barbeiro(_api_tid())
    resultado = []
    for ag in ags:
        p = pedidos.get(ag.pedido_id)
        if ag.funcionario_id in (0, None):
            barbeiro = gestor_nome or 'Gestor'
        elif ag.funcionario:
            barbeiro = ag.funcionario.nome
        else:
            barbeiro = None
        resultado.append({
            'id': ag.id,
            'data_hora': ag.data_hora.isoformat(),
            'status': ag.status,
            'total': p.total if p else 0,
            'barbeiro': barbeiro,
            'servicos': [{'nome': i.nome, 'preco': i.preco} for i in p.itens] if p else [],
        })
    return jsonify({'historico': resultado})

_SECRET = os.environ.get('SECRET_KEY', 'dev-secret')

TOKEN_TTL_SEGUNDOS = int(os.environ.get('TOKEN_TTL_HORAS', '12')) * 3600

def _gerar_token(tenant_id: int, func_id: int = 0, version: int = 0) -> str:
    iat = int(datetime.utcnow().timestamp())
    msg = f"{tenant_id}:{func_id}:{version}:{iat}"
    sig = hmac.new(_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return base64.b64encode(f"{msg}:{sig}".encode()).decode()

def _extrair_tenant_token(token: str):
    """Retorna (tenant_id, func_id, version) ou (None, None, None) se inválido.
    Aceita o formato legado tid:fid:version (sem emissão) durante a transição,
    mas esse formato não expira — remova o ramo após todos os apps atualizarem."""
    try:
        decoded = base64.b64decode(token.encode()).decode()
        payload, sig = decoded.rsplit(':', 1)
        expected = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected): return None, None, None
        parts = payload.split(':')
        if len(parts) == 4:
            tid, fid, ver, iat = (int(p) for p in parts)
            if datetime.utcnow().timestamp() - iat > TOKEN_TTL_SEGUNDOS:
                return None, None, None          # expirado
            return tid, fid, ver
        if len(parts) == 3:
            return int(parts[0]), int(parts[1]), int(parts[2])
        if len(parts) == 2:
            # formato legado: tid:fid — versão implícita 0
            return int(parts[0]), int(parts[1]), 0
        return None, None, None
    except Exception:
        return None, None, None

def _token_valido(tid, version) -> bool:
    """Valida se a versão do token bate com a versão atual do tenant no banco."""
    if not tid: return False
    t = db.session.get(Tenant, tid)
    if not t: return False
    return (t.token_version or 0) == version

def verificar_token(req):
    """Retorna tenant_id se válido e na versão correta, None caso contrário.
    Se o token for de funcionário, ele precisa continuar ativo e no mesmo
    tenant — demitir alguém corta o acesso na hora."""
    token = req.headers.get('Authorization', '').replace('Bearer ', '').strip()
    tid, fid, ver = _extrair_tenant_token(token)
    if not tid or not _token_valido(tid, ver):
        return None
    if fid:
        f = db.session.get(Funcionario, fid)
        if not f or f.tenant_id != tid or not f.ativo:
            return None
    return tid

def verificar_token_admin(req):
    """Retorna tenant_id se o token pertence ao gestor (func_id==0) e versão válida."""
    token = req.headers.get('Authorization', '').replace('Bearer ', '').strip()
    tid, fid, ver = _extrair_tenant_token(token)
    if tid and fid == 0 and _token_valido(tid, ver):
        return tid
    return None

_PERMS_VALIDAS = ('agendamentos', 'calendario', 'marcar', 'clientes',
                  'servicos', 'precos', 'pedidos', 'entradas', 'fotos')

def verificar_token_perm(req, perm):
    """Retorna tenant_id se o portador do token tem a permissão `perm`.
    Dono (func_id==0) tem tudo. Funcionário precisa estar ativo, pertencer ao
    tenant e ter a flag correspondente — demitir alguém passa a cortar o acesso
    imediatamente, sem depender de rotação de token."""
    token = req.headers.get('Authorization', '').replace('Bearer ', '').strip()
    tid, fid, ver = _extrair_tenant_token(token)
    if not tid or not _token_valido(tid, ver):
        return None
    if fid == 0:
        return tid
    f = db.session.get(Funcionario, fid)
    if not f or f.tenant_id != tid or not f.ativo:
        return None
    return tid if getattr(f, f'perm_{perm}', False) else None

def get_tenant_by_slug(slug):
    return Tenant.query.filter_by(slug=slug, ativo=True).first()

def get_tenant_atual():
    # 1. Subdomínio (produção com *.dominio.com)
    host = request.host.split(':')[0]
    # Domínio principal nunca é um tenant
    if host == APP_DOMAIN or host == f'www.{APP_DOMAIN}':
        return None
    slug = host.split('.')[0]
    _reservados = ('www', 'localhost', '127', 'seuapp', 'ibarber', '0', '192', '10')
    if slug not in _reservados and '.' in request.host:
        tenant = get_tenant_by_slug(slug)
        if tenant and tenant.assinatura_ativa:
            return tenant

    # 2. Rota por caminho (dev / domínio único) via session
    tid = session.get(_SESSION_PATH_TENANT_ID)
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

    # Fallback: gestão ou repersonalizar logados
    _fallback_tid = session.get(_SESSION_GESTAO_TENANT_ID) or session.get(_SESSION_REPERSON_TID)
    if t is None and _fallback_tid:
        try:
            t = db.session.get(Tenant, _fallback_tid)
        except Exception:
            t = None

    # Fallback para preview sem banco
    if t is None and session.get(_SESSION_IS_PREVIEW):
        t = _MockTenant()

    tema_config = {}
    _gt_raw = {}
    _gt_logo_url = ''
    _gt_nome_display = ''
    if t and getattr(t, 'tema', None):
        try:
            raw = json.loads(t.tema)
            if isinstance(raw, dict):
                if 'ag' in raw and isinstance(raw.get('ag'), dict):
                    tema_config = raw['ag']
                    _gt_raw = raw.get('gt', {})
                else:
                    tema_config = raw
                    _gt_raw = raw
                if isinstance(_gt_raw, dict):
                    _gt_logo_url     = _gt_raw.get('logoUrl',     '') or ''
                    _gt_nome_display = _gt_raw.get('nomeDisplay', '') or ''
                # Fallback: se gt não tem logo/nome, herda do ag
                if not _gt_logo_url:
                    _gt_logo_url = tema_config.get('logoUrl', '') or ''
                if not _gt_nome_display:
                    _gt_nome_display = tema_config.get('nomeDisplay', '') or ''
        except Exception:
            pass

    def _build_tema_css(cfg):
        """Gera (css_str, font_link_str) a partir de um dict de config de tema."""
        if not cfg:
            return '', ''
        css = [':root{']
        _bg  = _safe_color(cfg.get('fundo', ''), '')
        _sur = _safe_color(cfg.get('superficie', ''), '')
        _brd = _safe_color(cfg.get('borda', ''), '')
        _gld = _safe_color(cfg.get('destaque', ''), '')
        _txt = _safe_color(cfg.get('texto', ''), '')
        if _bg:  css.append(f"--bg:{_bg};")
        if _sur: css.append(f"--surface:{_sur};--surface2:{_sur};")
        if _brd: css.append(f"--border:{_brd};")
        if _gld: css.append(f"--gold:{_gld};--gold-dim:{_gld}cc;--gold-hover:{_gld}dd;")
        if _txt: css.append(f"--text:{_txt};--text-muted:{_txt}88;--placeholder:{_txt}55;")
        _tf_raw = re.sub(r'[^A-Za-z0-9 ]', '', cfg.get('fonteTitulo', ''))[:50]
        _cf_raw = re.sub(r'[^A-Za-z0-9 ]', '', cfg.get('fonteCorpo', ''))[:50]
        if _tf_raw: css.append(f"--font-serif:'{_tf_raw}',Georgia,serif;")
        if _cf_raw: css.append(f"--font-sans:'{_cf_raw}',system-ui,sans-serif;")
        _cr = _safe_radius(cfg.get('cardRadius'), '')
        if _cr: css.append(f"--radius:{_cr}px;")
        css.append('}')
        css_str = '<style>' + ''.join(css) + '</style>'
        fonts_qs = []
        if _tf_raw: fonts_qs.append(f"family={_tf_raw.replace(' ','+')}:wght@400;600;700")
        if _cf_raw: fonts_qs.append(f"family={_cf_raw.replace(' ','+')}:wght@300;400;500")
        font_link = (f'<link href="https://fonts.googleapis.com/css2?{"&".join(fonts_qs)}&display=swap" rel="stylesheet">'
                     if fonts_qs else '')
        return css_str, font_link

    # Gera CSS de variáveis do tema em Python (mais seguro que Jinja2)
    tema_css, tema_font_link = _build_tema_css(tema_config)
    # CSS separado para o painel de gestão (usa raw['gt'], não raw['ag'])
    gt_tema_css, _ = _build_tema_css(_gt_raw if _gt_raw else tema_config)
    def _build_tema_js(cfg, include_hero=True):
        """Gera JS de btnEstilo/hero a partir de um dict de config de tema."""
        if not cfg:
            return ''
        parts = ['<script>(function(){']
        bs = cfg.get('btnEstilo', '')
        cr = cfg.get('cardRadius', 6)
        if bs:
            br = '999px' if bs == 'pilula' else '0px' if bs == 'angular' else f'{cr}px'
            parts.append(f"document.querySelectorAll('.btn,.btn-gold,.g-btn,.g-btn-primary,.g-btn-outline').forEach(function(el){{el.style.borderRadius='{br}';}});")
        if include_hero:
            hero = cfg.get('heroUrl', '')
            if hero:
                hero_safe = hero.replace('\\', '').replace('"', '').replace("'", '').replace('(', '').replace(')', '')
                parts.append(
                    f"var h=document.querySelector('.hero-central');"
                    f"if(h){{"
                    f"h.style.backgroundImage=\"url('{hero_safe}')\";"
                    f"h.style.backgroundSize='cover';"
                    f"h.style.backgroundPosition='center';"
                    f"h.classList.add('has-hero');"
                    f"document.body.style.backgroundImage='';"
                    f"}}else{{"
                    f"document.body.style.backgroundImage=\"url('{hero_safe}')\";"
                    f"document.body.style.backgroundSize='cover';"
                    f"document.body.style.backgroundPosition='center';"
                    f"document.body.style.backgroundAttachment='fixed';"
                    f"}}"
                )
        parts.append('})();</script>')
        return ''.join(parts) if len(parts) > 2 else ''

    # JS para btnEstilo e heroUrl (site de agendamento)
    tema_js    = _build_tema_js(tema_config, include_hero=True)
    # JS para btnEstilo da gestão (sem hero — gestão não tem seção hero)
    gt_tema_js = _build_tema_js(_gt_raw if _gt_raw else tema_config, include_hero=False)

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

    # FAB visibility — número/URL é pré-requisito; 'mostrar' só pode esconder, nunca mostrar sem dado
    fab_wpp_mostrar = False
    fab_maps_mostrar = False
    if t and not session.get(_SESSION_IS_PREVIEW):
        if t.whatsapp:
            try:
                wpp_cfg = json.loads(t.fab_wpp) if t.fab_wpp else {}
                fab_wpp_mostrar = wpp_cfg.get('mostrar', False)
            except Exception:
                fab_wpp_mostrar = False
        if t.maps_url:
            try:
                maps_cfg = json.loads(t.fab_maps) if t.fab_maps else {}
                fab_maps_mostrar = maps_cfg.get('mostrar', False)
            except Exception:
                fab_maps_mostrar = False

    return {'tenant': t, 'tema_config': tema_config, 'tema_css': tema_css,
            'gt_tema_css': gt_tema_css,
            'tema_font_link': tema_font_link, 'tema_js': tema_js,
            'preview_identity': preview_identity,
            'gt_logo_url': _gt_logo_url, 'gt_nome_display': _gt_nome_display,
            'gt_tema_js': gt_tema_js,
            'fab_wpp_mostrar': fab_wpp_mostrar, 'fab_maps_mostrar': fab_maps_mostrar}

def _get_tenant_para_api():
    """Retorna o tenant a partir do token JWT-like do app Flutter."""
    token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    tid, *_ = _extrair_tenant_token(token)
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
    # Altera identidade pública da barbearia — restrito ao dono
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    tenant = _get_tenant_para_api()
    if not tenant: return jsonify({'erro': 'não encontrado'}), 404
    d = request.get_json(force=True) or {}
    for campo in ('nome', 'whatsapp', 'maps_url'):
        if campo in d:
            if campo == 'maps_url' and d[campo]:
                if not re.match(r'^https?://', str(d[campo])):
                    continue
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
    try:
        # Gestão owner e Bearer token têm prioridade — nunca mostrar preview a donos reais
        tid = session.get(_SESSION_GESTAO_TENANT_ID) or verificar_token(request)
        if not tid:
            # Preview mode (personalizar) — sem tenant real
            if session.get(_SESSION_IS_PREVIEW):
                return jsonify([{'id': c['id'], 'nome': c['nome'], 'icone': c['icone'], 'ordem': i} for i, c in enumerate(_PREVIEW_CATS)])
            # Agendamento (cliente visitando o site)
            tid = session.get(_SESSION_TENANT_ID) or session.get(_SESSION_PATH_TENANT_ID)
            if not tid:
                t = get_tenant_atual()
                tid = t.id if t else None
        if not tid:
            return jsonify([])
        cats = Categoria.query.filter_by(ativo=True, tenant_id=tid).order_by(Categoria.ordem).all()
        return jsonify([{'id': c.id, 'nome': c.nome, 'icone': c.icone, 'ordem': c.ordem} for c in cats])
    except Exception:
        return jsonify([])

@app.route('/api/categorias', methods=['POST'])
def api_categorias_criar():
    tid = verificar_token_perm(request, 'servicos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    d = request.get_json() or {}
    nome = d.get('nome', '').strip()[:100]
    if not nome: return jsonify({'erro': 'nome obrigatório'}), 400
    cat = Categoria(
        nome=nome, icone=(d.get('icone', '✦') or '✦')[:10],
        ordem=Categoria.query.filter_by(tenant_id=tid).count(),
        tenant_id=tid,
    )
    db.session.add(cat); db.session.commit()
    return jsonify({'ok': True, 'id': cat.id, 'nome': cat.nome, 'icone': cat.icone})

@app.route('/api/categorias/<int:cid>', methods=['PUT', 'DELETE'])
def api_categoria_detalhe(cid):
    tid = verificar_token_perm(request, 'servicos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    cat = db.session.get(Categoria, cid)
    if not cat or cat.tenant_id != tid: return jsonify({'erro': 'não encontrado'}), 404
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
    try:
        # Gestão owner e Bearer token têm prioridade — nunca mostrar preview a donos reais
        tid = session.get(_SESSION_GESTAO_TENANT_ID) or verificar_token(request)
        if not tid:
            # Preview mode (personalizar) — sem tenant real
            if session.get(_SESSION_IS_PREVIEW):
                return jsonify(_PREVIEW_SVCS)
            # Agendamento (cliente visitando o site)
            tid = session.get(_SESSION_TENANT_ID) or session.get(_SESSION_PATH_TENANT_ID)
            if not tid:
                t = get_tenant_atual()
                tid = t.id if t else None
        if not tid:
            return jsonify([])
        svs = Servico.query.filter_by(ativo=True, tenant_id=tid).order_by(Servico.categoria_id, Servico.ordem).all()
        return jsonify([{
            'id': s.id, 'nome': s.nome,
            'categoria_id': s.categoria_id,
            'categoria_nome': s.cat_ref.nome if s.cat_ref else (s.categoria or ''),
            'preco': s.preco,
            'duracao': s.duracao or 30
        } for s in svs])
    except Exception:
        return jsonify([])

@app.route('/api/servicos', methods=['POST'])
def api_servicos_criar():
    tid = verificar_token_perm(request, 'servicos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    d    = request.get_json() or {}
    nome = d.get('nome', '').strip()
    if not nome: return jsonify({'erro': 'nome obrigatório'}), 400
    cat_id = d.get('categoria_id')
    preco = _sf(d.get('preco', 0))
    if preco < 0:
        return jsonify({'erro': 'preco inválido'}), 400
    sv = Servico(
        nome=nome,
        categoria='',
        categoria_id=cat_id,
        preco=preco,
        duracao=max(5, min(int(d.get('duracao', 30)), 480)),
        ordem=Servico.query.filter_by(categoria_id=cat_id, ativo=True, tenant_id=tid).count(),
        tenant_id=tid,
    )
    db.session.add(sv); db.session.commit()
    return jsonify({'ok': True, 'id': sv.id})

@app.route('/api/servicos/<int:sid>', methods=['PUT', 'DELETE'])
def api_servico_detalhe(sid):
    tid = verificar_token_perm(request, 'servicos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    sv = db.session.get(Servico, sid)
    if not sv or sv.tenant_id != tid: return jsonify({'erro': 'não encontrado'}), 404
    if request.method == 'DELETE':
        sv.ativo = False; db.session.commit(); return jsonify({'ok': True})
    d = request.get_json() or {}
    if 'nome'         in d: sv.nome         = d['nome'].strip()
    if 'categoria_id' in d: sv.categoria_id = d['categoria_id']
    if 'preco'        in d:
        novo_preco = _sf(d['preco'])
        if novo_preco < 0:
            return jsonify({'erro': 'preco inválido'}), 400
        sv.preco = novo_preco
    if 'duracao'      in d: sv.duracao      = max(5, min(int(d['duracao']), 480))
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/horarios-disponiveis')
@limiter.limit('60 per minute')
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
    duracao   = _duracao_segura(duracao_s.value if duracao_s and duracao_s.value else 40)

    # Duração solicitada pelo cliente (soma dos serviços escolhidos)
    _dur_raw = request.args.get('duracao')
    if _dur_raw is not None:
        try:
            _dur_int = int(_dur_raw)
        except (TypeError, ValueError):
            return jsonify({'erro': 'duração inválida'}), 400
        if not (DURACAO_MIN <= _dur_int <= DURACAO_MAX):
            return jsonify({'erro': f'duração deve estar entre {DURACAO_MIN} e {DURACAO_MAX} minutos'}), 400
        duracao_solicitada = _dur_int
    else:
        duracao_solicitada = duracao

    # Hora atual em BRT
    agora_brt = datetime.utcnow() - timedelta(hours=3)
    if data_obj < agora_brt.date():
        return jsonify({'disponiveis': [], 'tomados': [], 'fechado': False})

    # Busca agendamentos do dia antes de gerar os candidatos
    inicio = datetime.combine(data_obj, datetime.min.time())
    fim    = inicio + timedelta(days=1)
    agendados = (Agendamento.query
                 .filter(Agendamento.data_hora >= inicio,
                         Agendamento.data_hora < fim,
                         Agendamento.status == 'ativo',
                         Agendamento.tenant_id == _tid)
                 .all())
    ativos     = _funcionarios_ativos_para_data(data_str)
    capacidade = max(1, len(ativos))

    def _ag_min(ag): return ag.data_hora.hour * 60 + ag.data_hora.minute
    def _ag_dur(ag): return ag.duracao_total or duracao

    agendados_times = {ag.data_hora.strftime('%H:%M') for ag in agendados}

    fechamento_str = '18:00'
    abertura_str   = '08:00'
    he = HorarioEspecial.query.filter_by(data=data_str, tenant_id=_tid).first()
    if he:
        abertura_str   = he.abertura
        fechamento_str = he.fechamento
    else:
        config_s = _get_setting('horario_funcionamento', _tid)
        if config_s and config_s.value:
            config  = json.loads(config_s.value)
            dia_key = _DIAS_KEYS[data_obj.weekday()]
            dia_cfg = config.get(dia_key, {})
            if not dia_cfg.get('aberto', True):
                return jsonify({'disponiveis': [], 'tomados': [], 'fechado': True})
            abertura_str   = dia_cfg.get('abertura', '08:00')
            fechamento_str = dia_cfg.get('fechamento', '18:00')
        else:
            if data_obj.weekday() == 6:
                return jsonify({'disponiveis': [], 'tomados': [], 'fechado': True})
            fechamento_str = '19:00'

    ah, am = map(int, abertura_str.split(':'))
    fh, fm = map(int, fechamento_str.split(':'))
    _aber_min = ah * 60 + am
    _fech_min = fh * 60 + fm

    slots_fixos_s = _get_setting('slots_predefinidos_ativo', _tid)
    usar_grade_fixa = slots_fixos_s and slots_fixos_s.value == '1'

    if usar_grade_fixa:
        # Grade fixa: abertura → fechamento em passos de duracao (intervalo_minutos)
        todos_slots = _gerar_slots(abertura_str, fechamento_str, duracao)
    else:
        # Dinâmico: passos de duracao_solicitada + fim de agendamentos existentes
        candidatos = set()
        cur = _aber_min
        while cur + duracao_solicitada <= _fech_min:
            candidatos.add(cur)
            cur += duracao_solicitada
        for ag in agendados:
            fim_ag = _ag_min(ag) + _ag_dur(ag)
            if _aber_min <= fim_ag and fim_ag + duracao_solicitada <= _fech_min:
                candidatos.add(fim_ag)
        todos_slots = [f'{m // 60:02d}:{m % 60:02d}' for m in sorted(candidatos)]

    def _slot_disponivel(s):
        s_min = int(s[:2]) * 60 + int(s[3:])
        s_fim = s_min + duracao_solicitada
        if s_fim > _fech_min:
            return False
        conflitos = sum(
            1 for ag in agendados
            if _ag_min(ag) < s_fim and _ag_min(ag) + _ag_dur(ag) > s_min
        )
        return conflitos < capacidade

    # Filtro de intervalo de descanso
    _int_s = _get_setting('intervalos_descanso', _tid)
    _intervalos = json.loads(_int_s.value) if _int_s and _int_s.value else {}
    _dia_key_int = _DIAS_KEYS[data_obj.weekday()]
    _int_cfg = _intervalos.get(_dia_key_int, {})
    _int_ativo = _int_cfg.get('ativo', False)
    if _int_ativo:
        _int_ini = int(_int_cfg.get('inicio', '00:00')[:2]) * 60 + int(_int_cfg.get('inicio', '00:00')[3:])
        _int_fim = int(_int_cfg.get('fim', '00:00')[:2]) * 60 + int(_int_cfg.get('fim', '00:00')[3:])
        def _fora_intervalo(s):
            s_min = int(s[:2]) * 60 + int(s[3:])
            s_fim_slot = s_min + duracao_solicitada
            # slot está dentro do intervalo se começa antes do fim E termina depois do início
            return not (s_min < _int_fim and s_fim_slot > _int_ini)
        todos_slots = [s for s in todos_slots if _fora_intervalo(s)]

    if data_obj == agora_brt.date():
        disponiveis = [
            s for s in todos_slots
            if _slot_disponivel(s) and
               datetime.combine(data_obj, datetime.strptime(s, '%H:%M').time()) > agora_brt
        ]
    else:
        disponiveis = [s for s in todos_slots if _slot_disponivel(s)]

    disponiveis_set = set(disponiveis)
    todos_tomados = sorted(agendados_times - disponiveis_set)

    return jsonify({'disponiveis': disponiveis, 'tomados': todos_tomados, 'fechado': False})

@app.route('/api/barbeiros-lista')
def api_barbeiros_lista():
    """Lista todos os barbeiros ativos do tenant (sem filtro de horário — para preferência)."""
    _tid = _api_tid()
    funs = Funcionario.query.filter_by(tenant_id=_tid, ativo=True).all()
    result = []
    gestor_ativo, gestor_nome = _gestor_como_barbeiro(_tid)
    if gestor_ativo:
        gf = _get_setting('gestor_foto', _tid)
        foto_url = f'/static/uploads/{gf.value}' if gf and gf.value else None
        result.append({'id': 0, 'nome': gestor_nome or 'Proprietário', 'foto_url': foto_url})
    for f in funs:
        foto = f'/static/uploads/{f.foto}' if f.foto else None
        result.append({'id': f.id, 'nome': f.nome, 'foto_url': foto})
    return jsonify({'funcionarios': result})

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
    # Quais já têm agendamento nesse slot (filtrado por tenant via _api_tid já aplicado em ativos)
    _tid = _api_tid()
    ocupados_ids = {
        ag.funcionario_id
        for ag in Agendamento.query
            .filter(Agendamento.tenant_id == _tid,
                    Agendamento.status == 'ativo',
                    Agendamento.data_hora == data_hora,
                    Agendamento.funcionario_id.isnot(None))
            .all()
    }
    disponiveis = [f for f in ativos if f['id'] not in ocupados_ids]
    return jsonify({'funcionarios': disponiveis})

@app.route('/api/gestor-barbeiro', methods=['GET', 'POST'])
def api_gestor_barbeiro():
    """Lê/salva se o gestor conta como barbeiro e seu nome."""
    _tid = verificar_token_admin(request)
    if not _tid: return jsonify({'erro': 'não autorizado'}), 403
    ativo, nome = _gestor_como_barbeiro(_tid)
    if request.method == 'GET':
        gf = _get_setting('gestor_foto', _tid)
        foto_url = f'/static/uploads/{gf.value}' if gf and gf.value else None
        return jsonify({'ativo': ativo, 'nome': nome, 'foto_url': foto_url})
    data = request.get_json(silent=True) or {}
    if 'ativo' in data:
        _upsert_setting('gestor_e_barbeiro', '1' if data['ativo'] else '0', _tid)
        if not data['ativo'] and data.get('cancelar'):
            tenant = db.session.get(Tenant, _tid)
            conflitos = _conflitos_agendamentos_futuros(_tid, funcionario_id=0)
            _cancelar_conflitos(conflitos, tenant.nome if tenant else 'Barbearia',
                                'Barbeiro não está mais disponível')
    if 'nome' in data:
        _upsert_setting('gestor_nome', data['nome'], _tid)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/escala/<data_str>', methods=['GET', 'POST'])
def api_escala(data_str):
    """GET: lista funcionários e se trabalham na data. POST: salva ausências."""
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', data_str):
        return jsonify({'erro': 'data inválida'}), 400
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    todos = Funcionario.query.filter_by(ativo=True, tenant_id=tid).order_by(Funcionario.nome).all()
    emp_ids = [f.id for f in todos]
    ausentes_ids = set()
    if emp_ids:
        ausentes_ids = {
            a.funcionario_id
            for a in FuncionarioAusencia.query.filter(
                FuncionarioAusencia.funcionario_id.in_(emp_ids),
                FuncionarioAusencia.data == data_str
            ).all()
        }
    gestor_ativo, gestor_nome = _gestor_como_barbeiro(tid)
    gestor_ausente_key = f'gestor_ausente_{data_str}'
    gestor_ausente_setting = _get_setting(gestor_ausente_key, tid)
    gestor_trabalhando = not (gestor_ausente_setting and gestor_ausente_setting.value == '1')

    if request.method == 'GET':
        result = [{'id': f.id, 'nome': f.nome, 'trabalhando': f.id not in ausentes_ids} for f in todos]
        if gestor_ativo:
            result.insert(0, {'id': 0, 'nome': gestor_nome or 'Proprietário', 'trabalhando': gestor_trabalhando})
        return jsonify(result)

    # POST: recebe lista de IDs que VÃO trabalhar (0 = gestor)
    data = request.get_json(silent=True) or {}
    trabalhando_ids = set(data.get('trabalhando', []))
    # Salvar ausência do gestor
    if gestor_ativo:
        if 0 in trabalhando_ids:
            if gestor_ausente_setting:
                db.session.delete(gestor_ausente_setting)
        else:
            _upsert_setting(gestor_ausente_key, '1', tid)
    # Salvar ausências dos funcionários
    if emp_ids:
        FuncionarioAusencia.query.filter(
            FuncionarioAusencia.funcionario_id.in_(emp_ids),
            FuncionarioAusencia.data == data_str
        ).delete(synchronize_session=False)
    for f in todos:
        if f.id not in trabalhando_ids:
            db.session.add(FuncionarioAusencia(funcionario_id=f.id, data=data_str))
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/config-horarios', methods=['GET', 'POST'])
def api_config_horarios():
    _tid = verificar_token_admin(request)
    if not _tid: return jsonify({'erro': 'não autorizado'}), 403
    if request.method == 'POST':
        data     = request.get_json(silent=True) or {}
        intervalo = data.pop('intervalo_minutos', None)
        if intervalo is not None:
            _upsert_setting('intervalo_minutos', str(_int_seguro(intervalo, 40, 10, 120)), _tid)
        dias_agenda = data.pop('dias_agenda', None)
        if dias_agenda is not None:
            _upsert_setting('dias_agenda', str(_int_seguro(dias_agenda, 20, 1, 60)), _tid)
        _upsert_setting('horario_funcionamento', json.dumps(data, ensure_ascii=False), _tid)
        db.session.commit()
        return jsonify({'ok': True})
    s  = _get_setting('horario_funcionamento', _tid)
    si = _get_setting('intervalo_minutos', _tid)
    sd = _get_setting('dias_agenda', _tid)
    cfg = json.loads(s.value) if s and s.value else \
          {k: {'aberto': k != 'dom', 'abertura': '08:00', 'fechamento': '18:00'} for k in _DIAS_KEYS}
    cfg['intervalo_minutos'] = _duracao_segura(si.value if si and si.value else 40)
    cfg['dias_agenda'] = _int_seguro(sd.value if sd and sd.value else 20, 20, 1, 60)
    return jsonify(cfg)

@app.route('/api/config-publica', methods=['GET'])
def api_config_publica():
    """Configurações públicas lidas pelo site (sem autenticação)."""
    sd = _get_setting('dias_agenda', _api_tid())
    return jsonify({'dias_agenda': _int_seguro(sd.value if sd and sd.value else 20, 20, 1, 60)})

@app.route('/api/horarios/conflitos', methods=['POST'])
def api_horarios_conflitos():
    """Verifica agendamentos futuros que ficam fora do novo horário semanal."""
    _tid = verificar_token(request)
    if not _tid: return jsonify({'erro': 'token inválido'}), 401
    novos = request.get_json(silent=True) or {}
    DOW_MAP = {'Monday':'seg','Tuesday':'ter','Wednesday':'qua','Thursday':'qui',
               'Friday':'sex','Saturday':'sab','Sunday':'dom'}
    hoje = datetime.utcnow()
    futuros = Agendamento.query.filter(
        Agendamento.tenant_id == _tid,
        Agendamento.status == 'ativo',
        Agendamento.data_hora > hoje
    ).order_by(Agendamento.data_hora).all()
    conflitos = []
    for ag in futuros:
        dia_key = DOW_MAP.get(ag.data_hora.strftime('%A'), '')
        cfg = novos.get(dia_key, {})
        hora = ag.data_hora.strftime('%H:%M')
        if not cfg.get('aberto') or hora < cfg.get('abertura','00:00') or hora >= cfg.get('fechamento','24:00'):
            user = db.session.get(User, ag.user_id)
            conflitos.append({'id': ag.id, 'nome': user.name if user else 'Cliente',
                              'hora': ag.data_hora.strftime('%d/%m/%Y %H:%M')})
    return jsonify({'conflitos': conflitos})

@app.route('/api/dias-fechados/conflitos', methods=['GET'])
def api_dias_fechados_conflitos():
    _tid = verificar_token(request)
    if not _tid: return jsonify({'erro': 'token inválido'}), 401
    data_val = request.args.get('data', '')
    if not data_val:
        return jsonify({'erro': 'data obrigatória'}), 400
    # H6: rejeitar datas que não seguem o formato YYYY-MM-DD (previne SQL injection via data maliciosa)
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', data_val):
        return jsonify({'erro': 'data inválida'}), 400
    conflitos = _conflitos_agendamentos_futuros(_tid, data=data_val)
    return jsonify({'conflitos': [{'id': c['ag'].id, 'nome': c['nome'], 'hora': c['data_hora_fmt']} for c in conflitos]})

@app.route('/api/dias-fechados', methods=['GET', 'POST', 'DELETE'])
def api_dias_fechados():
    _tid = verificar_token_admin(request)
    if not _tid: return jsonify({'erro': 'não autorizado'}), 403
    s = _get_setting('dias_fechados', _tid)
    dias = json.loads(s.value) if s and s.value else []
    if request.method in ('POST', 'DELETE'):
        body = request.get_json(silent=True) or {}
        data_val = body.get('data', '')
        if request.method == 'POST' and data_val:
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', data_val):
                return jsonify({'erro': 'data inválida'}), 400
        if request.method == 'POST' and data_val and data_val not in dias:
            dias.append(data_val); dias.sort()
            if body.get('cancelar'):
                tenant = db.session.get(Tenant, _tid)
                conflitos = _conflitos_agendamentos_futuros(_tid, data=data_val)
                _cancelar_conflitos(conflitos, tenant.nome if tenant else 'Barbearia',
                                    'Dia fechado pela barbearia')
        elif request.method == 'DELETE' and data_val in dias:
            dias.remove(data_val)
        _upsert_setting('dias_fechados', json.dumps(dias), _tid)
        db.session.commit()
        return jsonify({'ok': True, 'dias': dias})
    return jsonify({'dias': dias})

@app.route('/api/horarios-especiais', methods=['GET', 'POST'])
def api_horarios_especiais():
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    if request.method == 'POST':
        d = request.get_json(silent=True) or {}
        data       = d.get('data', '').strip()
        abertura   = d.get('abertura', '08:00').strip()
        fechamento = d.get('fechamento', '18:00').strip()
        acao       = d.get('acao', '')  # '' | 'cancelar' | 'manter'

        if not data: return jsonify({'erro': 'data obrigatória'}), 400
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', data):
            return jsonify({'erro': 'data inválida, use YYYY-MM-DD'}), 400
        if not re.match(r'^\d{2}:\d{2}$', abertura) or not re.match(r'^\d{2}:\d{2}$', fechamento):
            return jsonify({'erro': 'horário inválido, use HH:MM'}), 400
        if abertura >= fechamento:
            return jsonify({'erro': 'abertura deve ser antes do fechamento'}), 400

        # Detecta agendamentos ativos que ficam fora do novo horário
        if not acao:
            data_obj   = datetime.strptime(data, '%Y-%m-%d').date()
            inicio_dia = datetime.combine(data_obj, datetime.min.time())
            fim_dia    = datetime.combine(data_obj, datetime.max.time())
            ags = (Agendamento.query
                   .filter(Agendamento.tenant_id == tid,
                           Agendamento.data_hora >= inicio_dia,
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
                   .filter(Agendamento.tenant_id == tid,
                           Agendamento.data_hora >= inicio_dia,
                           Agendamento.data_hora <= fim_dia,
                           Agendamento.status == 'ativo')
                   .all())
            for ag in ags:
                hora = ag.data_hora.strftime('%H:%M')
                if hora < abertura or hora >= fechamento:
                    ag.status = 'cancelado'

        HorarioEspecial.query.filter_by(data=data, tenant_id=tid).delete()
        db.session.add(HorarioEspecial(data=data, abertura=abertura, fechamento=fechamento, tenant_id=tid))
        db.session.commit()
        return jsonify({'ok': True})
    hes = HorarioEspecial.query.filter_by(tenant_id=tid).order_by(HorarioEspecial.data).all()
    return jsonify([{'id': h.id, 'data': h.data, 'abertura': h.abertura, 'fechamento': h.fechamento} for h in hes])

@app.route('/api/horarios-especiais/<int:hid>', methods=['DELETE'])
def api_horario_especial_del(hid):
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    h = db.session.get(HorarioEspecial, hid)
    if not h or h.tenant_id != tid: return jsonify({'erro': 'não encontrado'}), 404
    db.session.delete(h)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/slots-predefinidos', methods=['GET', 'POST'])
def api_slots_predefinidos():
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    if request.method == 'POST':
        d = request.get_json() or {}
        ativo = bool(d.get('ativo', False))
        _upsert_setting('slots_predefinidos_ativo', '1' if ativo else '0', tid)
        db.session.commit()
        return jsonify({'ok': True})
    ativo_s = _get_setting('slots_predefinidos_ativo', tid)
    return jsonify({'ativo': ativo_s.value == '1' if ativo_s else False})

@app.route('/api/intervalos-descanso', methods=['GET', 'POST'])
def api_intervalos_descanso():
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    if request.method == 'POST':
        d = request.get_json() or {}
        _upsert_setting('intervalos_descanso', json.dumps(d, ensure_ascii=False), tid)
        db.session.commit()
        return jsonify({'ok': True})
    s = _get_setting('intervalos_descanso', tid)
    return jsonify(json.loads(s.value) if s and s.value else {})

@app.route('/api/agendamentos/<int:ag_id>/status', methods=['POST'])
def api_agendamento_status(ag_id):
    tid = verificar_token_perm(request, 'agendamentos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    ag = db.session.get(Agendamento, ag_id)
    if not ag or ag.tenant_id != tid:
        return jsonify({'erro': 'não encontrado'}), 404
    status = (request.get_json(silent=True) or {}).get('status', '').strip()
    if status not in ('ativo', 'cancelado', 'concluido', 'nao_compareceu'):
        return jsonify({'erro': 'status inválido'}), 400
    ag.status = status
    if status in ('cancelado', 'nao_compareceu') and ag.pedido_id:
        pedido = db.session.get(Pedido, ag.pedido_id)
        if pedido:
            pedido.status = status
        # Pagar no local não recebido: remove registro financeiro
        if ag.forma_pagamento == 'dinheiro':
            EntradaMonetaria.query.filter_by(pedido_id=ag.pedido_id).delete()
    db.session.commit()
    return jsonify({'ok': True, 'status': ag.status})

_SENSITIVE_KEYS = {'mp_token', 'mp_public_key', 'pix_chave'}

@app.route('/api/credenciais', methods=['GET', 'POST'])
def api_credenciais():
    _keys = ['pix_chave', 'mp_token', 'mp_public_key', 'pix_ativo', 'cartao_ativo']
    _tid = verificar_token_admin(request)
    if not _tid: return jsonify({'erro': 'token inválido'}), 401
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        for k in _keys:
            if k in data:
                v = str(data[k])
                _upsert_setting(k, _enc(v) if k in _SENSITIVE_KEYS and v else v, _tid)
        db.session.commit()
        return jsonify({'ok': True})
    result = {}
    for k in _keys:
        s = _get_setting(k, _tid)
        raw = s.value if s else ''
        result[k] = _dec(raw) if k in _SENSITIVE_KEYS and raw else raw
    return jsonify(result)

@app.route('/api/credenciais/conta', methods=['POST'])
def api_credenciais_conta():
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    tenant = db.session.get(Tenant, tid)
    if not tenant: return jsonify({'erro': 'não encontrado'}), 404
    data = request.get_json(silent=True) or {}
    email_novo = data.get('email', '').strip().lower()
    senha_nova = data.get('senha', '').strip()
    senha_atual = data.get('senha_atual', '').strip()
    if not email_novo and not senha_nova:
        return jsonify({'erro': 'Nada a atualizar'}), 400
    # Senha atual só é exigida para trocar a senha
    if senha_nova:
        if not senha_atual or not tenant.conferir_senha(senha_atual):
            return jsonify({'erro': 'Senha atual incorreta'}), 400
        if len(senha_nova) < 8:
            return jsonify({'erro': 'Nova senha deve ter mínimo 8 caracteres'}), 400
        tenant.senha = senha_nova
        # Invalida todos os tokens emitidos antes da troca de senha
        tenant.token_version = (tenant.token_version or 0) + 1
    if email_novo and email_novo != tenant.email:
        if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email_novo):
            return jsonify({'erro': 'E-mail inválido'}), 400
        if Tenant.query.filter(Tenant.email == email_novo, Tenant.id != tid).first():
            return jsonify({'erro': 'E-mail já em uso'}), 400
        tenant.email = email_novo
    db.session.commit()
    return jsonify({'ok': True})

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
@limiter.limit('10 per minute')
def criar_pagamento():
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    data      = request.get_json(silent=True) or {}
    metodo    = data.get('metodo', 'pix')
    pedido_id = data.get('pedido_id')

    _tid = _api_tid()
    mp_token_s = _get_setting('mp_token', _tid)
    mp_token   = _dec(mp_token_s.value) if mp_token_s and mp_token_s.value else ''

    if not mp_token:
        return jsonify({'erro': 'Mercado Pago não configurado. Configure o Access Token em Credenciais.'}), 400

    # C2+C3: validar pedido no servidor — rejeitar se não pertencer a este tenant
    if pedido_id:
        pedido_obj = db.session.get(Pedido, pedido_id)
        if not pedido_obj or pedido_obj.tenant_id != _tid:
            return jsonify({'erro': 'pedido não encontrado'}), 404
        total = pedido_obj.total
    else:
        total = _sf(data.get('total', 0))

    if metodo == 'pix':
        email_pagador = (data.get('payer_email') or '').strip().lower() or session.get('user_email', '').strip().lower() or 'cliente@barbearia.com'
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
            return jsonify({'erro': 'Erro ao gerar QR Code'}), 502
        d  = r.json()
        td = d.get('point_of_interaction', {}).get('transaction_data', {})
        return jsonify({
            'mp_payment_id': d['id'],
            'pedido_id':     pedido_id,
            'qr_code':       td.get('qr_code', ''),
            'qr_code_base64': td.get('qr_code_base64', ''),
        })

    else:  # cartao – Checkout Pro
        if not pedido_id:
            return jsonify({'erro': 'pedido_id obrigatório para pagamento com cartão'}), 400
        _base = request.host_url.rstrip('/')
        payload = {
            'items': [{'title': 'Barbearia – Serviços', 'quantity': 1,
                       'unit_price': round(total, 2), 'currency_id': 'BRL'}],
            'external_reference': str(pedido_id or ''),
        'back_urls': {
            'success': _base,
            'failure': _base,
            'pending': _base,
        },
            'auto_return': 'approved',
        }
        r = req_http.post(
            'https://api.mercadopago.com/checkout/preferences',
            json=payload,
            headers={'Authorization': f'Bearer {mp_token}',
                     'Content-Type': 'application/json'},
            timeout=15,
        )
        if r.status_code not in (200, 201):
            return jsonify({'erro': 'Erro MP'}), 502
        d = r.json()
        return jsonify({'checkout_url': d.get('init_point', '')})

@app.route('/api/verificar-pagamento/<int:mp_payment_id>', methods=['GET'])
@limiter.limit('30 per minute')
def verificar_pagamento(mp_payment_id):
    if _SESSION_USER_ID not in session:
        return jsonify({'erro': 'não autenticado'}), 401
    pedido_id  = request.args.get('pedido_id', type=int)
    mp_token_s = _get_setting('mp_token', _api_tid())
    if not mp_token_s or not mp_token_s.value:
        return jsonify({'status': 'unknown'}), 400
    _mp_tok = _dec(mp_token_s.value)
    r = req_http.get(
        f'https://api.mercadopago.com/v1/payments/{mp_payment_id}',
        headers={'Authorization': f'Bearer {_mp_tok}'},
        timeout=10,
    )
    if r.status_code != 200:
        return jsonify({'status': 'unknown'}), 404
    status = r.json().get('status', 'unknown')
    if status == 'approved' and pedido_id:
        pedido = db.session.get(Pedido, pedido_id)
        # O pedido precisa ser do próprio usuário: só conferir o tenant deixaria
        # um cliente quitar o pedido de outro com um pagamento aprovado seu.
        if not pedido or pedido.tenant_id != _api_tid() or pedido.user_id != session[_SESSION_USER_ID]:
            return jsonify({'status': 'unknown'}), 403
        if pedido.status != 'pago':
            pedido.status = 'pago'
            _entrada = EntradaMonetaria.query.filter_by(pedido_id=pedido_id).first()
            if _entrada:
                _entrada.forma = 'pix'
            else:
                db.session.add(EntradaMonetaria(
                    descricao=f'Pedido #{pedido_id} — PIX',
                    valor=pedido.total,
                    forma='pix',
                    pedido_id=pedido_id,
                    tenant_id=pedido.tenant_id,
                ))
            db.session.commit()
            user = db.session.get(User, pedido.user_id)
            if user:
                _enviar_comprovante_pagamento(user, pedido)
    return jsonify({'status': status})

@app.route('/retorno-pagamento', methods=['GET'])
def retorno_pagamento():
    """Redirect-back URL after Checkout Pro card payment (auto_return=approved)."""
    pedido_id  = request.args.get('pedido_id', type=int)
    payment_id = request.args.get('collection_id') or request.args.get('payment_id')
    status_mp  = request.args.get('status', '')
    slug = ''
    if pedido_id:
        pedido = db.session.get(Pedido, pedido_id)
        if pedido:
            # Mesmo critério do /api/verificar-pagamento: o pedido precisa ser
            # do usuário logado, não apenas do mesmo tenant.
            if pedido.tenant_id != _api_tid() or pedido.user_id != session.get(_SESSION_USER_ID):
                return redirect('/')
            _tenant = db.session.get(Tenant, pedido.tenant_id)
            slug = _tenant.slug if _tenant else ''
            if status_mp == 'approved' and payment_id and pedido.status != 'pago':
                mp_token_s = _get_setting('mp_token', pedido.tenant_id)
                mp_token_v = _dec(mp_token_s.value) if mp_token_s and mp_token_s.value else ''
                try:
                    rv = req_http.get(
                        f'https://api.mercadopago.com/v1/payments/{payment_id}',
                        headers={'Authorization': f'Bearer {mp_token_v}'}, timeout=10,
                    )
                    if rv.status_code == 200 and rv.json().get('status') == 'approved':
                        pedido.status = 'pago'
                        entrada = EntradaMonetaria.query.filter_by(pedido_id=pedido_id).first()
                        if entrada:
                            entrada.forma = 'cartao_credito'
                        else:
                            db.session.add(EntradaMonetaria(
                                descricao=f'Pedido #{pedido_id} — Cartão',
                                valor=pedido.total,
                                forma='cartao_credito',
                                pedido_id=pedido_id,
                                tenant_id=pedido.tenant_id,
                            ))
                        db.session.commit()
                        user = db.session.get(User, pedido.user_id)
                        if user:
                            _enviar_comprovante_pagamento(user, pedido)
                except Exception:
                    pass
    return redirect(f'/{slug}' if slug else '/')

@app.route('/api/precos', methods=['GET', 'POST'])
def api_precos():
    tid = verificar_token_perm(request, 'precos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        for nome, valor in data.items():
            sv = Servico.query.filter_by(nome=nome, tenant_id=tid).first()
            if sv:
                sv.preco = _sf(valor)
        db.session.commit()
        svs = Servico.query.filter_by(ativo=True, tenant_id=tid).order_by(Servico.categoria, Servico.ordem).all()
        return jsonify({'ok': True, 'precos': {sv.nome: sv.preco for sv in svs}})
    svs = Servico.query.filter_by(ativo=True, tenant_id=tid).order_by(Servico.categoria, Servico.ordem).all()
    return jsonify({sv.nome: sv.preco for sv in svs})

@app.route('/api/usuarios', methods=['GET', 'POST'])
def api_usuarios():
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
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
            base = f"{slug}.{contact.replace(' ','').replace('-','').replace('(','').replace(')','')}"
            email = f"{base}@admin.local"
            if User.query.filter_by(email=email).first():
                import time; email = f"{base}.{int(time.time())}@admin.local"
        elif User.query.filter_by(email=email, tenant_id=tid).first():
            return jsonify({'erro': 'E-mail já cadastrado'}), 400
        senha_temp = secrets.token_hex(16)
        receber_lembretes = data.get('receber_lembretes', True)
        user = User(name=name, email=email, senha=senha_temp,
                    contact=contact, observation=observation,
                    tenant_id=tid,
                    receber_lembretes=bool(receber_lembretes))
        db.session.add(user)
        db.session.commit()
        return jsonify({'ok': True, 'id': user.id, 'usuario': user_dict(user)})
    usuarios = User.query.filter_by(tenant_id=tid).order_by(User.criado_em.desc()).limit(5000).all()
    return jsonify([user_dict(u) for u in usuarios])

@app.route('/api/usuarios/<int:uid>', methods=['GET'])
def api_usuario(uid):
    tid = verificar_token_perm(request, 'clientes')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    u = db.session.get(User, uid)
    if not u or u.tenant_id != tid:
        return jsonify({'erro': 'não encontrado'}), 404
    data = user_dict(u)
    data['contact'] = u.contact or ''
    data['name']    = u.name
    data['pedidos'] = [pedido_dict(p) for p in u.pedidos]

    _, gestor_nome = _gestor_como_barbeiro(tid)
    ags = (Agendamento.query
           .filter_by(user_id=uid, tenant_id=tid)
           .options(joinedload(Agendamento.funcionario), joinedload(Agendamento.pedido))
           .order_by(Agendamento.data_hora.desc())
           .limit(500)
           .all())
    agendamentos = []
    for ag in ags:
        if ag.funcionario_id in (0, None):
            barbeiro = gestor_nome or 'Proprietário'
        elif ag.funcionario:
            barbeiro = ag.funcionario.nome
        else:
            barbeiro = '—'
        servicos = []
        total = None
        if ag.pedido:
            servicos = [i.nome for i in ag.pedido.itens]
            total = ag.pedido.total
        agendamentos.append({
            'id':        ag.id,
            'data_hora': (ag.data_hora - timedelta(hours=3)).isoformat() if ag.data_hora else None,
            'status':    ag.status,
            'barbeiro':  barbeiro,
            'servicos':  servicos,
            'total':     total,
        })
    data['agendamentos'] = agendamentos
    return jsonify(data)

@app.route('/api/pedidos', methods=['GET'])
def api_pedidos():
    tid = verificar_token_perm(request, 'pedidos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    pedidos = (Pedido.query
               .filter_by(tenant_id=tid)
               .order_by(Pedido.criado_em.desc())
               .limit(5000).all())
    return jsonify([pedido_dict(p) for p in pedidos])

@app.route('/api/pedidos/<int:pid>', methods=['GET'])
def api_pedido(pid):
    tid = verificar_token_perm(request, 'pedidos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    p = db.session.get(Pedido, pid)
    if not p or p.tenant_id != tid:
        return jsonify({'erro': 'não encontrado'}), 404
    return jsonify(pedido_dict(p))

@app.route('/api/pedidos/<int:pid>/status', methods=['POST'])
def api_pedido_status(pid):
    tid = verificar_token_perm(request, 'pedidos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    p = db.session.get(Pedido, pid)
    if not p or p.tenant_id != tid:
        return jsonify({'erro': 'não encontrado'}), 404
    status = (request.get_json(silent=True) or {}).get('status', '').strip()
    if status not in ('pendente', 'pago', 'cancelado', 'nao_compareceu'):
        return jsonify({'erro': 'status inválido'}), 400
    p.status = status
    db.session.commit()
    return jsonify({'ok': True, 'status': p.status})

@app.route('/api/stats/servicos', methods=['GET'])
def api_stats_servicos():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    contagem = {}
    pedidos = Pedido.query.filter(Pedido.tenant_id == tid, Pedido.status != 'cancelado').limit(5000).all()
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
    # inclui receita por barbeiro
    tid = verificar_token_perm(request, 'entradas')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    contagem  = {}
    receita   = {}
    _, gestor_nome = _gestor_como_barbeiro(tid)
    ags = (Agendamento.query
           .filter(Agendamento.tenant_id == tid, Agendamento.status != 'cancelado')
           .options(joinedload(Agendamento.funcionario), joinedload(Agendamento.pedido))
           .all())
    for ag in ags:
        if ag.funcionario_id in (0, None):
            nome = gestor_nome or 'Gestor'
        elif ag.funcionario:
            nome = ag.funcionario.nome
        else:
            continue
        contagem[nome] = contagem.get(nome, 0) + 1
        if ag.pedido and ag.pedido.total:
            receita[nome] = receita.get(nome, 0.0) + float(ag.pedido.total)
    resultado = sorted([
        {'nome': k, 'total': contagem[k], 'receita': round(receita.get(k, 0.0), 2)}
        for k in contagem
    ], key=lambda x: x['receita'], reverse=True)
    return jsonify(resultado)

@app.route('/api/stats/agendamentos_por_mes', methods=['GET'])
def api_stats_ags_mes():
    tid = verificar_token(request)
    if not tid: return jsonify({'erro': 'token inválido'}), 401
    ags = Agendamento.query.filter(Agendamento.tenant_id == tid, Agendamento.status != 'cancelado').limit(5000).all()
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
    ags = Agendamento.query.filter(Agendamento.tenant_id == tid, Agendamento.status != 'cancelado').limit(5000).all()
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
        Agendamento.tenant_id == tid,
        Agendamento.status != 'cancelado',
        Agendamento.forma_pagamento.isnot(None)
    ).limit(5000).all()
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
    tid = verificar_token_perm(request, 'entradas')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    entradas = EntradaMonetaria.query.filter_by(tenant_id=tid).limit(5000).all()
    contagem = {}
    for e in entradas:
        chave = e.criado_em.strftime('%Y-%m')
        contagem[chave] = round(contagem.get(chave, 0.0) + (e.valor or 0), 2)
    resultado = [{'mes': k, 'total': v} for k, v in sorted(contagem.items())]
    return jsonify(resultado)

@app.route('/api/stats/receita_por_forma', methods=['GET'])
def api_stats_receita_forma():
    tid = verificar_token_perm(request, 'entradas')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    entradas = EntradaMonetaria.query.filter_by(tenant_id=tid).limit(5000).all()
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
    pedidos = Pedido.query.filter_by(tenant_id=tid).limit(5000).all()
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
    pedidos = Pedido.query.filter(Pedido.tenant_id == tid, Pedido.status != 'cancelado').limit(5000).all()
    contagem = {}
    for p in pedidos:
        for i in p.itens:
            cat = (i.categoria or 'Sem categoria').strip()
            if cat:
                contagem[cat] = contagem.get(cat, 0) + 1
    resultado = sorted([{'categoria': k, 'total': v} for k, v in contagem.items()],
                       key=lambda x: x['total'], reverse=True)
    return jsonify(resultado)

CSV_LIMIT = 500

# Excel/Sheets interpretam células iniciadas por estes caracteres como fórmula.
# Um cliente cadastrado como =HYPERLINK(...) executaria código na planilha do dono.
_CSV_PREFIXOS_PERIGOSOS = ('=', '+', '-', '@', '\t', '\r')

def _csv_safe(valor):
    s = '' if valor is None else str(valor)
    return "'" + s if s.startswith(_CSV_PREFIXOS_PERIGOSOS) else s

def _csv_row(writer, valores):
    writer.writerow([_csv_safe(v) for v in valores])

@app.route('/api/export', methods=['GET'])
def api_export():
    # Exporta base de clientes e financeiro inteiros — restrito ao dono.
    # Aceita _token na query só para permitir download via <a href>; o valor
    # precisa ser um token de dono (func_id==0) igual ao do header.
    _qt = request.args.get('_token', '')
    if _qt:
        _ext_tid, _ext_fid, _ext_ver = _extrair_tenant_token(_qt)
        tid = _ext_tid if (_ext_tid and _ext_fid == 0 and _token_valido(_ext_tid, _ext_ver)) else None
    else:
        tid = verificar_token_admin(request)
    if not tid:
        return jsonify({'erro': 'não autorizado'}), 403
    tipo   = request.args.get('tipo', 'agendamentos')
    mes    = request.args.get('mes', '')
    inicio = request.args.get('inicio', '')
    fim    = request.args.get('fim', '')

    d_ini = None; d_fim = None
    if mes:
        try:
            y, m = map(int, mes.split('-'))
            d_ini = datetime(y, m, 1)
            d_fim = datetime(y, m, monthrange(y, m)[1], 23, 59, 59)
        except (ValueError, TypeError):
            return jsonify({'erro': 'mês inválido (use AAAA-MM)'}), 400
    elif inicio:
        try:
            d_ini = datetime.strptime(inicio, '%Y-%m-%d')
        except ValueError:
            return jsonify({'erro': 'data inicial inválida (use AAAA-MM-DD)'}), 400
    if fim and not mes:
        try:
            d_fim = datetime.strptime(fim + ' 23:59:59', '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return jsonify({'erro': 'data final inválida (use AAAA-MM-DD)'}), 400

    out = io.StringIO()
    w   = csv.writer(out)
    STATUS_MAP = {'ativo':'Ativo','cancelado':'Cancelado','concluido':'Concluído','nao_compareceu':'Não compareceu','pendente':'Pendente','pago':'Pago'}
    FORMA_MAP  = {'pix':'PIX','cartao_credito':'Cartão Crédito','cartao_debito':'Cartão Débito','dinheiro':'Dinheiro','pagar_no_local':'No local'}

    if tipo == 'agendamentos':
        w.writerow(['Data','Hora','Cliente','Contato','Serviços','Barbeiro','Status','Forma Pagamento'])
        q = Agendamento.query.filter(Agendamento.tenant_id == tid)
        if d_ini: q = q.filter(Agendamento.data_hora >= d_ini)
        if d_fim: q = q.filter(Agendamento.data_hora <= d_fim)
        for ag in q.order_by(Agendamento.data_hora.desc()).limit(CSV_LIMIT).all():
            user   = db.session.get(User, ag.user_id)
            pedido = db.session.get(Pedido, ag.pedido_id) if ag.pedido_id else None
            servs  = ', '.join(i.nome for i in pedido.itens) if pedido else '—'
            barb   = ag.funcionario.nome if ag.funcionario else 'Proprietário'
            _csv_row(w, [ag.data_hora.strftime('%d/%m/%Y'), ag.data_hora.strftime('%H:%M'),
                         user.name if user else '—', user.contact if user else '—',
                         servs, barb, STATUS_MAP.get(ag.status, ag.status),
                         FORMA_MAP.get(ag.forma_pagamento or '', ag.forma_pagamento or '—')])

    elif tipo == 'financeiro':
        w.writerow(['Data','Descrição','Valor (R$)','Forma Pagamento'])
        q = EntradaMonetaria.query.filter(EntradaMonetaria.tenant_id == tid)
        if d_ini: q = q.filter(EntradaMonetaria.criado_em >= d_ini)
        if d_fim: q = q.filter(EntradaMonetaria.criado_em <= d_fim)
        for e in q.order_by(EntradaMonetaria.criado_em.desc()).limit(CSV_LIMIT).all():
            _csv_row(w, [e.criado_em.strftime('%d/%m/%Y'), e.descricao,
                         f'{e.valor:.2f}'.replace('.',','), FORMA_MAP.get(e.forma or '', e.forma or '—')])

    elif tipo == 'clientes':
        w.writerow(['Nome','Email','Contato','Cadastrado em'])
        q = User.query.filter(User.tenant_id == tid)
        for c in q.order_by(User.name).limit(CSV_LIMIT).all():
            _csv_row(w, [c.name, c.email, c.contact or '—', c.criado_em.strftime('%d/%m/%Y')])

    elif tipo == 'servicos':
        w.writerow(['Data','Cliente','Serviço','Categoria','Preço (R$)','Status'])
        q = Pedido.query.filter(Pedido.tenant_id == tid)
        if d_ini: q = q.filter(Pedido.criado_em >= d_ini)
        if d_fim: q = q.filter(Pedido.criado_em <= d_fim)
        for p in q.options(joinedload(Pedido.itens), joinedload(Pedido.usuario)).order_by(Pedido.criado_em.desc()).limit(CSV_LIMIT).all():
            for item in p.itens:
                _csv_row(w, [p.criado_em.strftime('%d/%m/%Y'),
                             p.usuario.name if p.usuario else '—',
                             item.nome, item.categoria or '—',
                             f'{item.preco:.2f}'.replace('.',','),
                             STATUS_MAP.get(p.status, p.status)])

    # tipo/mes vêm da query string — sanitizar evita injeção de cabeçalho HTTP
    _sufixo = re.sub(r'[^A-Za-z0-9_-]', '', str(mes or inicio or 'todos'))[:20] or 'todos'
    _tipo_s = re.sub(r'[^a-z]', '', str(tipo))[:20] or 'dados'
    nome = f'ibarber_{_tipo_s}_{_sufixo}.csv'
    return Response('﻿' + out.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{nome}"'})

@app.route('/api/agendamentos', methods=['GET', 'POST'])
def api_agendamentos():
    tid = verificar_token_perm(request, 'marcar' if request.method == 'POST' else 'agendamentos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403

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
        if not user or user.tenant_id != tid:
            return jsonify({'erro': 'usuário não encontrado'}), 404
        data_str = data_hora.strftime('%Y-%m-%d')
        dias_s = _get_setting('dias_fechados', tid)
        if dias_s and dias_s.value and data_str in json.loads(dias_s.value):
            return jsonify({'erro': 'Este dia não está disponível.'}), 400
        slot_ocupado = (Agendamento.query
                        .filter_by(status='ativo', tenant_id=tid)
                        .filter(Agendamento.data_hora == data_hora)
                        .first())
        if slot_ocupado:
            return jsonify({'erro': 'Este horário já foi reservado.'}), 400
        # Aceita servico_ids (lista) ou servico_id (legado, único)
        raw_ids = data.get('servico_ids') or (
            [data['servico_id']] if data.get('servico_id') else []
        )
        try:
            servico_ids = [int(x) for x in raw_ids if x]
        except (TypeError, ValueError):
            return jsonify({'erro': 'serviço inválido'}), 400
        forma_pag = data.get('forma_pagamento', 'pagar_no_local')
        if forma_pag not in ('pagar_no_local', 'pix', 'cartao_credito', 'cartao_debito', 'dinheiro'):
            return jsonify({'erro': 'forma de pagamento inválida'}), 400

        # O barbeiro precisa ser desta barbearia e estar ativo; sem isso dava
        # para vincular o agendamento a um funcionário de OUTRO tenant.
        funcionario_id = data.get('funcionario_id')
        if funcionario_id is not None:
            try:
                funcionario_id = int(funcionario_id)
            except (TypeError, ValueError):
                return jsonify({'erro': 'funcionário inválido'}), 400
            if funcionario_id == 0:
                funcionario_id = None          # 0 = proprietário, sem registro em funcionario
            else:
                _f = db.session.get(Funcionario, funcionario_id)
                if not _f or _f.tenant_id != tid or not _f.ativo:
                    return jsonify({'erro': 'funcionário inválido'}), 400

        ag = Agendamento(user_id=user_id, data_hora=data_hora,
                         funcionario_id=funcionario_id, tenant_id=tid)

        # Cria Pedido se houver serviços
        pedido = None
        servicos_validos = []
        if servico_ids:
            for sid in servico_ids:
                sv = db.session.get(Servico, sid)
                if sv and sv.tenant_id == tid:
                    servicos_validos.append(sv)
        if servicos_validos:
            total_pedido = sum(sv.preco or 0 for sv in servicos_validos)
            pedido = Pedido(
                user_id=user_id,
                status='pago' if forma_pag == 'pagar_no_local' else 'pendente',
                total=total_pedido,
                tenant_id=tid,
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
                tenant_id=tid,
            ))

        db.session.commit()
        resp = {'ok': True, 'id': ag.id}
        if pedido:
            resp['pedido_id'] = pedido.id
            resp['total'] = float(pedido.total or 0)
        return jsonify(resp)

    ags = (Agendamento.query
           .filter_by(tenant_id=tid)
           .options(joinedload(Agendamento.usuario), joinedload(Agendamento.funcionario))
           .order_by(Agendamento.data_hora.asc())
           .limit(2000)
           .all())
    pedido_ids = [ag.pedido_id for ag in ags if ag.pedido_id]
    pedidos = (
        {p.id: p for p in
         Pedido.query.filter(Pedido.tenant_id == tid, Pedido.id.in_(pedido_ids))
                     .options(joinedload(Pedido.itens)).limit(2000).all()}
        if pedido_ids else {}
    )
    _, gestor_nome = _gestor_como_barbeiro(tid)
    result = []
    for ag in ags:
        u = ag.usuario
        p = pedidos.get(ag.pedido_id)
        if ag.funcionario_id in (0, None):
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
    tid = verificar_token_perm(request, 'entradas')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    entradas = EntradaMonetaria.query.filter_by(tenant_id=tid).order_by(EntradaMonetaria.criado_em.desc()).limit(5000).all()
    return jsonify([{
        'id': e.id, 'descricao': e.descricao, 'valor': e.valor,
        'forma': e.forma, 'criado_em': e.criado_em.isoformat(),
    } for e in entradas])

@app.route('/api/entradas', methods=['POST'])
def api_entrada_criar():
    tid = verificar_token_perm(request, 'entradas')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    d = request.get_json() or {}
    descricao = d.get('descricao', '').strip()[:200]
    valor     = _sf(d.get('valor', 0))
    forma     = d.get('forma', 'dinheiro').strip()
    formas_validas = {'dinheiro', 'pix', 'cartao_credito', 'cartao_debito', 'cartao'}
    if not descricao:
        return jsonify({'erro': 'descrição obrigatória'}), 400
    if valor <= 0:
        return jsonify({'erro': 'valor deve ser maior que zero'}), 400
    if valor > 99999:
        return jsonify({'erro': 'valor inválido'}), 400
    if forma not in formas_validas:
        forma = 'dinheiro'
    e = EntradaMonetaria(descricao=descricao, valor=valor, forma=forma, tenant_id=tid)
    db.session.add(e)
    db.session.commit()
    return jsonify({'ok': True, 'id': e.id})

@app.route('/api/entradas/<int:eid>', methods=['DELETE'])
def api_entrada_deletar(eid):
    tid = verificar_token_perm(request, 'entradas')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    e = db.session.get(EntradaMonetaria, eid)
    if not e or e.tenant_id != tid:
        return jsonify({'erro': 'não encontrado'}), 404
    db.session.delete(e)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/fotos', methods=['GET'])
def api_fotos_listar():
    tid = _api_tid()
    if not tid: return jsonify({'erro': 'tenant não encontrado'}), 404
    categoria = request.args.get('categoria')
    q = FotoServico.query.filter_by(tenant_id=tid)
    if categoria:
        q = q.filter_by(categoria=categoria)
    fotos = q.order_by(FotoServico.criado_em.desc()).limit(1000).all()
    return jsonify([{
        'id': f.id,
        'categoria': f.categoria,
        'servico': f.servico,
        'url': f'/static/uploads/{f.filename}',
        'criado_em': f.criado_em.isoformat(),
    } for f in fotos])

@app.route('/api/fotos', methods=['POST'])
@limiter.limit('30 per hour')
def api_fotos_upload():
    tid = verificar_token_perm(request, 'fotos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    categoria = request.form.get('categoria', 'outros')
    servico   = request.form.get('servico', '').strip()
    arquivo   = request.files.get('foto')
    if not arquivo:
        return jsonify({'erro': 'nenhum arquivo enviado'}), 400
    if arquivo and request.content_length and request.content_length > 10 * 1024 * 1024:
        return jsonify({'erro': 'Imagem muito grande. Máximo 10 MB.'}), 413
    buf, err, ext = _processar_imagem(arquivo)
    if err:
        return jsonify({'erro': err}), 400
    filename = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as fh:
        fh.write(buf.read())
    foto = FotoServico(categoria=categoria, servico=servico or None, filename=filename, tenant_id=tid)
    db.session.add(foto)
    db.session.commit()
    return jsonify({'ok': True, 'id': foto.id, 'url': f'/static/uploads/{filename}'})

@app.route('/api/fotos/<int:fid>', methods=['DELETE'])
def api_fotos_deletar(fid):
    tid = verificar_token_perm(request, 'fotos')
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    foto = db.session.get(FotoServico, fid)
    if not foto or foto.tenant_id != tid:
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
      <p>Olá, <strong>{html.escape(user_name)}</strong>!</p>
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
        try:
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
                        app.logger.info('[LEMBRETE] %s enviado para %s', tipo, user.email)
        except Exception as e:
            app.logger.error('[SCHEDULER] verificar_lembretes erro: %s', e)

@app.route('/api/testar-lembretes', methods=['POST'])
def testar_lembretes():
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    with app.app_context():
        user = User.query.filter_by(tenant_id=tid).first()
        if not user:
            return jsonify({'erro': 'nenhum usuário cadastrado'}), 404
        ag = (Agendamento.query.filter_by(user_id=user.id, tenant_id=user.tenant_id, status='ativo')
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
        try:
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
                        <a href="https://ibarber.shop/planos"
                           style="color:#C9A96E;">ibarber.shop</a>
                        para reativar seu site.
                      </p></div>''')
            db.session.commit()
        except Exception as e:
            app.logger.error('[SCHEDULER] verificar_assinaturas erro: %s', e)

def limpar_guests():
    with app.app_context():
        try:
            limite = datetime.utcnow() - timedelta(hours=24)
            guests = User.query.filter_by(guest=True).filter(User.criado_em < limite).all()
            for g in guests:
                tem_ag = Agendamento.query.filter_by(user_id=g.id, status='ativo').first()
                if not tem_ag:
                    db.session.delete(g)
            db.session.commit()
        except Exception as e:
            app.logger.error('[SCHEDULER] limpar_guests erro: %s', e)

def limpar_trials_expirados():
    with app.app_context():
        try:
            agora = datetime.utcnow()
            expirados = Tenant.query.filter(
                Tenant.trial_expira != None,
                Tenant.trial_expira < agora,
                Tenant.assinatura_ativa == False,
            ).all()
            for t in expirados:
                db.session.delete(t)
            if expirados:
                db.session.commit()
                app.logger.info('[TRIAL] %d tenant(s) expirado(s) excluídos.', len(expirados))
        except Exception as e:
            app.logger.error('[SCHEDULER] limpar_trials_expirados erro: %s', e)

def enviar_retorno_automatico():
    """Envia email 28 dias após um corte sugerindo reagendar."""
    with app.app_context():
        try:
            agora = datetime.utcnow()
            alvo_inicio = agora - timedelta(days=30)
            alvo_fim    = agora - timedelta(days=27)
            ags = (Agendamento.query
                   .filter(Agendamento.status == 'concluido',
                           Agendamento.data_hora >= alvo_inicio,
                           Agendamento.data_hora < alvo_fim)
                   .options(joinedload(Agendamento.usuario))
                   .all())
            ag_ids = [ag.id for ag in ags]
            if not ag_ids:
                return
            ja_enviados = {
                l.agendamento_id
                for l in LembreteEnviado.query.filter(
                    LembreteEnviado.agendamento_id.in_(ag_ids),
                    LembreteEnviado.tipo == 'retorno'
                ).all()
            }
            for ag in ags:
                if ag.id in ja_enviados:
                    continue
                user = ag.usuario
                if not user or not user.receber_lembretes:
                    continue
                if not user.email or user.email.endswith('@ibarber.local'):
                    continue
                tenant = db.session.get(Tenant, ag.tenant_id)
                nome_b = tenant.nome if tenant else 'Barbearia'
                slug   = tenant.slug if tenant else ''
                _corpo = f"""
                <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                            background:#0f0f0f;color:#f0f0f0;padding:28px;border-radius:10px;">
                  <h2 style="color:#C9A96E;margin-top:0;">✦ Hora de renovar!</h2>
                  <p>Olá, <strong>{html.escape(user.name)}</strong>!</p>
                  <p>Faz cerca de <strong>30 dias</strong> desde seu último corte em <strong>{nome_b}</strong>.</p>
                  <p>Que tal agendar seu próximo horário?</p>
                  <div style="text-align:center;margin:24px 0;">
                    <a href="{_tenant_url(slug)}"
                       style="background:#C9A96E;color:#000;padding:12px 28px;border-radius:8px;
                              text-decoration:none;font-weight:bold;font-size:15px;">
                      Agendar agora
                    </a>
                  </div>
                  <p style="color:#888;font-size:12px;">
                    Para cancelar esses lembretes, acesse seu perfil no site.
                  </p>
                </div>
                """
                ok = _enviar_email(user.email, f'Hora de renovar — {nome_b}', _corpo)
                if ok:
                    db.session.add(LembreteEnviado(agendamento_id=ag.id, tipo='retorno'))
                    db.session.commit()
                    app.logger.info('[RETORNO] enviado para %s', user.email)
        except Exception as e:
            app.logger.error('[SCHEDULER] enviar_retorno_automatico erro: %s', e)

def _migrate_db():
    """Adiciona colunas novas sem quebrar instâncias existentes."""
    try:
        with db.engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE tenant ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"
            ))
            conn.commit()
            app.logger.info('[MIGRATE] token_version adicionado à tabela tenant')
    except Exception as e:
        # "duplicate column" é esperado quando a coluna já existe; o resto é erro real
        _msg = str(e).lower()
        if 'duplicate' not in _msg and 'exists' not in _msg:
            app.logger.error('[MIGRATE] token_version falhou: %s', e)

with app.app_context():
    _migrate_db()

# O scheduler roda em UM processo só. A guarda anterior (`not _is_dev`) era
# verdadeira em cada worker do gunicorn: com 4 workers, cada job executava 4x —
# lembretes duplicados para o cliente e limpezas concorrentes.
# Produção: suba um processo dedicado com RUN_SCHEDULER=1.
# Dev: o Werkzeug cria dois processos, e só o principal tem WERKZEUG_RUN_MAIN.
_RODAR_SCHEDULER = (
    os.environ.get('RUN_SCHEDULER') == '1'
    or (_is_dev and os.environ.get('WERKZEUG_RUN_MAIN') == 'true')
)
if _RODAR_SCHEDULER:
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(verificar_lembretes,      'interval', minutes=30, max_instances=1, coalesce=True)
    scheduler.add_job(verificar_assinaturas,    'interval', hours=12,   max_instances=1, coalesce=True)
    scheduler.add_job(limpar_guests,            'interval', hours=24,   max_instances=1, coalesce=True)
    scheduler.add_job(limpar_trials_expirados,  'interval', hours=24,   max_instances=1, coalesce=True)
    scheduler.add_job(enviar_retorno_automatico,'interval', hours=12,   max_instances=1, coalesce=True)
    scheduler.start()
    app.logger.info('[SCHEDULER] iniciado neste processo')
elif not _is_dev:
    app.logger.info('[SCHEDULER] inativo neste worker (defina RUN_SCHEDULER=1 no processo dedicado)')


@app.route('/api/ping', methods=['GET'])
def ping():
    return jsonify({'ok': True, 'app': 'barbearia'})

# ── Token rotation ─────────────────────────────────────────────────────────────

@app.route('/api/rotar-token', methods=['POST'])
def api_rotar_token():
    """Gestor invalida todos os tokens emitidos anteriormente e recebe um novo."""
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 401
    tenant = db.session.get(Tenant, tid)
    if not tenant: return jsonify({'erro': 'tenant não encontrado'}), 404
    tenant.token_version = (tenant.token_version or 0) + 1
    db.session.commit()
    novo_token = _gerar_token(tenant.id, 0, tenant.token_version)
    return jsonify({'ok': True, 'token': novo_token, 'version': tenant.token_version})

# ── Email blast — atualização do APK ───────────────────────────────────────────

def _email_atualizar_apk(tenant):
    apk_url   = f'https://{APP_DOMAIN}/static/app/ibarber.apk'
    painel_url = f'https://{APP_DOMAIN}/gestao/login'
    corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;
      background:#0f0f0f;color:#f0f0f0;padding:32px;border-radius:12px;">
      <h2 style="color:#C9A96E;margin-top:0;">✦ iBarber</h2>
      <h3 style="font-weight:500;color:#f0f0f0;margin-bottom:6px;">
        Atualização importante do app
      </h3>
      <p style="color:#aaa;margin-bottom:24px;">
        Lançamos uma nova versão do <strong style="color:#C9A96E;">iBarber Admin</strong>
        com melhorias de segurança. Recomendamos atualizar o quanto antes.
      </p>
      <a href="{apk_url}" style="display:inline-block;background:#C9A96E;color:#000;
        font-weight:700;padding:14px 28px;border-radius:8px;text-decoration:none;
        margin-bottom:24px;">
        Baixar nova versão
      </a>
      <p style="color:#888;font-size:13px;margin-bottom:4px;">
        <strong>Como instalar:</strong>
      </p>
      <ol style="color:#888;font-size:13px;line-height:1.8;padding-left:20px;">
        <li>Baixe o arquivo pelo botão acima</li>
        <li>Abra o arquivo .apk no seu celular</li>
        <li>Se solicitado, autorize a instalação de fontes desconhecidas</li>
        <li>Faça login com o e-mail <strong style="color:#C9A96E;">{tenant.email}</strong></li>
      </ol>
      <p style="color:#555;font-size:12px;border-top:1px solid #222;
        padding-top:16px;margin-top:24px;">
        Precisa de ajuda? Acesse seu painel em
        <a href="{painel_url}" style="color:#C9A96E;">{painel_url}</a>
      </p>
    </div>
    """
    return _enviar_email(
        tenant.email,
        '✦ iBarber — Atualização de segurança disponível',
        corpo
    )

@app.route('/api/admin/email-apk-update', methods=['POST'])
@limiter.limit('5 per minute')
@admin_key_required
def api_admin_email_apk_update():
    """Dispara e-mail de atualização do APK para todos os tenants ativos."""
    tenants = Tenant.query.filter_by(ativo=True, assinatura_ativa=True).all()
    enviados = 0
    falhas   = 0
    for t in tenants:
        if _email_atualizar_apk(t):
            enviados += 1
        else:
            falhas += 1
    return jsonify({'ok': True, 'enviados': enviados, 'falhas': falhas, 'total': len(tenants)})

@app.route('/api/admin/login', methods=['POST'])
@limiter.limit('10 per minute')
def admin_login():
    data  = request.get_json(force=True) or {}
    email = data.get('email', '').strip().lower()
    senha = data.get('password', '')
    tenant = Tenant.query.filter_by(email=email, ativo=True).first()
    if not tenant or not tenant.conferir_senha(senha):
        return jsonify({'erro': 'credenciais inválidas'}), 401
    # func_id=0 aqui é correto: quem autenticou foi o próprio dono do tenant
    token = _gerar_token(tenant.id, func_id=0, version=tenant.token_version or 0)
    return jsonify({'ok': True, 'token': token, 'nome': tenant.nome, 'tipo': 'admin'})

@app.route('/api/funcionarios/login', methods=['POST'])
@limiter.limit('5 per minute')
def api_funcionarios_login():
    data  = request.get_json(force=True) or {}
    email = data.get('email', '').strip().lower()
    senha = data.get('password', '')
    candidatos = Funcionario.query.filter_by(email=email, ativo=True).all()
    f = next((c for c in candidatos if c.conferir_senha(senha)), None)
    if not f:
        return jsonify({'erro': 'credenciais inválidas'}), 401
    t = db.session.get(Tenant, f.tenant_id)
    token = _gerar_token(f.tenant_id, f.id, t.token_version or 0 if t else 0)
    return jsonify({
        'ok': True, 'tipo': 'funcionario',
        'nome': f.nome, 'token': token,
        'permissoes': f.to_dict()['permissoes'],
    })

@app.route('/api/funcionarios', methods=['GET'])
def api_funcionarios_listar():
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    funs = Funcionario.query.filter_by(tenant_id=tid, ativo=True).order_by(Funcionario.nome).limit(5000).all()
    return jsonify([f.to_dict() for f in funs])

@app.route('/api/funcionarios', methods=['POST'])
def api_funcionarios_criar():
    # Criar funcionário define permissões — restrito ao dono
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    d = request.get_json() or {}
    email_func = d.get('email', '').strip().lower() or None
    if email_func:
        # Bloqueia se é o email do próprio gestor (causaria login como dono)
        tenant_obj = db.session.get(Tenant, tid)
        if tenant_obj and tenant_obj.email.lower() == email_func:
            return jsonify({'erro': 'Este e-mail pertence ao gestor — use outro e-mail para o funcionário'}), 400
        # Bloqueia se já existe funcionário ATIVO com esse email
        if Funcionario.query.filter_by(email=email_func, tenant_id=tid, ativo=True).first():
            return jsonify({'erro': 'E-mail já cadastrado nesta barbearia'}), 400
    senha = d.get('senha', '').strip()
    if len(senha) < 8:
        return jsonify({'erro': 'Senha deve ter mínimo 8 caracteres'}), 400
    perms = d.get('permissoes', {})
    nome_func = d.get('nome', '').strip()[:100]
    if not nome_func:
        return jsonify({'erro': 'nome obrigatório'}), 400
    tel_func = (d.get('telefone', '').strip() or '')[:20] or None
    f = Funcionario(
        nome=nome_func,
        email=email_func,
        senha=senha,
        telefone=tel_func,
        tenant_id=tid,
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
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'erro': 'E-mail já cadastrado nesta barbearia'}), 400
    return jsonify({'ok': True, 'id': f.id})

@app.route('/api/funcionarios/<int:fid>', methods=['PUT', 'DELETE'])
def api_funcionario_detalhe(fid):
    # Altera permissões e senha de funcionário — restrito ao dono
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    f = db.session.get(Funcionario, fid)
    if not f or f.tenant_id != tid:
        return jsonify({'erro': 'não encontrado'}), 404
    if request.method == 'DELETE':
        body = request.get_json(silent=True) or {}
        if body.get('cancelar'):
            tenant = db.session.get(Tenant, tid)
            conflitos = _conflitos_agendamentos_futuros(tid, funcionario_id=fid)
            _cancelar_conflitos(conflitos, tenant.nome if tenant else 'Barbearia',
                                f'Barbeiro {f.nome} não está mais disponível')
        # Libera o slot único no banco: email deletado não bloqueia re-cadastro
        if f.email and not f.email.startswith('_deleted_'):
            f.email = f'_deleted_{f.id}_{f.email}'
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
    nova_senha = d.get('nova_senha', '').strip()
    if nova_senha and len(nova_senha) < 8:
        return jsonify({'erro': 'Senha deve ter mínimo 8 caracteres'}), 400
    if nova_senha:
        f.senha = nova_senha
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

@app.route('/api/funcionarios/<int:fid>/conflitos', methods=['GET'])
def api_funcionario_conflitos(fid):
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    f = db.session.get(Funcionario, fid)
    if not f or f.tenant_id != tid:
        return jsonify({'erro': 'não encontrado'}), 404
    conflitos = _conflitos_agendamentos_futuros(tid, funcionario_id=fid)
    return jsonify({'conflitos': [{'id': c['ag'].id, 'nome': c['nome'], 'hora': c['data_hora_fmt']} for c in conflitos]})

@app.route('/api/gestor-barbeiro/conflitos', methods=['GET'])
def api_gestor_barbeiro_conflitos():
    _tid = verificar_token_admin(request)
    if not _tid: return jsonify({'erro': 'não autorizado'}), 403
    conflitos = _conflitos_agendamentos_futuros(_tid, funcionario_id=0)
    return jsonify({'conflitos': [{'id': c['ag'].id, 'nome': c['nome'], 'hora': c['data_hora_fmt']} for c in conflitos]})

@app.route('/api/funcionarios/<int:fid>/foto', methods=['POST', 'DELETE'])
@limiter.limit('20 per hour', methods=['POST'])
def api_funcionario_foto(fid):
    tid = verificar_token_admin(request)
    if not tid: return jsonify({'erro': 'não autorizado'}), 403
    f = db.session.get(Funcionario, fid)
    if not f or f.tenant_id != tid:
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
    if arquivo and request.content_length and request.content_length > 10 * 1024 * 1024:
        return jsonify({'erro': 'Imagem muito grande. Máximo 10 MB.'}), 413
    buf, err, ext = _processar_imagem(arquivo)
    if err:
        return jsonify({'erro': err}), 400
    if f.foto:
        old = os.path.join(UPLOAD_FOLDER, f.foto)
        if os.path.exists(old): os.remove(old)
    filename = f"func_{fid}_{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as fh:
        fh.write(buf.read())
    f.foto = filename
    db.session.commit()
    return jsonify({'ok': True, 'foto_url': f'/static/uploads/{filename}'})

@app.route('/api/gestor-foto', methods=['POST', 'DELETE'])
@limiter.limit('20 per hour', methods=['POST'])
def api_gestor_foto():
    _tid = verificar_token_admin(request)
    if not _tid: return jsonify({'erro': 'não autorizado'}), 403
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
    if arquivo and request.content_length and request.content_length > 10 * 1024 * 1024:
        return jsonify({'erro': 'Imagem muito grande. Máximo 10 MB.'}), 413
    buf, err, ext = _processar_imagem(arquivo)
    if err:
        return jsonify({'erro': err}), 400
    s = _get_setting('gestor_foto', _tid)
    if s and s.value:
        old = os.path.join(UPLOAD_FOLDER, s.value)
        if os.path.exists(old): os.remove(old)
    filename = f"gestor_{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as fh:
        fh.write(buf.read())
    _upsert_setting('gestor_foto', filename, _tid)
    db.session.commit()
    return jsonify({'ok': True, 'foto_url': f'/static/uploads/{filename}'})

def _ativar_tenant(tenant):
    """Ativa a assinatura e dispara DNS/SSL. Chamado APENAS após pagamento
    confirmado (ou ativação manual pelo admin) — nunca no cadastro, para não
    permitir que qualquer visitante consuma cota da Let's Encrypt."""
    tenant.ativo = True
    tenant.assinatura_ativa = True
    db.session.commit()
    threading.Thread(target=_provisionar_ssl_tenant,
                     args=(tenant.slug, tenant.email), daemon=True).start()

def _enviar_boas_vindas(tenant):
    site_url   = _tenant_url(tenant.slug)
    painel_url = f'https://{APP_DOMAIN}/gestao/login'
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

@app.route('/privacidade')
def privacidade():
    return render_template('privacidade.html')

@app.route('/termos')
def termos():
    return render_template('termos.html')

# ─── Site de Gestão (HTML/Flask) ──────────────────────────────────────────────

def _gestao_login_required():
    """Retorna None se ok, ou um redirect se não autenticado."""
    tid = session.get(_SESSION_GESTAO_TENANT_ID) or session.get(_SESSION_REPERSON_TID)
    if not tid:
        return redirect(url_for('gestao_login'))
    if not db.session.get(Tenant, tid):
        session.clear()
        return redirect(url_for('gestao_login'))
    # Funcionário desativado perde a sessão imediatamente
    fid = session.get(_SESSION_GESTAO_FUNC_ID)
    if fid:
        f = db.session.get(Funcionario, fid)
        if not f or f.tenant_id != tid or not f.ativo:
            session.clear()
            return redirect(url_for('gestao_login'))
    return None

def _gestao_tenant():
    return db.session.get(Tenant, session.get(_SESSION_GESTAO_TENANT_ID) or session.get(_SESSION_REPERSON_TID))

def _gestao_token():
    """Token da API com a identidade REAL de quem está logado.
    func_id 0 = dono; qualquer outro = funcionário, que não passa em
    verificar_token_admin(). Emitir sempre 0 aqui daria a qualquer
    funcionário acesso às rotas restritas ao dono (credenciais, rotação
    de token, cadastro de funcionários)."""
    t = _gestao_tenant()
    if not t:
        return ''
    return _gerar_token(t.id, session.get(_SESSION_GESTAO_FUNC_ID) or 0, t.token_version or 0)

def _gestao_is_owner():
    return not session.get('gestao_func_id')

def _gestao_func_obj():
    fid = session.get('gestao_func_id')
    return db.session.get(Funcionario, fid) if fid else None

def _gestao_perms():
    if _gestao_is_owner():
        return {k: True for k in ['agendamentos','calendario','marcar','clientes',
                                   'servicos','precos','pedidos','entradas','fotos']}
    f = _gestao_func_obj()
    if not f:
        return {k: False for k in ['agendamentos','calendario','marcar','clientes',
                                    'servicos','precos','pedidos','entradas','fotos']}
    return {
        'agendamentos': bool(f.perm_agendamentos),
        'calendario':   bool(f.perm_calendario),
        'marcar':       bool(f.perm_marcar),
        'clientes':     bool(f.perm_clientes),
        'servicos':     bool(f.perm_servicos),
        'precos':       bool(f.perm_precos),
        'pedidos':      bool(f.perm_pedidos),
        'entradas':     bool(f.perm_entradas),
        'fotos':        bool(f.perm_fotos),
    }

def _gestao_perm_required(perm_name):
    if _gestao_is_owner():
        return None
    if _gestao_perms().get(perm_name):
        return None
    flash('Acesso não autorizado.', 'error')
    return redirect(url_for('gestao_dashboard'))

def _gestao_owner_required():
    if _gestao_is_owner():
        return None
    flash('Acesso restrito ao gestor.', 'error')
    return redirect(url_for('gestao_dashboard'))

@app.context_processor
def _gestao_template_ctx():
    if session.get(_SESSION_GESTAO_TENANT_ID) or session.get(_SESSION_REPERSON_TID):
        return {'gestao_is_owner': _gestao_is_owner(), 'gestao_perms': _gestao_perms()}
    return {}

def _csrf_ok():
    """Confere o token CSRF da sessão. Mantido para as rotas que já o chamavam;
    a proteção geral está em _csrf_protect() (before_request).
    A versão anterior checava Origin/Referer e liberava quando ambos faltavam —
    justamente o caso de um form cross-site com referrer suprimido."""
    enviado  = request.form.get('_csrf') or request.headers.get('X-CSRF-Token', '')
    esperado = session.get('_csrf_token', '')
    return bool(esperado) and hmac.compare_digest(str(enviado), str(esperado))

# Rotas isentas: webhook do MP (autenticado por HMAC próprio) e o callback OAuth.
_CSRF_ISENTOS = ('/api/pagamento/webhook', '/auth/google/callback')

def _autenticado_por_chave():
    """True se a requisição traz a chave mestra correta (body, query ou header).

    Quem autentica assim não depende do cookie de sessão, então não é alvo de
    CSRF: um site atacante não conhece a chave. Já uma chamada a /api/admin/*
    apoiada só na sessão continua exigindo o token CSRF."""
    if not API_TOKEN:
        return False
    chave = ((request.get_json(silent=True) or {}).get('key')
             or request.args.get('key', '')
             or request.headers.get('X-Admin-Key', ''))
    return bool(chave) and hmac.compare_digest(str(chave), API_TOKEN)

@app.before_request
def _csrf_protect():
    if request.method in ('GET', 'HEAD', 'OPTIONS', 'TRACE'):
        return
    if request.path.startswith(_CSRF_ISENTOS):
        return
    # Chamadas de API por Bearer token não usam cookie, logo não são forjáveis
    # por CSRF; a mesma lógica vale para a chave mestra do painel admin.
    if request.headers.get('Authorization', '').startswith('Bearer '):
        return
    if _autenticado_por_chave():
        return
    if not _csrf_ok():
        if request.path.startswith('/api/'):
            return jsonify({'erro': 'CSRF inválido — recarregue a página'}), 403
        return 'Requisição inválida (CSRF)', 403

@app.context_processor
def _csrf_ctx():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe(32)
    return {'csrf_token': session['_csrf_token']}

@app.route('/gestao/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute', methods=['POST'])
def gestao_login():
    if session.get(_SESSION_GESTAO_TENANT_ID):
        return redirect(url_for('gestao_dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        tenant = Tenant.query.filter_by(email=email, ativo=True).first()
        if tenant and tenant.conferir_senha(senha):
            session.clear()
            session.permanent = True
            session[_SESSION_GESTAO_TENANT_ID] = tenant.id
            session[_SESSION_GESTAO_NOME] = tenant.nome
            return redirect(url_for('gestao_dashboard'))
        # Tenta login como funcionário (checa senha em todos os candidatos para evitar ambiguidade de tenant)
        func = next((c for c in Funcionario.query.filter_by(email=email, ativo=True).all()
                     if c.conferir_senha(senha)), None)
        if func and func.tenant_id:
            t = db.session.get(Tenant, func.tenant_id)
            if t and t.ativo:
                session.clear()
                session.permanent = True
                session[_SESSION_GESTAO_TENANT_ID] = func.tenant_id
                session[_SESSION_GESTAO_FUNC_ID]   = func.id
                session[_SESSION_GESTAO_NOME]      = func.nome
                return redirect(url_for('gestao_dashboard'))
        flash('E-mail ou senha incorretos.', 'error')
    return render_template('gestao/login.html')

@app.route('/gestao/logout')
def gestao_logout():
    # session.clear() em vez de pops seletivos: a versão anterior deixava
    # user_id, onb_tenant_id e — o mais grave — admin_ok vivos após o logout.
    session.clear()
    return redirect(url_for('gestao_login'))

@app.route('/gestao')
@app.route('/gestao/')
def gestao_dashboard():
    redir = _gestao_login_required()
    if redir: return redir
    _, gestor_nome = _gestor_como_barbeiro(_gestao_tid())
    hoje = datetime.utcnow().date()
    MESES_ABREV = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    DIAS_ABREV = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']
    # Todos agendamentos (últimos 60 dias + próximos 30) para o JS filtrar por dia
    janela_ini = datetime.utcnow() - timedelta(days=60)
    janela_fim = datetime.utcnow() + timedelta(days=30)
    _tid = _gestao_tid()
    ags = (Agendamento.query
           .filter_by(tenant_id=_tid)
           .options(joinedload(Agendamento.usuario), joinedload(Agendamento.funcionario))
           .filter(Agendamento.data_hora >= janela_ini, Agendamento.data_hora <= janela_fim)
           .order_by(Agendamento.data_hora.asc()).all())
    pedido_ids = [ag.pedido_id for ag in ags if ag.pedido_id]
    pedidos_map = {p.id: p for p in Pedido.query.filter(Pedido.tenant_id == _tid, Pedido.id.in_(pedido_ids)).options(joinedload(Pedido.itens)).all()} if pedido_ids else {}
    # Serializar para JSON
    ags_json = []
    for ag in ags:
        u = ag.usuario
        p = pedidos_map.get(ag.pedido_id)
        if ag.funcionario_id in (0, None):
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
                'dia_semana': DIAS_ABREV[cursor.weekday()],
                'mes': MESES_ABREV[cursor.month - 1],
                'tem_ag': cursor.strftime('%Y-%m-%d') in datas_com_ag,
            })
        cursor += timedelta(days=1)
    return render_template('gestao/dashboard.html',
        active='dashboard',
        hoje=hoje.strftime('%Y-%m-%d'),
        hoje_dia=hoje.day,
        hoje_mes=MESES_ABREV[hoje.month - 1],
        proximos_dias=proximos_dias,
        agendamentos_json=_safe_json(ags_json),
        token=_gestao_token(),
    )

@app.route('/gestao/agendamentos', methods=['GET'])
def gestao_agendamentos():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_perm_required('agendamentos')
    if perm: return perm
    tenant = _gestao_tenant()
    # Gera token de admin para o JS usar na API
    token = _gestao_token()
    clientes = User.query.filter_by(tenant_id=tenant.id).order_by(User.name).limit(5000).all()
    clientes_json = _safe_json([
        {'id': c.id, 'name': c.name, 'email': c.email, 'contact': c.contact or ''}
        for c in clientes
    ])
    return render_template('gestao/agendamentos.html', active='agendamentos',
                           token=token, clientes_json=clientes_json)

@app.route('/gestao/agendamentos/<int:ag_id>/status', methods=['POST'])
def gestao_agendamento_status(ag_id):
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_perm_required('agendamentos')
    if perm: return perm
    ag = db.session.get(Agendamento, ag_id)
    if ag and ag.tenant_id == _gestao_tid():
        novo_status = request.form.get('status', 'ativo')
        ag.status = novo_status
        db.session.commit()
        if novo_status == 'cancelado':
            user = db.session.get(User, ag.user_id)
            if user and not user.email.endswith('@ibarber.local'):
                data_fmt = f"{DIAS_PT[ag.data_hora.weekday()]}, {ag.data_hora.day} de {MESES_PT[ag.data_hora.month-1]}"
                hora_fmt = ag.data_hora.strftime('%H:%M')
                html_cancel = f"""
                <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;
                            background:#0f0f0f;color:#f0f0f0;padding:28px;border-radius:10px;">
                  <h2 style="color:#c0392b;margin-top:0;">Agendamento Cancelado</h2>
                  <p>Olá, <strong>{html.escape(user.name)}</strong>! Seu agendamento foi cancelado pela barbearia.</p>
                  <div style="background:#1a1a1a;border-left:4px solid #c0392b;
                              padding:16px 20px;border-radius:6px;margin:20px 0;">
                    <p style="margin:0;font-size:15px;color:#888;">📅 {data_fmt}</p>
                    <p style="margin:8px 0 0;font-size:28px;font-weight:bold;
                              color:#c0392b;letter-spacing:2px;">⏰ {hora_fmt}</p>
                  </div>
                  <p style="color:#888;font-size:13px;">Acesse o site para reagendar.</p>
                </div>
                """
                _enviar_email(user.email, 'Agendamento cancelado — Barbearia', html_cancel)
    return redirect(url_for('gestao_agendamentos'))

@app.route('/gestao/pedidos')
def gestao_pedidos():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_perm_required('pedidos')
    if perm: return perm
    tenant = _gestao_tenant()
    token = _gestao_token()
    pedidos = (Pedido.query
               .filter_by(tenant_id=tenant.id)
               .options(joinedload(Pedido.usuario), joinedload(Pedido.itens))
               .order_by(Pedido.criado_em.desc()).limit(500).all())
    pedidos_json = _safe_json([{
        'id': p.id, 'status': p.status, 'total': p.total,
        'criado_em': p.criado_em.strftime('%Y-%m-%dT%H:%M:%S'),
        'usuario': p.usuario.name if p.usuario else '—',
        'itens': [{'nome': i.nome, 'categoria': i.categoria or '', 'preco': i.preco} for i in p.itens],
    } for p in pedidos])
    return render_template('gestao/pedidos.html', active='pedidos', pedidos_json=pedidos_json, token=token)

@app.route('/gestao/clientes')
def gestao_clientes():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_perm_required('clientes')
    if perm: return perm
    tenant = _gestao_tenant()
    token = _gestao_token()
    clientes = User.query.filter_by(tenant_id=tenant.id).order_by(User.name).limit(5000).all()
    clientes_json = _safe_json([{
        'id': c.id, 'name': c.name, 'email': c.email,
        'contact': c.contact or '', 'criado_em': c.criado_em.strftime('%Y-%m-%d'),
    } for c in clientes])
    return render_template('gestao/clientes.html', active='clientes', clientes_json=clientes_json, token=token)

@app.route('/gestao/clientes/<int:uid>')
def gestao_cliente_detalhe(uid):
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_perm_required('clientes')
    if perm: return perm
    token = _gestao_token()
    return render_template('gestao/cliente_detalhe.html', active='clientes', uid=uid, token=token)

@app.route('/gestao/entradas', methods=['GET', 'POST'])
def gestao_entradas():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_perm_required('entradas')
    if perm: return perm
    if request.method == 'POST' and not _csrf_ok():
        return 'Requisição inválida', 403
    _tid = _gestao_tid()
    if request.method == 'POST':
        desc = request.form.get('descricao', '').strip()
        valor = _sf(request.form.get('valor', 0))
        formas_validas = {'dinheiro', 'cartao', 'pix'}
        forma = request.form.get('forma', 'dinheiro')
        if forma not in formas_validas:
            forma = 'dinheiro'
        if desc and valor > 0:
            db.session.add(EntradaMonetaria(descricao=desc, valor=valor, forma=forma, tenant_id=_tid))
            db.session.commit()
        return redirect(url_for('gestao_entradas'))
    ags = (Agendamento.query.filter_by(tenant_id=_tid)
           .options(joinedload(Agendamento.usuario))
           .order_by(Agendamento.data_hora.desc()).all())
    pedido_ids = [ag.pedido_id for ag in ags if ag.pedido_id]
    pedidos_map = {p.id: p for p in Pedido.query.filter(Pedido.tenant_id == _tid, Pedido.id.in_(pedido_ids))
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
    entradas = EntradaMonetaria.query.filter_by(tenant_id=_tid).order_by(EntradaMonetaria.criado_em.desc()).limit(5000).all()
    entradas_json = _safe_json([{
        'id': e.id, 'descricao': e.descricao, 'valor': e.valor,
        'forma': e.forma or 'dinheiro', 'criado_em': e.criado_em.strftime('%Y-%m-%dT%H:%M:%S'),
    } for e in entradas])
    return render_template('gestao/entradas.html', active='entradas',
                           entradas_json=entradas_json,
                           agendamentos_json=_safe_json(ags_json))

@app.route('/gestao/entradas/<int:eid>/deletar', methods=['POST'])
def gestao_entrada_deletar(eid):
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_perm_required('entradas')
    if perm: return perm
    e = db.session.get(EntradaMonetaria, eid)
    if e and e.tenant_id == _gestao_tid():
        db.session.delete(e)
        db.session.commit()
    return redirect(url_for('gestao_entradas'))

@app.route('/gestao/funcionarios')
def gestao_funcionarios():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_owner_required()
    if perm: return perm
    token = _gestao_token()
    return render_template('gestao/funcionarios.html', active='funcionarios', token=token)

@app.route('/gestao/servicos')
def gestao_servicos():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_perm_required('servicos')
    if perm: return perm
    token = _gestao_token()
    return render_template('gestao/servicos.html', active='servicos', token=token)

@app.route('/gestao/precos', methods=['GET', 'POST'])
def gestao_precos():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_perm_required('precos')
    if perm: return perm
    if request.method == 'POST' and not _csrf_ok():
        return 'Requisição inválida', 403
    tenant = _gestao_tenant()
    token = _gestao_token()
    servicos = Servico.query.filter_by(ativo=True, tenant_id=tenant.id).order_by(Servico.nome).limit(1000).all()
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
    perm = _gestao_perm_required('fotos')
    if perm: return perm
    token = _gestao_token()
    return render_template('gestao/fotos.html', active='fotos', token=token)

@app.route('/gestao/loja')
def gestao_loja():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_owner_required()
    if perm: return perm
    tenant = _gestao_tenant()
    produtos = Produto.query.filter_by(tenant_id=tenant.id).order_by(Produto.criado_em.desc()).all()
    return render_template('gestao/loja.html', active='loja', tenant=tenant, produtos=produtos)

@app.route('/api/gestao/loja/toggle', methods=['POST'])
def api_gestao_loja_toggle():
    redir = _gestao_login_required()
    if redir: return jsonify({'erro': 'não autenticado'}), 401
    if not _gestao_is_owner(): return jsonify({'erro': 'sem permissão'}), 403
    tenant = _gestao_tenant()
    tenant.loja_ativa = not getattr(tenant, 'loja_ativa', False)
    db.session.commit()
    return jsonify({'loja_ativa': tenant.loja_ativa})

@app.route('/api/gestao/produtos', methods=['POST'])
@limiter.limit('60 per hour')
def api_gestao_produto_criar():
    redir = _gestao_login_required()
    if redir: return jsonify({'erro': 'não autenticado'}), 401
    if not _gestao_is_owner(): return jsonify({'erro': 'sem permissão'}), 403
    tid = _gestao_tid()
    nome = request.form.get('nome', '').strip()
    if not nome:
        return jsonify({'erro': 'Nome obrigatório'}), 400
    descricao = request.form.get('descricao', '').strip() or None
    valor = _sf(request.form.get('valor', 0))
    if valor < 0:
        return jsonify({'erro': 'valor inválido'}), 400
    p = Produto(tenant_id=tid, nome=nome, descricao=descricao, valor=valor)
    db.session.add(p)
    db.session.flush()
    arquivo = request.files.get('foto')
    if arquivo and request.content_length and request.content_length > 10 * 1024 * 1024:
        db.session.rollback()
        return jsonify({'erro': 'Imagem muito grande. Máximo 10 MB.'}), 413
    if arquivo and arquivo.filename:
        buf, err, ext = _processar_imagem(arquivo)
        if err:
            db.session.rollback()
            return jsonify({'erro': err}), 400
        pasta = os.path.join(UPLOAD_FOLDER, str(tid), 'produtos')
        os.makedirs(pasta, exist_ok=True)
        filename = f"{tid}/produtos/prod_{p.id}_{uuid.uuid4().hex}.{ext}"
        with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as fh:
            fh.write(buf.read())
        p.foto = filename
    db.session.commit()
    foto_url = f'/static/uploads/{p.foto}' if p.foto else None
    return jsonify({'ok': True, 'id': p.id, 'nome': p.nome,
                    'descricao': p.descricao or '', 'valor': p.valor,
                    'em_estoque': p.em_estoque, 'foto_url': foto_url})

@app.route('/api/gestao/produtos/<int:pid>', methods=['PUT', 'DELETE'])
@limiter.limit('120 per hour', methods=['PUT'])
def api_gestao_produto(pid):
    redir = _gestao_login_required()
    if redir: return jsonify({'erro': 'não autenticado'}), 401
    if not _gestao_is_owner(): return jsonify({'erro': 'sem permissão'}), 403
    tid = _gestao_tid()
    p = db.session.get(Produto, pid)
    if not p or p.tenant_id != tid:
        return jsonify({'erro': 'não encontrado'}), 404
    if request.method == 'DELETE':
        if p.foto:
            caminho = os.path.join(UPLOAD_FOLDER, p.foto)
            if os.path.exists(caminho):
                os.remove(caminho)
        db.session.delete(p)
        db.session.commit()
        return jsonify({'ok': True})
    nome = request.form.get('nome', '').strip()
    if not nome:
        return jsonify({'erro': 'Nome obrigatório'}), 400
    p.nome = nome
    p.descricao = request.form.get('descricao', '').strip() or None
    novo_valor = _sf(request.form.get('valor', 0))
    if novo_valor < 0:
        return jsonify({'erro': 'valor inválido'}), 400
    p.valor = novo_valor
    arquivo = request.files.get('foto')
    if arquivo and request.content_length and request.content_length > 10 * 1024 * 1024:
        return jsonify({'erro': 'Imagem muito grande. Máximo 10 MB.'}), 413
    if arquivo and arquivo.filename:
        buf, err, ext = _processar_imagem(arquivo)
        if err:
            return jsonify({'erro': err}), 400
        if p.foto:
            old = os.path.join(UPLOAD_FOLDER, p.foto)
            if os.path.exists(old):
                os.remove(old)
        pasta = os.path.join(UPLOAD_FOLDER, str(tid), 'produtos')
        os.makedirs(pasta, exist_ok=True)
        filename = f"{tid}/produtos/prod_{p.id}_{uuid.uuid4().hex}.{ext}"
        with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as fh:
            fh.write(buf.read())
        p.foto = filename
    db.session.commit()
    foto_url = f'/static/uploads/{p.foto}' if p.foto else None
    return jsonify({'ok': True, 'nome': p.nome, 'descricao': p.descricao or '',
                    'valor': p.valor, 'foto_url': foto_url})

@app.route('/api/gestao/produtos/<int:pid>/estoque', methods=['POST'])
def api_gestao_produto_estoque(pid):
    redir = _gestao_login_required()
    if redir: return jsonify({'erro': 'não autenticado'}), 401
    if not _gestao_is_owner(): return jsonify({'erro': 'sem permissão'}), 403
    tid = _gestao_tid()
    p = db.session.get(Produto, pid)
    if not p or p.tenant_id != tid:
        return jsonify({'erro': 'não encontrado'}), 404
    p.em_estoque = not p.em_estoque
    db.session.commit()
    return jsonify({'em_estoque': p.em_estoque})

@app.route('/gestao/horarios', methods=['GET', 'POST'])
def gestao_horarios():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_owner_required()
    if perm: return perm
    if request.method == 'POST' and not _csrf_ok():
        return 'Requisição inválida', 403
    tenant = _gestao_tenant()
    token = _gestao_token()
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
        _upsert_setting('intervalo_minutos', str(_duracao_segura(request.form.get('slot_minutos'), 40)), _tid)
        _upsert_setting('dias_agenda', str(_int_seguro(request.form.get('dias_agenda'), 20, 1, 60)), _tid)
        # Cancelar agendamentos conflitantes se solicitado
        if request.form.get('_confirmar') == 'cancelar':
            DOW_MAP = {'Monday':'seg','Tuesday':'ter','Wednesday':'qua','Thursday':'qui',
                       'Friday':'sex','Saturday':'sab','Sunday':'dom'}
            futuros = Agendamento.query.filter(
                Agendamento.tenant_id == _tid, Agendamento.status == 'ativo',
                Agendamento.data_hora > datetime.utcnow()
            ).all()
            conflitos = []
            for ag in futuros:
                dia_key = DOW_MAP.get(ag.data_hora.strftime('%A'), '')
                cfg = horarios.get(dia_key, {})
                hora = ag.data_hora.strftime('%H:%M')
                if not cfg.get('aberto') or hora < cfg.get('abertura','00:00') or hora >= cfg.get('fechamento','24:00'):
                    user = db.session.get(User, ag.user_id)
                    conflitos.append({'ag': ag, 'user': user, 'nome': user.name if user else 'Cliente',
                                      'data_hora_fmt': ag.data_hora.strftime('%d/%m/%Y %H:%M')})
            if conflitos:
                _cancelar_conflitos(conflitos, tenant.nome, 'Mudança de horário de funcionamento')
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
    especiais = HorarioEspecial.query.filter_by(tenant_id=tenant.id).order_by(HorarioEspecial.data).limit(1000).all()
    config_geral = {
        'slot_minutos': _duracao_segura(slot_s.value if slot_s and slot_s.value else 40),
        'dias_agenda': _int_seguro(dias_ag_s.value if dias_ag_s and dias_ag_s.value else 20, 20, 1, 60),
    }
    especiais_json = _safe_json([{'id': e.id, 'data': e.data,
        'abertura': e.abertura, 'fechamento': e.fechamento} for e in especiais])
    return render_template('gestao/horarios.html', active='horarios',
                           config_dias=config_dias, config_geral=config_geral,
                           dias_fechados=dias_fechados, especiais_json=especiais_json, token=token)

@app.route('/gestao/contato', methods=['GET', 'POST'])
def gestao_contato():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_owner_required()
    if perm: return perm
    if request.method == 'POST' and not _csrf_ok():
        return 'Requisição inválida', 403
    tenant = _gestao_tenant()
    if request.method == 'POST':
        whatsapp  = request.form.get('whatsapp', '').strip()
        maps_url  = request.form.get('maps_url', '').strip()
        contato   = request.form.get('contato', '').strip()
        if maps_url and not re.match(r'^https?://', maps_url):
            maps_url = ''
        tenant.whatsapp = whatsapp
        tenant.maps_url = maps_url
        tenant.contato  = contato
        # Botões FAB — mostrar=True se texto preenchido, False se vazio
        wpp_texto  = request.form.get('wpp_texto', '').strip()
        maps_texto = request.form.get('maps_texto', '').strip()
        try:
            wpp_cfg = json.loads(tenant.fab_wpp or '{}')
        except (ValueError, TypeError):
            wpp_cfg = {}
        wpp_cfg['texto']   = wpp_texto or 'Chame no Zap'
        wpp_cfg['mostrar'] = bool(wpp_texto)
        tenant.fab_wpp = json.dumps(wpp_cfg)
        try:
            maps_cfg = json.loads(tenant.fab_maps or '{}')
        except (ValueError, TypeError):
            maps_cfg = {}
        maps_cfg['texto']   = maps_texto or 'Como chegar'
        maps_cfg['mostrar'] = bool(maps_texto)
        tenant.fab_maps = json.dumps(maps_cfg)
        db.session.commit()
        flash('Contato atualizado.', 'success')
        return redirect(url_for('gestao_contato'))
    try:
        wpp_texto = json.loads(tenant.fab_wpp or '{}').get('texto', '')
    except (ValueError, TypeError, AttributeError):
        wpp_texto = ''
    try:
        maps_texto = json.loads(tenant.fab_maps or '{}').get('texto', '')
    except (ValueError, TypeError, AttributeError):
        maps_texto = ''
    return render_template('gestao/contato.html', active='contato', tenant=tenant,
                           wpp_texto=wpp_texto, maps_texto=maps_texto)

@app.route('/gestao/credenciais', methods=['GET', 'POST'])
def gestao_credenciais():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_owner_required()
    if perm: return perm
    tenant = _gestao_tenant()
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip().lower()
        senha_atual = request.form.get('senha_atual', '')
        nova_senha  = request.form.get('nova_senha', '')
        confirmar   = request.form.get('confirmar_senha', '')
        if nome: tenant.nome = nome
        if email:
            if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
                flash('E-mail inválido.', 'error')
                return redirect(url_for('gestao_credenciais'))
            existing = Tenant.query.filter(Tenant.email == email, Tenant.id != tenant.id).first()
            if existing:
                flash('E-mail já está em uso por outra conta.', 'error')
                return redirect(url_for('gestao_credenciais'))
            tenant.email = email
        if nova_senha:
            if not tenant.conferir_senha(senha_atual):
                flash('Senha atual incorreta.', 'error')
                return redirect(url_for('gestao_credenciais'))
            if nova_senha != confirmar:
                flash('As senhas não coincidem.', 'error')
                return redirect(url_for('gestao_credenciais'))
            # Mesmo mínimo exigido em /api/cadastro e /api/credenciais/conta;
            # sem isto, o setter de Tenant.senha levantaria ValueError aqui.
            if len(nova_senha) < 8:
                flash('Nova senha deve ter ao menos 8 caracteres.', 'error')
                return redirect(url_for('gestao_credenciais'))
            tenant.senha = nova_senha
        db.session.commit()
        session[_SESSION_GESTAO_NOME] = tenant.nome
        flash('Dados atualizados.', 'success')
        return redirect(url_for('gestao_credenciais'))
    def _sv(key): s = _get_setting(key, tenant.id); return s.value if s else ''
    return render_template('gestao/credenciais.html',
        active='credenciais', tenant=tenant,
        token=_gestao_token(),
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
    perm = _gestao_owner_required()
    if perm: return perm
    return render_template('gestao/graficos.html',
        active='graficos',
        token=_gestao_token())

@app.route('/gestao/calendario')
def gestao_calendario():
    redir = _gestao_login_required()
    if redir: return redir
    perm = _gestao_perm_required('calendario')
    if perm: return perm
    return render_template('gestao/calendario.html',
        active='calendario',
        token=_gestao_token())

@app.route('/personalizar')
def personalizar():
    resp = make_response(render_template('personalizar.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp

@app.route('/planos')
def planos():
    return render_template('planos.html')

@app.route('/trial')
def trial():
    return redirect(url_for('personalizar'), 301)

@app.route('/api/trial', methods=['POST'])
def api_trial():
    return jsonify({'erro': 'Trial encerrado. Acesse /personalizar para criar sua conta.'}), 410

@app.route('/cadastro')
def cadastro():
    return render_template('cadastro.html')

@app.route('/pagamento')
def pagamento():
    return render_template('pagamento.html')

@app.route('/api/personalizar/upload', methods=['POST'])
@limiter.limit('20 per hour')
def api_personalizar_upload():
    # Upload público: usado no onboarding (sem sessão) e no repersonalizar (com sessão)
    arquivo = request.files.get('imagem')
    if not arquivo:
        return jsonify({'erro': 'nenhum arquivo'}), 400
    if arquivo and request.content_length and request.content_length > 10 * 1024 * 1024:
        return jsonify({'erro': 'Imagem muito grande. Máximo 10 MB.'}), 413
    buf, err, ext = _processar_imagem(arquivo)
    if err:
        return jsonify({'erro': err}), 400
    filename = f"pers_{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as fh:
        fh.write(buf.read())
    return jsonify({'ok': True, 'url': f'/static/uploads/{filename}'})

def _criar_dns_cloudflare(slug):
    if not CF_TOKEN or not CF_ZONE_ID or not VPS_IP:
        return
    domain = f"{slug}.{APP_DOMAIN}"
    headers = {
        'Authorization': f'Bearer {CF_TOKEN}',
        'Content-Type': 'application/json',
    }
    r = req_http.get(
        f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records',
        headers=headers,
        params={'type': 'A', 'name': domain},
        timeout=10,
    )
    if r.ok and r.json().get('result'):
        return
    req_http.post(
        f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records',
        headers=headers,
        json={'type': 'A', 'name': domain, 'content': VPS_IP, 'ttl': 60, 'proxied': False},
        timeout=10,
    )


def _provisionar_ssl_tenant(slug, email):
    """Emite o certificado do subdomínio e registra o vhost no nginx.

    Depende de rodar na VPS com root: usa certbot, escreve em
    /etc/nginx/sites-enabled e recarrega o nginx.
    """
    _criar_dns_cloudflare(slug)
    domain = f"{slug}.{APP_DOMAIN}"
    cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
    if os.path.exists(cert_path):
        return
    for _ in range(60):
        try:
            socket.gethostbyname(domain)
            break
        except socket.gaierror:
            time.sleep(10)
    else:
        return
    try:
        r = subprocess.run(
            ['certbot', 'certonly', '--nginx', '-d', domain,
             '--non-interactive', '--agree-tos', '-m', email],
            capture_output=True, text=True, timeout=120
        )
        if r.returncode != 0:
            return
    except Exception:
        return
    nginx_block = f"""
server {{
    listen 443 ssl;
    server_name {domain};
    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    location / {{
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
"""
    try:
        with open('/etc/nginx/sites-enabled/ibarber', 'a') as f:
            f.write(nginx_block)
        subprocess.run(['nginx', '-t'], check=True, capture_output=True, timeout=10)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True, timeout=10)
    except Exception:
        pass


@app.route('/api/cadastro-personalizar', methods=['POST'])
@limiter.limit('5 per hour')
def api_cadastro_personalizar():
    d = request.get_json(force=True) or {}
    slug = d.get('slug', '').lower().strip()
    if not re.match(r'^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$', slug):
        return jsonify({'erro': 'slug inválido'}), 400
    if Tenant.query.filter_by(slug=slug).first():
        return jsonify({'erro': 'slug já em uso'}), 400
    email = d.get('email', '').strip().lower()
    if not email or not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
        return jsonify({'erro': 'E-mail inválido'}), 400
    if Tenant.query.filter_by(email=email).first():
        return jsonify({'erro': 'e-mail já cadastrado'}), 400
    nome = d.get('nome', '').strip()
    if not nome:
        return jsonify({'erro': 'nome obrigatório'}), 400
    senha = d.get('senha', '').strip()
    if len(senha) < 8:
        return jsonify({'erro': 'Senha deve ter ao menos 8 caracteres'}), 400
    tenant = Tenant(
        slug=slug,
        nome=nome,
        email=email,
        senha=senha,
        whatsapp=d.get('whatsapp', '').strip() or None,
        tema=json.dumps(d.get('tema', {})),
        ativo=True,
        assinatura_ativa=False,
    )
    db.session.add(tenant)
    db.session.flush()
    for nome_cat in ['Corte', 'Barba', 'Combo']:
        db.session.add(Categoria(nome=nome_cat, tenant_id=tenant.id))
    _upsert_setting('gestor_e_barbeiro', '1', tenant.id)
    db.session.commit()
    session.pop('is_preview', None)
    session[_SESSION_ONB_TENANT_ID] = tenant.id
    # DNS/SSL só após pagamento confirmado — ver _ativar_tenant()
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
@limiter.limit('5 per minute')
def api_repersonalizar_auth():
    d = request.get_json(force=True) or {}
    email = d.get('email', '').strip().lower()
    senha = d.get('senha', '')
    tenant = Tenant.query.filter_by(email=email).first()
    if not tenant or not tenant.conferir_senha(senha):
        return jsonify({'erro': 'E-mail ou senha incorretos'}), 401
    if not tenant.assinatura_ativa:
        return jsonify({'erro': 'Conta sem assinatura ativa'}), 403
    tema = json.loads(tenant.tema) if tenant.tema else {}
    session[_SESSION_REPERSON_TID] = tenant.id
    return jsonify({
        'ok': True,
        'tenant_id': tenant.id,
        'slug': tenant.slug,
        'nome': tenant.nome,
        'email': tenant.email,
        'whatsapp': tenant.whatsapp or '',
        'tema': tema,
        'editacoes': tenant.tema_editacoes or 0,
    })

@app.route('/api/repersonalizar/credenciais', methods=['POST'])
def api_repersonalizar_credenciais():
    d = request.get_json(force=True) or {}
    tid_req = d.get('tenant_id')
    if not tid_req or session.get(_SESSION_REPERSON_TID) != tid_req:
        return jsonify({'erro': 'Sessão inválida'}), 403
    tenant = db.session.get(Tenant, tid_req)
    if not tenant or not tenant.assinatura_ativa:
        return jsonify({'erro': 'Não autorizado'}), 403
    nome = (d.get('nome') or '').strip()
    email = (d.get('email') or '').strip().lower()
    senha = (d.get('senha') or '').strip()
    whatsapp = (d.get('whatsapp') or '').strip()
    slug_novo = (d.get('slug') or '').strip().lower()
    if nome:
        tenant.nome = nome
    if email and email != tenant.email:
        if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
            return jsonify({'erro': 'E-mail inválido'}), 400
        if Tenant.query.filter_by(email=email).first():
            return jsonify({'erro': 'E-mail já cadastrado'}), 400
        tenant.email = email
    if senha:
        if len(senha) < 8:
            return jsonify({'erro': 'Senha deve ter pelo menos 8 caracteres'}), 400
        tenant.senha = senha
        # Invalida todos os tokens emitidos antes da troca de senha
        tenant.token_version = (tenant.token_version or 0) + 1
    if whatsapp is not None:
        tenant.whatsapp = whatsapp or None
    if slug_novo and slug_novo != tenant.slug:
        if not re.match(r'^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$', slug_novo):
            return jsonify({'erro': 'Link inválido: use letras minúsculas, números e hífens (3–30 chars, sem hífens nas pontas)'}), 400
        if Tenant.query.filter(Tenant.slug == slug_novo, Tenant.id != tenant.id).first():
            return jsonify({'erro': 'Esse link já está em uso. Escolha outro.'}), 400
        tenant.slug = slug_novo
    db.session.commit()
    return jsonify({'ok': True, 'slug': tenant.slug})

@app.route('/api/repersonalizar/salvar', methods=['POST'])
def api_repersonalizar_salvar():
    d = request.get_json(force=True) or {}
    tid_req = d.get('tenant_id')
    if not tid_req or session.get(_SESSION_REPERSON_TID) != tid_req:
        return jsonify({'erro': 'Sessão inválida'}), 403
    tenant = db.session.get(Tenant, tid_req)
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
    tid_req = d.get('tenant_id')
    if not tid_req or session.get(_SESSION_REPERSON_TID) != tid_req:
        return jsonify({'erro': 'Sessão inválida'}), 403
    tenant = db.session.get(Tenant, tid_req)
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
@limiter.limit('10 per hour')
def api_cadastro():
    d = request.get_json(force=True) or {}
    slug = d.get('slug', '').lower().strip()
    if not re.match(r'^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$', slug):
        return jsonify({'erro': 'slug inválido'}), 400
    if Tenant.query.filter_by(slug=slug).first():
        return jsonify({'erro': 'slug já em uso'}), 400
    email = d.get('email', '').strip().lower()
    if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
        return jsonify({'erro': 'E-mail inválido'}), 400
    if Tenant.query.filter_by(email=email).first():
        return jsonify({'erro': 'e-mail já cadastrado'}), 400
    nome = (d.get('nome') or '').strip()[:100]
    if not nome:
        return jsonify({'erro': 'nome obrigatório'}), 400
    senha = (d.get('senha') or '').strip()
    if len(senha) < 8:
        return jsonify({'erro': 'Senha deve ter ao menos 8 caracteres'}), 400
    tenant = Tenant(
        slug=slug,
        nome=nome,
        email=email,
        senha=senha,
        contato=d.get('contato', '').strip() or None,
        tema=json.dumps(d.get('tema', {})),
        ativo=True,
        # Assinatura só é ativada pelo webhook do Mercado Pago após pagamento
        # confirmado — o mesmo vale para o provisionamento de DNS/SSL.
        assinatura_ativa=False,
    )
    db.session.add(tenant)
    db.session.flush()
    for nome_cat in ['Corte', 'Barba', 'Combo']:
        db.session.add(Categoria(nome=nome_cat, tenant_id=tenant.id))
    _upsert_setting('gestor_e_barbeiro', '1', tenant.id)
    db.session.commit()
    session.pop('is_preview', None)
    session[_SESSION_ONB_TENANT_ID] = tenant.id
    return jsonify({'ok': True, 'tenant_id': tenant.id, 'slug': slug})


@app.route('/api/pagamento/cartao', methods=['POST'])
@limiter.limit('5 per minute')
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
    onb_tid = session.get(_SESSION_ONB_TENANT_ID)
    if not onb_tid or int(onb_tid) != tenant.id:
        return jsonify({'erro': 'sessão de cadastro inválida'}), 403
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
            _ativar_tenant(tenant)
            _enviar_boas_vindas(tenant)
        return jsonify({'ok': True, 'status': status, 'slug': tenant.slug})
    return jsonify({'erro': data.get('message', 'Pagamento recusado'), 'status': status}), 402

@app.route('/admin/painel', methods=['GET', 'POST'])
@limiter.limit('20 per minute')
def admin_painel():
    if request.method == 'POST':
        _k = request.form.get('key', '')
        if _k and hmac.compare_digest(_k, API_TOKEN):
            session[_SESSION_ADMIN_OK] = True
        return redirect(url_for('admin_painel'))
    if not session.get(_SESSION_ADMIN_OK):
        # HTML inline (fora do render_template) — o token CSRF precisa ser
        # garantido e embutido aqui, o context_processor não alcança esta resposta.
        if '_csrf_token' not in session:
            session['_csrf_token'] = secrets.token_urlsafe(32)
        _csrf = html.escape(session['_csrf_token'])
        return f'''<html><body style="background:#0a0a0a;color:#f0ece4;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <form method="post" style="text-align:center">
          <input type="hidden" name="_csrf" value="{_csrf}">
          <h2 style="color:#C9A96E;margin-bottom:1.5rem">✦ Admin — iBarber</h2>
          <input name="key" type="password" placeholder="Token de acesso"
            style="padding:10px 16px;border-radius:6px;border:1px solid #333;background:#1e1e1e;color:#f0ece4;font-size:15px;width:260px">
          <br><br>
          <button type="submit" style="padding:10px 28px;background:#C9A96E;color:#000;border:none;border-radius:6px;font-weight:bold;cursor:pointer">Entrar</button>
        </form></body></html>''', 401
    tenants = Tenant.query.order_by(Tenant.id.desc()).limit(5000).all()
    mp_pub  = _get_setting('admin_mp_public_key')

    now = datetime.utcnow()
    chart_data = []
    for i in range(11, -1, -1):
        raw   = now.month - i - 1
        month = raw % 12 + 1
        year  = now.year + raw // 12
        label = datetime(year, month, 1).strftime('%b/%y')
        count = 0; receita = 0.0
        for t in tenants:
            for asn in t.assinaturas:
                if asn.criado_em and asn.criado_em.year == year and asn.criado_em.month == month:
                    count += 1
                    receita += float(asn.valor_total or 0)
        chart_data.append({'label': label, 'count': count, 'receita': receita})

    planos_dist = defaultdict(int)
    for t in tenants:
        latest = next((a for a in t.assinaturas if a.status == 'ativo'), None)
        if latest:
            planos_dist[latest.plano.capitalize()] += 1

    receita_total = sum(float(asn.valor_total or 0) for t in tenants for asn in t.assinaturas if asn.status == 'ativo')

    return render_template('admin_painel.html',
        tenants=tenants,
        mp_public_key=mp_pub.value if mp_pub else '',
        mp_token_set=bool(get_mp_token()),
        chart_data=chart_data,
        planos_dist=dict(planos_dist),
        receita_total=receita_total)

@app.route('/api/admin/credenciais', methods=['POST'])
@limiter.limit('10 per minute')
@admin_key_required
def api_admin_credenciais():
    data = request.get_json(force=True) or {}
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
@limiter.limit('20 per minute')
@admin_key_required
def api_admin_ativar(tid):
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
    _ativar_tenant(t)
    return jsonify({'ok': True})

@app.route('/api/admin/tenant/<int:tid>', methods=['DELETE'])
@limiter.limit('20 per minute')
@admin_key_required
def api_admin_tenant_delete(tid):
    t = db.session.get(Tenant, tid)
    if not t:
        return jsonify({'erro': 'não encontrado'}), 404
    t.ativo = False
    t.assinatura_ativa = False
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/admin/desativar/<int:tid>', methods=['POST'])
@limiter.limit('20 per minute')
@admin_key_required
def api_admin_desativar(tid):
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
@limiter.limit('5 per minute')
@admin_key_required
def api_admin_excluir(tid):
    t = db.session.get(Tenant, tid)
    if not t:
        return jsonify({'erro': 'não encontrado'}), 404

    # 1. LembreteEnviado (FK → agendamento)
    ag_ids = [a.id for a in Agendamento.query.filter_by(tenant_id=tid).with_entities(Agendamento.id)]
    if ag_ids:
        LembreteEnviado.query.filter(LembreteEnviado.agendamento_id.in_(ag_ids)).delete(synchronize_session=False)

    # 2. ListaEspera
    ListaEspera.query.filter_by(tenant_id=tid).delete()

    # 3. Agendamentos
    Agendamento.query.filter_by(tenant_id=tid).delete()

    # 4. EntradaMonetaria
    EntradaMonetaria.query.filter_by(tenant_id=tid).delete()

    # 5. PedidoItem (via pedidos do tenant)
    pedido_ids = [p.id for p in Pedido.query.filter_by(tenant_id=tid).with_entities(Pedido.id)]
    if pedido_ids:
        PedidoItem.query.filter(PedidoItem.pedido_id.in_(pedido_ids)).delete(synchronize_session=False)

    # 6. Pedidos
    Pedido.query.filter_by(tenant_id=tid).delete()

    # 7. FuncionarioAusencia (FK → funcionario)
    func_ids = [f.id for f in Funcionario.query.filter_by(tenant_id=tid).with_entities(Funcionario.id)]
    if func_ids:
        FuncionarioAusencia.query.filter(FuncionarioAusencia.funcionario_id.in_(func_ids)).delete(synchronize_session=False)

    # 8. Funcionarios
    Funcionario.query.filter_by(tenant_id=tid).delete()

    # 9. Usuários
    User.query.filter_by(tenant_id=tid).delete()

    # 10. Fotos, horários, serviços, categorias, assinaturas
    FotoServico.query.filter_by(tenant_id=tid).delete()
    HorarioEspecial.query.filter_by(tenant_id=tid).delete()
    Servico.query.filter_by(tenant_id=tid).delete()
    Categoria.query.filter_by(tenant_id=tid).delete()
    Assinatura.query.filter_by(tenant_id=tid).delete()

    # 11. Settings com prefixo do tenant
    Setting.query.filter(Setting.key.like(f'{tid}:%')).delete(synchronize_session=False)

    # 12. Tenant
    db.session.delete(t)
    try:
        db.session.commit()
    except Exception as e:
        # Sem rollback, uma FK que falhe no meio deixaria o tenant meio apagado
        db.session.rollback()
        app.logger.error('[ADMIN] falha ao excluir tenant %s: %s', tid, e)
        return jsonify({'erro': 'falha ao excluir — nenhuma alteração foi aplicada'}), 500
    return jsonify({'ok': True})

@app.route('/api/admin/vencimento/<int:tid>', methods=['POST'])
@limiter.limit('20 per minute')
@admin_key_required
def api_admin_vencimento(tid):
    data = request.get_json(force=True) or {}
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
    _ativar_tenant(t)
    return jsonify({'ok': True})

@app.route('/admin/entrar-gestao/<int:tid>')
def admin_entrar_gestao(tid):
    # Usa a sessão do painel admin em vez de aceitar a chave mestra na query
    # string, que vazaria em histórico do navegador e log do nginx.
    if not session.get(_SESSION_ADMIN_OK):
        return 'Não autorizado', 401
    t = db.session.get(Tenant, tid)
    if not t:
        return 'Tenant não encontrado', 404
    session[_SESSION_GESTAO_TENANT_ID] = t.id
    session[_SESSION_GESTAO_NOME] = t.nome
    return redirect(url_for('gestao_dashboard'))

@app.route('/api/pagamento/criar', methods=['POST'])
@limiter.limit('5 per minute')
def api_pagamento_criar_v2():
    d = request.get_json(force=True) or {}
    tenant = db.session.get(Tenant, d.get('tenant_id'))
    if not tenant:
        return jsonify({'erro': 'tenant não encontrado'}), 404
    # A sessão de onboarding é obrigatória. Antes, sem cookie a checagem era
    # pulada e qualquer um criava Assinatura 'pendente' para qualquer tenant —
    # e o webhook casava com a primeira pendente que encontrasse.
    onb_tid = session.get(_SESSION_ONB_TENANT_ID)
    if not onb_tid or int(onb_tid) != tenant.id:
        return jsonify({'erro': 'sessão de cadastro inválida'}), 403
    plano = d.get('plano', '')
    if plano not in PLANOS:
        return jsonify({'erro': 'plano inválido'}), 400

    mp_token = get_mp_token()
    # Public key: env var primeiro, depois DB setting
    mp_pub_key = os.environ.get('MP_PUBLIC_KEY', '')
    if not mp_pub_key:
        mp_pub_key_s = _get_setting('admin_mp_public_key')
        mp_pub_key = mp_pub_key_s.value if mp_pub_key_s else ''

    amount = PLANOS[plano]['total']
    meta   = {'tenant_id': tenant.id, 'plano': plano, 'tipo': 'assinatura'}

    # ── Gerar QR Code PIX ──────────────────────────────────────────────────────
    pix_code = None
    pix_url  = None
    pix_payment_id = None
    if mp_token:
        try:
            pix_r = req_http.post(
                'https://api.mercadopago.com/v1/payments',
                json={
                    'transaction_amount': float(amount),
                    'description': f'iBarber — Plano {plano.capitalize()}',
                    'payment_method_id': 'pix',
                    'payer': {'email': tenant.email},
                    'metadata': meta,
                    'notification_url': f'{request.host_url}api/pagamento/webhook',
                },
                headers={'Authorization': f'Bearer {mp_token}',
                         'Content-Type': 'application/json',
                         'X-Idempotency-Key': str(uuid.uuid4())},
                timeout=15,
            )
            if pix_r.status_code in (200, 201):
                pix_data = pix_r.json()
                td = pix_data.get('point_of_interaction', {}).get('transaction_data', {})
                pix_code       = td.get('qr_code', '')
                pix_url        = td.get('qr_code_base64', '')
                pix_payment_id = str(pix_data.get('id', ''))
        except Exception:
            pass

    # ── Gerar preference para Brick de cartão ──────────────────────────────────
    pref_id = None
    if mp_token:
        try:
            pref_r = req_http.post(
                'https://api.mercadopago.com/checkout/preferences',
                headers={'Authorization': f'Bearer {mp_token}', 'Content-Type': 'application/json'},
                json={
                    'items': [{'title': f'iBarber — Plano {plano.capitalize()}',
                               'quantity': 1, 'currency_id': 'BRL', 'unit_price': float(amount)}],
                    'back_urls': {'success': f'https://{APP_DOMAIN}/sucesso/{tenant.slug}',
                                  'failure':  f'https://{APP_DOMAIN}/falha',
                                  'pending':  f'https://{APP_DOMAIN}/pendente'},
                    'auto_return': 'approved',
                    'notification_url': f'https://{APP_DOMAIN}/api/pagamento/webhook',
                    'metadata': meta,
                },
                timeout=15,
            )
            pref_id = pref_r.json().get('id')
        except Exception:
            pass

    assinatura = Assinatura(
        tenant_id=tenant.id, plano=plano,
        valor_total=PLANOS[plano]['total'], valor_mensal=PLANOS[plano]['mensal'],
        status='pendente', mp_preference_id=pref_id,
    )
    db.session.add(assinatura)
    db.session.commit()

    return jsonify({
        'ok': True,
        'preference_id': pref_id,
        'public_key':    mp_pub_key,
        'amount':        amount,
        'tenant_id':     tenant.id,
        'plano':         plano,
        'pix_code':      pix_code,
        'pix_url':       pix_url,
        'pix_payment_id': pix_payment_id,
    })

@app.route('/api/pagamento/webhook', methods=['POST'])
def api_pagamento_webhook():

    # Verificação de assinatura do Mercado Pago — obrigatória
    mp_secret = os.environ.get('MP_WEBHOOK_SECRET', '')
    if not mp_secret:
        return '', 401  # secret não configurado → rejeitar toda requisição
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
    if not received or not hmac.compare_digest(expected, received):
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
        if not tenant_id:
            app.logger.error('[WEBHOOK] payment %s sem tenant_id no metadata', payment_id)
            return '', 200
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
                if plano not in PLANOS:
                    app.logger.error('[WEBHOOK] payment %s com plano inválido: %r', payment_id, plano)
                    return '', 200
                meses = PLANOS[plano]['meses']
                # Casa pelo plano que foi efetivamente pago; pegar "a primeira
                # pendente" ativaria uma assinatura de plano/valor diferente.
                assinatura = (Assinatura.query
                              .filter_by(tenant_id=tenant_id, status='pendente', plano=plano)
                              .order_by(Assinatura.id.desc()).first())
                if not assinatura:
                    assinatura = Assinatura(
                        tenant_id=tenant_id, plano=plano,
                        valor_total=PLANOS[plano]['total'],
                        valor_mensal=PLANOS[plano]['mensal'],
                    )
                    db.session.add(assinatura)
                assinatura.status = 'ativo'
                assinatura.mp_payment_id = payment_id
                assinatura.inicio = datetime.utcnow()
                assinatura.vencimento = datetime.utcnow() + timedelta(days=30 * meses)
                _ativar_tenant(tenant)
                _enviar_boas_vindas(tenant)
    return '', 200

@app.route('/verificar-slug/<slug>')
@limiter.limit('30 per minute')
def verificar_slug(slug):
    slug = slug.lower().strip()
    valido = bool(re.match(r'^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$', slug))
    existe = Tenant.query.filter_by(slug=slug).first() is not None
    return jsonify({'disponivel': valido and not existe})

@app.route('/api/minha-assinatura')
def api_minha_assinatura():
    tenant_id = verificar_token_admin(request)
    if not tenant_id: return jsonify({'erro': 'não autorizado'}), 403
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            sock.sendto(b'BARBEARIA_SERVER:5000', ('255.255.255.255', 5001))
        except Exception:
            pass
        time.sleep(2)

@app.route('/<slug>/loja')
def tenant_loja(slug):
    tenant = get_tenant_by_slug(slug)
    if not tenant or not tenant.assinatura_ativa or not getattr(tenant, 'loja_ativa', False):
        return redirect(url_for('tenant_site', slug=slug))
    if session.get(_SESSION_USER_ID) and session.get(_SESSION_PATH_TENANT_ID) and session[_SESSION_PATH_TENANT_ID] != tenant.id:
        session.pop('user_id', None)
        session.pop('user_name', None)
        session.pop('user_email', None)
    session[_SESSION_PATH_TENANT_ID] = tenant.id
    produtos = Produto.query.filter_by(tenant_id=tenant.id).order_by(Produto.criado_em.desc()).all()
    return render_template('loja.html', tenant=tenant, produtos=produtos,
                           preview_mode=False, tema_override=None, hide_fabs=False)

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
    if session.get(_SESSION_USER_ID) and session.get(_SESSION_PATH_TENANT_ID) and session[_SESSION_PATH_TENANT_ID] != tenant.id:
        session.pop('user_id', None)
        session.pop('user_name', None)
        session.pop('user_email', None)
    session[_SESSION_PATH_TENANT_ID] = tenant.id
    session.pop('is_preview', None)
    session.pop('tenant_id', None)
    if request.args.get('p') == '1':
        tema_override = _build_ag_tema_override(request.args)
        return render_template('index.html', user=None, preview_mode=True, tema_override=tema_override, hide_fabs=True)
    user = None
    if 'user_id' in session:
        user = db.session.get(User, session[_SESSION_USER_ID])
    return render_template('index.html', user=user, preview_mode=False, tema_override=None, hide_fabs=False)

@app.errorhandler(413)
def erro_upload_grande(e):
    return jsonify({'erro': 'Arquivo muito grande. Máximo 50 MB.'}), 413

@app.errorhandler(429)
def erro_rate_limit(e):
    return jsonify({'erro': 'Muitas tentativas. Aguarde um momento.'}), 429

if __name__ == '__main__':
    # debug=True fixo expunha o console interativo do Werkzeug (execução remota
    # de código) em 0.0.0.0 para quem rodasse `python app.py` fora de casa.
    # Produção usa gunicorn e não passa por aqui.
    _debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    _host  = os.environ.get('FLASK_RUN_HOST', '127.0.0.1' if not _debug else '0.0.0.0')
    if _debug:
        threading.Thread(target=_udp_broadcast, daemon=True).start()
    app.run(host=_host, port=int(os.environ.get('FLASK_RUN_PORT', '5000')), debug=_debug)
