"""Shared primitives for disposable Linux/desktop release verification."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def docker_host():
    host = os.environ.get('ASSOZETA_TEST_HOST')
    if not host:
        if sys.platform == 'darwin':
            host = 'host.docker.internal'
        else:
            network = json.loads(subprocess.check_output(['docker', 'network', 'inspect', 'bridge'], text=True))
            host = network[0]['IPAM']['Config'][0]['Gateway']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', host):
        raise ValueError('The test host must be an IPv4 address or hostname')
    return host


def write_evidence(kind, details):
    directory = os.environ.get('ASSOZETA_QUALITY_REPORT_DIR')
    if not directory:
        return
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    report = {'schema_version': 1, 'check': kind, 'commit': revision, 'passed': True, 'working_tree_clean': not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip(), **details}
    (destination / f'{kind}.json').write_text(json.dumps(report, indent=2) + '\n')


def assert_project_removed(project):
    for name in (project, project + '-updater'):
        if subprocess.check_output(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={name}'], text=True).strip():
            raise RuntimeError(f'Disposable containers remain for {name}')
    if subprocess.check_output(['docker', 'volume', 'ls', '-q', '--filter', f'name=^{project}_'], text=True).strip():
        raise RuntimeError(f'Disposable data volumes remain for {project}')


def remove_disposable_project(project):
    """Remove only this fixture's resources, including updater helper containers.

    A failed upgrade can leave its updater replacing itself while Compose down
    runs. Stop the exact fixture containers before removing its data volumes.
    """
    if not re.fullmatch(r'assozeta-update-test-[0-9a-f]{10}', project):
        raise ValueError('Refusing cleanup outside a disposable update fixture')
    for _ in range(10):
        rows = subprocess.check_output(['docker', 'ps', '-a', '--format', '{{.ID}} {{.Names}}'], text=True)
        identifiers = [row.split()[0] for row in rows.splitlines()
                       if row.split()[1].startswith(project + '-')]
        if not identifiers:
            break
        subprocess.run(['docker', 'rm', '--force', *identifiers], check=True,
                       stdout=subprocess.DEVNULL)
        time.sleep(0.5)
    for kind in ('network', 'volume'):
        rows = subprocess.check_output(['docker', kind, 'ls', '--format', '{{.Name}}'], text=True)
        names = [name for name in rows.splitlines()
                 if name.startswith(project + '_') or name.startswith(project + '-updater_')]
        if names:
            subprocess.run(['docker', kind, 'rm', *names], check=True, stdout=subprocess.DEVNULL)
    assert_project_removed(project)
