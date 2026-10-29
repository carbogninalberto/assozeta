"""Source-authored collaborator-delete admission over the real Django route.

This matrix exercises transport and identity admission only. Authenticated
DELETE cases target a nonexistent UUID so they cannot perform a business
deletion. Expected statuses, bodies and headers come from the corrected view,
the pinned Django/DRF/simplejwt sources and the repository's admission code;
they are not learned from observed responses. Database/cache snapshots guard
every case. The parent owns execution and full HTTP parity classification.
"""
import gzip
import http.client
import json
from urllib.parse import urlsplit


AUTHORED_EXECUTION_STATUS = "unexecuted_by_transport_worker"
FOUND = ["Collaborator not found"]
BAD_UUID = ["badly formed hexadecimal UUID string"]
ROLE = {"detail": "User is not a sport association"}
CREDENTIALS = {"detail": "Authentication credentials were not provided."}
NOT_ACCEPTABLE = {"detail": "Could not satisfy the request Accept header."}
NOT_FOUND = {"detail": "Not found."}
BAD_REQUEST = (
    "\n<!doctype html>\n<html lang=\"en\">\n<head>\n"
    "  <title>Bad Request (400)</title>\n</head>\n<body>\n"
    "  <h1>Bad Request (400)</h1><p></p>\n</body>\n</html>\n"
)
OPTIONS = {
    "name": "Collaborators Delete",
    "description": "",
    "renders": ["application/json"],
    "parses": [
        "application/json",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    ],
}


def uid(number):
    assert 760000 <= number <= 769999, number
    return f"a2000000-0000-4000-8000-{number:012d}"


def _method_detail(method):
    return {"detail": f'Method "{method}" not allowed.'}


def _request(base, case):
    parsed = urlsplit(base)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=30)
    headers = list(case.get("headers", []))
    present = {name.lower() for name, _ in headers}
    if "accept-language" not in present:
        headers.append(("Accept-Language", "en"))
    if "x-correlation-id" not in present:
        headers.append(("X-Correlation-ID", case["name"]))
    if case.get("token") is not None:
        headers.append(("Authorization", f'Bearer {case["token"]}'))

    body = case.get("body")
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        if "content-type" not in {name.lower() for name, _ in headers}:
            headers.append(("Content-Type", "application/json"))
    elif isinstance(body, str):
        body = body.encode()

    try:
        connection.putrequest(
            case.get("method", "DELETE"), case["path"],
            skip_host=False, skip_accept_encoding=True,
        )
        for name, value in headers:
            connection.putheader(name, value)
        if body is not None and "content-length" not in {
            name.lower() for name, _ in headers
        }:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        payload = response.read()
        response_headers = {
            name.lower(): response.headers.get_all(name) or []
            for name, _ in response.getheaders()
        }
        return response.status, payload, response_headers
    finally:
        connection.close()


def _mismatch(case, kind, expected, actual):
    raise AssertionError(
        f'collaborator-delete-admission {case["name"]}: {kind} mismatch; '
        f"expected={expected!r} actual={actual!r}"
    )


def _header_one(case, headers, name):
    values = headers.get(name.lower(), [])
    if len(values) != 1:
        _mismatch(case, f"{name} header cardinality", 1, values)
    return values[0]


def _assert_response(case, status, payload, headers):
    if status != case["status"]:
        _mismatch(case, "status", case["status"], status)

    encoding = headers.get("content-encoding", [])
    if case.get("gzip", False):
        if encoding != ["gzip"]:
            _mismatch(case, "content encoding", ["gzip"], encoding)
        payload = gzip.decompress(payload)
    elif encoding:
        _mismatch(case, "content encoding", [], encoding)

    if case.get("empty_body", False):
        actual_body = payload.decode("utf-8")
    elif isinstance(case["response"], str):
        actual_body = payload.decode("utf-8")
    else:
        try:
            actual_body = json.loads(payload)
        except (UnicodeDecodeError, ValueError) as error:
            _mismatch(case, "JSON response", case["response"], (error, payload[:500]))
    if actual_body != case["response"]:
        _mismatch(case, "response", case["response"], actual_body)

    expected_language = case.get("language", "en")
    language = headers.get("content-language", [])
    if language != [expected_language]:
        _mismatch(case, "Content-Language", [expected_language], language)

    allow = headers.get("allow", [])
    if case.get("allow") is None:
        if allow:
            _mismatch(case, "Allow", None, allow)
    else:
        expected_allow = case.get("allow", "DELETE, OPTIONS")
        actual_methods = {
            value.strip()
            for value in ",".join(allow).split(",")
            if value.strip()
        }
        expected_methods = {
            value.strip()
            for value in expected_allow.split(",")
            if value.strip()
        }
        if actual_methods != expected_methods:
            _mismatch(case, "Allow", expected_methods, actual_methods)

    expected_type = case.get("content_type", "application/json")
    actual_type = _header_one(case, headers, "Content-Type").split(";", 1)[0]
    if actual_type != expected_type:
        _mismatch(case, "Content-Type", expected_type, actual_type)

    expected_auth = 'Bearer realm="api"' if status == 401 else None
    auth = headers.get("www-authenticate")
    actual_auth = auth[0] if auth and len(auth) == 1 else auth
    if actual_auth != expected_auth:
        _mismatch(case, "WWW-Authenticate", expected_auth, actual_auth)
    if headers.get("set-cookie"):
        _mismatch(case, "Set-Cookie", None, headers["set-cookie"])


