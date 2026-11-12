# Two-factor provisioning

`GET /two-fa/setup` creates a fresh 32-character Base32 secret and returns the
existing `data.otp_secret` and `data.qrcode_uri` fields. Nothing is saved until a
valid OTP is submitted to `/two-fa/update`. Bearer authentication and the existing
`other.settings.update` permission apply even though setup is a GET. Collaborator,
administrator, impersonation and foreign-reference restrictions retain the shared
identity resolver. OPTIONS returns current metadata; HEAD and POST retain 405.

The composition root supplies cryptographic entropy, a local QR encoder and the
existing branding configuration explicitly. The service receives trusted resolved
identity and `context.Context`; it has no database dependency. Entropy errors,
encoding failures and cancellation do not return partial provisioning material.
The DTO exposes the secret only in the successful response. No provider, file,
new schema or new environment variable is needed.

The provisioning URI preserves PyOTP's SHA1, six digits and thirty-second defaults,
label quoting and query order. The account label comes from `EMAIL_HOST_SUPPORT`,
including the existing distinction between an absent value (`None` as text) and
an empty value (PyOTP's `Secret` fallback). `WHITELABEL_NAME` defaults to
`Bakney Sport`; an empty issuer is omitted. Django-environ aliases apply to the
issuer, while the support address remains literal. The environment oracle now
contains 103 declared cases, including eight branding/default/precedence cases.

The MIT-licensed [Go QR encoder](https://github.com/skip2/go-qrcode/tree/da1b6568686e)
is pinned in `go.mod`; its license is included in the image. Provisioning URIs are
percent-encoded ASCII containing lowercase text. A byte-mode capacity probe keeps
Segno's minimum version and correction-level boosting policy. The existing
2953-byte maximum is rejected before matrix allocation. SVG retains the white
four-module border, black horizontal strokes and UTF-8 data-URI shape. Go can split
payload segments and choose a different mask, so encoded matrices/path strings may
differ. Verification covers decoded content, dimensions and correction level.

## Executed contracts

`TESTS/contracts/test_two_factor_setup.py` and its Django adapter exercise actual
Django ASGI and the compiled Go root with private PostgreSQL/Redis. The declared
suite covers 31 HTTP scenarios and 62 concurrent requests per backend across six
branding/capacity configurations, including expected over-capacity failures.
All managed models and Django session/cache bytes remain unchanged by setup.
The bootstrap readiness probe owns separate short-lived Redis keys and is excluded
explicitly from that setup-state comparison.

Four producer/consumer combinations exercise setup, confirmation, status and
disabling across both runtimes. Four further combinations require a fresh password
login after setup: absent/incorrect OTP is denied, correct OTP issues credentials
accepted by both runtimes, and disabling preserves the existing secret semantics.
Django reads back the written secret and audit attribution. Fixture restoration
checks all managed tables and cache values. Login uses the production password
hasher list, avoiding the fixture adapter's otherwise cheap MD5 default.

The independent Pillow/ZXing helper rasterizes the emitted SVG subset and decodes
its actual pixels. The HTTP verifier selects QR format and the generated-image
reader mode. A separate deterministic comparison decodes 256 synthetic symbols
from each encoder in both generated-image and detector modes, checking expected
URI, symbol size and error-correction level. This proves 512 decoded symbols;
physical phone-camera interaction and the complete frontend workflow still need
separate evidence. Portable QR dependencies are pinned in
`TESTS/contracts/requirements-qr.txt` and included in CI's adapter dependencies.

PostgreSQL tracing measures one identity SELECT for direct owner/athlete setup and
two for collaborators, with zero added provisioning queries. Thirty-two mixed
concurrent requests use 48 SELECTs in at most four connections. These are service
admission counts, excluding the rest of the HTTP middleware chain.

Run `python TESTS/adapters/django/accounts.py --only two-factor-setup` with the
production Python version and test dependencies, or run the full accounts adapter.
Initial missing-route, missing implementation and configuration failures are kept
under `quality-reports/go-migration/two-factor-setup/`. The migration evidence index
records final checks. Fixtures use current-model run-syncdb; historical migrations,
versioned proxy routing, the complete authentication/frontend matrix and production
activation remain unverified.
