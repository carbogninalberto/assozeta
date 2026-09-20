"""Shared primitives for disposable Linux/desktop release verification."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


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