def run(
    *,
    base,
    owner,
    foreign,
    association,
    person,
    seed,
    snapshot,
    cache_snapshot,
    models,
    created,
    report,
):
    from application.impersonation import session_key
    from application.models import Associate, User
    from django.core.cache import cache
    from rest_framework_simplejwt.tokens import AccessToken

    # Reserved range for this helper. Target 769999 intentionally does not exist.
    created_at_start = len(created)
    athlete = person(760001, User.ATHLETE)
    full = person(
        760002, User.COLLABORATOR, connected_user=owner,
        collaborator_role=User.FULL, collaborator_permissions=None,
    )
    limited = person(
        760003, User.COLLABORATOR, connected_user=owner,
        collaborator_role=User.ONLY_ACCOUNTING, collaborator_permissions=[],
    )
    granted = person(
        760004, User.COLLABORATOR, connected_user=owner,
        collaborator_role=User.ONLY_ACCOUNTING,
        collaborator_permissions=["other.users.collaborators.delete"],
    )
    seed(
        Associate, 760005, user=athlete, sport_association=association,
        first_name="Admission", last_name="Athlete",
    )
    assert owner.pk != foreign.pk

    def token(account):
        value = AccessToken()
        value["user_id"] = str(account.pk)
        return str(value)

    tokens = {
        "owner": token(owner),
        "athlete": token(athlete),
        "full": token(full),
        "limited": token(limited),
        "granted": token(granted),
        "invalid": "not-a-valid-token",
    }
    missing = uid(769999)
    sessions = {
        "full": uid(762001),
        "athlete": uid(762002),
    }
    session_targets = {
        sessions["full"]: full,
        sessions["athlete"]: athlete,
    }
    cases = []

    def add(
        name, status, response, *, actor="owner", method="DELETE",
        suffix="", headers=None, body=None, language="en",
        allow="DELETE, OPTIONS", content_type="application/json",
        gzip_expected=False, empty_body=False,
    ):
        token_value = tokens.get(actor, actor) if actor is not None else None
        cases.append({
            "name": "collaborator-delete-admission-" + name,
            "path": f"/collaborators/{missing}/delete" + suffix,
            "method": method,
            "token": token_value,
            "headers": list(headers or []),
            "body": body,
            "status": status,
            "response": response,
            "language": language,
            "allow": allow,
            "content_type": content_type,
            "gzip": gzip_expected,
            "empty_body": empty_body,
        })

    # Actor roles and the corrected view's role-before-UUID order.
    add("owner-missing-target", 400, FOUND)
    add("athlete-role-before-uuid", 403, ROLE, actor="athlete")
    add("full-collaborator-missing-target", 400, FOUND, actor="full")
    add("granted-collaborator-missing-target", 400, FOUND, actor="granted")
    add(
        "limited-collaborator-missing-permission", 403,
        {"detail": "Missing permission: other.users.collaborators.delete"},
        actor="limited",
    )
    add(
        "owner-malformed-uid", 400, BAD_UUID,
        suffix="", headers=[], method="DELETE",
    )
    cases[-1]["path"] = "/collaborators/bad/delete"
    add("athlete-malformed-uid-role-wins", 403, ROLE, actor="athlete")
    cases[-1]["path"] = "/collaborators/bad/delete"
    add("full-collaborator-malformed-uid", 400, BAD_UUID, actor="full")
    cases[-1]["path"] = "/collaborators/bad/delete"
    add("granted-collaborator-malformed-uid", 400, BAD_UUID, actor="granted")
    cases[-1]["path"] = "/collaborators/bad/delete"
    add(
        "limited-malformed-uid-permission-wins", 403,
        {"detail": "Missing permission: other.users.collaborators.delete"},
        actor="limited",
    )
    cases[-1]["path"] = "/collaborators/bad/delete"

    # OPTIONS authenticates but does not run DELETE's role/service gate.
    add("owner-options", 200, OPTIONS, method="OPTIONS")
    add("athlete-options", 200, OPTIONS, actor="athlete", method="OPTIONS")
    add("full-collaborator-options", 200, OPTIONS, actor="full", method="OPTIONS")
    add(
        "limited-options-permission-denied", 403,
        {"detail": "Missing permission: other.users.collaborators.delete"},
        actor="limited", method="OPTIONS",
    )
    add("anonymous-options", 401, CREDENTIALS, actor=None, method="OPTIONS")

    # Unsupported methods are checked after authentication, before the view body.
    add("owner-get", 405, _method_detail("GET"), method="GET")
    add("athlete-get", 405, _method_detail("GET"), actor="athlete", method="GET")
    add("full-collaborator-get", 405, _method_detail("GET"), actor="full", method="GET")
    add(
        "owner-head", 405, "", method="HEAD", empty_body=True,
    )
    add(
        "owner-post-body-ignored-unscoped", 405, _method_detail("POST"),
        method="POST", body={"ignored": True},
    )

    # QueryDict parsing: format is last-value-wins, semicolon is data, and the
    # 1000-field limit runs during DRF content negotiation before every gate.
    add("format-empty", 400, FOUND, suffix="?format=")
    add("format-json", 400, FOUND, suffix="?format=json")
    add("format-invalid", 404, NOT_FOUND, suffix="?format=xml")
    add("format-repeat-last-json", 400, FOUND, suffix="?format=xml&format=json")
    add("format-repeat-last-invalid", 404, NOT_FOUND, suffix="?format=json&format=xml")
    add("format-semicolon-is-data", 404, NOT_FOUND, suffix="?format=xml;format=json")
    add(
        "format-before-accept-auth", 404, NOT_FOUND, actor="invalid",
        suffix="?format=xml", headers=[("Accept", "text/html")],
    )
    query1000 = "&".join(f"ignored{i}=0" for i in range(1000))
    add("query-1000-unique", 400, FOUND, suffix="?" + query1000)
    add("query-1000-duplicate", 400, FOUND, suffix="?" + "&".join(["ignored=0"] * 1000))
    add("query-1000-empty", 400, FOUND, suffix="?" + "&" * 999)
    add(
        "query-1001-before-format", 400, BAD_REQUEST,
        actor="invalid", suffix="?format=xml&" + "&".join(
            f"ignored{i}=0" for i in range(1000)
        ),
        headers=[("Accept", "text/html")], allow=None,
        content_type="text/html",
    )
    add(
        "query-1001-before-method", 400, BAD_REQUEST,
        method="OPTIONS", suffix="?" + "&".join(["ignored=0"] * 1001),
        allow=None, content_type="text/html",
    )
    add(
        "query-empty-segments-over-limit", 400, BAD_REQUEST,
        suffix="?" + "&" * 1000, allow=None, content_type="text/html",
    )

    # Content negotiation and locale selection, including repeated source headers.
    add(
        "accept-json-in-list", 400, FOUND,
        headers=[("Accept", "text/html, application/json")],
    )
    add(
        "accept-repeated-header-join", 400, FOUND,
        headers=[("Accept", "text/html"), ("Accept", "application/json")],
    )
    add(
        "accept-case-insensitive", 400, FOUND,
        headers=[("Accept", "Application/JSON")],
    )
    add(
        "accept-media-type-whitespace", 400, FOUND,
        headers=[("Accept", "application/json ; charset=utf-8")],
    )
    add(
        "accept-before-auth", 406, NOT_ACCEPTABLE, actor="invalid",
        headers=[("Accept", "text/html")],
    )
    add(
        "locale-accept-french", 406,
        {"detail": "L'en-tête « Accept » n'a pas pu être satisfaite."},
        headers=[("Accept", "text/html"), ("Accept-Language", "fr")],
        language="fr",
    )
    add(
        "locale-method-french", 405,
        {"detail": "Méthode «\u00a0POST\u00a0» non autorisée."},
        method="POST", headers=[("Accept-Language", "fr")], language="fr",
    )
    add(
        "locale-credentials-french", 401,
        {"detail": "Informations d'authentification non fournies."},
        actor=None, headers=[("Accept-Language", "fr")], language="fr",
    )
    add(
        "locale-cookie-overrides-header", 403, ROLE,
        actor="athlete",
        headers=[
            ("Accept-Language", "it"),
            ("Cookie", "django_language=fr"),
        ],
        language="fr",
    )
    add(
        "locale-repeated-header-join", 403, ROLE, actor="athlete",
        headers=[("Accept-Language", "xx"), ("Accept-Language", "fr")],
        language="fr",
    )

    # Unscoped DELETE bodies are ignored because the corrected view never reads
    # request.data. A valid impersonation context parses action-method bodies.
    add(
        "unscoped-malformed-json-ignored", 400, FOUND,
        body="{", headers=[("Content-Type", "application/json")],
    )
    full_session = sessions["full"]
    add(
        "scoped-malformed-json", 400,
        {"detail": "JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"},
        body="{",
        headers=[
            ("Content-Type", "application/json"),
            ("X-Impersonation-Id", full_session),
        ],
    )
    long_media = "application/x-" + "a" * 300
    add(
        "scoped-unsupported-media-gzip", 415,
        {"detail": f'Unsupported media type "{long_media}" in request.'},
        method="POST", body="ignored",
        headers=[
            ("Content-Type", long_media),
            ("X-Impersonation-Id", full_session),
            ("Accept-Encoding", "gzip"),
        ],
        gzip_expected=True,
    )
    add(
        "scoped-options-ignores-body", 200, OPTIONS, method="OPTIONS",
        body="{",
        headers=[
            ("Content-Type", "application/json"),
            ("X-Impersonation-Id", full_session),
        ],
    )
    add(
        "scoped-get-ignores-body", 405, _method_detail("GET"), method="GET",
        body="{",
        headers=[
            ("Content-Type", "application/json"),
            ("X-Impersonation-Id", full_session),
        ],
    )
    add(
        "scoped-athlete-target-denied-before-body", 403,
        {"detail": "Operazione non disponibile nel contesto dell’associazione."},
        body="{",
        headers=[
            ("Content-Type", "application/json"),
            ("X-Impersonation-Id", sessions["athlete"]),
            ("User-Id", str(athlete.pk)),
        ],
    )

    # Forged or mismatched scope headers deny before business lookup and writes.
    add(
        "forged-user-id-without-session", 403,
        {
            "detail": "Sessione di impersonificazione non valida. Torna al tuo account.",
            "impersonation_invalid": "True",
        },
        headers=[("User-Id", str(owner.pk))],
    )
    add(
        "forged-mismatched-target", 403,
        {
            "detail": "Sessione di impersonificazione scaduta o revocata. Torna al tuo account.",
            "impersonation_invalid": "True",
        },
        headers=[
            ("X-Impersonation-Id", full_session),
            ("User-Id", str(athlete.pk)),
        ],
    )
    add(
        "anonymous-forged-scope", 403,
        {"detail": "Accesso non consentito.", "impersonation_invalid": "True"},
        actor=None,
        headers=[
            ("X-Impersonation-Id", full_session),
            ("User-Id", str(full.pk)),
        ],
    )

    admission = {
        "executed": False,
        "execution_status": AUTHORED_EXECUTION_STATUS,
        "scope": "transport and identity admission; no successful business deletion",
        "complete_parity": False,
        "managed_models": len(models),
        "expected_cases": len(cases),
        "cases": [],
    }
    report["collaborator_delete_admission"] = admission
    try:
        for identifier, target in session_targets.items():
            cache.set(
                session_key(identifier),
                {
                    "actor_id": str(owner.pk),
                    "target_id": str(target.pk),
                    "association_id": str(association.pk),
                },
                timeout=3600,
            )
        for case in cases:
            before, before_cache = snapshot(), cache_snapshot()
            status, payload, headers = _request(base, case)
            try:
                _assert_response(case, status, payload, headers)
                if snapshot() != before:
                    _mismatch(case, "managed model state", "unchanged", "changed")
                if cache_snapshot() != before_cache:
                    _mismatch(case, "cache state", "unchanged", "changed")
            except BaseException as error:
                admission["cases"].append({
                    "name": case["name"],
                    "success": False,
                    "expected_status": case["status"],
                    "actual_status": status,
                    "failure": str(error),
                })
                raise
            admission["cases"].append({
                "name": case["name"],
                "success": True,
                "expected_status": case["status"],
                "actual_status": status,
                "managed_state_unchanged": True,
                "cache_unchanged": True,
            })
        admission["executed"] = True
        admission["execution_status"] = "passed_in_django_oracle"
        admission["created_fixture_rows"] = len(created) - created_at_start
        print(
            f"collaborator delete admission: {len(cases)} Django HTTP cases passed",
            flush=True,
        )
    finally:
        for identifier in sessions.values():
            cache.delete(session_key(identifier))
    return report
