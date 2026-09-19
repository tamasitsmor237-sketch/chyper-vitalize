import os
import tempfile
import unittest
from types import SimpleNamespace as Obj
from unittest.mock import patch
import access
from app import app

class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ, {'CHYPER_DATA_DIR':self.tmp.name,'PUBLIC_BASE_URL':'https://example.test','STRIPE_WEBHOOK_SECRET':'test'})
        self.env.start()
        self.clock=patch('access.now',return_value=1000000)
        self.clock.start()
        app.testing=True
        access.bp.testing=True
        self.c=app.test_client()
        self.headers={'X-Chyper-Request':'1'}
        self.register()
    def tearDown(self):
        self.clock.stop();self.env.stop();self.tmp.cleanup()
    def register(self):
        r=self.c.post('/api/account/register',json={'email':'test@example.org','password':'a-long-test-password'},headers=self.headers)
        self.assertEqual(r.status_code,200)
    def purchase(self,plan='day',sid='cs_test'):
        with access.db() as c:
            uid=c.execute('SELECT id FROM users').fetchone()[0]
            c.execute('INSERT INTO orders(id,user_id,plan,amount) VALUES(?,?,?,?)',(sid,uid,plan,access.PLANS[plan][0]))
        s=Obj(id=sid,payment_status='paid',mode='payment',amount_total=access.PLANS[plan][0],currency='eur',client_reference_id=uid)
        with access.db() as c: access.grant(c,s,1000000)
        return s
    def test_all_plans_expire_at_exact_boundary(self):
        for plan,(_,seconds) in access.PLANS.items():
            with self.subTest(plan=plan):
                with access.db() as c:c.execute('UPDATE users SET expires=0')
                self.purchase(plan,'cs_'+plan)
                with access.db() as c:c.execute('UPDATE sessions SET expires=?',(1000000+40*86400,))
                with patch('access.now',return_value=1000000+seconds-1):
                    self.assertTrue(self.c.get('/api/account').json['active'])
                    self.assertEqual(self.c.put('/api/account/draft',json={'fields':{},'_revision':self.c.get('/api/account').json['revision']},headers=self.headers).status_code,200)
                with patch('access.now',return_value=1000000+seconds):
                    self.assertFalse(self.c.get('/api/account').json['active'])
                    self.assertEqual(self.c.put('/api/account/draft',json={'fields':{}},headers=self.headers).status_code,402)
                    for path in ['/api/pdf','/api/ai-review','/api/ai-interview','/api/mock-interview']:
                        self.assertEqual(self.c.post(path,json={},headers=self.headers).status_code,402)
    def test_repeated_webhook_is_idempotent(self):
        s=self.purchase()
        with access.db() as c:access.grant(c,s,1000050)
        self.assertEqual(self.c.get('/api/account').json['expires_at'],1086400)
    def test_renewal_extends_existing_access(self):
        self.purchase();self.purchase('week','cs_2')
        self.assertEqual(self.c.get('/api/account').json['expires_at'],1000000+8*86400)
    def test_relogin_restores_draft_and_purchase(self):
        self.purchase()
        draft={'fields':{'name':{'value':'Test Person'}},'storage':{}}
        self.c.put('/api/account/draft',json={**draft,'_revision':0},headers=self.headers)
        self.c.post('/api/account/logout',json={},headers=self.headers)
        self.assertEqual(self.c.get('/api/account').status_code,401)
        r=self.c.post('/api/account/login',json={'email':'test@example.org','password':'a-long-test-password'},headers=self.headers)
        self.assertEqual(r.status_code,200)
        d=self.c.get('/api/account').json
        self.assertEqual(d['draft'],draft);self.assertTrue(d['active'])
    def test_another_account_cannot_use_checkout_id(self):
        self.purchase()
        stranger=app.test_client()
        stranger.post('/api/account/register',json={'email':'other@example.org','password':'another-long-password'},headers=self.headers)
        self.assertFalse(stranger.get('/api/payment-status?session_id=cs_test').json['paid'])
        self.assertEqual(stranger.post('/api/pdf',json={'session_id':'cs_test'},headers=self.headers).status_code,402)
    def test_invalid_plan_and_client_amount_ignored(self):
        self.assertEqual(self.c.post('/api/create-checkout-session',json={'plan':'forever'},headers=self.headers).status_code,400)
        mock=Obj(checkout=Obj(sessions=Obj(create=lambda **kwargs: self.checkout_assert(kwargs))))
        with patch('access.client',return_value=mock):
            self.assertEqual(self.c.post('/api/create-checkout-session',json={'plan':'week','amount':1,'referral':True},headers=self.headers).status_code,200)
    def checkout_assert(self,kw):
        self.assertEqual(kw['params']['line_items'][0]['price_data']['unit_amount'],499)
        return Obj(id='cs_new',url='https://checkout.stripe.com/test')
    def test_no_csrf_header_and_invalid_webhook(self):
        self.assertEqual(self.c.post('/api/account/logout',json={}).status_code,403)
        self.assertEqual(self.c.post('/api/stripe-webhook',data='fake').status_code,400)
    def test_late_payment_reconciliation_does_not_reset_clock(self):
        with access.db() as c:
            uid=c.execute('SELECT id FROM users').fetchone()[0]
            c.execute('INSERT INTO orders(id,user_id,plan,amount) VALUES(?,?,?,?)',('cs_late',uid,'day',199))
        s=Obj(id='cs_late',payment_status='paid',mode='payment',amount_total=199,currency='eur',client_reference_id=uid,payment_intent='pi')
        mock=Obj(checkout=Obj(sessions=Obj(retrieve=lambda _:s)),payment_intents=Obj(retrieve=lambda _:Obj(latest_charge='ch')),charges=Obj(retrieve=lambda _:Obj(created=900000)))
        with patch('access.client',return_value=mock):
            d=self.c.get('/api/account').json
        self.assertFalse(d['active']);self.assertEqual(d['expires_at'],986400)
    def test_concurrent_draft_update_is_rejected(self):
        first=self.c.put('/api/account/draft',json={'fields':{},'_revision':0},headers=self.headers)
        self.assertEqual(first.status_code,200)
        second=self.c.put('/api/account/draft',json={'fields':{'name':'stale'},'_revision':0},headers=self.headers)
        self.assertEqual(second.status_code,409)

    def test_storage_misconfiguration_fails_closed(self):
        with patch.dict(os.environ,{'CHYPER_DATA_DIR':''}):
            self.assertEqual(self.c.get('/api/account').status_code,503)

if __name__=='__main__': unittest.main()

