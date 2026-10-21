# Login second-factor gate

`SecondFactor.Check` implements the OTP decision inside the existing login view.
It accepts **trusted persisted** two-factor settings and a parsed request value;
clients must never supply the enabled flag or secret used by this service. The
composition still needs to run credentials, active-status checks and username/audit
normalization before this gate, then response generation and the remaining login
writes after it. No public Go login route is registered yet.

The crypto helper uses the existing PyOTP defaults: HMAC-SHA1, six decimal digits,
30-second counters, and only the current counter. Base32 secrets accept ASCII lower
case and omitted padding, including the existing decoder's unused-bit behavior.
Non-ASCII, whitespace, invalid alphabet and malformed padding are rejected without
including the secret in errors. Empty persisted secrets retain PyOTP's behavior;
this is compatibility for existing state, not a provisioning policy.

The view checks Python length before verifying a code. Missing values or lengths
other than six return `OTP code required.`; a six-character mismatch returns
`OTP code not valid.`. Null/numeric/boolean JSON values have no Python length and
retain the server-error category. Lists and objects of length six still reach
secret decoding before failing comparison. Missing/corrupt secrets are server
errors only when execution reaches generation. Disabled two-factor settings ignore
both the supplied OTP and secret. Errors do not contain either value.

Strings count Unicode code points, then undergo PyOTP's NFKC normalization before
a constant-time comparison. Fullwidth, superscript and mathematical digits can
therefore work, while Arabic digits remain distinct. Input whitespace is not
trimmed. A multipart file uses its byte size for Python `len(file)` and filename
for `str(file)`; its contents are never used as the code. The transport parser now
retains that metadata while streaming/discarding opaque file contents. Other
credential/reference consumers continue to treat files as opaque values.

The gate has no persistent effects or replay counter, matching current behavior.
The same valid code can be accepted again within its interval. Cancellation is
checked before and after computation; no credential is issued by this component.

`TESTS/adapters/django/second_factor.py`, invoked by the private account adapter,
runs 47 explicit cases through the actual Django login view with production
password hashers. Only PyOTP's clock is replaced, with fixed timestamps; Django
signing, serialization and database writes remain real. The adapter checks the
selected token account on success and verifies that denied OTPs never update
`last_login`. It restores declared fixture fields and compares every managed
model table. The same decisions run through Go under the `otpintegration` tag;
unit tests also cover known codes, exact boundaries, malformed secrets, cancellation
and the actual multipart parser. All keys and accounts are synthetic.

This is component evidence, not Go login HTTP parity. Username/audit normalization,
full response construction, social login, distributed rate limiting, remaining
login writes, OTP provisioning/reset, session middleware and backend switching
remain required. Multipart filename sanitization and other parser edge behavior
also remain subject to the broader transport compatibility work.
