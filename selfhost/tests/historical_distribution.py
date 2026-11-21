"""The dual-API self-host distribution that legacy suites pin.

Until Phase 2 of the instance-wide Django/Go switch, one distribution ran both
APIs side by side: Compose had no runtime profiles, `web` waited on api and
api-go, Caddy chose the backend by path, the realtime relay and the Bakney
protocol had their own selectors and worker-go was an opt-in `go-tasks`
profile. Installations of those releases still exist, and the current CLI and
updater must still operate on them (upgrade, recovery to a source release).

Tests that characterize that behaviour read it from the baseline revision of
the switch plan, not from the current checkout, so they keep failing if that
behaviour is broken and stay green once the current distribution is
exclusive. The new exclusive behaviour is pinned in TESTS/handoff/test_p2_*.py.

    from historical_distribution import legacy_root
    LEGACY = legacy_root()            # LEGACY / 'selfhost/bin/assozeta', ...

Verification only: ASSOZETA_LEGACY_ROOT=<dir> uses a directory with the same
layout (a mutated copy) instead of the git archive.
"""
import atexit
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
# The plan's baseline (feat/go-handoff was cut here); on main, so CI clones with
# full history can read it.
REVISION = '2284ed46'
_EXTRACTED = None


def legacy_root():
    """A directory laid out like the repository, holding the baseline `selfhost/`."""
    global _EXTRACTED
    override = os.environ.get('ASSOZETA_LEGACY_ROOT')
    if override:
        return Path(override).resolve()
    if _EXTRACTED is None:
        directory = Path(tempfile.mkdtemp(prefix='assozeta-legacy-distribution-'))
        atexit.register(shutil.rmtree, directory, ignore_errors=True)
        try:
            archive = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', REVISION, '--', 'selfhost'],
                                     check=True, capture_output=True).stdout
        except subprocess.CalledProcessError as error:
            raise AssertionError(f'the historical distribution {REVISION} is not in this clone (fetch full history): '
                                 + error.stderr.decode(errors='replace')[-300:]) from None
        subprocess.run(['tar', '-x', '-C', str(directory)], input=archive, check=True, capture_output=True)
        _EXTRACTED = directory
    return _EXTRACTED


def legacy_definitions():
    """The baseline CLI's function definitions (everything before its command dispatch)."""
    return (legacy_root() / 'selfhost/bin/assozeta').read_text().split('\ncommand=${1:-help}\n')[0]
