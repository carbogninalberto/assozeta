# oauth2 signup, partial signup, password reset and account deletion — implementing with HTTP parity

`POST oauth2/signup` (`oauth2_signup`), `oauth2/partial-signup` (`partial_signup`), `oauth2/reset` (`oauth2_reset_password`) and
`oauth2/delete-account` (`oauth2_delete_account`), all in `BE/application/views/auth_views.py`, are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/oauth_accounts.go`, `dto/oauth_signup.go`, routes in
`cmd/server/main.go`). Family id for `tools/go-migration/family.py`: `oauth-accounts` (family names are letters only).

## Shared behaviour

- All four are `@api_view(['POST'])` (`Allow: POST, OPTIONS`); OPTIONS names `Oauth2 Signup`, `Partial Signup`,
  `Oauth2 Reset Password`, `Oauth2 Delete Account` with the docstrings (none for partial signup and delete account).
- signup, partial-signup and reset are `AllowAny`: a Bearer token still authenticates and its account is the audit actor;
  delete-account is `IsAuthenticated` (anonymous 401 with the Bearer challenge, before the method check).
- The paths are in `EXCLUDED_ENDPOINTS` and start with `oauth2/`: restricted collaborators and administrators without a session
  pass identity resolution. Impersonation sessions with an association boundary go through `enforce_scoped_request`
  (an athlete target: `Operazione non disponibile nel contesto dell’associazione.`; delete-account:
  `Torna al tuo account per gestire le credenziali.`).
- Writes are not atomic in Django; Go commits each row write with its auditlog entry and index row, in Django's order.
- Audit index: `UserPartial` and `CollaborationInvites` entries and the new `User` entry resolve to the actor's live association
  (`actor.sport_association`), none for anonymous requests; the delete-account update entry uses the user's live association
  (`user.sport_association`), then the actor's.
- E-mails: the reset, reset confirmation and welcome templates are rendered with `internal/emailrender` (three templates added,
  byte-identical copies of `BE/templates/email/account/`) and queued as `core.tasks.send_mail_async.apply_async(kwargs=...)`
  (subject, message, from_email, recipient_list, html_message, fail_silently; no association); partial signup and delete
  account queue `send_email_text.delay(recipient_list=..., subject=..., message=...)`. Messages are published after the commit.

## partial-signup

- `request.data` must be a dict (lists and other JSON values: 500). No `email` key: `{'msg': 'Email added.'}` and nothing else.
- `UserPartial.objects.filter(email__iexact=email).first()`: `iexact` does not prepare its value, so numbers, booleans, lists,
  dicts and None reach PostgreSQL and raise (500); for None the lookup is `IS NULL` and `create()` violates `NOT NULL`.
- A new e-mail (stored as sent; over 254 characters the INSERT raises) gets its create entry (`UserPartial object (<id>)`,
  `object_id` set: integer key) and `send_email_text(['info@bakney.com'], 'Nuovo utente parziale', 'Utente parziale con email <email> registrato.')`.
  `modelAuditEntry.ObjectID` was added for integer-keyed models (NULL otherwise, as before).

## reset

- Exactly `{'email'}`: `str(email).strip()`, live users `email__iexact` ordered by `user_id` (inactive ones included); per user a
  `token_hex(16)` set in Redis DB 2 (`ex=3600`, value the user id) and the reset e-mail to `user.email`.
  `{'msg': 'Email sent to the user if the email exists.'}` in every case.
- Exactly `{'token', 'password'}`: `r.get(token)` (strings, ints by `str()`, floats by `repr()`; None, booleans, lists, dicts: 500),
  then `re.search` of the password pattern (a non-string password: 500, even for an unknown token). The pattern is checked on the
  raw value: the first line holds `[A-Z]`, one of `!@#$&.-_*` and a digit, has ten or more characters and is followed by nothing
  or one final newline. Unknown token or failing password: 400 `{'msg': 'Invalid request.'}` (the token is kept).
  Then `User.objects.get` (a stored value that is not a UUID, or a deleted user: 500), `set_password` (PBKDF2-SHA256,
  1,000,000 iterations, `pbkdf2_sha256$1000000$<salt>$<hash>`; no audit entry: the password is excluded), the key is deleted and
  the confirmation e-mail sent; `{'msg': 'Password reset.'}`.
- Anything else 400. Go uses its own client on Redis DB 2 with the shared connection options.

## delete-account

- The user is `request.user`, or the acting collaborator when the scope is a collaborator's owner (also when an administrator
  impersonates a collaborator). `delete_on` becomes the UTC date 30 days ahead; an update entry
  `{'delete_on': [<old or None>, <new>]}` (`name: <first> <last>`) only when the date changes;
  `send_email_text([user.email], '<WHITELABEL_NAME> | Eliminazione account', '... il giorno dd/mm/YYYY.')`.

