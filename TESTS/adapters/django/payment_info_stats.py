"""Tuple-write evidence for the disposable payment-info HTTP fixtures.

All user tables are covered, including operational/audit tables and non-public
schemas. Catalog maintenance is diagnostic only: ANALYZE writes pg_statistic.
Counters detect rolled-back tuple writes that durable-state snapshots cannot.
This does not claim to detect every attempted SQL statement or temporary table.
"""
import time


COUNTERS = ('inserted', 'updated', 'deleted')


def capture(connection, *, timeout=10):
    autocommit = (connection.get_autocommit() if hasattr(connection, 'get_autocommit')
                  else connection.autocommit)
    assert autocommit, 'Statistics sampling requires an autocommit observer'
    started = time.monotonic()
    observed, polls = set(), 0
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(),pg_backend_pid()')
        database, observer = cursor.fetchone()
        assert database in ('assozeta_contract_accounts', 'assozeta_contract_category_image'), database
        while True:
            polls += 1
            cursor.execute('SELECT pg_stat_clear_snapshot()')
            cursor.execute("""SELECT pid FROM pg_stat_activity
                WHERE datname=current_database() AND backend_type='client backend'
                  AND pid<>pg_backend_pid() ORDER BY pid""")
            clients = [row[0] for row in cursor.fetchall()]
            observed.update(clients)
            if not clients:
                break
            assert time.monotonic() - started < timeout, ('fixture client backends did not drain', clients)
            # Condition-based polling; elapsed time alone never establishes a barrier.
            time.sleep(.02)
        # Backend exit flushes each departed writer's counters. Force the one
        # remaining observer's pending fixture writes, then discard cached views.
        cursor.execute('SELECT pg_stat_force_next_flush()')
        cursor.execute('SELECT pg_stat_clear_snapshot()')
        tables = {}
        for label, view in (('user_tables', 'pg_stat_user_tables'), ('system_tables', 'pg_stat_sys_tables')):
            cursor.execute(f'''SELECT relid,schemaname,relname,n_tup_ins,n_tup_upd,n_tup_del
                FROM {view} ORDER BY relid''')
            tables[label] = {str(row[0]): {'schema': row[1], 'name': row[2],
                **dict(zip(COUNTERS, row[3:]))} for row in cursor.fetchall()}
        cursor.execute('''SELECT tup_inserted,tup_updated,tup_deleted,stats_reset::text
            FROM pg_stat_database WHERE datname=current_database()''')
        row = cursor.fetchone()
    return {**tables, 'database': dict(zip(COUNTERS, row[:3])), 'stats_reset': row[3],
            'barrier': {'database': database, 'observer_pid': observer,
                        'other_client_backends': [], 'observed_departing_pids': sorted(observed),
                        'polls': polls, 'elapsed_seconds': time.monotonic() - started,
                        'observer_flush_requested': True}}


def compare(before, after):
    def totals(tables):
        return {key: sum(row[key] for row in tables.values()) for key in COUNTERS}

    def delta(left, right):
        return {key: right[key] - left[key] for key in COUNTERS}

    def changed(left, right):
        result = []
        for oid in sorted(set(left) | set(right)):
            old, new = left.get(oid), right.get(oid)
            if old != new:
                result.append({'oid': oid, 'before': old, 'after': new,
                               'delta': delta(old, new) if old is not None and new is not None else None})
        return result

    left, right = before['user_tables'], after['user_tables']
    identities = lambda rows: {oid: (row['schema'], row['name']) for oid, row in rows.items()}
    changes = changed(left, right)
    identity_match = identities(left) == identities(right)
    reset_match = before['stats_reset'] == after['stats_reset']
    database_match = before['barrier']['database'] == after['barrier']['database']
    return {'success': not changes and identity_match and reset_match and database_match,
            'scope': 'All pg_stat_user_tables relations across all user schemas; tuple counters include rolled-back DML',
            'user_totals_delta': delta(totals(left), totals(right)),
            'user_table_changes': changes, 'relation_identities_unchanged': identity_match,
            'statistics_reset_unchanged': reset_match, 'database_unchanged': database_match,
            'before': before, 'after': after,
            'diagnostics': {'whole_database_delta': delta(before['database'], after['database']),
                            'system_table_changes': changed(before['system_tables'], after['system_tables'])}}


def require_no_user_writes(evidence):
    assert evidence['success'], ('payment-info user-table write guard failed',
        {key: value for key, value in evidence.items() if key not in ('before', 'after')})
