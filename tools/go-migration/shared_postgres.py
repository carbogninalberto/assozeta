#!/usr/bin/env python3
"""Manage the shared PostgreSQL server used by parity test runs.

Family runs use the shared server by default; ASSOZETA_TEST_PRIVATE_POSTGRES=1
opts out and starts a private server per run.

  python3 tools/go-migration/shared_postgres.py [--directory DIR] start|stop|status|cleanup
                                                [--hours N] [--templates]

The server directory is --directory when given, otherwise
ASSOZETA_TEST_SHARED_POSTGRES, otherwise the user cache directory
(assozeta-test-postgres under XDG_CACHE_HOME or HOME/.cache).

Environment variables:
  ASSOZETA_TEST_SHARED_POSTGRES  directory of the shared server
  ASSOZETA_TEST_PRIVATE_POSTGRES=1
                                 force private mode; overrides the shared directory
  ASSOZETA_TEST_BINARY_CACHE     cache directory for Go and codec binaries

Commands:
  start    start the shared server, initialising its data directory if needed
  stop     stop the shared server
  status   report whether the server runs and which az_ databases exist
  cleanup  drop abandoned run and build databases (--templates also drops templates)

Results are printed as JSON and the exit status is 0 on success.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django import shared_postgres  # noqa: E402


def _directory(explicit):
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get('ASSOZETA_TEST_SHARED_POSTGRES')
    if configured:
        return Path(configured).expanduser().resolve()
    return shared_postgres.default_directory(os.environ)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Manage the shared PostgreSQL server used by parity test runs.',
        epilog=(
            'ASSOZETA_TEST_SHARED_POSTGRES selects the server directory, '
            'ASSOZETA_TEST_PRIVATE_POSTGRES=1 forces private mode, and '
            'ASSOZETA_TEST_BINARY_CACHE selects the Go/codec binary cache.'
        ),
    )
    parser.add_argument('--directory', metavar='DIR')
    parser.add_argument('command', choices=('start', 'stop', 'status', 'cleanup'))
    parser.add_argument('--hours', type=float, default=6,
                        help='age in hours after which a run database is dropped')
    parser.add_argument('--templates', action='store_true',
                        help='also drop shared template databases')
    args = parser.parse_args(argv)
    directory = _directory(args.directory)
    if args.command == 'start':
        binaries = shared_postgres.ensure_server(directory)
        result = shared_postgres.status(directory)
        result['binaries'] = str(binaries)
    elif args.command == 'stop':
        result = {'directory': str(directory), 'stopped': shared_postgres.stop(directory)}
    elif args.command == 'status':
        result = shared_postgres.status(directory)
    else:
        result = {
            'directory': str(directory),
            'dropped': shared_postgres.cleanup(
                directory, hours=args.hours, templates=args.templates),
            'hours': args.hours,
            'templates': args.templates,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