## signup (non-social path)

In Django's order:

1. `sport_association`: missing 400 `This field is required.`, not a bool 400 `Must be a boolean.` (form bodies always),
   `true` 403 `Association accounts can only be created during instance setup.`.
2. E-mail: missing or not a string 500. With `DEBUG` off, `validate_disposable_email` on the lower-cased value: a valid e-mail
   matching `re.match('.*<entry>$')` for an entry of `custom_email_domain_loader()` (entries are regular expressions:
   `mailcatch.*`, `.` matches any character) is 400 `{'msg': 'You are nasty 🖕( •_• )🖕'}`. The list is embedded in
   `service/oauth_disposable_domains.txt`; the runner requires it to equal Django's loader output. Then
   `email.split('@')[1]`: no `@` raises (500); a second part equal to `skrank.com` (any case) raises `TypeError` from
   `raise ValidationError` without a message (500).
3. `collaboratorToken` present: role collaborator. `subscription_transfer` with `subscription_transfer_token`: a truthy token
   that `UUIDField` rejects raises (500), whatever the flag.
4. `token`, `username` and `backend` present: the social path (next section). Other backends 400
   `{'error': 'Unsupported backend: <str(backend)>'}`.
5. `UserSerializerSignup`: first/last name (150, blank allowed), email (254, `EmailValidator`), phone (20, null/blank allowed),
   username (required; `UnicodeUsernameValidator`, the `UniqueValidator` on all users with an exact match, max length, null
   characters — every validator's message is listed), password (`RegexField`, trimmed before the pattern). Errors:
   400 `{'msg': 'Invalid request.', 'details': {...}}`.
6. The `UserPartial` with the same e-mail (`iexact`, first by key) is deleted (delete entry).
7. Collaborators: `CollaborationInvites.objects.filter(token=str(value)).first()` (by key); none 400 `Collaboration invite not found.`; the invite is deleted (delete entry), then a null expiration raises
   (500) and a past one is 400 `Collaboration invite expired.`.
8. A missing first or last name raises (500). `create_user`: upper-cased username, `normalize_email`, the raw (untrimmed)
   password, role, and for collaborators the invite's owner, collaborator role and permissions; the validated phone is not
   passed. A username equal to an existing one after upper-casing violates the unique constraint (500, earlier deletions kept).
   Create entry `name: <first> <last>` with every field (reverse related managers included).
9. `subscription_transfer` `true` with its token key: `QuerySet.save()` raises (500, the user is kept).
10. JWT pair (`jwt.Manager.Issue`), `build_login_response` (`service.LoginResponses`), the welcome e-mail to the lower-cased
    address; for collaborators `user_data.sport_association` is replaced by the association serialized again (with
    `empty_sections`) and `user_data.requires_welcome` is false. An owner without a live association: 500 after the user is created.

## signup (social path)

After steps 1–3 above, with `token`, `username` and `backend` in the body:

1. `SocialAuthService.verify_google_token` (`google-oauth2`, `google-identity`, `google`) or `verify_apple_token` (`apple-id`,
   `apple`): Google's certificates and `verify_oauth2_token` with the configured client id, falling back to token info and user info;
   Apple's keys and PyJWT (`RS256`, audience, issuer). No configured client id, or a token the provider rejects: 401
   `{'error': 'Invalid social token'}` (a view response: no Bearer challenge). Go uses the login route's provider clients
   (`pkg/social`, `service.SocialProviders`), which the composition root now shares with this route.
2. `SocialAuthService.has_verified_email`: the identity's e-mail is a non-empty string and `email_verified` is `True` or the string
   `'true'` (Apple sends the claim as text or as a boolean; a private relay address is a verified address like any other and becomes
   the account's e-mail). Otherwise the same 401. Go: `SocialIdentity.VerifiedEmail`.
3. Live accounts with that e-mail (`iexact`, by `user_id`). When there is one, `_existing_account_signin` applies the login rules and
   writes nothing: more than one account or an inactive one is 401 `{'error': 'invalid credentials'}`; `two_fa` follows the login
   view's check (`'otp' in data and len(data['otp']) == 6`, else `OTP code required.`; a wrong code `OTP code not valid.`; a value
   without a length raises); then the tokens and `build_login_response` for the stored account with the collaborator override
   (`_signup_content`). No row changes, no invite is consumed and no e-mail is sent. Go: `existingAccountSignin` with the login
   candidates query and `SecondFactor`.
4. No account: `collaboratorToken` goes through `_accept_collaboration_invite`, the same lookup, deletion and expiry check as the
   password path (not found 400, expired 400 after the deletion, a null expiry raises).
5. `create_user`: names from the identity or else the body as sent (default `''`), `data['username'].upper()` (not a string or
   empty: 500 after step 4), the identity's e-mail through `normalize_email`, no usable password (`!` and 40 random characters),
   the role and, for collaborators, the invite's owner, collaborator role and permissions. A username conflict is a 500.
6. The shared tail: the `UserPartial` of the body's e-mail is deleted (after the user, unlike the password path), a subscription
   transfer raises (the user is kept), then tokens, response and the welcome e-mail as in the password path.

## Not ported (Go answers the 500 page before any write)

- signup social path: an identity or body first/last name that is not a string, and a non-ASCII identity e-mail or username when
  a new account would be created (Python's Unicode `lower()`/`upper()`).
- signup e-mails with non-ASCII characters (Python's Unicode `lower()`) and usernames with non-ASCII characters (Python's
  Unicode `\w` and `upper()`).
- reset passwords with NUL characters or lone surrogates.
- Non-English languages are rendered (validation messages in Italian for other languages, e-mails through `emailrender`) but the
  HTTP family runs in English only.

## Evidence

`TESTS/fixtures/oauth_accounts_http.json` (109 cases from `TESTS/fixtures/generate_oauth_accounts_http.py`; contract
`TESTS/contracts/test_oauth_accounts_http.py`, adapter `TESTS/adapters/django/oauth_accounts_http.py`). The runner seeds users,
invites, partial users and a card configuration, sets authored tokens in Redis DB 2 and serves Django with its default password
hashers. Every case checks the complete model state, Redis DB 2, the audit entries (auditlog's own diff of the stored or
pre-case instance) and index rows, and the Celery messages byte for byte (e-mail bodies rendered with `render_to_string`).
New users are checked field by field (defaults included), passwords with `check_password` and the default hasher; signup
responses are verified with SimpleJWT and compared with Django's `build_login_response` for the stored user.

Shared changes: `http_family.assert_case` takes an optional `challenged` predicate (default: every 401);
`family_harness.fixture_context_for` purges with each model's own seeded keys; `modelAuditEntry.ObjectID`.

### Social signup (sec-oauth-signup)

The fixture has 159 cases (50 new). `service_context` now also owns the login family's provider stub
(`TESTS/adapters/django/login_provider_proxy.py`: a CONNECT proxy with a private certificate authority answering Google's
certificates, token info and user info and Apple's keys), the proxy and CA settings both servers read, and the Google and Apple
client ids. Tokens (`{'$social': ...}`) are signed with the stub's key when the request is sent and one-time codes (`{'$totp': ...}`)
come from the seeded secret; every case must make exactly its authored provider requests. The two cases that ran without a
configured client id now send a token the provider rejects (same request and answer); the unconfigured answer stays covered by
`service/social_providers_test.go`. New cases: rejected tokens (signature, expiry, client id) for both providers, every
`email_verified` shape, missing and non-string e-mails, a Google access token whose user info carries no verification, existing
owner / athlete / collaborator sign-in with and without a `collaboratorToken`, inactive and shared-e-mail accounts, the OTP
answers, invites (unknown, expired, used, valid), names from the identity or the body, a private relay address, a deleted
account's e-mail, username conflicts and the transfer path. A `signin` effect requires that nothing changes and nothing is sent.

- Django tests first: `application.tests.test_signup_policy.SocialSignupTests` (10 tests; 23 failures and errors before the change).
- RED (`secoauth-red-r2`): Django 159/159; Go 500 on `oauth-accounts-signup-google-rejected-token`.
- GREEN (`secoauth-versioned-r1`, after the base merge; natively `secoauth-native-r1`): 159/159 on native Django, native Go, Caddy
  `/api/v1` and `/api/v2`.
- Mutations: accepting any `email_verified` value fails Go on `oauth-accounts-signup-social-google-unverified`
  (`secoauth-mutation-verified-r1`, CAUGHT); writing the role while reading the second factor of an existing account fails Go on
  `oauth-accounts-signup-social-google-existing-owner` (`state.no-effect`, `secoauth-mutation-role-r1`, CAUGHT).
- `go test ./internal/service ./internal/handler ./internal/repository ./pkg/social ./cmd/...`.

- Re-run after the login route gained the same verified e-mail rule and the provider stub its per-token claim
  (`secoauth-login-versioned-r1`): 159/159 on four transports; see `BE_GO/internal/service/social_login.md`.

### First port

- RED (`native-r1`): Django 109/109; Go 404 on the first case.
- GREEN (`versioned-r2`): 109/109 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): dropping `NOT deleted` from the reset e-mail lookup issues a token for the deleted account and fails
  Go on `oauth-accounts-reset-shared-email` (`reset.tokens`) — CAUGHT.
- Re-run with the shared changes (`oauth-rerun-r1`): email-render-routes 82/82, instance-setup 86/86, collaborators-writes 45/45
  on native Django and Go; `go test ./internal/...`.
