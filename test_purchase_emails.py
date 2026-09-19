import json
import os
import unittest
from unittest.mock import patch
import access
import purchase_emails as mail
from test_access import AccessTests

class PurchaseEmailTests(AccessTests):
    def setUp(self):
        super().setUp()
        self.mailenv=patch.dict(os.environ, {'RESEND_API_KEY':'test-only-key','PURCHASE_EMAIL_FROM':'Chyper <test@example.org>'})
        self.mailenv.start()
        self.provider=patch('purchase_emails.send_provider',return_value='email-test-id')
        self.send=self.provider.start()
    def tearDown(self):
        self.provider.stop();self.mailenv.stop();super().tearDown()
    def test_paid_purchase_queues_exact_message_and_account_recipient(self):
        self.purchase()
        self.assertTrue(mail.deliver('cs_test',access.db,clock=lambda:1000000))
        payload,key=self.send.call_args.args
        d=json.loads(payload)
        self.assertEqual(d['to'],['test@example.org'])
        self.assertEqual(d['subject'],'Köszönjük a vásárlásodat!')
        self.assertEqual(d['text'],'Köszönjük a vásárlásodat és a bizalmadat!\n\nChyper')
        self.assertEqual(key,'chyper-purchase-v1/cs_test')
    def test_repeat_payment_notification_does_not_send_twice(self):
        s=self.purchase()
        mail.deliver(s.id,access.db,clock=lambda:1000000)
        with access.db() as c:access.grant(c,s,1000020)
        mail.deliver(s.id,access.db,clock=lambda:1000020)
        self.assertEqual(self.send.call_count,1)
    def test_provider_timeout_retries_same_payload_and_key(self):
        self.purchase()
        self.send.side_effect=[TimeoutError(),'email-test-id']
        self.assertFalse(mail.deliver('cs_test',access.db,clock=lambda:1000000))
        with patch.dict(os.environ,{'PURCHASE_EMAIL_FROM':'Changed <different@example.org>'}):
            self.assertTrue(mail.deliver('cs_test',access.db,clock=lambda:1000060))
        self.assertEqual(self.send.call_args_list[0],self.send.call_args_list[1])
        with access.db() as c:self.assertEqual(c.execute('SELECT expires FROM users').fetchone()[0],1086400)
    def test_missing_configuration_leaves_queued_without_sending(self):
        self.purchase()
        with patch.dict(os.environ,{'RESEND_API_KEY':''}):
            self.assertFalse(mail.deliver('cs_test',access.db))
        self.send.assert_not_called()
    def test_unknown_or_unpaid_purchase_never_sends(self):
        mail.deliver('cs_unknown',access.db)
        self.send.assert_not_called()
    def test_ambiguous_send_stops_before_provider_dedupe_expires(self):
        self.purchase();self.send.side_effect=TimeoutError()
        mail.deliver('cs_test',access.db,clock=lambda:1000000)
        self.assertFalse(mail.deliver('cs_test',access.db,clock=lambda:1000000+23*3600))
        self.assertEqual(self.send.call_count,1)
        with access.db() as c:self.assertEqual(c.execute('SELECT status FROM purchase_emails').fetchone()[0],'review')

if __name__=='__main__':unittest.main()

