"""Parent-executed composition for the existing saved-message UI, v1 or v2.

Importing has no effects. Parent supplies a fresh prepared mutation fixture,
measured client IP, actual service/process guard and existing build/browser tools.
This module launches no services at import and never builds or installs anything.
"""
import base64
import copy
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path

from TESTS.adapters.django import message_mutations_browser as browser
from TESTS.adapters.django import message_reads_browser_runtime as shared


def observed_traffic(private_rows, events, django_events, *, version, consumer='mutations'):
    """Bind full private HTTP bytes to complete independently observed windows.

    Neither the browser payload nor a response backend header selects evidence.
    Multiplicity matters, including all noncorrelated bootstrap/coexistence HTTP.
    """
    shared.require(version in ('v1', 'v2'), 'mutation-version')
    shared.require(consumer in ('mutations', 'mutation-denials'), 'unknown-mutation-traffic-consumer')
    if consumer == 'mutation-denials':
        # Revalidate every event against the real policy, including uncorrelated
        # traffic that would otherwise be skipped below. No coexistence window.
        for event in events:
            route = shared.route_target('/api' + event['path'], event['method'],
                version=version, artifacts={}, consumer=consumer)
            shared.validate_correlation(event['cid'], route, consumer)
        shared.require(len({event['cid'] for event in events}) == len(events),
                       'denial-duplicate-correlation')
    shared.verify_proxy_asgi_window(events, django_events)
    selected = [row for row in events if row.get('cid')]
    shared.require(len(selected) == len(private_rows), 'mutation-private-traffic-count')
    result = []
    backend = 'django-v1' if version == 'v1' else 'go-v2'
    for private, event in zip(private_rows, selected, strict=True):
        shared.require(private['complete'] is True and isinstance(private['body'], bytes) and
            shared.observed_body(private['wire_body'], private['response_headers']) == private['body'] and
            private['headers'] == dict(private['response_headers']) and
            (private['request_body'] is None or isinstance(private['request_body'], bytes)) and
            {key: private[key] for key in event} == event and
            event['backend'] == event['intended_backend'] == backend and event['fallback'] is False,
            'mutation-private-proxy-binding')
        shared.validate_correlation(event['cid'], {'path': event['path']}, consumer)
        result.append({key: copy.deepcopy(private[key]) for key in
            ('cid', 'method', 'path', 'status', 'complete', 'body', 'wire_body', 'response_headers', 'request_body', 'headers')} |
            {'proxy': {key: event[key] for key in ('cid', 'method', 'path', 'status', 'backend', 'fallback')},
             'django_events': [row for row in django_events if row['cid'] == event['cid']]})
    return result


