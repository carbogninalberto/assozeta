"""Parent-owned live integration for the unchanged saved-message LIST screen.

No CLI, services, browser launch, fixture writes, or cleanup of parent resources.
See quality-reports/message-browser-progress.md for the checkpoint contract.
All imports and verifier controls work without Django/Playwright installed.
"""
import copy
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from TESTS.adapters.django import message_reads as oracle

ROOT = oracle.ROOT
UPSTREAM_PATH = '/communications/messages/list'
LIST_PATH = '/api' + UPSTREAM_PATH
ACTORS = ('owner', 'full_collaborator', 'foreign_owner', 'restricted_collaborator')
READERS = ACTORS[:-1]
CHECKPOINTS = tuple(
    (actor, action) for actor in ACTORS
    for action in (('login', 'read', 'reload') if actor in READERS else ('login', 'denied'))
)
# Exact paths, no wildcards or fallback. These bootstrap routes already have Go
# implementations; the named Django dependencies alone may coexist on v2.
SELECTED_ROUTES = {
    '/api/instance/status': 'GET', '/api/instance/config': 'GET',
    '/api/instance/access': 'GET', '/api/oauth2/login': 'POST',
    '/api/profile/info': 'GET', LIST_PATH: 'GET',
}
COEXISTENCE_ROUTES = {
    '/api/instance/manifest.json': 'GET', '/api/instance/logo.png': 'GET',
    '/api/association/export/active': 'GET', '/api/statistic/dashboard': 'GET',
    '/api/communications/staff-board/list': 'GET',
}
WEBSOCKET_ROUTES = ('/ws/health/', '/ws/notifications/')
# Exact active references in unchanged UI/index.html. These are BLOCKED attempts,
# never permitted requests. No font files or provider subresources are allowlisted.
BLOCKED_EXTERNAL_RESOURCES = (
    {'url': 'https://fonts.googleapis.com/css2?family=Poppins:ital,wght@0,100;0,200;0,300;0,400;0,500;0,600;0,700;0,800;0,900;1,100;1,200;1,300;1,400;1,500;1,600;1,700;1,800;1,900&display=swap',
     'method': 'GET', 'resource_type': 'stylesheet'},
    {'url': 'https://fonts.googleapis.com/css?family=Inter:300,400,500,600,700',
     'method': 'GET', 'resource_type': 'stylesheet'},
    {'url': 'https://js.stripe.com/v3/', 'method': 'GET', 'resource_type': 'script'},
    {'url': 'https://accounts.google.com/gsi/client', 'method': 'GET', 'resource_type': 'script'},
)
EXPECTED_PRECONNECT_HINTS = ('https://fonts.gstatic.com', 'https://fonts.googleapis.com')
TRANSPORT = 'route.fetch(maxRedirects=0,maxRetries=0)->route.fulfill(response)'


def _validate_external_source(root):
    class References(HTMLParser):
        resources, hints = [], []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            url = attrs.get('src') if tag == 'script' else attrs.get('href') if tag == 'link' else None
            if not url or not url.startswith('https://'):
                return
            if tag == 'link' and attrs.get('rel') == 'preconnect':
                self.hints.append(url)
            else:
                self.resources.append({'url': url, 'method': 'GET',
                    'resource_type': 'script' if tag == 'script' else 'stylesheet'})
    parser = References()
    parser.feed((root / 'UI/index.html').read_text())
    assert parser.resources == list(BLOCKED_EXTERNAL_RESOURCES), 'External fixture references changed'
    assert tuple(parser.hints) == EXPECTED_PRECONNECT_HINTS, 'Preconnect fixture hints changed'


