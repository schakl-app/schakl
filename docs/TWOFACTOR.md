# Two-factor authentication (2FA)

Second factors for **local (password) login**: TOTP authenticator apps, single-use backup
codes, and — when the instance operator configures a gateway — SMS codes. An org that
enforces OIDC gets its MFA from the IdP; the whole surface below sits behind the same
per-org `require_local_login` guard as the rest of the password machinery (docs/SSO.md).

## Model

FastAPI Users has no second-factor concept, so this is a layer on top of it that reuses the
framework's own primitives (`app/core/auth/twofactor.py` + `twofactor_router.py`):

- `/auth/login` is replaced (same route name, same contract for accounts without a factor).
  When the account has a **confirmed** factor, a correct password returns
  `200 {"two_factor_required": true, "challenge_token", "methods"}` and **no cookie**.
- The challenge token is a short-lived JWT (`fastapi_users.jwt`, its own audience, default
  5 minutes — `SCHAKL_TWOFACTOR_CHALLENGE_LIFETIME_SECONDS`). It proves a fresh password
  check and is redeemable only at `POST /auth/2fa/verify {challenge_token, code, method}`,
  which is what finally issues the session cookie.
- One row per enrolled user in `user_two_factor`. Like `users` this is **global identity,
  not tenant data** (CLAUDE.md §5): no `org_id`, no RLS — a user's factor follows them
  across every org they belong to.
- Secrets at rest follow the house rules (`app/core/crypto`): the TOTP secret is
  Fernet-encrypted (it must round-trip to verify codes); backup and SMS codes are stored
  **hashed** (verify-only, the API-key rule).

## Enrollment (Mijn account → Tweestapsverificatie)

1. `POST /auth/2fa/setup` mints a secret and returns it as QR (server-rendered SVG, segno)
   plus manual key. Re-calling while unconfirmed **rotates** the secret; a confirmed setup
   409s (disable first).
2. `POST /auth/2fa/confirm {code}` — a valid code from the freshly scanned app is what turns
   2FA on, and mints the 10 single-use backup codes, returned exactly once.
3. TOTP verification tolerates ±1 time step and refuses **replay** of an already-accepted
   step (`totp_last_counter`).
4. Backup codes redeem a login challenge once each; `POST /auth/2fa/backup-codes {code}`
   regenerates the set (costs a current TOTP code — a stolen session alone must not be able
   to mint recovery codes).
5. `POST /auth/2fa/disable {password}` — turning off a confirmed setup costs the password;
   abandoning an unconfirmed one is free.

Every verify path shares brute-force damping: 8 failures lock the factor for 15 minutes
(`429 errors.two_factor_locked`).

## SMS codes (instance-level opt-in)

SMS exists only when the **operator** configured a gateway — it is instance configuration,
like `SCHAKL_INSTANCE_ADMIN_ENABLED`, never tenant data:

```
SCHAKL_SMS_GATEWAY_URL=https://your-gateway.example/send   # unset = no SMS anywhere
SCHAKL_SMS_GATEWAY_TOKEN=...                               # optional; sent as Bearer
SCHAKL_SMS_GATEWAY_SENDER=...                              # optional sender id
```

The API POSTs `{"to": "+31612345678", "message": "...", "sender": "..."}` as JSON with an
optional `Authorization: Bearer` header — a generic webhook seam that fronts Twilio,
MessageBird, Spryng or a self-hosted gateway equally well. The message text is a catalog
string (`twofactor.sms_message`) in the user's locale.

Per user, SMS is an **add-on to a confirmed TOTP setup, never the only factor**: the number
is registered under `POST /auth/2fa/sms/setup {phone}` (E.164), becomes usable only after
echoing a code sent to it (`/auth/2fa/sms/confirm`), and can be dropped at any time
(`DELETE /auth/2fa/sms`) without leaving the account factor-less. At login,
`POST /auth/2fa/challenge/sms {challenge_token}` texts a 6-digit code (10-minute expiry,
5 attempts, 30s resend interval, single-use).

## Losing the phone — the escape hatches

- **Backup codes** are the self-service path.
- **An org admin resets** (Instellingen → Gebruikers → Tweestapsverificatie resetten):
  `DELETE /members/{membership_id}/two-factor`, gated on `members.member.write`. It deletes
  the enrollment outright — the account is a plain password login until the member
  re-enrolls; no secret is ever *read*. The target is addressed by **membership**, so an
  admin of another org has no id of theirs to name (404); because identity is global, the
  reset genuinely clears their 2FA everywhere (on the self-hosted single-org deployment
  those are the same thing). Every reset is written to the org's `role_audit_log`
  (`membership.two_factor_reset`).
- The operator break-glass: with database access, `DELETE FROM user_two_factor WHERE
  user_id = ...` is the same operation.

## What it deliberately does not do

- **No 2FA on OIDC logins** — the IdP owns MFA for federated sessions (`docs/SSO.md`).
- **No org-level "require 2FA" policy yet** — enrollment is per-user opt-in. The
  enforcement toggle is a natural follow-up; the login flow already has the seam.
- **No trusted-device / remember-me** — every password login with a confirmed factor asks
  for a code.
- API keys (#20) and MCP are untouched: those authenticate with their own scoped
  credentials, not passwords.
