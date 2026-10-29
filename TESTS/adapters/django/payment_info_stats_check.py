"""Exercise the payment-info write guard against an owned disposable PostgreSQL.

No existing database is used. No skips: PostgreSQL and psycopg2 are required.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.payment_info_stats import capture, compare, require_no_user_writes

DATABASE = 'assozeta_contract_accounts'
SOURCES = ('TESTS/adapters/django/payment_info_stats.py',
           'TESTS/adapters/django/payment_info_stats_check.py')


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def main():
    output = ROOT / 'quality-reports/go-migration/payment-info-stats'
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / 'results.json'
    report = {'success': False, 'cleanup_verified': False, 'cases': [],
              'scope': 'All-user-table tuple write guard, catalog diagnostics and writer drain',
              'source_sha256': source_hashes()}
    result_path.write_text(json.dumps(report) + '\n')
    postgres = shutil.which('postgres')
    if not postgres:
        candidates = sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'))
        postgres = str(candidates[-1]) if candidates else None
    if not postgres:
        raise RuntimeError('PostgreSQL required; write-guard checks are not skipped')
    binaries = Path(postgres).parent
    started, stopped, observer, held = False, False, None, None
    try:
        with tempfile.TemporaryDirectory(prefix='az-payment-info-stats-', dir='/tmp') as directory:
            work = Path(directory)
            report['private_directory'] = str(work)
            data = work / 'postgres'

            def run(arguments):
                return subprocess.run([str(arg) for arg in arguments], check=True, timeout=30,
                                      capture_output=True, text=True)

            def connect():
                connection = psycopg2.connect(host=str(work), port=5432,
                                              user='contracts', dbname=DATABASE)
                connection.autocommit = True
                return connection

            def writer(statement):
                result = run([binaries / 'psql', '-X', '-A', '-t', '-q', '-v', 'ON_ERROR_STOP=1',
                              '-h', work, '-p', '5432', '-U', 'contracts', '-d', DATABASE,
                              '-c', 'SELECT pg_backend_pid(); ' + statement])
                return int(result.stdout.strip().splitlines()[0])

            def business_rows():
                with observer.cursor() as cursor:
                    cursor.execute('SELECT id,amount FROM application_payment ORDER BY id')
                    return cursor.fetchall()

            try:
                run([binaries / 'initdb', '-D', data, '-U', 'contracts', '-A', 'trust',
                     '--no-sync', '--no-locale', '-E', 'UTF8'])
                run([binaries / 'pg_ctl', '-D', data, '-l', work / 'postgres.log',
                     '-o', f"-k {work} -h '' -c autovacuum=off -c track_counts=on", '-w', 'start'])
                started = True
                run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
                observer = connect()
                with observer.cursor() as cursor:
                    cursor.execute('SHOW server_version')
                    report['server_version'] = cursor.fetchone()[0]
                    cursor.execute('SHOW listen_addresses')
                    assert cursor.fetchone()[0] == '', 'Private fixture unexpectedly listens on TCP'
                writer('''CREATE TABLE application_payment(id integer PRIMARY KEY,amount integer);
                    INSERT INTO application_payment VALUES(1,100);
                    CREATE SCHEMA operations;
                    CREATE TABLE operations.audit_events(id integer PRIMARY KEY,event text);
                    INSERT INTO operations.audit_events VALUES(1,'original');''')
                scenarios = (
                    ('analyze_catalog_only', 'ANALYZE application_payment; ANALYZE operations.audit_events;', False),
                    ('committed_business_update', 'UPDATE application_payment SET amount=amount+1 WHERE id=1;', True),
                    ('rolled_back_business_update', 'BEGIN; UPDATE application_payment SET amount=amount+1 WHERE id=1; ROLLBACK;', True),
                    ('rolled_back_operational_update', "BEGIN; UPDATE operations.audit_events SET event='changed' WHERE id=1; ROLLBACK;", True),
                    ('relation_replaced', 'DROP TABLE operations.audit_events; CREATE TABLE operations.audit_events(id integer PRIMARY KEY,event text);', True),
                    ('statistics_reset', 'SELECT pg_stat_reset();', True),
                )
                for name, statement, expected_rejection in scenarios:
                    before, previous_rows = capture(observer), business_rows()
                    pid = writer(statement)
                    evidence = compare(before, capture(observer))
                    rejected = False
                    try:
                        require_no_user_writes(evidence)
                    except AssertionError:
                        rejected = True
                    assert rejected == expected_rejection, (name, evidence)
                    if name.startswith('rolled_back'):
                        assert business_rows() == previous_rows
                    if name == 'analyze_catalog_only':
                        assert evidence['diagnostics']['system_table_changes'] and not evidence['user_table_changes']
                        assert sum(evidence['diagnostics']['whole_database_delta'].values()) > 0
                    if name == 'rolled_back_operational_update':
                        assert any(row['after']['schema'] == 'operations' for row in evidence['user_table_changes'])
                        with observer.cursor() as cursor:
                            cursor.execute('SELECT event FROM operations.audit_events WHERE id=1')
                            assert cursor.fetchone()[0] == 'original'
                    if name == 'relation_replaced':
                        assert not evidence['relation_identities_unchanged']
                    if name == 'statistics_reset':
                        assert not evidence['statistics_reset_unchanged']
                    with observer.cursor() as cursor:
                        cursor.execute('SELECT NOT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=%s)', [pid])
                        assert cursor.fetchone()[0]
                    report['cases'].append({'name': name, 'success': True,
                        'expected_rejection': expected_rejection, 'actual_rejection': rejected,
                        'writer_pid': pid, 'writer_absent_after_barrier': True, 'evidence': evidence})
                held = connect()
                held_pid = held.get_backend_pid()
                rejected = False
                try:
                    capture(observer, timeout=.1)
                except AssertionError as error:
                    rejected = 'fixture client backends did not drain' in str(error)
                    assert str(held_pid) in str(error)
                assert rejected, 'Live-client barrier accepted an undrained backend'
                held.close()
                held = None
                drained = capture(observer)
                report['cases'].append({'name': 'held_client_barrier', 'success': True,
                    'expected_rejection': True, 'actual_rejection': rejected, 'held_pid': held_pid,
                    'subsequent_capture': drained['barrier']})
                assert len(report['cases']) == 7
                report['checks_passed'] = True
            finally:
                if held is not None:
                    held.close()
                if observer is not None:
                    observer.close()
                if started:
                    run([binaries / 'pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop'])
                    stopped = True
    finally:
        report['cleanup_verified'] = bool(stopped and not Path(report['private_directory']).exists()) if started else False
        report['source_stable'] = report['source_sha256'] == source_hashes()
        report['success'] = bool(report.get('checks_passed') and report['cleanup_verified'] and report['source_stable'])
        result_path.write_text(json.dumps(report, indent=2) + '\n')
    assert report['success'], 'Write guard checks or cleanup failed; see results.json'
    print('Payment-info PostgreSQL write guard: seven actual database checks passed; cleanup verified')


if __name__ == '__main__':
    main()
