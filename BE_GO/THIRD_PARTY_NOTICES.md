# Third-party notices

This notice covers the static transport-message data derived from the pinned
Django and Django REST Framework locale catalogs. The source catalog payloads
are not vendored in this repository.

## Django 5.2.18

License expression from the installed distribution metadata: `BSD-3-Clause`.

Source license:
`django-5.2.18.dist-info/licenses/LICENSE`, SHA-256
`b846415d1b514e9c1dff14a22deb906d794bc546ca6129f950a18cd091e2a669`.

Copyright (c) Django Software Foundation and individual contributors.
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
       this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
       notice, this list of conditions and the following disclaimer in the
       documentation and/or other materials provided with the distribution.

    3. Neither the name of Django nor the names of its contributors may be used
       to endorse or promote products derived from this software without
       specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## Django REST Framework 3.17.2

License expression from the installed distribution metadata: `BSD-3-Clause`.

Source license:
`djangorestframework-3.17.2.dist-info/licenses/LICENSE.md`, SHA-256
`cc12b0b85368972177ebf422089419b9fe3eea5729fecb11105c640f6eeaccd4`.

# License

Copyright © 2011-present, [Encode OSS Ltd](https://www.encode.io/).
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## Catalog provenance

The pinned source root used for authoring was the sibling checkout's
`quality-reports/go-migration/python31316/lib/python3.13/site-packages`.
Extraction read only `django/**/LC_MESSAGES/django.mo` and
`rest_framework/**/LC_MESSAGES/django.mo`; no Go catalog map, application
runtime, or HTTP response was used to generate expectations.

Each catalog manifest digest is computed as SHA-256 over sorted UTF-8 lines of
the form `<site-packages-relative-path> NUL <SHA-256 of file> LF`. The fixture
records 1,130 Django catalog files with manifest SHA-256
`48e3df798c29e4c896fbca20447163eba267a63612127da8c009ce78812b236a`
and 60 DRF catalog files with manifest SHA-256
`b881575fdb1a8865f7fbf5d2c3f2d5a6d9ad33f0a8b40c6723fa7cb8c8ea3b56`.

The six DRF source messages are extracted from compiled gettext catalogs using
Django 5.2.18's `to_locale()` conversion and CPython 3.13.16's standard-library
gettext lookup. CPython's `locale.normalize()` alias table feeds
`gettext._expand_lang()`, and `gettext.translation()` merges every matching
catalog in expansion order. This matters for ten supported aliases in the
pinned catalogs: `ca`, `el`, `en`, `en-au`, `fa`, `gl`, `pt`, `pt-br`, `ru`, and
`tr`. For `ru`, expansion yields `ru_RU.UTF-8`, `ru_RU`, `ru.UTF-8`, and `ru`;
both `ru_RU` and `ru` exist, so the former supplies `permission` and
`not_found` while the latter supplies the other four messages.

English variants remain English when neither expanded catalog translates a
message. For any other absent catalog or message, Django's per-message default
language fallback selects the pinned DRF Italian message. Django catalogs do
not translate these six DRF source messages. The fixture stores all resulting
values as static data, and acceptance never reads these third-party files or
imports Django/DRF.

The following authoring-only script reproduces the extracted message map from
the pinned catalogs and the fixture's language list. It is not part of
acceptance and does not import application or framework runtime code.

```sh
python3 - PACKAGE_ROOT TESTS/fixtures/read-route-locales.json <<'PY'
import gettext
import hashlib
import json
import sys
from pathlib import Path

packages = Path(sys.argv[1]).resolve()
fixture_path = Path(sys.argv[2]).resolve()
languages = json.loads(fixture_path.read_text(encoding="utf-8"))["supported_languages"]
messages = {
    "credentials": "Authentication credentials were not provided.",
    "permission": "You do not have permission to perform this action.",
    "accept": "Could not satisfy the request Accept header.",
    "not_found": "Not found.",
    "method": 'Method "{method}" not allowed.',
    "media": 'Unsupported media type "{media_type}" in request.',
}

def catalog(path):
    with path.open("rb") as stream:
        return gettext.GNUTranslations(stream)

drf = packages / "rest_framework/locale"
italian = catalog(drf / "it/LC_MESSAGES/django.mo")

def to_locale(language):
    """Mirror django.utils.translation.to_locale() for the pinned version."""
    lang, _, country = language.lower().partition("-")
    if not country:
        return language[:3].lower() + language[3:]
    country, _, tail = country.partition("-")
    country = country.title() if len(country) > 2 else country.upper()
    if tail:
        country += "-" + tail
    return lang + "_" + country

def render(language, kind):
    source = messages[kind]
    selected = gettext.translation(
        "django",
        localedir=str(drf),
        languages=[to_locale(language)],
        fallback=True,
    )
    text = selected.gettext(source)
    if text == source and not language.startswith("en"):
        text = italian.gettext(source)
    return text.replace("{method}", "POST").replace("{media_type}", "text/plain")

for domain in ("django", "rest_framework"):
    paths = sorted(path for path in packages.glob(
        f"{domain}/**/LC_MESSAGES/django.mo") if path.is_file())
    manifest = b"".join(
        f"{path.relative_to(packages).as_posix()}\0"
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode()
        for path in paths
    )
    print(domain, len(paths), hashlib.sha256(manifest).hexdigest())
print(json.dumps({
    language: {kind: render(language, kind) for kind in messages}
    for language in languages
}, ensure_ascii=False, indent=2))
PY
```
