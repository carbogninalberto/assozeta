"""Independently authored subscription/memberships list, add, update and delete HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_memberships_http.json'
# The viewset's own delete() answers DELETE on every mapped route; the GET route also allows HEAD.
ALLOWED = {'list': {'GET', 'HEAD', 'DELETE', 'OPTIONS'}, 'add': {'POST', 'DELETE', 'OPTIONS'},
           'update': {'PATCH', 'DELETE', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def assert_case(case, base_url, *, token, sessions):
    # Memberships are unordered; each carries at most one document.
    return family.assert_case(case, base_url, token=token, sessions=sessions,
                              allowed=lambda case: ALLOWED[case['path'].split('?')[0].split('/')[-1]],
                              new=('subscription_membership_id',), now=('creation_date',),
                              unordered=((('data',), 'subscription_membership_id'),))
