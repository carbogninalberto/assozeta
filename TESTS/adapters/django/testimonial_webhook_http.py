"""Parent-only Senja webhook HTTP runner; imports are inert.

Reuses the testimonial fixture (every association unreviewed) and full-state
effect checks; signs authored payloads with the private environment's
SENJA_WEBHOOK_SECRET, which both backends read.
"""
import os

from TESTS.contracts import test_testimonial_webhook_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django.testimonials_http import assert_effect, fixture_context  # noqa: F401

SOURCES = ('TESTS/fixtures/testimonial_webhook_http.json', 'TESTS/contracts/test_testimonial_webhook_http.py',
           'TESTS/adapters/django/testimonial_webhook_http.py', 'TESTS/adapters/django/testimonials_http.py',
           'BE/application/views/profile_views.py', 'BE/application/utils/api_utils.py',
           'BE/application/models/user_models.py', 'BE/application/audit_resolvers.py')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    secret = os.environ['SENJA_WEBHOOK_SECRET']
    shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, secret=secret)
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote, fixture['sessions'])
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
