"""Independently authored folders/add and folders/<pk>/update HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/folder_writes_http.json'
# The viewset's own delete() answers DELETE on every mapped route.
ALLOWED = {'add': {'POST', 'DELETE', 'OPTIONS'}, 'update': {'PATCH', 'DELETE', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    # Folder ids are fixed by the runner; only a server-time created_at varies.
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=lambda case: ALLOWED[case['path'].split('/')[-1]],
                              now=('created_at',))
