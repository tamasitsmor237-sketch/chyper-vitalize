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

1. Attach a Railway persistent volume, e.g. `/data`, and set `CHYPER_DATA_DIR=/data`. Do not point it at the ephemeral application directory. SQLite supports multiple workers on one machine; use one Railway replica and enable volume backups. Horizontal scaling requires migration to a shared database.
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
- Verify proxy-aware login rate limiting on Railway; the current conservative DB limiter uses the socket peer IP and may group visitors behind a proxy.
