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
4. `token`, `username` and `backend` present: the social path. Other backends 400 `{'error': 'Unsupported backend: <str(backend)>'}`;
   Google/Apple with no configured client id (`provider_client_id`) 401 `{'error': 'Invalid social token'}` (a view response:
   no Bearer challenge).
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

## Not ported (Go answers the 500 page before any write)

- signup social path with a configured Google or Apple client id: token verification calls the provider (Google certificates or
  token info, Apple keys).
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

- RED (`native-r1`): Django 109/109; Go 404 on the first case.
- GREEN (`versioned-r2`): 109/109 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): dropping `NOT deleted` from the reset e-mail lookup issues a token for the deleted account and fails
  Go on `oauth-accounts-reset-shared-email` (`reset.tokens`) — CAUGHT.
- Re-run with the shared changes (`oauth-rerun-r1`): email-render-routes 82/82, instance-setup 86/86, collaborators-writes 45/45
  on native Django and Go; `go test ./internal/...`.
