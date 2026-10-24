"""Verify explicit collaborator policy contracts and registry drift, without a DB.

This is an adapter-level policy test, not resource-authorization or HTTP parity.
The same manually specified outcomes are checked against both implementations.
"""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]


def main():
    output = ROOT / 'quality-reports/go-migration/permissions.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'success': False, 'phase': 'started'}) + '\n')
    tools_env = {name: value for name, value in os.environ.items()
                 if name in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    import environ
    os.environ.clear()
    os.environ.update(tools_env)
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
                      JWT_SECRET_KEY='policy-only', JWT_PUBLIC_KEY='policy-only',
                      DJANGO_SETTINGS_MODULE='core.settings', DBHOST='127.0.0.1', DBPORT='1', STORE_ON_DISK='True')
    import django
    from django.db.backends.base.base import BaseDatabaseWrapper
    with patch.object(BaseDatabaseWrapper, 'ensure_connection', side_effect=RuntimeError('Policy tests prohibit database access')), patch('environ.Env.read_env'):
        with open(os.devnull, 'w') as quiet, contextlib.redirect_stderr(quiet):
            django.setup()
        from application.permissions_registry import check_collaborator_permission, PERMISSIONS_REGISTRY, EXCLUDED_ENDPOINTS
        from rest_framework.exceptions import PermissionDenied
        cases = json.loads((ROOT / 'TESTS/fixtures/collaborator-policy.json').read_text())
        for case in cases:
            effective = SimpleNamespace(collaborator_role=case['role'], collaborator_permissions=case['permissions'])
            # Deliberately different accounts: the effective collaborator's grants
            # must apply even when the real actor is a fully privileged owner.
            actor = SimpleNamespace(collaborator_role=1, collaborator_permissions=['*'])
            request = SimpleNamespace(collaborator=case['collaborator'], path=case['path'], method=case['method'],
                                      effective_user=effective, original_user=actor, user=actor)
            detail = ''
            try:
                check_collaborator_permission(request)
            except PermissionDenied as denied:
                detail = str(denied.detail)
            assert detail == case['detail'], f"Django policy {case['name']}: {detail!r} != {case['detail']!r}"
        print(f'Django: {len(cases)} explicit collaborator policy contracts passed', flush=True)
        manifest = {'rules': [{'method': key[0] if isinstance(key, tuple) else '',
                               'path': key[1] if isinstance(key, tuple) else key, 'permission': permission}
                              for key, permission in PERMISSIONS_REGISTRY.items()],
                    'excluded': EXCLUDED_ENDPOINTS}
    with tempfile.TemporaryDirectory(prefix='az-policy-') as work:
        fixture = Path(work) / 'registry.json'
        fixture.write_text(json.dumps(manifest))
        subprocess.run(['go', 'test', '-race', '-tags=interop', './internal/service', '-run', 'Collaborator|PermissionRegistry'],
                       cwd=ROOT / 'BE_GO', check=True, timeout=120,
                       env={**tools_env, 'ASSOZETA_PERMISSION_REGISTRY': str(fixture)})
    report = {'success': True, 'explicit_policy_contracts': len(cases),
              'registry_rules_compared': len(manifest['rules']), 'exclusion_patterns_compared': len(manifest['excluded']),
              'scope': 'collaborator route policy only; no HTTP or resource authorization', 'database_access': 'prohibited'}
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