def run_version(*, version, args, work, private_log, environment, identities, apps,
                raw, fixture, observer, django_port, go_port, process_guard, remote_addr,
                report):
    """Run inside the parent's private production-Python process.

    args: existing read-runtime inputs ui_dir, build_observation, node,
    playwright_module, go_binary, image_codec, go_build_record, timeout (30..900).
    work: existing private directory; work/version must be fresh. observer must be
    CompleteAsgiObserver around the actual Django application. process_guard must
    identify the actual selected service, and remote_addr must be independently
    measured for this proxy transport. Parent owns fixture/services/output cleanup
    and separate real HTTP denial evidence. No HTTP denials are synthesized here.
    """
    from TESTS.adapters.django.production_runtime import require_production_python
    from TESTS.adapters.django.message_mutations import require_private_database
    require_production_python()
    require_private_database()
    shared.require(__debug__ and version in ('v1', 'v2') and callable(process_guard) and
                   30 <= args.timeout <= 900, 'mutation-runtime-input')
    ipaddress.ip_address(remote_addr)
    shared.require(isinstance(observer, shared.CompleteAsgiObserver), 'complete-asgi-observer-required')
    guard = copy.deepcopy(process_guard())
    shared.require(isinstance(guard, dict) and guard.get('running') is True and
                   type(guard.get('pid')) is int and guard['pid'] > 0 and
                   guard.get('instance') is not None, 'mutation-live-process')
    before = browser.capture_provenance(args.ui_dir)
    build = shared.validate_build_record(args.build_observation, before)
    native_build = shared.validate_go_build_record(args.go_build_record, args.go_binary, args.image_codec)
    modules = shared.module_fingerprints(args.playwright_module)
    directory = Path(work) / version
    directory.mkdir(mode=0o700)
    shared.require(directory.stat().st_mode & 0o777 == 0o700, 'private-work-mode')
    previous = shared.durable_snapshot(apps, raw)
    shared.save_snapshot(directory / 'before-login.json', previous)
    previous_time = datetime.now(timezone.utc)
    verifier = browser.CheckpointVerifier(version=version, contrasts=fixture['contrasts'],
        initial_models=previous['models'], initial_cache=previous['cache'])
    journal = shared.ProxyJournal()
    offsets = {'events': 0, 'private': 0, 'asgi': observer.cursor()}
    websocket_start, websocket_end = observer.websocket_started, observer.websocket_completed
    attempt = {'version': version, 'checkpoints_verified': 0, 'browser_acceptance': False}
    report.setdefault('attempts', []).append(attempt)

    def snapshot(payload):
        nonlocal previous_time
        with journal.checkpoint():
            observer.wait_idle(include_websockets=payload['action'] == 'closed')
            shared.require(process_guard() == guard, 'mutation-process-changed')
            state = shared.durable_snapshot(apps, raw)
            finished = datetime.now(timezone.utc)
            events = journal.snapshot()
            django_events = observer.delta(offsets['asgi'])
            private = journal.private_traffic[offsets['private']:]
            traffic = observed_traffic(private, events[offsets['events']:], django_events, version=version)
            name = payload['actor'] + '-' + payload['action']
            shared.save_snapshot(directory / (name + '.json'), state)
            # Raw HTTP bodies, credentials and headers remain only in private files.
            encoded = [{**row, 'body': base64.b64encode(row['body']).decode(),
                'wire_body': base64.b64encode(row['wire_body']).decode(),
                'request_body': None if row['request_body'] is None else
                    base64.b64encode(row['request_body']).decode()} for row in traffic]
            shared.private_write(directory / (name + '-traffic.json'), json.dumps(encoded))
            ack = verifier.observe(payload, models=state['models'], cache=state['cache'],
                traffic=traffic, started=previous_time.timestamp(), finished=finished.timestamp(),
                remote_addr=remote_addr, process_before=guard, process_after=copy.deepcopy(process_guard()))
            shared.require(ack == {'success': True, 'checkpoint': len(checkpoints.states) + 1},
                           'mutation-verifier-ack')
            offsets.update(events=len(events), private=len(journal.private_traffic),
                           asgi=offsets['asgi'] + len(django_events))
            previous_time = finished
            attempt['checkpoints_verified'] = verifier.verified
            attempt['last_checkpoint'] = {'actor': payload['actor'], 'action': payload['action']}
            return state

    checkpoints = shared.Checkpoints(snapshot, consumer='mutations')
    try:
        with shared.owned_http_server(shared.proxy_handler(ui=args.ui_dir,
                artifacts=before['artifact_sha256'], version=version, django_port=django_port,
                go_port=go_port, journal=journal, consumer='mutations'), journal=journal) as origin:
            with shared.transport_probe() as (probe_origin, counts):
                child = shared.run_node(node=args.node, module=args.playwright_module, work=directory,
                    private_log=private_log, environment=environment,
                    input_data=browser.browser_input(version=version, origin=origin,
                        identities=identities, provenance=before), checkpoint=checkpoints,
                    probe_origin=probe_origin, timeout=args.timeout, consumer='mutations')
        events = journal.snapshot()
        observer.wait_idle(include_websockets=True)
        shared.require(offsets['events'] == len(events) and offsets['private'] == len(journal.private_traffic)
            and not observer.delta(offsets['asgi']), 'mutation-uncheckpointed-final-traffic')
        final, final_proof = shared.observe_closed_state(observer,
            lambda: shared.durable_snapshot(apps, raw), checkpoints.states[-1], fixture['contrasts'])
        shared.save_snapshot(directory / 'final-after-all-contexts.json', final)
        shared.require(process_guard() == guard, 'mutation-process-changed')
        shared.require(observer.websocket_started - websocket_start ==
            observer.websocket_completed - websocket_end ==
            len([row for row in events if row['backend'] == 'django-websocket']),
            'websocket-lifetime-observation-mismatch')
        shared.verify_transport_and_cookies(child, counts, events)
        after = browser.capture_provenance(args.ui_dir)
        shared.require(modules == shared.module_fingerprints(args.playwright_module), 'playwright-drift')
        shared.require(native_build == shared.validate_go_build_record(
            args.go_build_record, args.go_binary, args.image_codec), 'native-build-drift')
        shared.require(build == shared.validate_build_record(args.build_observation, after), 'build-record-drift')
        acceptance = verifier.finish(child['receipt'], provenance_before=before,
            provenance_after=after, build_observation=build)
        shared.private_write(directory / 'receipt.json', json.dumps(child))
        attempt['browser_acceptance'] = True
        return {'version': version, 'acceptance': acceptance,
            'context_close_proofs': checkpoints.closed, 'final_state_proof': final_proof,
            'proxy_observations': events, 'browser_binary_sha256': child['browser_binary']['sha256'],
            'transport_probe': child['transport'], 'real_cookie_forwarding': True,
            'native_build': native_build, 'playwright_version': modules['version'],
            'playwright_files_sha256': shared.json_digest(modules['files']),
            'proxy_cleanup_verified': True, 'browser_child_reaped': True,
            'provenance_before': before, 'provenance_after': after,
            'fixture_cleanup_owner': 'parent', 'http_denials_verified': False,
            'complete_message_family_parity': False}
    finally:
        # The child runner reaps browser descendants and unlinks its credential
        # input even on failure. Parent owns the private snapshot directory.
        with journal.lock:
            attempt['proxy_failure_codes'] = list(journal.failures)
            journal.private_traffic.clear()
