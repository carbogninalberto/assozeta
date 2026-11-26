"""Disposable updater HTTP handler and installed CLI for the reclaim-space tests."""
from io import BytesIO
import json
import threading
from pathlib import Path
from unittest.mock import Mock, patch


def runner_request(root, env, method, path='/backups/reclaim-space', body=None):
    return with_runner(root, env, lambda request: request(method, path, body))


def with_runner(root, env, exercise):
    """Dispatch requests to a fresh runner over an in-memory transport.

    The test drives schedule_tick/run_backup itself. Suppress background threads and
    remap the container's socket directories into this installation.
    """
    import server

    api = root / 'assozeta-updater'
    notices = root / 'notices'
    for directory, token in [(api, 'a' * 32), (notices, 'b' * 32)]:
        directory.mkdir(exist_ok=True)
        (directory / 'token').write_text(token)
    results = []

    def listener(socket_path, handler_class):
        Path(socket_path).touch()
        transport = Mock()

        def request(method, path, body=None):
            handler = object.__new__(handler_class)
            payload = json.dumps(body).encode() if body is not None else b''
            handler.path = path
            handler.headers = {'Authorization': 'Bearer ' + 'a' * 32, 'Content-Length': str(len(payload))}
            handler.rfile = BytesIO(payload)
            handler.respond = Mock()
            getattr(handler, 'do_' + method)()

            handler.respond.assert_called_once()
            return handler.respond.call_args.args

        transport.serve_forever.side_effect = lambda **kwargs: results.append(exercise(request))
        return transport

    def local_path(value):
        value = Path(value)
        return root / value.name if value.parent == Path('/run') else value

    with (
        patch.object(server, 'Path', side_effect=local_path),
        patch.object(server, 'Server', side_effect=listener),
        patch.object(server.notices, 'DIRECTORY', notices),
        patch.object(server.notices, 'start'),
        patch.object(server.os, 'chown'),
        patch.object(server, 'threading', Mock(wraps=threading, Thread=Mock())),
        patch.object(server.signal, 'signal'),
    ):
        server.serve(root, env)
    return results[0]


# Keep the dispatcher, leases, stop/start and reclaim code from the installed CLI.
# Stub Compose, the filesystem capacity, readiness, and the separate backup step.
CLI_STUBS = r'''
preflight() { :; }
ensure_configuration_values() { :; }
ensure_runtime_references() { :; }
prod_start_profile() { echo django; }
wait_public_readiness() { :; }
require_app_services_running() { :; }
require_no_surviving_lifecycle_helpers() { :; }
df() {
    printf 'Filesystem 1024-blocks Used Available Capacity Mounted\n/dev/x 20000000 1 %s 1%% /\n' "$TEST_FREE_KIB"
}
prod_compose() {
    case "$*" in
        *'config --services')
            case "$*" in *'--profile django'*) echo 'api worker beat realtime-relay web postgres' ;;
                *) echo 'api api-go worker beat realtime-relay web postgres' ;; esac ;;
        *'ps --status running'*) echo api ;;
        *'postgres df '*|*'postgres sh '*df*) df ;;
        *'postgres psql '*|*'postgres env '*psql*)
            printf '%s\n' "$*" >> "$TEST_EVENTS"
            case "$*" in
                *VACUUM*|*vacuum*)
                    # Only options forwarded into the container affect this psql session.
                    pgoptions=
                    while [ "$1" != psql ]; do
                        case "$1" in
                            PGOPTIONS|--env=PGOPTIONS) pgoptions=${PGOPTIONS:-} ;;
                            PGOPTIONS=*|--env=PGOPTIONS=*) pgoptions=${1#*PGOPTIONS=} ;;
                        esac
                        shift
                    done
                    python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "$pgoptions" "$@" >> "$SELFHOST_DIR/vacuum-commands.jsonl"
                    if [ -n "${TEST_VACUUM_GATE:-}" ] && [ ! -f "$TEST_VACUUM_GATE.done" ]; then
                        touch "$TEST_VACUUM_GATE.started"
                        while [ ! -f "$TEST_VACUUM_GATE.done" ]; do sleep 0.05; done
                    fi
                    test -f "$MAINTENANCE_FLAG" && test -d "$PROD_LOCK_DIR" || return 91
                    if command -v flock >/dev/null; then
                        test "$prod_operations_lock_held" = true || return 92
                    fi
                    if [ -f "$SELFHOST_DIR/first-vacuum" ]; then
                        test "$TEST_VACUUM_FAILURE" = 0 || return 17
                        touch "$SELFHOST_DIR/vacuum-finished"
                    else
                        touch "$SELFHOST_DIR/first-vacuum"
                    fi ;;
                *pg_database_size*)
                    if [ -f "$SELFHOST_DIR/vacuum-finished" ]; then
                        test "${TEST_AFTER_SIZE_FAILURE:-0}" = 0 || return 18
                        echo 3145728000
                    else
                        echo 7340032000
                    fi ;;
                *pg_total_relation_size*)
                    case "$*" in
                        *auditlog_logentry*application_auditlogindex*|*application_auditlogindex*auditlog_logentry*) echo 3145728 ;;
                        *auditlog_logentry*) echo 2097152 ;;
                        *application_auditlogindex*) echo 1048576 ;;
                        *) return 93 ;; esac ;;
                *) return 94 ;;
            esac ;;
        *stop*|*start*|*'up -d'*) printf '%s\n' "$*" >> "$TEST_EVENTS" ;;
        *) echo "unexpected compose command: $*" >&2; return 95 ;;
    esac
}
backup_prod() {
    echo "backup${1:+ $*}" >> "$TEST_EVENTS"
    # Observe the persisted maintenance result at the instant the backup starts.
    if [ "${1:-}" = --scheduled ] && [ ! -d "$TEST_BACKUP_SNAPSHOT" ]; then
        cp -R "$SELFHOST_DIR" "$TEST_BACKUP_SNAPSHOT"
    fi
}
'''


def install_cli(root):
    source = (Path(__file__).resolve().parents[1] / 'bin/assozeta').read_text()
    definitions, dispatcher = source.split('\ncommand=${1:-help}\n')
    directory = root / 'bin'
    directory.mkdir()
    cli = directory / 'assozeta'
    cli.write_text(definitions + CLI_STUBS + '\ncommand=${1:-help}\n' + dispatcher)
    cli.chmod(0o755)
    # A direct Docker call outside the fake Compose boundary must fail locally.
    docker = directory / 'docker'
    docker.write_text('#!/bin/sh\necho "unexpected docker call: $*" >&2\nexit 96\n')
    docker.chmod(0o755)
    return cli
