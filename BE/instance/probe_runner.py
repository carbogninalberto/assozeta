"""Hard deadlines around network checks, including DNS and SMTP handshakes."""
import json
import os
from pathlib import Path
import subprocess
import sys

from django.utils import timezone

PROBE_IDS = frozenset(('public_url', 'api', 'database', 'storage', 'worker', 'scheduler',
                       'renderer', 'email', 'updater', 'backups', 'email_connect', 'email_send',
                       'integration_ai', 'integration_stripe', 'integration_google', 'integration_apple'))
STATUSES = frozenset(('passed', 'warning', 'failed', 'not_configured', 'not_applicable', 'not_checked'))


def run_probe(probe, payload=None, timeout=10):
    if probe not in PROBE_IDS:
        raise ValueError('Unknown diagnostic')
    result = {'status': 'failed', 'level': 'connectivity', 'message': 'Verifica non riuscita. Controlla il servizio e riprova.'}
    try:
        process = subprocess.run(
            [sys.executable, '-m', 'instance.diagnostic_probe', probe],
            input=json.dumps(payload or {}), text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'core.settings'}, check=False,
        )
        candidate = json.loads(process.stdout.strip().splitlines()[-1]) if process.returncode == 0 else {}
        if (isinstance(candidate, dict) and candidate.get('status') in STATUSES and candidate.get('level') in ('configuration', 'connectivity', 'functional')
                and isinstance(candidate.get('message'), str) and len(candidate['message']) <= 700):
            result = {key: candidate[key] for key in ('status', 'level', 'message')}
    except subprocess.TimeoutExpired:
        result['message'] = ('Tempo massimo superato: invio non confermato. Verifica il destinatario prima di ripetere il test.' if probe == 'email_send' else 'Tempo massimo superato. Controlla la disponibilità del servizio e riprova.')
    except (OSError, ValueError, IndexError, TypeError):
        pass
    return {**result, 'checked_at': timezone.now().isoformat()}
