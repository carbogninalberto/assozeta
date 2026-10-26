# Initial SSO asset diagnostics

The initial-pairing receipt supplements the existing failure observation. It does
not change navigation, retries, timeouts, pairing assertions, or the acceptance
workflow in `selfhost/tests/browser/bakney-sso.mjs`.

## Evidence and current uncertainty

The supplied CI40381406632/job121779148610 receipt reported profile route, no
pairing button, successful instance status/config responses, and a TypeError
categorized as `Failed to fetch dynamically imported module`, without frames.
That does not establish which asset failed or why.

Read-only source observations:

- `UI/src/routes.js` dynamically imports `routes/profile/Profile.svelte`.
- `UI/vite.config.js` emits entry/chunk JS and other hashed assets using
  `build-assets/[name]-[hash][extname]` under `UI/dist/public`.
- The fixture's `staticFile` falls back to `index.html` on a file read failure,
  returning HTTP 200 with `text/html`. Therefore HTTP 200 alone cannot establish
  that a requested module was served as JavaScript.

An HTML fallback for a missing or unreadable chunk is one hypothesis. Transport,
certificate, blocking, and other module/dependency failures remain possible.
No built output or browser reproduction was available or run in this assignment.

## Additive receipt schema

`version: 1` and all existing fields remain. The additions are:

| Field | Retained evidence |
| --- | --- |
| `assetResponses` | Rows `{asset, status, contentType, count}`; equal basename/status/category tuples aggregate |
| `assetFailures` | Rows `{asset, category, count}`; equal basename/category tuples aggregate |
| `assetEventsDropped` | `{responses, failedRequests}` counts of qualifying events whose new tuple could not fit |
| `pageErrorDetails[].importAsset` | Optional validated `.js` basename from Chromium's exact `Failed to fetch dynamically imported module: <absolute URL>` message shape, including frameless errors |

`contentType` is one of `javascript`, `html`, `css`, `json`, `binary`, `image`,
`font`, `missing`, or `other`. MIME parameters and raw header values are discarded.
`missing` also covers headers unavailable to the observer. The synchronous
Playwright `Response.headers()` API supplies only the selected content-type to
the classifier; no headers are copied to the receipt and no asynchronous header
work can complete after `stop()`.

Failure `category` is one of `failed`, `aborted`, `network-changed`, `dns`,
`connection`, `offline`, `timeout`, `blocked`, `certificate`, or `other`. Only exact
allowlisted Chromium error codes receive a specific category. Missing, extended,
or unknown error strings become `other`.

Existing endpoint/status/failure counters and fixed page-error categories remain
unchanged. Stack-frame rows retain their existing `{asset, line, column}` shape
but now pass the same origin/path validation as other assets. Previously accepted
cross-origin or loose substring matches are deliberately rejected. Consumers
that permit additive fields remain compatible; exact object/schema consumers
must allow the new fields. Receipt absence in older artifacts is not equivalent
to an empty observation in a new artifact.

## Redaction, bounds, and lifecycle

- Accept absolute HTTP(S) asset URLs only when their origin equals the page's
  current HTTP(S) origin at event time. This handles observation beginning on
  `about:blank`, before fixture seeding and profile navigation. Invalid/unavailable
  page URLs fail closed. Cross-origin asset evidence is omitted.
- Validate the raw path before URL normalization: exactly `/build-assets/` plus
  one ASCII hashed basename, at most 128 characters including extension. Names
  start with a letter, digit, or underscore, use letters/digits/underscore/dot/hyphen,
  have an 8–32 character Vite-style hash, and end in `.js`, `.css`, `.svg`, `.png`,
  or `.woff2`. Dot-dot sequences, nested paths, percent escapes in the authority or
  path, credentials (including empty userinfo), backslashes, whitespace, lookalike
  prefixes/suffixes, and traversal are rejected. This intentionally omits other
  filenames/extensions rather than guessing their safety.
- Queries/fragments on otherwise valid URLs are discarded and never emitted.
  Full URLs/origins, request/response bodies, raw headers, tokens, exception text,
  DOM text, and cross-origin paths are never copied to the receipt. Only the
  existing fixed route/message identifiers and explicitly validated public build
  basenames are emitted.
- Keep at most 64 response tuples and 64 failure tuples, in first-seen order.
  Duplicate tuples update their existing counter even when full. Counters and
  omitted-event counts saturate at 1000. At most 128 distinct basenames can appear
  in network rows. Additional error-only basenames are bounded by five error
  details, each with five frames and one optional import basename (30 more).
- Existing endpoint and error-name sets stay fixed. Error detail capacity remains
  five; frame capacity remains five per detail; line/column numbers have at most
  nine digits. Asset URL parsing is limited to 4096 characters; exact import
  messages to 4096 characters; error classification and stack inspection to an
  8192-character prefix, with at most 20 candidate frame lines per error. Raw
  content-type inspection is limited to 256 characters.
- `stop()` removes all three observer listeners and disables already captured
  callbacks. It is idempotent and preserves unrelated listeners. Snapshots copy
  every nested retained row and counter object. Snapshots may still inspect the
  existing bounded UI indicators after stopping; network/error evidence is frozen.

An empty or saturated array is not proof that every asset loaded. The receipt
does not retain timing, request IDs, redirect chains, content bytes, or a full
network log. Equal basename observations aggregate, and an import error may name
an importer whose dependency actually failed.

## Verification and next CI evidence

Local TDD used the existing Node executable with no installs, services, browser,
or network:

```sh
node --test selfhost/tests/browser/sso-initial-diagnostics.test.mjs selfhost/tests/browser/sso-fixture-navigation.test.mjs
```

Baseline: 9 passing tests. Initial RED: 7 failures/3 passes against the old
observer. Further malformed-URL tests exposed acceptance of empty userinfo and a
trailing newline before tightening validation. Final GREEN: 16 passing tests,
including the five unchanged navigation tests. Coverage includes the actual
frameless Chromium error shape, MIME fallback, fixed failure categories,
secret-bearing input redaction, malformed/cross-origin/encoded URLs, floods,
detached snapshots, and stopped/captured listeners.

The parent should integrate the owned source/test files and reproduce the exact
existing SSO CI workflow. If the initial button still fails, inspect the existing
`initial-pairing-failure.json` artifact:

1. Confirm route/button and existing endpoint/page-error evidence, then find the
   optional `pageErrorDetails[].importAsset`.
2. Match that basename against `assetResponses` and `assetFailures`, also inspecting
   other failing dependency basenames. A `.js` response with `200/html` demonstrates
   HTML was served for that asset request; fixture fallback is consistent with
   this, but a missing file still needs confirmation against that CI build's
   output. A `404`/`500` is HTTP evidence, and an `assetFailures` row supplies a
   transport/browser category rather than a response status.
3. For a matching `200/javascript`, inspect other dependency rows; it does not
   establish that the importing module evaluated or every dependency loaded.
   With no matching row, check omissions/limits and whether the error exposed a
   qualifying same-origin URL. Do not infer success or change acceptance behavior.

Only the parent owns actual CI reproduction, source integration, and any later
application or fixture change supported by new evidence.
