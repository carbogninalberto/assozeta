"""Test-only driver: run one runtime switch and block at a named barrier.

    python3 handoff_controller_driver.py ROOT ENV_FILE OPERATION_ID POINT PSQL_JSON JOURNAL UPDATER_DIR

At POINT it prints 'AT <point>' and blocks on stdin, so the test can SIGKILL
the real process on a known side of a durable write. The barrier is the
controller's own injected callback; nothing here changes production code.
"""
import json
from pathlib import Path
import sys

sys.path[:0] = [sys.argv[7], str(Path(__file__).resolve().parents[2] / 'BE/instance')]

import runtime_switch  # noqa: E402
from journal import Journal  # noqa: E402


def main():
    root, env_file, operation_id, point, command, journal_path = sys.argv[1:7]
    journal = Journal(Path(journal_path))
    operation = next(record for record in journal.records() if record['id'] == operation_id)
    authority = runtime_switch.PsqlAuthority(json.loads(command))

    def barrier(reached):
        if reached == point:
            print('AT ' + reached, flush=True)
            sys.stdin.read()

    steps = {stage: (lambda context: None) for stage in runtime_switch.STEP_STAGES}
    runtime_switch.run(root, env_file, journal, authority, operation, steps=steps, barrier=barrier)
    print('FINISHED', flush=True)


if __name__ == '__main__':
    main()
