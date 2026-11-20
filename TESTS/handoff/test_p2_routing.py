"""Phase 2 (routing): every public and internal entry point reaches the active runtime only.

Boundary: the installation's own Caddyfile in the pinned Caddy image
(TESTS/handoff/p2_harness.evidence_proxy). Only the running application
services resolve, under their service names and their Compose network
aliases; the other runtime is absent, so a route to it fails at the proxy.
Each upstream is an evidence marker: the serving runtime is identified by
`X-Fixture-Backend`/`X-Fixture-Runtime` and by what that container itself
received (URI after rewrites, method, Host, forwarded headers, cookie,
authorization, Stripe/Meta signatures, body length), never by comparing
two identical bodies. Needs a Docker daemon and caddy:<pinned> locally.

The running set per mode is what Compose configures for that runtime
(`--profile django|go`, see test_p2_process_sets); the historical proxy is the
baseline distribution (git archive 2284ed46) with its own legacy set.

Contract (bus topic p2, t-p2):
  - In each mode, the unversioned, /api/v1 and /api/v2 aliases, the exact
    version roots, readiness (/api/readyz, /api/v1/readyz, /api/v2/readyz),
    the four socket prefixes (/ws/, /api/ws/, /api/v1/ws/, /api/v2/ws/), the
    Bakney protocol (root and prefixed paths, pairing admin), Stripe and
    WhatsApp webhooks, Google OAuth callback, signed downloads and session
    requests are answered by the active API (api or api-go).
  - Whatever reaches the upstream and the client (rewritten path and query,
    method, Host, X-Forwarded-*, cookies and Set-Cookie, Authorization,
    callback signatures, body length, Cache-Control, Referrer-Policy,
    X-Content-Type-Options, Retry-After) is identical to the historical
    proxy for the same request. Static PWA files (/, /sw.js, /manifest.json,
    /offline.html, logo, /static/*) stay on Caddy with the same caching.
  - Maintenance: /instance-update-status is served by the controller socket,
    readiness still reaches the active API, everything else is 503.
  - ASSOZETA_BAKNEY_PROTOCOL_BACKEND / _UPSTREAM no longer pick a runtime.

    python3 -B -m TESTS.handoff.run_expected_red TESTS.handoff.test_p2_routing
"""
import contextlib
from pathlib import Path
import tempfile
import unittest

from TESTS.handoff.p2_harness import (ACTIVE_API, SHARED, application, configured_installation, evidence_proxy,
                                      mode_services, render)
from TESTS.handoff.topology import BASELINE_REVISION

SIGNED = '?expires=1893456000&signature=fixture-signature'
STRIPE = ('Stripe-Signature: t=1700000000,v1=fixture-stripe-signature', 'Content-Type: application/json')
META = ('X-Hub-Signature-256: sha256=fixture-meta-signature', 'Content-Type: application/json')
SESSION = ('Cookie: sessionid=fixture-browser-session; csrftoken=fixture-csrf', 'Authorization: Bearer fixture-token',
           'X-Forwarded-For: 203.0.113.9', 'X-Forwarded-Proto: https')
# (label, path, method, headers, body)
API_REQUESTS = (
    ('cold client status', '/api/instance/status', 'GET', (), None),
    ('v1 status', '/api/v1/instance/status', 'GET', (), None),
    ('v2 status', '/api/v2/instance/status?x=1&x=2', 'GET', (), None),
    ('v1 exact root', '/api/v1?probe=1', 'POST', ('Content-Type: application/json',), '{"root": 1}'),
    ('v2 exact root', '/api/v2', 'GET', (), None),
    ('readiness', '/api/readyz', 'GET', (), None),
    ('v1 readiness', '/api/v1/readyz', 'GET', (), None),
    ('v2 readiness', '/api/v2/readyz', 'GET', (), None),
    ('root socket', '/ws/notifications/?token=fixture', 'GET', (), None),
    ('api socket', '/api/ws/notifications/?token=fixture', 'GET', (), None),
    ('v1 socket', '/api/v1/ws/updates/', 'GET', (), None),
    ('v2 socket', '/api/v2/ws/notifications/?token=fixture', 'GET', (), None),
    ('bakney root protocol', '/bakney/v1/pairing', 'POST', ('Content-Type: application/json',), '{"code": "x"}'),
    ('bakney under api', '/api/bakney/v1/pairing', 'GET', (), None),
    ('bakney under v1', '/api/v1/bakney/v1/pairing', 'GET', (), None),
    ('bakney under v2', '/api/v2/bakney/v1/pairing', 'GET', (), None),
    ('bakney pairing admin', '/api/instance/admin/bakney-pairing', 'GET', SESSION, None),
    ('bakney pairing admin v2', '/api/v2/instance/admin/bakney-pairing', 'GET', SESSION, None),
    ('stripe webhook', '/api/stripe/webhook', 'POST', STRIPE, '{"id": "evt_fixture"}'),
    ('whatsapp webhook', '/api/communications/whatsapp/webhook', 'POST', META, '{"entry": []}'),
    ('whatsapp verification', '/api/communications/whatsapp/webhook?hub.mode=subscribe&hub.challenge=42'
                              '&hub.verify_token=fixture', 'GET', (), None),
    ('google oauth callback', '/api/google/oauth2callback?code=fixture-code&state=fixture-state', 'GET', SESSION, None),
    ('signed download', '/api/document/download/7' + SIGNED, 'GET', (), None),
    ('signed download v2', '/api/v2/document/download/7' + SIGNED, 'GET', (), None),
    ('session request', '/api/v1/profile/info', 'GET', SESSION, None),
    ('email link target', '/api/subscription/confirm/fixture-token', 'GET', (), None),
)
STATIC_REQUESTS = (
    ('pwa shell', '/', 'GET', (), None),
    ('service worker', '/sw.js', 'GET', (), None),
    ('offline page', '/offline.html', 'GET', (), None),
    ('root manifest', '/manifest.json', 'GET', (), None),
    ('logo', '/oem/assozeta/brand/logo.svg', 'GET', (), None),
    ('ui bundle', '/static/app.js', 'GET', (), None),
    ('spa route', '/profile/settings', 'GET', (), None),
)
READINESS = ('/api/readyz', '/api/v1/readyz', '/api/v2/readyz')


