"""Account-bound, non-renewing passes. Requires a persistent CHYPER_DATA_DIR."""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from flask import Blueprint, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
import stripe
import purchase_emails

bp = Blueprint('access', __name__)
PLANS = {'day': (199, 86400), 'week': (499, 7*86400), 'month': (999, 30*86400)}
COOKIE = 'chyper_account'

def now():
    return int(time.time())

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

@contextmanager
def db():
    root = os.environ.get('CHYPER_DATA_DIR')
    if not root or not os.path.isdir(root):
        raise RuntimeError('Persistent CHYPER_DATA_DIR is required')
    c = sqlite3.connect(os.path.join(root, 'accounts.sqlite3'), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,recovery TEXT NOT NULL,expires INTEGER NOT NULL DEFAULT 0,draft TEXT,revision INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,plan TEXT NOT NULL,amount INTEGER NOT NULL,granted INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,start INTEGER NOT NULL,count INTEGER NOT NULL);
    ''')
    purchase_emails.init_schema(c)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def current_user(c):
    token = request.cookies.get(COOKIE, '')
    return c.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?', (digest(token), now())).fetchone()

def status(u):
    return {'email': u['email'], 'active': u['expires'] > now(), 'expires_at': u['expires'], 'purchased': u['expires'] > 0}

def login_response(c, u, recovery=None):
    token = secrets.token_urlsafe(32)
    c.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(token), u['id'], now()+30*86400))
    data = status(u)
    if recovery:
        data['recovery_code'] = recovery
    r = jsonify(data)
    r.set_cookie(COOKIE, token, secure=not bp.testing, httponly=True, samesite='Lax', max_age=30*86400)
    return r

bp.testing = False

def client():
    return stripe.StripeClient(os.environ['STRIPE_SECRET_KEY'])

def grant(c, checkout, paid_at):
    """Only our recorded orders can grant access. Transactions make repeats idempotent."""
    if checkout.payment_status != 'paid' or checkout.mode != 'payment':
        return
    c.execute('BEGIN IMMEDIATE')
    order = c.execute('SELECT * FROM orders WHERE id=?', (checkout.id,)).fetchone()
    if not order or order['granted']:
        return
    if checkout.amount_total != order['amount'] or checkout.currency != 'eur' or checkout.client_reference_id != order['user_id']:
        raise ValueError('Order mismatch')
    u = c.execute('SELECT * FROM users WHERE id=?', (order['user_id'],)).fetchone()
    end = max(u['expires'], paid_at) + PLANS[order['plan']][1]
    c.execute('UPDATE users SET expires=? WHERE id=?', (end, u['id']))
    c.execute('UPDATE orders SET granted=1 WHERE id=?', (order['id'],))
    purchase_emails.enqueue(c, order['id'], u['email'])

def reconcile(user_id):
    with db() as c:
        orders = c.execute('SELECT id FROM orders WHERE user_id=? AND granted=0', (user_id,)).fetchall()
    for order in orders:
        checkout = client().checkout.sessions.retrieve(order['id'])
        if checkout.payment_status == 'paid':
            intent = client().payment_intents.retrieve(checkout.payment_intent)
            charge = client().charges.retrieve(intent.latest_charge)
            with db() as c:
                grant(c, checkout, int(charge.created))
    purchase_emails.drain(db, user_id)

def install(app):
    app.register_blueprint(bp)

    @app.before_request
    def access_guard():
        protected = request.path in ('/api/pdf', '/api/ai-interview', '/api/ai-review', '/api/mock-interview')
        if request.path.startswith('/api/account') or request.path in ('/api/create-checkout-session', '/api/payment-status') or protected:
            try:
                with db() as c:
                    u = current_user(c)
                    g.account = dict(u) if u else None
            except (RuntimeError, sqlite3.Error):
                return jsonify(error='Account storage is not configured'), 503
            if request.method not in ('GET', 'HEAD') and request.headers.get('X-Chyper-Request') != '1':
                return jsonify(error='Invalid request'), 403
            if protected and (not u or u['expires'] <= now()):
                return jsonify(error='Active pass required'), 402

    @app.after_request
    def private_responses(response):
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

@bp.post('/api/account/<action>')
def account_action(action):
    if action not in ('register', 'login', 'logout', 'recover'):
        return jsonify(error='Unknown action'), 404
    data = request.get_json(silent=True) or {}
    email = str(data.get('email', '')).strip().lower()
    password = str(data.get('password', ''))
    with db() as c:
        if action == 'logout':
            c.execute('DELETE FROM sessions WHERE token=?', (digest(request.cookies.get(COOKIE, '')),))
            r = jsonify(ok=True)
            r.delete_cookie(COOKIE)
            return r
        # Database-backed rate limit survives process restarts and multiple workers.
        key = digest(request.remote_addr or '')
        c.execute('INSERT INTO attempts VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN start<? THEN 1 ELSE count+1 END,start=CASE WHEN start<? THEN excluded.start ELSE start END', (key, now(), now()-900, now()-900))
        c.commit()
        if c.execute('SELECT count FROM attempts WHERE key=?', (key,)).fetchone()[0] > 20:
            return jsonify(error='Too many attempts; retry in 15 minutes'), 429
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email)>254 or not 12 <= len(password) <= 128:
            return jsonify(error='Valid email and a 12–128 character password required'), 400
        u = c.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        recovery = None
        if action == 'register':
            if u:
                return jsonify(error='Unable to create account; try signing in'), 409
            recovery = secrets.token_urlsafe(24)
            uid = secrets.token_hex(16)
            c.execute('INSERT INTO users(id,email,password,recovery) VALUES(?,?,?,?)', (uid,email,generate_password_hash(password),digest(recovery)))
            u = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        elif action == 'recover':
            if not u or not secrets.compare_digest(u['recovery'], digest(str(data.get('recovery_code', '')))):
                return jsonify(error='Invalid credentials'), 401
            recovery = secrets.token_urlsafe(24)
            c.execute('UPDATE users SET password=?,recovery=? WHERE id=?', (generate_password_hash(password),digest(recovery),u['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?', (u['id'],))
        elif not u or not check_password_hash(u['password'], password):
            return jsonify(error='Invalid credentials'), 401
        return login_response(c, u, recovery)

@bp.get('/api/account')
def account():
    if not g.account:
        return jsonify(error='Sign in required'), 401
    # Reconcile unfulfilled payments on every login/refresh, even without the return URL.
    try:
        reconcile(g.account['id'])
    except Exception:
        return jsonify(error='Unable to verify purchases; please retry'), 503
    with db() as c:
        u = current_user(c)
        return jsonify(**status(u), draft=json.loads(u['draft']) if u['draft'] else None, revision=u['revision'])

@bp.route('/api/account/draft', methods=['PUT'])
def save_draft():
    if not g.account:
        return jsonify(error='Sign in required'), 401
    raw = request.get_data()
    if len(raw) > 4_000_000:
        return jsonify(error='Draft is too large'), 413
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='Invalid draft'), 400
    with db() as c:
        u = current_user(c)
        if u['expires'] and u['expires'] <= now():
            return jsonify(error='Pass expired; renew to edit'), 402
        revision = data.pop('_revision', -1)
        updated = c.execute('UPDATE users SET draft=?,revision=revision+1 WHERE id=? AND revision=?', (json.dumps(data),u['id'],revision))
        if updated.rowcount != 1:
            return jsonify(error='CV changed on another device. Reload before saving.'), 409
    return jsonify(ok=True,revision=revision+1)

@bp.post('/api/create-checkout-session')
def checkout():
    if not g.account:
        return jsonify(error='Sign in required'), 401
    plan = (request.get_json(silent=True) or {}).get('plan')
    if plan not in PLANS:
        return jsonify(error='Invalid plan'), 400
    base = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
    if not base.startswith('https://') or not os.environ.get('STRIPE_WEBHOOK_SECRET'):
        return jsonify(error='Checkout configuration missing'), 503
    amount, seconds = PLANS[plan]
    s = client().checkout.sessions.create(params={
        'mode':'payment', 'client_reference_id':g.account['id'],
        'customer_email':g.account['email'],
        'line_items':[{'price_data':{'currency':'eur','unit_amount':amount,'product_data':{'name':f'Chyper — {seconds//86400} days access (no auto-renewal)'}},'quantity':1}],
        'success_url':base+'/?payment=success&session_id={CHECKOUT_SESSION_ID}',
        'cancel_url':base+'/?payment=cancelled',
        'metadata':{'user_id':g.account['id'],'plan':plan},
    })
    with db() as c:
        c.execute('INSERT INTO orders(id,user_id,plan,amount) VALUES(?,?,?,?)', (s.id,g.account['id'],plan,amount))
    return jsonify(url=s.url)

@bp.get('/api/payment-status')
def payment_status():
    if not g.account:
        return jsonify(paid=False), 401
    reconcile(g.account['id'])
    with db() as c:
        u = current_user(c)
        return jsonify(paid=u['expires']>now(), **status(u))

@bp.post('/api/stripe-webhook')
def webhook():
    try:
        event = stripe.Webhook.construct_event(request.get_data(), request.headers.get('Stripe-Signature',''), os.environ['STRIPE_WEBHOOK_SECRET'])
    except Exception:
        return jsonify(error='Invalid signature'), 400
    if event.type in ('checkout.session.completed','checkout.session.async_payment_succeeded'):
        s = event.data.object
        with db() as c:
            if not c.execute('SELECT 1 FROM orders WHERE id=?',(s.id,)).fetchone():
                # Can arrive before the checkout-creation transaction commits.
                return jsonify(error='Order not ready'), 503
            grant(c,s,int(event.created))
        if not purchase_emails.deliver(s.id, db):
            return jsonify(error='Purchase email pending; retry notification'), 503
    return jsonify(ok=True)