WORKFLOW_SOURCES = (
    'src/App.svelte', 'src/routes.js', 'src/layouts/DashboardLayout.svelte',
    'src/routes/login/Login.svelte', 'src/components/Sidebar.svelte',
    'src/components/tables/BKNDatatable.svelte',
    'src/components/tables/datatableColumnLayout.js',
    'src/routes/association/communication/messages/Messages.svelte',
    'src/routes/association/communication/messages/modals/Send.svelte',
    'src/utils/ApiMiddleware.js', 'src/utils/Permissions.js',
    'src/utils/loginSession.js', 'src/store/stores.js',
    'src/store/instanceStore.js', 'src/utils/HealthWebSocket.js',
    'src/utils/NotificationWebSocket.js',
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def required_source_paths():
    return (
        'TESTS/browser/message-reads.mjs',
        'TESTS/adapters/django/message_reads_browser.py',
        'TESTS/test_message_reads_browser.py',
        'TESTS/fixtures/message_reads.json',
        'TESTS/adapters/django/message_reads.py',
        'TESTS/adapters/django/historical_read_cache.py',
        'TESTS/adapters/django/profile_browser.py',
        'UI/endpoints.js', 'UI/Dockerfile', 'UI/package.json', 'UI/index.html',
        'UI/vite.config.js', 'UI/postcss.config.cjs', 'UI/scripts/build-version.js', 'UI/release_notes.txt',
        *(f'UI/{name}' for name in WORKFLOW_SOURCES),
    )


def capture_provenance(artifact, *, root=ROOT):
    """Read an already built private artifact; verify exact embedded UI sources.

    Reuses the existing artifact hashing helper; never extracts/builds an image.
    Parent calls before AND after each version's run and retains these receipts.
    This binds maps to JS, not build provenance. verify_acceptance additionally
    requires an independent parent build invocation/input/artifact observation.
    """
    from TESTS.adapters.django.profile_browser import fingerprints

    artifact, root = Path(artifact), Path(root)
    assert artifact.is_dir() and (artifact / 'index.html').is_file(), 'Built UI is required'
    assert not any(path.is_symlink() for path in artifact.rglob('*')), 'Artifact symlink'
    _validate_external_source(root)
    # Require real relative sourceMappingURL links; a detached .map and an
    # arbitrary JS file cannot jointly establish executable/source provenance.
    links = {}
    def resolve_relative(base, value):
        assert isinstance(value, str) and value, 'Missing source-map link/file'
        url = urlsplit(value)
        assert not (url.scheme or url.netloc or url.query or url.fragment), 'Nonlocal source-map link'
        assert not value.startswith('/') and '\\' not in value, 'Absolute source-map link'
        resolved = (base.parent / unquote(url.path)).resolve()
        assert resolved.is_relative_to(artifact.resolve()) and resolved.is_file(), 'Source-map path escapes/missing'
        return resolved
    for generated in sorted(artifact.rglob('*')):
        if generated.suffix not in {'.js', '.mjs', '.cjs'}:
            continue
        directives = re.findall(r'(?m)^[ \t]*(?://[#@][ \t]*sourceMappingURL=([^\s]+)[ \t]*$|/\*[#@][ \t]*sourceMappingURL=([^\s*]+)[ \t]*\*/[ \t]*$)',
                                generated.read_text())
        # Only valid unambiguous local links can bind workflow sources. Vendor
        # scripts may retain missing third-party map references; those confer no
        # provenance and their executable bytes are still hashed normally.
        if len(directives) == 1:
            try:
                target = resolve_relative(generated, next(value for value in directives[0] if value))
            except AssertionError:
                continue
            links.setdefault(target, []).append(generated.resolve())
    maps = {name: [] for name in WORKFLOW_SOURCES}
    bindings = {}
    for path in sorted(artifact.rglob('*.map')):
        data = json.loads(path.read_text())
        sources, contents = data.get('sources', []), data.get('sourcesContent', [])
        assert len(sources) == len(contents), 'Source map content scope differs'
        applicable = False
        for name, content in zip(sources, contents):
            for required in WORKFLOW_SOURCES:
                if name == required or name.endswith('/' + required):
                    assert isinstance(content, str), 'Embedded workflow source missing'
                    assert content.encode() == (root / 'UI' / required).read_bytes(), \
                        'Built workflow source differs: ' + required
                    applicable = True
                    maps[required].append(str(path.relative_to(artifact)))
        if applicable:
            assert data.get('version') == 3, 'Unsupported source-map format'
            generated = links.get(path.resolve(), [])
            assert generated, 'Orphan workflow source map'
            # Vite entry maps use the output-root path; split chunks use the
            # map-relative basename. Both must identify the exact linked file.
            targets = set()
            for base in (path, artifact / 'index.html'):
                try:
                    targets.add(resolve_relative(base, data.get('file')))
                except AssertionError:
                    pass
            assert len(targets) == 1, 'Source-map file target missing or ambiguous'
            target = targets.pop()
            assert generated == [target], 'Source-map file field differs from generated JS link'
            bindings[str(path.relative_to(artifact))] = [str(target.relative_to(artifact.resolve()))]
    assert all(maps.values()), 'Built UI lacks required workflow source maps'
    paths = set(required_source_paths())
    # Full frontend source and public assets, plus root build configuration.
    paths.update(str(path.relative_to(root)) for folder in ('src', 'public', 'scripts')
                 for path in (root / 'UI' / folder).rglob('*') if path.is_file())
    paths.update(str(path.relative_to(root)) for path in (root / 'UI').iterdir()
                 if path.is_file() and path.suffix in {'.js', '.mjs', '.cjs', '.json', '.ts', '.html', '.txt'})
    return {
        'source_sha256': {name: _sha(root / name) for name in sorted(paths)},
        'artifact_sha256': fingerprints(artifact), 'workflow_source_maps': maps,
        'source_map_bindings': bindings,
    }


def _text(value):
    return ' '.join(html.unescape(value).split())


def visible_row(row):
    """Authored response -> independent screen expectation, never observed data."""
    message, subject = str(row['message'] or '-').upper(), str(row['subject'] or '-').upper()
    date = (datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
            if row['created_at'] else datetime(1970, 1, 1, tzinfo=timezone.utc))
    return {
        'id': row['message_id'],
        'message': _text(message[:25] + '...' if len(message) > 25 else message),
        'message_title': html.unescape(row['message']),
        'type': {'EMAIL': 'Email', 'INSIDE_APP': 'Post'}[row['type']],
        'subject': _text(subject[:15] + '...' if len(subject) > 15 else subject),
        'subject_title': html.unescape(row['subject']),
        'date': date.strftime('%d/%m/%Y, %H:%M:%S'),
    }


def authored_spec():
    fixture = oracle.load_authored_manifest()
    owner = copy.deepcopy(next(case['response'] for case in fixture['cases']
                               if case['name'] == 'message-reads-list-owner'))
    foreign_seed = next(row for row in oracle.MESSAGE_SEED_RECORDS
                        if row['association'] == 'foreign')
    foreign = [{
        'message_id': fixture['ids']['foreign_email'],
        'sport_association': fixture['ids']['foreign_association'],
        'type': foreign_seed['type'], 'message': html.escape(foreign_seed['message']),
        'subject': html.escape(foreign_seed['subject']),
        'created_at': foreign_seed['created_at'].isoformat().replace('+00:00', 'Z'),
    }]
    actors = {}
    for actor in ACTORS:
        rows = foreign if actor == 'foreign_owner' else owner
        forbidden = (owner if actor == 'foreign_owner' else foreign)
        # Unsupported scoped SMS is a separate, nonvacuous contrast for every reader.
        forbidden_ids = [row['message_id'] for row in forbidden] + [fixture['ids']['unsupported_type']]
        asc = sorted(rows, key=lambda row: (row['created_at'] is None, row['created_at'] or ''))
        query = 'Foreign' if actor == 'foreign_owner' else 'Avviso'
        matches = [row['message_id'] for row in rows if query.lower() in row['message'].lower()]
        actors[actor] = {
            'user_id': oracle._uid(700007) if actor == 'foreign_owner' else fixture['ids'][actor],
            'expected_response': rows, 'visible_rows': [visible_row(row) for row in rows],
            'forbidden_ids': forbidden_ids,
            'negative_query': 'Post senza data' if actor == 'foreign_owner' else 'Foreign private body',
            'controls': {
                # UI/public/global.css uppercases the visible header spans;
                # innerText reflects that CSS while DOM text remains title case.
                'headings': ['CONTENUTO', 'TIPO', 'OGGETTO', 'DATA'],
                'search_query': query, 'search_ids': matches, 'search_empty': True,
                'sort_field': 'created_at', 'sort_asc_ids': [row['message_id'] for row in asc],
                'sort_desc_ids': [row['message_id'] for row in reversed(asc)],
                'page_sizes': [10, 20, 10], 'pages': 1,
                'next_disabled': True, 'previous_disabled': True,
                'summary': f'Mostrati 1 - {len(rows)} di {len(rows)}',
                'list_requests_during_controls': 0,
            },
        }
    return {'actors': actors, 'selected_routes': SELECTED_ROUTES,
            'coexistence_routes': COEXISTENCE_ROUTES, 'websocket_routes': list(WEBSOCKET_ROUTES),
            'blocked_external_resources': copy.deepcopy(list(BLOCKED_EXTERNAL_RESOURCES))}


def browser_input(*, version, origin, identities, provenance):
    """Private input; identities maps ACTORS to {username, password, user_id}.

    Parent serializes privately if using a subprocess. No auth storage or bearer
    tokens are accepted. Passwords must already work through the unchanged UI.
    """
    assert version in ('v1', 'v2') and set(identities) == set(ACTORS)
    spec = authored_spec()
    for actor in ACTORS:
        supplied = identities[actor]
        assert set(supplied) == {'username', 'password', 'user_id'}
        assert supplied['user_id'] == spec['actors'][actor]['user_id']
        assert all(isinstance(value, str) and value for value in supplied.values())
        spec['actors'][actor].update(supplied)
    return dict(spec, origin=origin, version=version, artifact_sha256=provenance['artifact_sha256'])


def _validate_provenance(before, after):
    assert before == after, 'Source/frontend artifact changed during workflow'
    assert set(before) == {'source_sha256', 'artifact_sha256', 'workflow_source_maps', 'source_map_bindings'}
    for key in ('source_sha256', 'artifact_sha256'):
        assert before[key] and all(re.fullmatch('[0-9a-f]{64}', value)
                                   for value in before[key].values()), 'Missing SHA256 provenance'
    assert set(required_source_paths()) <= set(before['source_sha256']), 'Missing source provenance'
    assert 'index.html' in before['artifact_sha256']
    assert set(before['workflow_source_maps']) == set(WORKFLOW_SOURCES)
    assert all(before['workflow_source_maps'].values()), 'Missing workflow source-map proof'
    applicable = {name for names in before['workflow_source_maps'].values() for name in names}
    assert set(before['source_map_bindings']) == applicable, 'Missing/orphan source-map bindings'
    for name, generated in before['source_map_bindings'].items():
        assert name in before['artifact_sha256'] and name.endswith('.map')
        assert len(generated) == 1 and generated[0].endswith(('.js', '.mjs', '.cjs'))
        assert generated[0] in before['artifact_sha256'], 'Generated source-map target missing'


def verify_acceptance(receipt, *, state_checkpoints, contrasts, attribution,
                      provenance_before, provenance_after, spec=None, build_observation=None):
    """Verify real receipts using parent-owned RAW state and upstream observations.

    state_checkpoints: one {actor, action, models, cache} per CHECKPOINTS; models
    are oracle.snapshot_models(apps), cache snapshot_historical_read_cache(raw).
    Every login establishes a new baseline AFTER ordinary password login. Every
    read/reload/denied checkpoint compares all 128 models and complete DB1.

    attribution: one {cid, proxy, django_events} per selected list response.
    proxy is an independent real proxy observation, never constructed from the
    browser's X-Fixture-Backend header. It records upstream path/backend/status
    and fallback=False. django_events is the entire correlated ASGI delta.
    Parent must reject any other list traffic in its complete observation window.
    """
    spec = authored_spec() if spec is None else spec
    assert spec == authored_spec(), 'Expectations must remain fixture-authored'
    _validate_provenance(provenance_before, provenance_after)
    # This is an independent parent-owned build observation, not evidence inferred
    # from source maps. Parent retains the raw invocation/input/output record.
    assert build_observation and set(build_observation) == {
        'invocation', 'record_sha256', 'source_sha256', 'artifact_sha256'}, 'Parent build observation required'
    assert isinstance(build_observation['invocation'], list) and build_observation['invocation']
    assert all(isinstance(arg, str) and arg for arg in build_observation['invocation'])
    assert re.fullmatch('[0-9a-f]{64}', build_observation['record_sha256']), 'Missing raw build record digest'
    assert build_observation['source_sha256'] == {
        k: v for k, v in provenance_before['source_sha256'].items() if k.startswith('UI/')}, 'Build inputs differ'
    assert build_observation['artifact_sha256'] == provenance_before['artifact_sha256'], 'Build outputs differ'
    assert receipt['version'] in ('v1', 'v2')
    backend = 'django-v1' if receipt['version'] == 'v1' else 'go-v2'
    assert receipt['success'] is True and not receipt['failures'], 'Incomplete browser workflow'
    assert receipt['contexts_closed'] is True and receipt['browser_owned_by_parent'] is True
    assert receipt['service_workers_blocked'] is True
    assert receipt['business_responses_mocked'] is False
    assert receipt['authentication_state_seeded'] is False
    assert receipt['transport'] == TRANSPORT
    assert receipt['all_response_bodies_complete'] is True
    assert receipt['external_integrations_excluded'] is True and receipt['font_parity_excluded'] is True
    for dependency in receipt['blocked_fixture_dependencies']:
        assert set(dependency) == {'actor', 'action', 'url', 'method', 'resource_type', 'blocked'}
        assert dependency['actor'] in ACTORS and dependency['action'] in {'login', 'read', 'reload', 'denied'}
        assert dependency['blocked'] is True
        assert {k: dependency[k] for k in ('url', 'method', 'resource_type')} in BLOCKED_EXTERNAL_RESOURCES
    assert receipt['artifact_sha256'] == provenance_before['artifact_sha256']
    assert 'index.html' in receipt['static_verified']
    assert any(name.endswith('.js') for name in receipt['static_verified']), 'Built JS was not observed'
    assert set(receipt['static_verified']) <= set(receipt['artifact_sha256'])
    for names in provenance_before['workflow_source_maps'].values():
        assert any(set(provenance_before['source_map_bindings'][name]) <= set(receipt['static_verified'])
                   for name in names), 'Workflow source map not bound to observed executable JS'
    order = lambda rows: tuple((row['actor'], row['action']) for row in rows)
    assert order(receipt['checkpoints']) == CHECKPOINTS, 'Missing/reordered browser checkpoint'
    assert order(state_checkpoints) == CHECKPOINTS, 'Missing/reordered durable state proof'
    expected_checks = tuple(row for row in CHECKPOINTS if row[1] != 'login')
    assert order(receipt['checks']) == expected_checks, 'Missing/reordered browser case'
    logins = []
    for row in receipt['responses']:
        assert row.get('body_complete') is True, 'Incomplete response body'
        path = row['path']
        assert row['actor'] in ACTORS, 'Unexpected response actor'
        if path in SELECTED_ROUTES:
            assert row['method'] == SELECTED_ROUTES[path] and row['status'] == 200
            assert row['backend'] == backend, 'Wrong selected dependency backend'
            if path == '/api/oauth2/login':
                assert row['action'] == 'login'
                logins.append(row['actor'])
        elif path in COEXISTENCE_ROUTES:
            assert row['method'] == COEXISTENCE_ROUTES[path] and row['status'] in (200, 403, 404)
            assert row['backend'] == 'django-coexistence'
        else:
            assert not path.startswith(('/api/', '/instance-')), 'Unexpected API dependency'
            file = 'index.html' if path == '/' else path.lstrip('/')
            assert file in receipt['artifact_sha256'] and file in receipt['static_verified']
            assert row['method'] == 'GET' and row['status'] == 200 and row['backend'] == 'ui-static'
    assert tuple(logins) == ACTORS, 'Missing/extra ordinary password login'
    selected = [row for row in receipt['responses'] if row['path'] == LIST_PATH]
    assert order(selected) == tuple(row for row in expected_checks if row[1] != 'denied')
    assert len(selected) == len(attribution) == 6, 'Unexpected/missing selected request'
    assert receipt['list_requests'] == [
        {key: row[key] for key in ('actor', 'action', 'cid')} for row in selected
    ], 'Extra/aborted or missing actual UI list request'
    assert len({row['cid'] for row in selected}) == 6
    assert [row['cid'] for row in selected] == [row['cid'] for row in attribution]
    for response, proof in zip(selected, attribution):
        assert response['method'] == 'GET' and response['status'] == 200
        assert response['backend'] == backend, 'Wrong selected backend'
        assert re.fullmatch(
            f"message-browser-{receipt['version']}-{response['actor']}-{response['action']}-[1-9][0-9]*",
            response['cid']), 'Case/version correlation differs'
        assert proof['proxy'] == {
            'cid': response['cid'], 'method': 'GET', 'path': UPSTREAM_PATH,
            'status': 200, 'backend': backend, 'fallback': False,
        }, 'Proxy misroute/fallback or missing upstream proof'
        oracle.assert_attribution('django' if backend == 'django-v1' else 'go',
                                  'GET', UPSTREAM_PATH, 200,
                                  proof['django_events'], response['cid'])
    for check in receipt['checks']:
        if check['action'] == 'denied':
            assert check == {'actor': 'restricted_collaborator', 'action': 'denied',
                             'sidebar_link_visible': False, 'redirect_hash': '#/404',
                             'list_requests': 0, 'table_rows': 0}
            continue
        actor = spec['actors'][check['actor']]
        assert check['rows'] == actor['visible_rows'], 'Visible content/foreign scope differs'
        assert check['foreign_ids_absent'] == actor['forbidden_ids'], 'Foreign contrast missing'
        assert check['controls'] == actor['controls'], 'Client controls missing or changed'
        response = next(row for row in selected
                        if (row['actor'], row['action']) == (check['actor'], check['action']))
        assert check['cid'] == response['cid'], 'Displayed case not bound to selected response'
    state_receipts = []
    baseline = None
    for state in state_checkpoints:
        assert len(state['models']) == 128, 'All 128 managed models are required'
        if state['action'] == 'login':
            baseline = state
        assert baseline and state['actor'] == baseline['actor']
        models = oracle.assert_state_unchanged(baseline['models'], state['models'])
        cache = oracle.assert_cache_unchanged(baseline['cache'], state['cache'], contrasts)
        state_receipts.append({'actor': state['actor'], 'action': state['action'],
                               'models': models, 'cache': cache})
    return {'version': receipt['version'], 'selected_list_requests': 6,
            'state_checkpoints': state_receipts,
            'limits': ['Restricted denial is the unchanged UI route guard, without HTTP 403.',
                       'Four owned rows cannot exercise a second page at minimum size ten.',
                       'External integrations and rendering font parity are excluded.',
                       'Parent owns build observation, login-effect accounting, egress containment and lifecycle.']}
