# Profile template — subscription-template save with HTTP parity

`PATCH profile/update/subscription/template` (`profile_update_subscription_template`) is implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/profile_template.go`). Django remains the execution owner.

## What the view does

- `@api_view(['PATCH'])`, `IsAuthenticated`, registry key `other.settings.update`. It is not an athlete view, so an owner scoped to an athlete gets `403 Operazione non disponibile nel contesto dell’associazione.`. Collaborators act as the owner.
- `request.data['sport_association']` becomes the serializer's `initial_data`; nothing is validated. A missing key, a list or string body, a form body, or a value that is neither a dict nor null is the 500 page. With null the view only builds the response.
- With a dict, the view loads `SportAssociation.objects` (active, impersonation-scoped) `.get(user=request.user)`. An athlete or an owner without an association gets the 500 page. It then reads 23 keys; any missing key is the 500 page. In order:
  - `additional_sections`: `len()` and iteration. A list keeps the dict sections that have both `name` and `text`, both non-empty. `text` goes through `BeautifulSoup(text, features="html.parser").prettify()`, `name` through `str.upper()`, and extra keys are kept. An empty list, dict or string stores `[]`; any other type raises.
  - `regulation`, `demand`: prettified when not None.
  - The six `show_*` flags: assigned raw when not None.
  - `logo`: `compress_base64(logo, max_size=(590, 130))`; None stores None.
  - `subscription_fee` / `membership_fee`: the raw value when `float(value) >= 0`. A `membership_fee` of `''` stores None.
  - `multiple_*_fee`: assigned raw when not None.
  - Fee plans: each plan's fee `''` counts as 0. A plan is kept with `float(fee)` when its name and fee are not None and the fee is `>= 0`; the name test comes first, so a nameless plan's fee is never parsed. An empty plan value stores `[]` and forces the matching `multiple_*_fee` to False.
  - `enable_quotes_management`: assigned raw when not None.
  - `enabled_for`, `additional_fields`, `stripe_available_methods`, `invoice_template`, `subscription_template`, `extra_text_invoices`: always assigned raw.
- One full `save()`. auditlog's `pre_save` receiver writes the entry before the UPDATE converts values. It compares `str()` of the in-memory raw value with the stored one, or `json.dumps(sort_keys=True)` for JSON fields. `'t'` for a flag records `['True', 't']`, and `20` for a fee stored as `20.00` records `['20.00', '20']`. The index row resolves `self`.
- Conversion and database errors inside `save()` are the 500 page and keep the audit entry, because the request is not atomic. These include: `BooleanField.to_python` rejecting a value (`'maybe'`, `2`), a non-finite decimal (`'inf'`), numeric(9, 2) overflow, and a plan fee `float('inf')` that `json.dumps` writes as `Infinity`, which jsonb rejects. Go writes the audit entry, runs the UPDATE in a savepoint, and commits the audit entry when the UPDATE fails.
- Decimal conversion matches `DecimalField.to_python`. Floats go through `Context(prec=9).create_decimal_from_float`, which rounds half to even, then PostgreSQL rounds numeric(9, 2) half away from zero. So `1234567.125` as a JSON number stores `1234567.12`, and as a string it stores `1234567.13`.
- The response has three parts:
  - `role`: `ROLE_CHOICES[user.role - 1]`.
  - `user_data`: `UserAuthSerializer(request.user)` plus `preview_and_custom_features` (for every role) and the avatar through `compress_base64` at 200x200.
  - For a role-1 owner, the re-read `SportAssociationSerializer` data with the logo through `compress_base64` at 590x130.

## Shared pieces

- `BE_GO/internal/bs4prettify` ports `BeautifulSoup(markup, features="html.parser").prettify()` of beautifulsoup4 4.13.3 on CPython 3.13.16 in full:
  - `htmlparser.go` is the html.parser tokenizer of CPython 3.13.16 (tags and attributes in every quoting shape, comments, doctypes, CDATA and bogus declarations, processing instructions, raw-text and rcdata elements, character and entity references, the recovery of unclosed and misnested markup), driven the way bs4's `_htmlparser.py` builder drives it;
  - `treebuilder.go` builds the bs4 tree (`__init__.py`, `builder/__init__.py`, `element.py`), including the `already_closed_empty_element` state, `<pre>` literal mode, the meta charset rewrite and the comment/declaration join rules;
  - `render.go` is `Tag.decode(indent_level=0, formatter="minimal")`: attribute sorting and quoting, cdata-list attributes re-joined with single spaces, `str.strip()` whitespace.

  Comments, doctypes, script/style and other raw-text elements, unquoted or duplicate attributes, stray `<` or `&`, control characters and lone surrogates all follow bs4. When bs4 itself raises (the integer digit limit for a character reference, or the constructor's surrogate check) `Prettify` returns `ErrPythonRaises`, which the view answers with the 500 page. The one refusal that stays is `ErrUnsupported` when the result would exceed 32 MiB: the indentation grows with the square of the nesting depth, so no streaming can hold it.

  Evidence: `TESTS/adapters/django/bs4_prettify_corpus.py` runs the real bs4 on:
  - editor documents (TipTap output shapes) and the model's default sections, plus their once- and twice-prettified stored forms;
  - 6,000 generated documents per seed;
  - random markup, malformed token soup, nested documents drawing from every tag kind and one- to three-character mutations of generated documents.

  `--write-full` writes `testdata/full.json`: 4,231 cases (74 of them where bs4 raises) with the html.parser callback stream, the bs4 tree and `prettify()`'s output or the exception. `go test ./internal/bs4prettify` checks the trees and the output of every case; the wide run of the script's default mode checked 18,932 cases (6,701 of them editor/generated documents), all matching or raising where bs4 raises. `testdata/editor.json` is the committed golden for the editor shapes.
- `pkg/storage/loginimage` bounds are now width x height (`sizeBound`; `decideSize` takes `min(w_max / w, h_max / h)`). The 200 and 512 square callers are unchanged, and `CompressTemplateLogo` uses 590x130. Evidence: `TESTS/adapters/django/template_logo_images.py` covers 50 vectors with the actual `compress_base64` and Pillow 13:
  - both limiting axes, truncation, enlargement above 1 MiB, unchanged-size passthrough, zero dimension;
  - RGBA, PNG/BMP/WEBP/JPEG/GIF, generation loss, malformed input.

  Go matches every vector, and a 590x590 bound fails 28 of them. The 512 avatar oracle (`profile_images.py`) still passes.
- Section names use `str.upper()` for code points whose uppercase is a single code point. `dto/testdata/python_upper.json` holds CPython's mapping for all 35,087 accepted code points.

## Not ported (refused with the 500 page before any write)

- A prettified result above 32 MiB in `regulation`, `demand` or a section `text`. The indentation grows with the square of the nesting depth, so the output is refused rather than held.
- Section names with characters outside the upper-case subset, for example `ß` or ligatures.
- A logo or avatar the image processor cannot decode (for example TIFF or AVIF). The request logo and its response re-compression are both checked before writing.

## Evidence

`TESTS/fixtures/profile_template_http.json` has 45 cases, generated by `generate_profile_template_http.py`:

- 6 authorization and transport cases;
- 16 exceptions before `save()`;
- 5 null-value responses: owner, athlete, collaborator, administrator session, owner-impersonated collaborator;
- 7 saves: editor save, collaborator re-save of the stored values, empty values, scalar conversions, skipped fees, logo at the bound, administrator-session save with a wide logo;
- 5 failed saves that keep their audit entry.

Expected HTML and images come from bs4 and Pillow following the view's steps. Each case checks the response body and either an unchanged state or the complete values of the 23 assigned fields. It also checks the ordered audit entries with actor, impersonation record, correlation id and index row, and that every other row is unchanged.

RED: Django 45/45, Go 404 (`native-r7`). GREEN: 45/45 on native Django, native Go, Caddy `/api/v1` and `/api/v2` (`versioned-r1`). Mutation control: removing the rule that an empty plan list switches the multiple fee off fails `profile-template-empty-values` on Go. Families re-run after the image-bound change: search-profile, oauth-accounts, sso-pairing and misc-routes, all green natively.

## Ported in t2docs

Section names are upper-cased with the full CPython table (`dto/python_upper_table.go`, generated from `str.upper()` of CPython 3.13.16): ß, ligatures and every other multi-character mapping are no longer refused. A case with `straße ﬁne ǰ ΐ` is added to the save sequence.

## Ported in t4l2

- `float()` is the shared `dto.PythonFloatOf`: white space, underscores, any Unicode decimal digit, integers of any length
  (`OverflowError` past the float range is the 500 page) and numbers or strings past the float range (inf). The fee column reads
  the same spellings as `Decimal()` does.
- A logo that is not a string is stored as its `str()`; containers and numbers in text and flag fields are written as their Python
  repr (`dto.PythonStr`). A flag holding a container fails its conversion after the audit entry.
- A NUL or lone surrogate in a text or flag string cannot enter the audit entry, the first statement of the save: the 500 page and
  nothing written. Inside a JSON field the audit entry holds the escaped JSON text and the jsonb column then refuses it: the 500
  page after the audit entry.
- Form and multipart bodies always end in the 500 page (the association value is a string or a file), so uploads are no longer
  handed off.

Cases: `float-spellings-and-containers`, `logo-object`, `logo-boolean`, `fee-*-past-float-range`, `flag-container`, `flag-object`,
`json-with-nul`, `json-key-with-lone-surrogate`, `text-container-with-nul`, `fee-double-underscore`, `fee-inner-space`,
`*-with-nul`, `*-with-lone-surrogate`, `multipart-*` (65 cases in all). Mutation control: dropping the non-string logo fails
`float-spellings-and-containers` on Go.

## Ported in t6x3

- `html.parser` recovery and bs4's tree builder and `prettify()` cover every input, not a subset. The committed golden is
  `BE_GO/internal/bs4prettify/testdata/full.json` (4,231 cases with the parser events, the bs4 tree and the output or exception,
  written by `TESTS/adapters/django/bs4_prettify_corpus.py --write-full`); the script's default mode checked 18,932 cases in the
  wide run, all accepted.
- What bs4 raises (a character reference past the integer digit limit, or a lone surrogate in a short document without `<`) is
  `ErrPythonRaises`: the 500 page and nothing written. A U+0000 or lone surrogate the audit entry cannot hold after prettifying
  is the same 500 page and nothing written.
- The bound that stays: an output above 32 MiB is `ErrUnsupported`, before any write.

Cases: `legacy-html-regulation`, `legacy-html-demand`, `legacy-html-sections`, `html-resave`, `html-control-characters`,
`html-charref-too-long`, `html-short-surrogate`, `html-nul-reference`, `html-surrogate-reference` (74 cases in all).

## t6x2

**Ported in t6x2**: a logo with a NUL or a lone surrogate before its first comma, or a NUL in its data, is saved when
the image is re-encoded (the new data URI has neither) and answers the 500 page when it is kept as sent; cases
`logo-header-with-nul-*`, `logo-data-with-nul-*`, `logo-header-with-lone-surrogate-unchanged`,
`logo-undecodable-header-with-nul`. `TESTS/fixtures/profile_template_http.json` has 71 cases.
