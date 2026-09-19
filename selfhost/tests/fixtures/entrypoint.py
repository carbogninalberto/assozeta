"""Keep the fixture manifest adapter active for distribution CLI invocations."""
import os
import sys

arguments = sys.argv[1:]
if arguments and arguments[0] == '/runner/distribution.py':
    import distribution
    action, root, *remaining = arguments[1:]
    if action not in ('prepare', 'rollback', 'commit', 'migration_started'):
        raise SystemExit('Unknown fixture distribution action')
    getattr(distribution, action)(root, *remaining)
else:
    os.execv(sys.executable, [sys.executable, *arguments])
