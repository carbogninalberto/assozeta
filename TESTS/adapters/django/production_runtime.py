"""Require the production interpreter for application-wide Django oracles."""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]


def require_production_python():
    match = re.search(r'^FROM python:(\d+\.\d+\.\d+)-',
                      (ROOT / 'BE/Dockerfile').read_text(), re.MULTILINE)
    assert match, 'production Python version must remain explicit in BE/Dockerfile'
    expected = tuple(int(part) for part in match[1].split('.'))
    if sys.version_info[:3] != expected:
        raise RuntimeError(
            f'Application parity requires Python {match[1]} from BE/Dockerfile; '
            f'got {sys.version.split()[0]}'
        )
    return {'success': True, 'python': match[1], 'source': 'BE/Dockerfile',
            'scope': 'production interpreter selection; not backend parity'}


if __name__ == '__main__':
    result = require_production_python()
    out = ROOT / 'quality-reports/go-migration/runtime.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
