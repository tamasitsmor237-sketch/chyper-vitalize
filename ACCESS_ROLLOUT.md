# Account-bound passes — rollout checklist

This branch is a reviewable implementation, not a production migration. Do not merge into the Railway deployment branch until the following gates are complete.

## Product behavior

- Day: EUR 1.99, exactly 86,400 seconds.
- Week: EUR 4.99, exactly 604,800 seconds.
- Month: EUR 9.99, explicitly 30 days (2,592,000 seconds).
- Non-renewing purchases. A new purchase extends remaining paid access, or starts at the payment timestamp if access has expired.
- Sign-in is required at checkout. Drafts and access live in a server database, not in a browser paid flag.
- Expired accounts may read their saved CV but cannot save edits, generate PDFs or call paid AI endpoints. The editor is made inert at expiration. The public pre-purchase editor remains a free preview; client-side code cannot prevent someone copying HTML or manipulating their own browser.
- Recovery uses a one-time recovery code shown at registration. Email delivery and email verification are not implemented. Recovery rotates the code and invalidates sessions.
- The old client-asserted 50% discount is hidden and ignored server-side. It cannot be safely combined with new fixed prices without a verified referral policy.

## Required deployment setup

1. DONE: Railway has deployed the 256 MB `chyper-data` persistent volume at `/data` and `CHYPER_DATA_DIR=/data`. `PUBLIC_BASE_URL=https://chypervitalize.com` is configured. The existing main-branch app was redeployed successfully; this feature branch is not deployed. Keep one replica. The current Railway plan does not offer volume backups; establish a separate backup plan before using this as the sole copy of customer data. Horizontal scaling requires a shared database.
2. Set `PUBLIC_BASE_URL` to the actual HTTPS production origin. Keep the existing `STRIPE_SECRET_KEY` secret.
3. Configure Stripe's signed webhook at `/api/stripe-webhook`, subscribing to `checkout.session.completed` and `checkout.session.async_payment_succeeded`. Store its signing secret as `STRIPE_WEBHOOK_SECRET` in Railway. A failed webhook is retried; login also reconciles pending orders.
4. Test with Stripe test keys and a real Checkout test session in staging. Verify delayed payment, webhook delivery, returning without the checkout URL, payment cancellation, account recovery, and reopening on another device. Unit tests mock Stripe and do not replace this gate.
5. Run browser QA for draft restore (photo, experience and education lists, colours, application/CV languages), renewals and the exact expiry screen. The current added account controls have Hungarian/English wording; remaining site languages need localized account strings before a fully multilingual rollout.
6. Establish a migration for previous paid Checkout sessions before deploying. Those purchases have no account ownership or duration; this code deliberately does not allow claiming them using only a session ID. Do not revoke existing buyers inadvertently. An operator-assisted verified-email migration or signed email claim flow is required.
7. Review refund/dispute handling: automated revocation is not in this branch. Establish support procedures before launch.

## Verification

Run `python -m unittest -v test_access` and `node --check static/account.js`.
Tests cover the exact expiration boundary for all three plans, reopening an account and its draft, ownership isolation, repeat fulfillment, renewal, delayed reconciliation using the original payment time, untrusted prices, CSRF/signature rejection and missing persistent storage.

## Limitations requiring follow-up before merge

- Browser QA and live Stripe sandbox integration are not yet performed.
- Account recovery requires the saved recovery code; there is no email password reset.
- Existing UI has multiple independent state restorers; dynamic education/experience editing must be exercised in a browser.
- Concurrent writes are rejected using a revision counter. Browser QA must exercise conflict recovery and photo autosave.
- Login rate limiting is now scoped to a hashed normalized account email, avoiding a single shared proxy-IP bucket. Add edge/network abuse protection before scaling.

## Purchase thank-you email

The paid-order transaction now queues one message per order in a durable `purchase_emails` outbox. The webhook sends it to the purchasing account's stored email address only after payment validation. Retries reuse the same immutable payload and Resend idempotency key; a stored provider ID prevents subsequent sends. Provider acceptance is not a guarantee of inbox delivery. A timeout never removes the purchased access.

Template (Hungarian, as requested):

- Subject: `Köszönjük a vásárlásodat!`
- Text: `Köszönjük a vásárlásodat és a bizalmadat!` followed by the signature `Chyper`.

Activation prerequisites, in addition to the account rollout gates above:

1. Connect/configure a Resend account and verify the sender's domain. The sender address must be selected by the owner; none has been fabricated.
2. Store `RESEND_API_KEY` as a Railway secret and `PURCHASE_EMAIL_FROM` as `Chyper <verified-address>`. Never commit keys. The current production configuration has neither variable.
3. Configure a reliable scheduled worker running `python -m purchase_emails` against the same persistent database to retry queued messages if webhook retries have ended. Account refresh also drains its user's queued messages. Do not run the worker on a separate unshared ephemeral database.
4. Provider idempotency lasts 24 hours. This implementation stops automatic retries after 23 hours from the first attempt and marks the message `review`; inspect provider logs to resolve ambiguous sends without duplicate emails.
5. Send an explicitly authorized end-to-end test to an owner's test address before enabling live purchase notifications. Tests in `test_purchase_emails.py` mock the provider; no real email was sent.

Reference: https://resend.com/docs/api-reference/emails/send-email and https://resend.com/docs/dashboard/emails/idempotency-keys

Service provisioning was not completed: Stripe Projects CLI is not installed in this runtime, and no existing mail-service credentials or verified sender are configured on the production service.

## Latest verification

- Existing production deployment with the mounted volume succeeded. The Chyper feature PR remains a draft.
- 36 Python test executions passed (includes inherited repetitions).
- Four DOM integration scenarios passed using the actual page scripts in jsdom: restored account/CV-language/download, expired account locking, edits during an in-flight save, and live timer expiry. No page-script diagnostics were emitted. These are DOM tests, not full visual browser QA or a real Stripe checkout.
- Run the DOM tests with `npm install --prefix /tmp/chyper-qa jsdom` then `CHYPER_JSDOM_PATH=/tmp/chyper-qa/node_modules/jsdom node test_account_ui.cjs`.
- Structured education/experience list controllers now rehydrate from the account draft and emit autosave notifications.
- Active access restores PDF controls on ordinary login, independent of a payment-return URL. Server time is used for the expiry timer.
- Connected Stripe connector exposes only `CEO Coster` in test mode (`acct_1UGnF6KHVo6nJejB`). User must identify whether this belongs to Chyper or connect the correct account before configuring webhooks or examining historical purchases. No Stripe account-specific mutation has been made.
- Purchase-email provider credentials and the owner's chosen verified sender are still missing.