def legacy_running(installation):
    return SHARED | application(render(installation)['services'])


class RoutingCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = contextlib.ExitStack()
        cls.directory = Path(cls.stack.enter_context(tempfile.TemporaryDirectory(prefix='assozeta-p2-routing-')))
        cls.log = cls.stack.enter_context((cls.directory / 'proxy.log').open('w'))

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    @classmethod
    def installation(cls, name, revision=None, **env):
        (cls.directory / name).mkdir()
        installation = configured_installation(cls.directory / name, revision=revision)
        if env:
            installation.set_env(**env)
        return installation

    @classmethod
    def serve(cls, installation, running):
        return cls.stack.enter_context(evidence_proxy(installation, running, cls.log))

    @staticmethod
    def measure(request, requests):
        return {label: request(path, method, headers, body) for label, path, method, headers, body in requests}


class ActiveRuntimeRouting(RoutingCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            historical = cls.installation('historical', revision=BASELINE_REVISION)
            current = cls.installation('current')
            cls.proxies = {'historical': cls.serve(historical, legacy_running(historical))}
            for mode in ('django', 'go'):
                cls.proxies[mode] = cls.serve(current, SHARED | mode_services(current, mode))
            cls.responses = {name: cls.measure(request, API_REQUESTS + STATIC_REQUESTS)
                             for name, request in cls.proxies.items()}
        except BaseException:
            cls.stack.close()
            raise

    def test_every_api_entry_point_reaches_the_active_runtime_with_the_other_absent(self):
        for mode in ('django', 'go'):
            with self.subTest(mode=mode):
                wrong = {label: repr(self.responses[mode][label]) for label, *_ in API_REQUESTS
                         if (self.responses[mode][label].status, self.responses[mode][label].backend,
                             self.responses[mode][label].runtime) != (200, ACTIVE_API[mode], mode)}
                self.assertEqual(wrong, {}, f'entry points not served by the active API ({ACTIVE_API[mode]}) '
                                            f'in runtime {mode}')

    def test_static_pwa_files_stay_on_the_proxy_in_both_modes(self):
        for mode in ('django', 'go'):
            with self.subTest(mode=mode):
                wrong = {label: repr(self.responses[mode][label]) for label, *_ in STATIC_REQUESTS
                         if (self.responses[mode][label].status, self.responses[mode][label].backend) != (200, None)}
                self.assertEqual(wrong, {})

    def test_request_and_response_evidence_matches_the_historical_proxy(self):
        historical = self.responses['historical']
        for mode in ('django', 'go'):
            with self.subTest(mode=mode):
                different = {label: {'historical': historical[label].comparable(),
                                     mode: self.responses[mode][label].comparable()}
                             for label, *_ in API_REQUESTS + STATIC_REQUESTS
                             if historical[label].comparable() != self.responses[mode][label].comparable()}
                self.assertEqual(different, {}, f'proxy behaviour changed in runtime {mode}')

    def test_maintenance_serves_status_from_the_controller_and_readiness_from_the_active_api(self):
        for mode in ('django', 'go'):
            with self.subTest(mode=mode):
                request = self.proxies[mode]
                flag = request.operations / 'maintenance.flag'
                flag.write_text('')
                try:
                    status = request('/instance-update-status')
                    ready = {path: request(path) for path in READINESS}
                    page, api = request('/'), request('/api/instance/status')
                finally:
                    flag.unlink()
                self.assertEqual((status.status, status.backend), (200, 'controller'), 'update status in maintenance')
                self.assertEqual((page.status, page.backend, api.status, api.backend), (503, None, 503, None),
                                 'the application is not in maintenance')
                wrong = {path: repr(response) for path, response in ready.items()
                         if (response.status, response.backend, response.seen()['uri']) != (200, ACTIVE_API[mode],
                                                                                           '/readyz')}
                self.assertEqual(wrong, {}, f'readiness during maintenance not served by the active API '
                                            f'({ACTIVE_API[mode]}) in runtime {mode}')


class LegacyProtocolSetting(RoutingCase):
    PATHS = ('/bakney/v1/pairing', '/api/bakney/v1/pairing', '/api/v1/bakney/v1/pairing', '/api/v2/bakney/v1/pairing')

    def test_bakney_protocol_follows_the_active_runtime_whatever_its_legacy_setting(self):
        for mode, backend, upstream in (('django', 'go', 'api-go:8000'), ('go', 'django', 'api:8000')):
            with self.subTest(mode=mode, setting=backend):
                current = self.installation(f'current-{mode}', ASSOZETA_BAKNEY_PROTOCOL_BACKEND=backend,
                                            ASSOZETA_BAKNEY_PROTOCOL_UPSTREAM=upstream)
                request = self.serve(current, SHARED | mode_services(current, mode))
                wrong = {path: repr(response) for path in self.PATHS
                         for response in [request(path)] if (response.status, response.backend) != (200, ACTIVE_API[mode])}
                self.assertEqual(wrong, {}, f'Bakney protocol not served by the active API ({ACTIVE_API[mode]}) in '
                                            f'runtime {mode} with ASSOZETA_BAKNEY_PROTOCOL_BACKEND={backend}')


if __name__ == '__main__':
    unittest.main()
