"""Phase 5 part A: the bidirectional runtime switch controller (CONTRACT P5-A v1).

Red-first contract for selfhost/updater/runtime_switch.py (Phase 1) wired through the
Phase 2 service sets and the Phase 3/4 job_handoff steps. Boundary: a simulated
installation of the current distribution (TESTS/handoff/p5_harness.py): real
`docker compose config`, simulated containers whose every change is stamped with
the authority row and the maintenance flag of that instant, and a real private
PostgreSQL (all operational migrations), a real private redis-server and the
checkout's updater tools. Installed images, Caddy and real processes are part D
(test_p5_installed.py).

Controller (selfhost/updater/runtime_switch.py, additive to Phase 1)
  STEP_STAGES = ('preflight', 'close_admission', 'settle', 'stage_backlog',
                 'stop_source', 'target_preflight', 'activate', 'verify')
      PRE_COMMIT = the first six; POST_COMMIT = ('activate', 'verify').
  BARRIER_POINTS = ('stage:preflight', 'stage:close_admission', 'stage:settle',
      'stage:stage_backlog', 'stage:stop_source', 'stage:target_preflight',
      'stage:commit', 'authority:committed', 'stage:activate', 'stage:verify',
      'outcome:succeeded')  (emitted by advance() exactly as in Phase 1)
  STEP_POINTS = ('activate:started', 'verify:opened')  (emitted by the default
      steps through the same barrier; never by advance with caller steps)
  PROGRESS_STAGES = ('preflight', 'close_admission', 'settle', 'stage_backlog',
      'stop_source', 'target_preflight', 'commit', 'activate', 'verify')
  default_steps(root, env_file, *, barrier=None) -> {stage: callable(record), 'abort': callable(record)}
      advance() calls default_steps(root, env_file, barrier=barrier) when steps is None.
      Built from job_handoff.handoff_steps(job_handoff.compose_store(root, env_file),
      job_handoff.compose_broker(root, env_file), ...) and the Phase 2 service sets
      (common.runtime_services). A step may return a dict; its counts are merged into
      the record (P5-STATUS: counts, dispositions, blockers, progress).
  job_handoff.compose_store(root, env_file): PsqlStore over
      `docker compose --env-file ENV -f ROOT/compose.yml exec -T postgres psql -U DBUSER -d DBNAME`.
  job_handoff.compose_broker(root, env_file): RespBroker.call semantics over
      `docker compose ... exec -T redis <cmd>` carrying raw RESP on stdin/stdout,
      authenticated with AUTH REDIS_USERNAME REDIS_PASSWORD sent over the pipe
      (never in argv or a log).

Evidence each stage needs before the next one starts (both directions):
  preflight        read-only. Distribution without runtime profiles -> switch_unavailable
                   before any command (Phase 1 pin). job_handoff preflight blockers ->
                   operation failed, error_code 'transfer_blocked', failed_stage 'preflight',
                   blockers reported; no maintenance flag, no container change, admission open.
  close_admission  .operations/maintenance.flag raised (records progress.maintenance_since /
                   _deadline), then job_handoff close_admission (admission frozen, every live
                   source consumer acknowledged). Source services keep running.
  settle           job_handoff settle.
  stage_backlog    job_handoff stage_backlog: every accepted item staged to this operation,
                   no source broker copy left.
  stop_source      `compose stop` of every source runtime service; none left running.
                   From here until activate neither application API runs.
  target_preflight private destination check after the source stopped: one-shot
                   `compose run --rm --no-deps [-d] [-e ASSOZETA_GO_WHATSAPP_OUTBOX=0 for go]
                   <target API service> ...` with no --use-aliases/--service-ports (so it never
                   holds the active-api alias and starts no autonomous effect); no `up` of any
                   target service before the commit; no one-shot left running afterwards.
  commit           Phase 1 PsqlAuthority.commit (binds staged/held work).
  activate         `compose up` of exactly the target set (only after the commit), then
                   job_handoff activate; barrier 'activate:started' with the maintenance flag
                   still present (the proxy is not open yet).
  verify           public readiness, job_handoff verify, service inspection (source set
                   stopped, target set running), audit line, maintenance flag removed,
                   barrier 'verify:opened', then the journal records success.
Maintenance window: .env ASSOZETA_RUNTIME_SWITCH_WINDOW_SECONDS (default 900) bounds
  close_admission..target_preflight; a pre-commit stage that cannot finish inside it fails
  with error_code 'window_exceeded'. Never a forced successful drain.
Failure before the commit (any pre-commit stage after preflight): default_steps['abort']
  -> job_handoff.abort, source set started again, target set stopped, maintenance flag
  removed; record 'failed' with failed_stage; effective runtime unchanged; status
  recovery_action 'retry'. Staged work is back at the source exactly once.
Failure after the commit: record 'recovery_required' (post_commit_failed), the maintenance
  flag stays (never open public traffic onto an unhealthy target); `runtime-recover`
  finishes forward; or a reversal: while that record is recovery_required, a request with
  target = its previous_runtime and expected_generation = its committed_generation is
  admitted (202), the old record becomes status 'failed' with superseded_by = the new id,
  and the new operation transfers everything the failed target accepted since the commit.
Schedules Go cannot publish (F1, task-coverage "unknown definitions identified before the source stops"):
  a switch to go fails at preflight (transfer_blocked, nothing changed) when an ENABLED persisted Beat
  definition (django_celery_beat_periodictask) is one the leased Go scheduler would skip: a task that is
  neither a Go periodic job kind nor instance.backups.notify_failures, a solar schedule, any of
  queue/exchange/routing_key/priority set, or non-empty args/kwargs/headers (the rule of
  BE_GO/internal/service/schedule_due.go validatePeriodicDefinition, which P4-B may widen, e.g.
  deliver-whatsapp). Each is one blocker {'task_id': 'periodic:<definition name>', 'reason':
  'unsupported_schedule'} (status items ref 'periodic:<name>', age_seconds 0; the definition name is shown,
  never its task, args or kwargs); disabled definitions and publishable ones never block. A switch to
  django is never blocked by definitions (Beat publishes every persisted definition).
Attempt leases (Phase 3 part B: broker attempts carry assozeta_handoff_attempts.lease_until, 30 s,
  renewed every 10 s by the executing process): the lease is the fence for the switch. settle releases a
  running source attempt only once its lease has expired, even while its consumer still heartbeats and
  acknowledged the freeze (the executor died; finished_at >= lease_until), then the unacked copy is staged
  and delivered once; it never releases an attempt whose lease is still being renewed, even when the
  consumer's heartbeat stopped: such a switch fails before the commit (window_exceeded or
  transfer_blocked 'running'), the attempt stays running and its task is not transferred.
Interruption: killing the controller at any BARRIER_POINT or STEP_POINT and running
  recover() (restarted runner) or `./bin/assozeta runtime-recover` converges to the
  uninterrupted outcome. runtime_switch.py main honours env ASSOZETA_HANDOFF_BARRIER_DIR
  with the Phase 3 file-barrier protocol (arrived.<point>.<operation id>, hold.<point>.<id|all>,
  release.<point>.<id|all>) for real process kills.

Run: python3 -B -m unittest -v TESTS.handoff.test_p5_controller
     (docker compose CLI, PostgreSQL server binaries, redis-server; the cold-client test
     also needs a Docker daemon and the pinned Caddy image). Expected reds are tracked in
     TESTS/handoff/expected_red.json (owner t-p5, phase 5); every red message starts with
     'P5 contract red'.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest

from TESTS.handoff import p5_harness as h
from TESTS.handoff.p2_harness import evidence_proxy

FORWARD_POINTS = ('stage:preflight', 'stage:close_admission', 'stage:settle', 'stage:stage_backlog',
                  'stage:stop_source', 'stage:target_preflight', 'stage:commit', 'authority:committed',
                  'stage:activate', 'activate:started', 'stage:verify', 'verify:opened', 'outcome:succeeded')
RED = 'P5 contract red'


def setUpModule():
    h.services()


def tearDownModule():
    h.stop_services()


class SwitchCase(h.ContractCase, unittest.TestCase):
    maxDiff = None

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='assozeta-p5-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.inst = h.P5Installation(self.directory)
        self.inst.install()
        self.eta = h.future(30)

    # -- helpers ------------------------------------------------------------------------------------------------
    def committed(self, record, generation, label):
        self.assertEqual((record['status'], record['outcome'], record['committed_generation'], record.get('error_code')),
                         ('succeeded', 'committed', generation, None),
                         f'{RED}: {label} did not commit generation {generation}: status {record["status"]}, '
                         f'error_code {record.get("error_code")}, failed_stage {record.get("failed_stage")}')

    def seed_django_work(self):
        inst = self.inst
        return {'mail': inst.push('celery', h.MAIL_TASK, args=h.mail_args(1)),
                'eta': inst.push('celery', h.MAIL_TASK, args=h.mail_args(2), eta=self.eta, priority=6),
                'db': inst.push('celery', h.DB_TASK)}

    def assert_on(self, task_id, runtime, generation, label):
        """One logical item: owned by the runtime's generation, one runnable copy there, none elsewhere."""
        inst = self.inst
        row = inst.ledger(task_id)
        self.assertIsNotNone(row, f'{RED}: {label}: {task_id} has no ledger row (lost)')
        self.assertEqual((row['owner_runtime'], row['owner_generation'], row['state']), (runtime, generation, 'pending'),
                         f'{RED}: {label}: {task_id} not pending on {runtime}/{generation}')
        copies = inst.copies(task_id)
        other = h.QUEUE['go' if runtime == 'django' else 'django']
        stale = {key: n for key, n in copies.items() if key.startswith(other)}
        self.assertEqual(stale, {}, f'{RED}: {label}: {task_id} left a runnable copy on the inactive queue {other}')
        job = inst.go_job(task_id)
        if runtime == 'go' and row['transport'] == 'sql':
            self.assertEqual((job or {}).get('state'), 'pending', f'{RED}: {label}: {task_id} has no pending Go row')
            self.assertEqual(sum(copies.values()), 0, f'{RED}: {label}: native Go job {task_id} also has broker copies')
        else:
            self.assertEqual(sum(copies.values()), 1, f'{RED}: {label}: {task_id} copies {copies}; expected exactly one '
                                                       f'on {h.QUEUE[runtime]}')
            if job is not None:
                self.assertEqual((job['state'], job['error_code']), ('cancelled', 'handoff_transferred'),
                                 f'{RED}: {label}: {task_id} transferred off Go but its Go row is {job}')

    def observe(self, items=()):
        """A barrier recording the durable world at each point it is called with."""
        inst = self.inst
        seen = []

        def barrier(point):
            record = next(iter(inst.runtime_records()), None)
            seen.append({'point': point, 'stage': record and record['stage'], 'operation': record and record['id'],
                         'authority': inst.authority(), 'admission': inst.admission(), 'maintenance': inst.maintenance(),
                         'app': inst.app_running(), 'events': inst.mark(), 'oneoffs': list(inst.oneoffs_running()),
                         'ledger': {task: ((inst.ledger(task) or {}).get('state'), (inst.ledger(task) or {}).get('operation_id'))
                                    for task in items},
                         'copies': {task: inst.copies(task) for task in items}})
        barrier.seen = seen
        return barrier

    def assert_protocol(self, source, target, seen, items, source_generation):
        """The order and the evidence of every stage of one switch (CONTRACT P5-A, module docstring)."""
        inst = self.inst
        self.assertEqual([entry['point'] for entry in seen], list(FORWARD_POINTS),
                         f'{RED}: barrier points of a {source}->{target} switch')
        at = {entry['point']: entry for entry in seen}
        source_app, target_app, api = set(h.APP[source]), set(h.APP[target]), h.ACTIVE_API[target]
        events = inst.events()

        def window(first, last):
            return [e for e in events[at[first]['events']:at[last]['events']]]
        self.assertEqual((at['stage:preflight']['maintenance'], at['stage:preflight']['app']), (False, source_app),
                         f'{RED}: preflight must start from the untouched {source} topology')
        self.assertEqual(inst.changes(at['stage:preflight']['events'], at['stage:close_admission']['events']), [],
                         f'{RED}: preflight changed a container')
        settle = at['stage:settle']
        self.assertEqual((settle['maintenance'], settle['admission'], settle['app']), (True, 'frozen', source_app),
                         f'{RED}: after close_admission: maintenance on, admission frozen, source still running')
        stop = at['stage:stop_source']
        operation = stop['operation']
        for task in items:
            self.assertEqual(stop['ledger'][task], ('staged', operation),
                             f'{RED}: after stage_backlog {task} must be staged to the operation')
            stale = {k: n for k, n in stop['copies'][task].items() if k.startswith(h.QUEUE[source])}
            self.assertEqual(stale, {}, f'{RED}: after stage_backlog {task} still has a source broker copy')
        stopped = {s for e in window('stage:stop_source', 'stage:target_preflight') for s in e.get('stopped', [])}
        self.assertTrue(source_app <= stopped, f'{RED}: stop_source did not stop {sorted(source_app - stopped)}')
        private = at['stage:target_preflight']
        self.assertEqual((private['maintenance'], private['app']), (True, set()),
                         f'{RED}: after stop_source neither application API (nor any app service) may run')
        probes = [e for e in window('stage:target_preflight', 'stage:commit')
                  if e['call'][:2] == ['compose', 'run'] and (e.get('oneoff') or {}).get('service') == api]
        self.assertTrue(probes, f'{RED}: no private {api} one-shot ran in target_preflight')
        for probe in probes:
            call = probe['call']
            self.assertTrue('--no-deps' in call and '--use-aliases' not in call and '--service-ports' not in call,
                            f'{RED}: private preflight one-shot {call} must use --no-deps and no aliases/ports')
            if target == 'go':
                self.assertTrue(any(call[i] in ('-e', '--env') and call[i + 1] == 'ASSOZETA_GO_WHATSAPP_OUTBOX=0'
                                    for i in range(len(call) - 1)),
                                f'{RED}: the private api-go preflight must disable the WhatsApp outbox: {call}')
            self.assertEqual(probe.get('maintenance'), True, f'{RED}: private preflight outside maintenance')
        self.assertEqual(at['stage:commit']['oneoffs'], [], f'{RED}: a private one-shot still runs at the commit')
        pre_commit = [e for e in inst.changes(at['stage:preflight']['events'])
                      if e.get('authority') and tuple(e['authority'][:2]) == (source, source_generation)]
        started_early = sorted({s for e in pre_commit for s in e.get('started', []) if s in target_app})
        self.assertEqual(started_early, [], f'{RED}: target services started before the authority commit')
        activated = at['activate:started']
        self.assertEqual((tuple(activated['authority'][:2]), activated['maintenance'], activated['app']),
                         ((target, source_generation + 1), True, target_app),
                         f'{RED}: activate:started must have the committed target running behind maintenance')
        self.assertFalse(at['verify:opened']['maintenance'], f'{RED}: verify must lift maintenance before success')
        after = [e for e in inst.changes(activated['events']) if set(e.get('stopped', [])) & target_app]
        self.assertEqual(after, [], f'{RED}: a target service was stopped after activation')

    # -- forward and reverse ------------------------------------------------------------------------------------
    def test_forward_switch_runs_every_stage_in_order_with_its_evidence(self):
        inst = self.inst
        rs = inst.rs('STEP_STAGES', 'BARRIER_POINTS', 'STEP_POINTS', 'PROGRESS_STAGES')
        self.assertEqual(tuple(rs.STEP_STAGES), ('preflight', 'close_admission', 'settle', 'stage_backlog', 'stop_source',
                                                 'target_preflight', 'activate', 'verify'), f'{RED}: STEP_STAGES')
        items = self.seed_django_work()
        inst.broker().call('SET', 'unrelated:p5', 'kept')
        barrier = self.observe(items.values())
        final = inst.switch('go', barrier=barrier)
        self.committed(final, 2, 'django->go')
        self.assert_protocol('django', 'go', barrier.seen, items.values(), 1)
        self.assertEqual(inst.authority(), ('go', 2, final['id']))
        inst.assert_mode(self, 'go', 'the forward switch')
        self.assertFalse(inst.maintenance(), f'{RED}: maintenance flag left after success')
        for label, task_id in items.items():
            self.assert_on(task_id, 'go', 2, f'forward {label}')
        queue, message = inst.broker_message(items['eta'])
        self.assertEqual((queue, message['headers']['eta']), (h.priority_key('assozeta-go', 6), self.eta.isoformat()),
                         f'{RED}: a job due next month must keep its due time and priority on the target')
        self.assertEqual((inst.go_job(items['db']) or {}).get('state'), 'pending',
                         f'{RED}: a Django-queued database task must become its Go durable row')
        self.assertEqual(inst.broker().call('GET', 'unrelated:p5'), b'kept', f'{RED}: unrelated Redis key changed')
        delivered = {t['task_id'] for t in inst.transfers(final['id']) if t['delivered_at']}
        self.assertEqual(delivered, set(items.values()), f'{RED}: transfer receipts of the forward switch')
        self.assertEqual(final.get('dispositions'), {'staged': 0, 'transferred': 3, 'blocked': 0, 'ambiguous': 0},
                         f'{RED}: dispositions of the finished forward switch')

    def test_reverse_switch_mirrors_the_forward_protocol(self):
        inst = self.inst
        inst.rs('STEP_POINTS')
        self.committed(inst.switch('go'), 2, 'django->go (setup)')
        mail = inst.push('assozeta-go', h.MAIL_TASK, args=h.mail_args(3))
        native = inst.go_row(h.DB_TASK)
        barrier = self.observe((mail, native))
        final = inst.switch('django', barrier=barrier)
        self.committed(final, 3, 'go->django')
        self.assert_protocol('go', 'django', barrier.seen, (mail, native), 2)
        self.assertEqual(inst.authority(), ('django', 3, final['id']))
        inst.assert_mode(self, 'django', 'the reverse switch')
        self.assert_on(mail, 'django', 3, 'reverse mail')
        self.assert_on(native, 'django', 3, 'reverse native Go row')
        queue, message = inst.broker_message(native)
        self.assertEqual((queue, message['headers']['task']), ('celery', h.DB_TASK),
                         f'{RED}: a Go-native job must reach Django as its Celery form on the default queue')

    # -- maintenance window and status ----------------------------------------------------------------------------
    def test_progress_is_visible_without_either_api_at_every_stage(self):
        inst = self.inst
        inst.rs('PROGRESS_STAGES')
        inst.set_env(ASSOZETA_RUNTIME_SWITCH_WINDOW_SECONDS='600')
        self.seed_django_work()
        reports = []

        def barrier(point):
            result, payload = inst.tool_json('status', str(inst.root), str(inst.env_file))
            reports.append((point, result.returncode, payload, inst.maintenance(), inst.app_running()))
        final = inst.switch('go', barrier=barrier)
        self.committed(final, 2, 'django->go')
        self.assertTrue(reports, f'{RED}: no barrier reached')
        for point, code, payload, flag, app in reports:
            with self.subTest(point=point):
                self.assertEqual(code, 0, f'{RED}: operator status failed at {point}')
                operation = (payload or {}).get('operation') or {}
                progress = operation.get('progress') or {}
                self.assertEqual(operation.get('id'), final['id'], f'{RED}: status at {point} lost the operation')
                self.assertEqual((payload.get('intended_runtime'), payload.get('maintenance')), ('go', flag),
                                 f'{RED}: status at {point}: intended runtime and maintenance flag')
                stage = operation.get('stage')
                expected = 1 + h.PROGRESS_STAGES.index(stage) if stage in h.PROGRESS_STAGES else (10 if stage == 'completed' else 0)
                self.assertEqual((progress.get('stage'), progress.get('index'), progress.get('total')), (stage, expected, 9),
                                 f'{RED}: status progress at {point}')
                if flag:
                    since, deadline = progress.get('maintenance_since'), progress.get('maintenance_deadline')
                    self.assertTrue(since and deadline, f'{RED}: maintenance window not reported at {point}')
                    from datetime import datetime
                    span = (datetime.fromisoformat(deadline) - datetime.fromisoformat(since)).total_seconds()
                    self.assertEqual(round(span), 600, f'{RED}: maintenance deadline must be since + window')
        quiet = [point for point, _, _, _, app in reports if not (app & {'api', 'api-go'})]
        self.assertTrue(quiet, f'{RED}: the switch never had a moment without either API (no source stop?)')

    def test_a_drain_that_cannot_finish_inside_the_window_reverts_to_the_source(self):
        inst = self.inst
        inst.set_env(ASSOZETA_RUNTIME_SWITCH_WINDOW_SECONDS='6')
        items = self.seed_django_work()
        _, halt = inst.register_consumer('django')
        self.addCleanup(halt)
        before = inst.mark()
        started = time.monotonic()
        final = inst.switch('go')
        elapsed = time.monotonic() - started
        halt()
        self.assertEqual((final['status'], final.get('error_code'), final.get('failed_stage')),
                         ('failed', 'window_exceeded', 'close_admission'),
                         f'{RED}: a freeze never acknowledged inside the window must fail before the commit')
        self.assertLess(elapsed, 6 + 60, f'{RED}: the maintenance window was not bounded ({elapsed:.0f} s)')
        self.assertEqual((inst.authority()[:2], inst.admission(), inst.maintenance()), (('django', 1), 'open', False),
                         f'{RED}: the source must be fully restored after a window overrun')
        inst.assert_mode(self, 'django', 'the window overrun')
        started_go = sorted({s for e in inst.changes(before) for s in e.get('started', []) if s in h.GO_APP})
        self.assertEqual(started_go, [], f'{RED}: Go services started by a switch that never committed')
        status = inst.status()
        self.assertEqual((status['operation'] or {}).get('recovery_action'), 'retry', f'{RED}: recovery action')
        for label, task_id in items.items():
            self.assertEqual(sum(inst.copies(task_id).values()), 1, f'{RED}: {label} not back at the source exactly once')

    # -- failure before and after the commit ------------------------------------------------------------------------
    def test_target_preflight_failure_before_commit_returns_everything_to_the_source(self):
        inst = self.inst
        items = self.seed_django_work()
        inst.fail_oneoff('api-go')
        before = inst.mark()
        final = inst.switch('go')
        self.assertEqual((final['status'], final.get('failed_stage'), final['committed_generation']),
                         ('failed', 'target_preflight', None),
                         f'{RED}: a failing private Go preflight must fail the switch before the commit')
        self.assertEqual((inst.authority(), inst.admission(), inst.maintenance()), (('django', 1, None), 'open', False),
                         f'{RED}: failed-before-commit must leave the source effective, admission open, no maintenance')
        inst.assert_mode(self, 'django', 'failed-before-commit')
        started_go = sorted({s for e in inst.changes(before) for s in e.get('started', []) if s in h.GO_APP})
        self.assertEqual((started_go, inst.oneoffs_running()), ([], []), f'{RED}: Go processes left or started')
        for label, task_id in items.items():
            row = inst.ledger(task_id)
            self.assertEqual((row['owner_runtime'], row['owner_generation'], row['state']), ('django', 1, 'pending'),
                             f'{RED}: {label} not returned to the source ledger')
            self.assertEqual(inst.copies(task_id), {h.priority_key('celery', 6 if label == 'eta' else 0): 1},
                             f'{RED}: {label} not back on the source queue exactly once')
        operation = inst.status()['operation']
        self.assertEqual((operation.get('recovery_action'), operation['error_code'] is not None), ('retry', True),
                         f'{RED}: a failed-before-commit switch must offer a retry')
        inst.fail_oneoff()
        self.committed(inst.switch('go'), 2, 'the retry once the target preflight passes')
        for label, task_id in items.items():
            self.assert_on(task_id, 'go', 2, f'retry {label}')

    def test_target_failure_after_commit_is_reversed_with_its_new_work(self):
        inst = self.inst
        mail = inst.push('celery', h.MAIL_TASK, args=h.mail_args(4))
        inst.crash('worker-go')
        failed = inst.switch('go')
        self.assertEqual((failed['status'], failed.get('error_code'), failed['committed_generation']),
                         ('recovery_required', 'post_commit_failed', 2),
                         f'{RED}: a target that cannot stay up after the commit must require recovery')
        self.assertEqual(inst.authority(), ('go', 2, failed['id']))
        self.assertTrue(inst.maintenance(), f'{RED}: public traffic must stay closed onto an unhealthy target')
        self.assertEqual(inst.status()['operation'].get('recovery_action'), 'recover', f'{RED}: recovery action')
        # Work the target accepted after the commit.
        late_mail = inst.push('assozeta-go', h.MAIL_TASK, args=h.mail_args(5))
        late_native = inst.go_row(h.DB_TASK)
        code, payload = inst.submit('django', generation=2)
        self.assertEqual(code, 202, f'{RED}: a reversal of a recovery_required switch must be admitted: {payload}')
        reverse = inst.run(payload['operation']['id'])
        self.committed(reverse, 3, 'the reversal')
        old = inst.record(failed['id'])
        self.assertEqual((old['status'], old.get('superseded_by')), ('failed', reverse['id']),
                         f'{RED}: the failed forward record must be closed as superseded by the reversal')
        self.assertEqual(inst.authority(), ('django', 3, reverse['id']))
        inst.assert_mode(self, 'django', 'the reversal')
        for label, task_id in (('mail', mail), ('late mail', late_mail), ('late native', late_native)):
            self.assert_on(task_id, 'django', 3, f'reversal {label}')
        self.assertFalse(inst.maintenance(), f'{RED}: maintenance after a successful reversal')

    def test_unresolved_blocker_prevents_success_and_is_reported_without_payloads(self):
        inst = self.inst
        sentinel = 'P5-JOB-ARGUMENT-SENTINEL'
        good = inst.push('celery', h.MAIL_TASK, args=h.mail_args(6))
        unknown = inst.push('celery', 'fixture.p5.retired_task', args=(sentinel,), admit=False)
        ambiguous = 'a5a5a5a5-0000-4000-8000-00000000000a'
        inst.sql("INSERT INTO public.assozeta_handoff_work (task_id, task_name, transport, queue, origin_runtime, "
                 "owner_runtime, owner_generation, state, accepted_at, block_reason) VALUES "
                 f"('{ambiguous}', '{h.MAIL_TASK}', 'broker', 'celery', 'django', 'django', 1, 'blocked', "
                 "clock_timestamp() - interval '2 hours', 'ambiguous_effect');")
        before, env = inst.mark(), inst.env_text()
        final = inst.switch('go')
        self.assertEqual((final['status'], final.get('error_code'), final.get('failed_stage')),
                         ('failed', 'transfer_blocked', 'preflight'),
                         f'{RED}: blockers must stop the switch at preflight')
        self.assertEqual((inst.changes(before), inst.maintenance(), inst.admission(), inst.authority(), inst.env_text()),
                         ([], False, 'open', ('django', 1, None), env),
                         f'{RED}: a blocked preflight must change nothing')
        status = inst.status()
        blockers = (status['operation'] or {}).get('blockers') or {}
        self.assertEqual((blockers.get('count'), blockers.get('by_reason')),
                         (2, {'unknown_task': 1, 'ambiguous_effect': 1}), f'{RED}: blocker counts by reason')
        self.assertGreaterEqual(blockers.get('oldest_age_seconds') or 0, 7000, f'{RED}: oldest blocked item age')
        self.assertEqual({item['ref'] for item in blockers.get('items', [])}, {unknown, ambiguous},
                         f'{RED}: blocker references')
        self.assertTrue(blockers.get('action'), f'{RED}: blockers need an actionable reason')
        self.assertEqual(status['operation'].get('recovery_action'), 'reconcile', f'{RED}: recovery action')
        published = json.dumps(status)
        for forbidden in (sentinel, 'fixture.p5.retired_task', h.MAIL_TASK, inst.services.redis_password, 'recipient'):
            self.assertNotIn(forbidden, published, f'{RED}: status leaks job data or credentials')
        self.assertEqual(sum(inst.copies(good).values()), 1, f'{RED}: unblocked work must stay at the source')

    # -- schedules Go cannot publish (F1) -------------------------------------------------------------------------
    def test_schedules_go_cannot_publish_block_a_switch_to_go_at_preflight(self):
        inst = self.inst
        work = inst.push('celery', h.MAIL_TASK, args=h.mail_args(10))
        custom = inst.periodic('p5-custom-nightly', 'fixture.p5.custom_periodic')
        with_args = inst.periodic('p5-balance-with-args', h.DB_TASK, args='[1]')
        routed = inst.periodic('p5-balance-routed', h.DB_TASK, queue='priority')
        inst.periodic('p5-balance-plain', h.DB_TASK)
        inst.periodic('p5-custom-disabled', 'fixture.p5.custom_periodic', enabled=False)
        before, env = inst.mark(), inst.env_text()
        final = inst.switch('go')
        self.assertEqual((final['status'], final.get('error_code'), final.get('failed_stage')),
                         ('failed', 'transfer_blocked', 'preflight'),
                         f'{RED}: definitions the Go scheduler would skip must block the switch at preflight')
        self.assertEqual((inst.changes(before), inst.maintenance(), inst.admission(), inst.authority(), inst.env_text()),
                         ([], False, 'open', ('django', 1, None), env), f'{RED}: a blocked preflight changed something')
        blockers = (inst.status()['operation'] or {}).get('blockers') or {}
        self.assertEqual(({item['ref']: item['reason'] for item in blockers.get('items', [])}, blockers.get('by_reason')),
                         ({custom: 'unsupported_schedule', with_args: 'unsupported_schedule',
                           routed: 'unsupported_schedule'}, {'unsupported_schedule': 3}),
                         f'{RED}: exactly the enabled unpublishable definitions block, by definition name')
        self.assertTrue(blockers.get('action'), f'{RED}: the schedule blockers need an actionable reason')
        self.assertEqual(inst.status()['operation'].get('recovery_action'), 'retry', f'{RED}: recovery action')
        self.assertNotIn('fixture.p5.custom_periodic', json.dumps(inst.status()), f'{RED}: status names a task')
        self.assertEqual(sum(inst.copies(work).values()), 1, f'{RED}: work moved by a blocked preflight')
        inst.sql("UPDATE public.django_celery_beat_periodictask SET enabled=false WHERE name LIKE 'p5-%' "
                 "AND name <> 'p5-balance-plain';")
        self.committed(inst.switch('go'), 2, 'the switch once the definitions are disabled')

    def test_definitions_never_block_a_switch_to_django(self):
        inst = self.inst
        self.committed(inst.switch('go'), 2, 'django->go')
        inst.periodic('p5-custom-nightly', 'fixture.p5.custom_periodic')
        inst.periodic('p5-balance-with-args', h.DB_TASK, args='[1]')
        self.committed(inst.switch('django'), 3, 'go->django with definitions only Beat can publish')
        inst.assert_mode(self, 'django', 'the reverse switch with Beat-only definitions')

    # -- attempt leases ---------------------------------------------------------------------------------------------
    def test_a_dead_executor_is_released_after_its_lease_and_its_task_transfers_once(self):
        inst = self.inst
        inst.set_env(ASSOZETA_RUNTIME_SWITCH_WINDOW_SECONDS='300')
        task = inst.push('celery', h.MAIL_TASK, args=h.mail_args(11))
        consumer, halt = inst.register_consumer('django', acknowledge=True)
        self.addCleanup(halt)
        inst.running_attempt('django', consumer, task, lease_seconds=8)
        final = inst.switch('go')
        halt()
        self.committed(final, 2, 'a switch with a dead executor whose consumer still heartbeats')
        attempt = inst.attempt(task)
        self.assertEqual((attempt['outcome'] in ('released', 'fenced'), attempt['after_lease']), (True, True),
                         f'{RED}: the dead attempt must be released, and only after its lease expired: {attempt}')
        self.assert_on(task, 'go', 2, 'the task of the dead executor')
        self.assertEqual(inst.broker().call('HLEN', 'unacked'), 0, f'{RED}: the unacked copy was left behind')

    def test_a_renewed_lease_is_never_released_by_the_switch(self):
        inst = self.inst
        inst.set_env(ASSOZETA_RUNTIME_SWITCH_WINDOW_SECONDS='12')
        task = inst.push('celery', h.MAIL_TASK, args=h.mail_args(12))
        consumer, halt = inst.register_consumer('django', acknowledge=True)
        inst.running_attempt('django', consumer, task, lease_seconds=30)
        halt()  # the consumer's heartbeat stops; its surviving executor keeps renewing the lease
        renewing, stop = True, __import__('threading').Event()

        def renew():
            while not stop.is_set():
                inst.renew(task, 30)
                stop.wait(2)
        thread = __import__('threading').Thread(target=renew, daemon=True)
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(10)))
        final = inst.switch('go')
        stop.set()
        thread.join(10)
        self.assertEqual((final['status'], final['committed_generation'], final.get('error_code') in
                          ('window_exceeded', 'transfer_blocked')), ('failed', None, True),
                         f'{RED}: a live lease must keep the switch from committing')
        self.assertEqual(((inst.attempt(task) or {}).get('outcome'), inst.authority()[:2], inst.maintenance()),
                         ('running', ('django', 1), False), f'{RED}: the switch released a live attempt or kept maintenance')
        self.assertEqual([t for t in inst.transfers(final['id']) if t['task_id'] == task and t['delivered_at']], [],
                         f'{RED}: the task of a live attempt was transferred')
        inst.assert_mode(self, 'django', 'a switch refused by a live lease')
        self.assertTrue(renewing)

    # -- interruption ---------------------------------------------------------------------------------------------
    def test_kill_at_every_point_converges_to_the_uninterrupted_outcome(self):
        self.inst.rs('STEP_POINTS')
        for point in FORWARD_POINTS:
            with self.subTest(point=point):
                directory = tempfile.TemporaryDirectory(prefix='assozeta-p5-kill-')
                self.addCleanup(directory.cleanup)
                inst = h.P5Installation(Path(directory.name))
                inst.install()
                self.inst = inst
                items = self.seed_django_work()
                barrier = h.killer(point)
                try:
                    inst.switch('go', barrier=barrier)
                except h.SimulatedKill:
                    pass
                self.assertIn(point, barrier.seen, f'{RED}: the controller never reached {point}')
                for _ in range(3):
                    if not inst.recover():
                        break
                [record] = inst.runtime_records()
                self.committed(record, 2, f'recovery after a kill at {point}')
                self.assertEqual(inst.authority()[:2], ('go', 2))
                inst.assert_mode(self, 'go', f'recovery after a kill at {point}')
                self.assertEqual((inst.maintenance(), inst.oneoffs_running()), (False, []),
                                 f'{RED}: recovery after {point} left maintenance or a one-shot')
                for label, task_id in items.items():
                    self.assert_on(task_id, 'go', 2, f'kill at {point}: {label}')
                self.assertEqual(inst.recover(), [], f'{RED}: recovery after {point} is not idempotent')

    def test_real_kill_after_commit_before_the_proxy_opens_is_finished_by_runtime_recover(self):
        inst = self.inst
        items = self.seed_django_work()
        barriers = self.directory / 'barriers'
        barriers.mkdir()
        (barriers / 'hold.activate:started.all').write_text('')
        process = inst.popen_tool('switch', str(inst.root), str(inst.env_file), 'go',
                                  extra_env={'ASSOZETA_HANDOFF_BARRIER_DIR': str(barriers)})
        try:
            h.wait_for(lambda: list(barriers.glob('arrived.activate:started.*')) or process.poll() is not None,
                       'the controller to reach activate:started', timeout=300)
            self.assertIsNone(process.poll(), f'{RED}: switch exited before activate:started: '
                                              f'{process.communicate()[0][-800:] if process.poll() is not None else ""}')
            os.killpg(process.pid, signal.SIGKILL)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(30)
        (barriers / 'hold.activate:started.all').unlink()
        self.assertEqual(inst.authority()[:2], ('go', 2), f'{RED}: the commit must precede activate:started')
        self.assertTrue(inst.maintenance(), f'{RED}: the proxy must still be closed at activate:started')
        result = inst.cli('runtime-recover', check=False)
        self.assertEqual(result.returncode, 0, f'{RED}: runtime-recover after a post-commit kill: '
                                               + (result.stderr or result.stdout)[-800:])
        [record] = inst.runtime_records()
        self.committed(record, 2, 'runtime-recover after a post-commit kill')
        inst.assert_mode(self, 'go', 'runtime-recover after a post-commit kill')
        self.assertFalse(inst.maintenance(), f'{RED}: runtime-recover must open the proxy when the target is healthy')
        for label, task_id in items.items():
            self.assert_on(task_id, 'go', 2, f'real kill: {label}')

    # -- repeated, rapid and opposite requests (D12) --------------------------------------------------------------
    def test_rapid_alternating_requests_each_complete_once(self):
        inst = self.inst
        items = self.seed_django_work()
        outcome = {}

        def contend(point):
            if point == 'stage:settle' and 'contended' not in outcome:
                outcome['contended'] = [inst.submit(target, generation=1, actor=h.OTHER_OWNER)[0:2]
                                        for target in ('django', 'go')]
        for index, target in enumerate(('go', 'django', 'go', 'django')):
            final = inst.switch(target, barrier=contend if index == 0 else None)
            self.committed(final, index + 2, f'request {index + 1} ({target})')
            inst.assert_mode(self, target, f'request {index + 1}')
            for label, task_id in items.items():
                self.assert_on(task_id, target, index + 2, f'after request {index + 1}: {label}')
        for code, payload in outcome.get('contended', []):
            self.assertIn((code, payload.get('code')), ((200, None), (409, 'operation_in_progress')),
                          f'{RED}: a competing request during a running switch')
        self.assertEqual(len(inst.runtime_records()), 4 + sum(1 for code, _ in outcome.get('contended', []) if code == 200))
        self.assertEqual(inst.authority()[:2], ('django', 5))

    def test_switch_submitted_from_the_outgoing_api_completes_once(self):
        inst = self.inst
        self.seed_django_work()
        key = '0b5a1f3e-9d2c-4c6b-8e7f-1a2b3c4d5e6f'
        code, payload = inst.submit('go', key=key)
        self.assertEqual(code, 202, payload)
        operation = payload['operation']['id']
        replays = []

        def barrier(point):
            again = inst.submit('go', generation=1, key=key)
            cold = inst.status()
            replays.append((point, again[0], again[1].get('replayed'), (again[1].get('operation') or {}).get('id'),
                            (cold.get('operation') or {}).get('id')))
        final = inst.run(operation, barrier=barrier)
        self.committed(final, 2, 'the switch submitted from the outgoing API')
        for point, code, replayed, replay_id, cold_id in replays:
            self.assertEqual((code, replayed, replay_id, cold_id), (202, True, operation, operation),
                             f'{RED}: replaying the same request or reopening status at {point}')
        self.assertTrue(any(point == 'stage:target_preflight' for point, *_ in replays),
                        f'{RED}: the switch never passed the moment its own API was stopped')
        self.assertEqual(len(inst.runtime_records()), 1, f'{RED}: one request, one operation')
        self.assertEqual(inst.authority(), ('go', 2, operation))

    # -- delayed work across round trips (D17) --------------------------------------------------------------------
    def test_delayed_retry_survives_round_trips_and_an_aborted_switch_without_the_inactive_runtime(self):
        inst = self.inst
        later = h.future(days=40)
        retry_eta = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(microsecond=0)
        delayed = inst.push('celery', h.MAIL_TASK, args=h.mail_args(7), eta=later, priority=3)
        retried = inst.push('celery', h.MAIL_TASK, args=h.mail_args(8), eta=retry_eta, retries=2)

        def check(runtime, generation, label):
            inst.assert_mode(self, runtime, label)
            for task_id, eta, retries in ((delayed, later, 0), (retried, retry_eta, 2)):
                self.assert_on(task_id, runtime, generation, f'{label}: delayed')
                _, message = inst.broker_message(task_id)
                self.assertEqual((message['headers']['eta'], message['headers']['retries']),
                                 (eta.isoformat(), retries), f'{RED}: {label}: due time or retry count changed')
        self.committed(inst.switch('go'), 2, 'forward')
        check('go', 2, 'forward')
        inst.fail_oneoff('api')
        aborted = inst.switch('django')
        self.assertEqual(aborted['status'], 'failed', f'{RED}: the aborted reverse switch')
        check('go', 2, 'after an aborted reverse switch')
        inst.fail_oneoff()
        self.committed(inst.switch('django'), 3, 'reverse')
        check('django', 3, 'reverse')
        self.committed(inst.switch('go'), 4, 'second forward')
        check('go', 4, 'second round trip')

    # -- operator path and a cold client --------------------------------------------------------------------------
    def test_operator_switch_leaves_only_go_processes_and_a_cold_client_reaches_go(self):
        inst = self.inst
        result = inst.cli('runtime-switch', 'go', check=False)
        self.assertEqual(result.returncode, 0, f'{RED}: ./bin/assozeta runtime-switch go exited {result.returncode}: '
                                               + (result.stderr or result.stdout)[-800:])
        inst.assert_mode(self, 'go', 'the operator switch')
        output = inst.cli('runtime-status').stdout
        reported = json.loads(output[output.index('{'):output.rindex('}') + 1])
        self.assertEqual((reported.get('effective_runtime'), reported.get('intended_runtime')), ('go', 'go'),
                         f'{RED}: runtime-status after the operator switch')
        with open(self.directory / 'proxy.log', 'w') as log, evidence_proxy(inst, set(inst.running()), log) as request:
            response = request('/api/instance/status')
        self.assertEqual((response.status, response.headers.get('x-fixture-runtime')), (200, 'go'),
                         f'{RED}: a cold client after the switch is not served by Go')


if __name__ == '__main__':
    unittest.main()
