"""Transactional purchase messages; durable outbox and provider idempotency."""
import json
import os
import time
from urllib.request import Request, urlopen

SUBJECT = 'Köszönjük a vásárlásodat!'
TEXT = 'Köszönjük a vásárlásodat és a bizalmadat!\n\nChyper'
HTML = '<p>Köszönjük a vásárlásodat és a bizalmadat!</p><p>Chyper</p>'


def init_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_emails (
        order_id TEXT PRIMARY KEY, recipient TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', payload TEXT,
        first_attempt INTEGER, lease_until INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0, provider_id TEXT,
        accepted_at INTEGER, error TEXT
    )''')


def enqueue(c, order_id, email):
    # Called in the same transaction as the validated payment grant.
    c.execute('INSERT OR IGNORE INTO purchase_emails(order_id,recipient) VALUES(?,?)', (order_id,email))


def send_provider(payload, key):
    req = Request('https://api.resend.com/emails',
        data=payload.encode('utf-8'), method='POST', headers={
            'Authorization': 'Bearer '+os.environ['RESEND_API_KEY'],
            'Content-Type':'application/json',
            'Idempotency-Key':key,
            'User-Agent':'Chyper/1.0',
        })
    with urlopen(req, timeout=10) as response:
        provider_id=json.load(response).get('id')
    if not provider_id:
        raise ValueError('Missing provider message ID')
    return provider_id


def deliver(order_id, db, clock=lambda:int(time.time())):
    """True means accepted by the provider (not proof of inbox delivery)."""
    if not os.environ.get('RESEND_API_KEY') or not os.environ.get('PURCHASE_EMAIL_FROM'):
        return False  # Leave queued. Never use a made-up sender address.
    current=clock()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM purchase_emails WHERE order_id=?',(order_id,)).fetchone()
        if not row:
            return True
        if row['status']=='accepted':
            return True
        if row['status']=='review' or row['lease_until']>current:
            return False
        # Resend deduplicates for 24h. Stop well before that window expires;
        # an ambiguous first send must be reviewed rather than risking duplicates.
        if row['first_attempt'] is not None and current-row['first_attempt']>=23*3600:
            c.execute("UPDATE purchase_emails SET status='review',error='Check provider logs before retrying' WHERE order_id=?",(order_id,))
            return False
        payload=row['payload'] or json.dumps({
            'from':os.environ['PURCHASE_EMAIL_FROM'], 'to':[row['recipient']],
            'subject':SUBJECT, 'text':TEXT, 'html':HTML,
        },ensure_ascii=False)
        c.execute("UPDATE purchase_emails SET payload=?,first_attempt=COALESCE(first_attempt,?),lease_until=?,attempts=attempts+1 WHERE order_id=?",(payload,current,current+60,order_id))
    try:
        provider_id=send_provider(payload,'chyper-purchase-v1/'+order_id)
    except Exception as exc:
        # Do not log provider response bodies, recipient addresses or credentials.
        with db() as c:
            c.execute('UPDATE purchase_emails SET lease_until=0,error=? WHERE order_id=?',(type(exc).__name__,order_id))
        return False
    with db() as c:
        c.execute("UPDATE purchase_emails SET status='accepted',provider_id=?,accepted_at=?,lease_until=0,error=NULL WHERE order_id=?",(provider_id,current,order_id))
    return True


def drain(db, user_id=None):
    with db() as c:
        rows=c.execute("SELECT e.order_id FROM purchase_emails e JOIN orders o ON o.id=e.order_id WHERE e.status='pending'"+(' AND o.user_id=?' if user_id else '')+' LIMIT 20', (user_id,) if user_id else ()).fetchall()
    return all([deliver(row['order_id'],db) for row in rows])


if __name__=='__main__':
    from access import db
    if not drain(db):
        raise SystemExit('Some purchase messages remain pending; check configuration/provider status.')

