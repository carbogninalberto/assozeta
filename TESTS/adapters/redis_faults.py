"""Real command-denial faults, restricted to owned disposable identity Redis.

The ACL applies to existing Django and Go connections immediately. It denies a
command for the default user across this private instance, not one key or call.
No application data or ACL password hashes are included in the public receipt.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import re
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

PASSWORD = 'synthetic-identity-only'
COMMANDS = frozenset(('GET', 'SET', 'DEL'))
_active = set()
_active_lock = threading.Lock()


def _acl(client):
    # Avoid redis-py's ACL GETUSER parser: it splits command/category rules and
    # loses their original ordering. Keep the exact command expression private.
    raw = client.execute_command('ACL', 'GETUSER', 'default')
    return dict(zip(raw[::2], raw[1::2]))


def _denials(client, command):
    counts = {}
    for entry in client.acl_log(128):
        if (entry['username'] == 'default' and entry['reason'] == 'command'
                and entry['object'].lower() == command.lower()):
            identity = entry.get('entry-id', entry['client-info'].get('id'))
            counts[str(identity)] = int(entry['count'])
    return counts


def _address_and_command(address, command):
    command = command.upper()
    if command not in COMMANDS:
        raise ValueError('Only GET, SET and DEL fixture faults are supported')
    host, separator, port_text = str(address).rpartition(':')
    if host != '127.0.0.1' or not separator or not port_text.isdecimal():
        raise ValueError('Fault injection requires the explicit loopback identity fixture')
    port = int(port_text)
    if not 1024 <= port <= 65535:
        raise ValueError('Fault injection requires a private unprivileged port')
    return host, port, command


@contextmanager
def _deny_acl(client, address, command):
    """Shared ACL mutation after a public context has verified fixture ownership."""
    import redis

    with _active_lock:
        if address in _active:
            raise RuntimeError('Overlapping fault contexts on one Redis fixture are forbidden')
        _active.add(address)
    try:
        original = _acl(client)
        if original.get('selectors'):
            raise ValueError('Fixture ACL selectors could bypass a command denial')
        arguments = ['fixture-probe', 'fixture-value'] if command == 'SET' else ['fixture-probe']
        assert client.execute_command('ACL', 'DRYRUN', 'default', command, *arguments) == 'OK', \
            'Fixture command was already forbidden'
        before = _denials(client, command)
        receipt = {'command': command, 'database': 1,
                   'scope': 'Persistent command denial for default user on the owned instance; no key or fail-once selection',
                   'fault_installed': False, 'observed_denials': 0, 'acl_restored': False}
        started = time.monotonic()
        try:
            client.execute_command('ACL', 'SETUSER', 'default', '-' + command.lower())
            receipt['fault_installed'] = True
            try:
                dry_run = client.execute_command('ACL', 'DRYRUN', 'default', command, *arguments)
            except redis.ResponseError:
                pass
            else:
                assert dry_run != 'OK', 'Redis did not install the requested command denial'
            yield receipt
        finally:
            try:
                after = _denials(client, command)
                receipt['observed_denials'] = sum(max(0, count - before.get(key, 0))
                                                  for key, count in after.items())
            finally:
                # Do not reset passwords, keys, channels, flags or unrelated
                # commands. Redis executes this ACL update atomically.
                client.execute_command('ACL', 'SETUSER', 'default', '-@all',
                                       *original['commands'].split())
                assert _acl(client) == original, 'Private fixture ACL was not restored exactly'
                receipt['acl_restored'] = True
                receipt['elapsed_seconds'] = round(time.monotonic() - started, 6)
        assert receipt['observed_denials'] > 0, 'No actual Redis command denial was observed'
    finally:
        with _active_lock:
            _active.remove(address)


@contextmanager
def deny_redis_command(address, work, command):
    """Deny GET/SET/DEL on the exact private native fixture; restore its ACL.

    The directory, loopback binding, unprivileged port, synthetic password and
    disabled persistence must match the original native identity fixture.
    Serialize faults; application processes may remain connected. The caller
    must exercise the denied command before leaving this context.
    """
    import redis

    host, port, command = _address_and_command(address, command)
    work = Path(work).resolve(strict=True)
    if not work.is_dir() or not work.name.startswith(('az-accounts-', 'az-redis-faults-', 'az-imp-cache-')):
        raise ValueError('Fault injection requires an owned identity temporary directory')
    client = redis.Redis(host=host, port=port, db=1, password=PASSWORD,
                         socket_connect_timeout=2, socket_timeout=2, decode_responses=True)
    try:
        config = client.config_get('dir', 'bind', 'save', 'appendonly')
        if (Path(config['dir']).resolve() != work or config['bind'].split() != ['127.0.0.1']
                or config['save'] != '' or config['appendonly'] != 'no'):
            raise ValueError('Redis ownership, loopback or nonpersistent fixture configuration differs')
        with _deny_acl(client, address, command) as receipt:
            yield receipt
    finally:
        client.close()


CONTAINER_LABEL = 'org.assozeta.category-image-proof'


def _docker(*arguments, check=True):
    try:
        result = subprocess.run(['docker', *arguments], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Owned Redis Docker operation timed out') from None
    if check and result.returncode:
        raise RuntimeError('Owned Redis Docker operation failed')
    return result


def _container_ownership(address, container_id, owner_label, owner_value, network_id):
    if (not re.fullmatch(r'[0-9a-f]{64}', container_id)
            or not re.fullmatch(r'[0-9a-f]{64}', network_id)
            or owner_label != CONTAINER_LABEL
            or not re.fullmatch(r'az-category-image-[0-9a-f]{32}', owner_value)):
        raise ValueError('Exact owned image-fixture container and network identities required')
    info = json.loads(_docker('container', 'inspect', container_id).stdout)[0]
    if (info['Id'] != container_id or not info['State']['Running']
            or info['Name'] != '/' + owner_value + '-redis'
            or (info['Config'].get('Labels') or {}).get(owner_label) != owner_value):
        raise ValueError('Redis container ownership differs')
    network = json.loads(_docker('network', 'inspect', network_id).stdout)[0]
    if (network['Id'] != network_id or network['Name'] != owner_value + '-net'
            or (network.get('Labels') or {}).get(owner_label) != owner_value
            or network['Driver'] != 'bridge' or network['Scope'] != 'local'
            or container_id not in (network.get('Containers') or {})):
        raise ValueError('Redis network ownership differs')
    owned_networks = _docker('network', 'ls', '--quiet', '--no-trunc', '--filter',
                            'label=' + owner_label + '=' + owner_value).stdout.split()
    if owned_networks != [network_id]:
        raise ValueError('Fixture must own exactly one Docker network')
    attached = info['NetworkSettings']['Networks']
    if (len(attached) != 1 or next(iter(attached.values()))['NetworkID'] != network_id
            or info['HostConfig']['NetworkMode'] not in (network_id, network['Name'])):
        raise ValueError('Redis must use only its owned private bridge network')
    ports = info['NetworkSettings']['Ports']
    requested = info['HostConfig']['PortBindings']
    if (set(ports) != {'6379/tcp'} or set(requested) != {'6379/tcp'}
            or ports['6379/tcp'] != [{'HostIp': '127.0.0.1', 'HostPort': address.rsplit(':', 1)[1]}]
            or len(requested['6379/tcp']) != 1
            or requested['6379/tcp'][0]['HostIp'] != '127.0.0.1'
            or requested['6379/tcp'][0]['HostPort'] not in ('', '0')):
        raise ValueError('Redis requires exactly one ephemeral loopback-only published port')
    if ('/data' not in (info['HostConfig'].get('Tmpfs') or {})
            or any(mount['Type'] != 'tmpfs' for mount in info.get('Mounts', []))):
        raise ValueError('Redis requires /data tmpfs and no persistent mounts')
    return {'container_id': container_id, 'network_id': network_id,
            'ownership_verified': True, 'ephemeral_loopback_port': True, 'data_tmpfs': True}


@contextmanager
def deny_container_redis_command(address, command, *, container_id, owner_label, owner_value, network_id):
    """Deny one command only after verifying the exact disposable image fixture.

    Native work-directory checks are not bypassed. This separate entry point
    instead binds immutable Docker identities, ownership, networking and tmpfs,
    then verifies the live Redis password and nonpersistent /data configuration.
    """
    import redis

    host, port, command = _address_and_command(address, command)
    ownership = _container_ownership(address, container_id, owner_label, owner_value, network_id)
    client = redis.Redis(host=host, port=port, db=1, password=PASSWORD,
                         socket_connect_timeout=2, socket_timeout=2, decode_responses=True)
    try:
        config = client.config_get('dir', 'save', 'appendonly', 'requirepass')
        if (config['dir'] != '/data' or config['save'] != ''
                or config['appendonly'] != 'no' or config['requirepass'] != PASSWORD):
            raise ValueError('Redis container persistence, directory or synthetic password differs')
        with _deny_acl(client, address, command) as receipt:
            receipt['container_fixture'] = ownership
            yield receipt
    finally:
        client.close()


def controls(output):
    """Exercise real Redis errors and restoration using two established clients."""
    output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'cleanup_verified': False, 'cases': [],
              'scope': 'Owned Redis ACL helper controls; not application HTTP/SQL acceptance',
              'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    receipt_path = output / 'results.json'
    receipt_path.write_text(json.dumps(report, indent=2) + '\n')
    try:
        _controls(report)
    except BaseException as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        report['source_stable'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == report['source_sha256']
        report['private_directory_removed'] = bool(report.get('private_directory')
            and not Path(report['private_directory']).exists())
        report['cleanup_verified'] = bool(report.get('private_process_stopped') and report['private_directory_removed'])
        report['success'] = bool(report.get('checks_passed') and report['cleanup_verified'] and report['source_stable'])
        receipt_path.write_text(json.dumps(report, indent=2) + '\n')
    assert report['success'], 'Redis fault controls incomplete'
    return report


def _controls(report):
    import redis

    binary = shutil.which('redis-server')
    if not binary:
        raise RuntimeError('Real redis-server is required; no skipped controls')
    with tempfile.TemporaryDirectory(prefix='az-redis-faults-', dir='/tmp') as directory:
        work = Path(directory)
        report['private_directory'] = str(work)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        address = f'127.0.0.1:{port}'
        with (work / 'redis.log').open('w') as log:
            process = subprocess.Popen([binary, '--bind', '127.0.0.1', '--port', str(port),
                '--save', '', '--appendonly', 'no', '--requirepass', PASSWORD, '--dir', str(work)],
                stdout=log, stderr=subprocess.STDOUT)
            clients = [redis.Redis(host='127.0.0.1', port=port, db=1, password=PASSWORD,
                                  socket_timeout=2, socket_connect_timeout=2) for _ in range(2)]
            try:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        for client in clients:
                            client.ping()
                        break
                    except redis.ConnectionError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError('Owned Redis did not start') from None
                        time.sleep(.025)
                owner = clients[0]
                # Verify restoration preserves an unrelated existing restriction.
                owner.execute_command('ACL', 'SETUSER', 'default', '-incr')
                for command in sorted(COMMANDS):
                    key = ':1:impersonation:v1:synthetic-helper-control'
                    owner.set(key, 'unchanged', ex=60)
                    with deny_redis_command(address, work, command) as measured:
                        for client in clients:
                            try:
                                client.execute_command(command, key, *(['changed'] if command == 'SET' else []))
                            except redis.exceptions.NoPermissionError:
                                pass
                            else:
                                raise AssertionError('Actual Redis client bypassed command denial')
                    assert measured['observed_denials'] >= 2 and measured['acl_restored']
                    assert owner.get(key) == b'unchanged' and 0 < owner.ttl(key) <= 60
                    try:
                        owner.execute_command('INCR', 'unrelated-rule')
                    except redis.exceptions.NoPermissionError:
                        pass
                    else:
                        raise AssertionError('Helper changed unrelated original ACL permissions')
                    assert owner.set(key, 'recovered', ex=60)
                    assert all(client.get(key) == b'recovered' for client in clients)
                    assert owner.delete(key) == 1
                    report['cases'].append({**measured, 'two_existing_clients_rejected': True,
                                            'value_and_ttl_preserved': True, 'unrelated_acl_preserved': True,
                                            'command_recovery_verified': True})
                class ExpectedFailure(Exception):
                    pass
                try:
                    with deny_redis_command(address, work, 'GET') as measured:
                        try:
                            owner.get('missing')
                        except redis.exceptions.NoPermissionError:
                            pass
                        raise ExpectedFailure()
                except ExpectedFailure:
                    pass
                assert measured['acl_restored'] and owner.get('missing') is None
                report['cases'].append({**measured, 'raised_body_cleanup_verified': True})
                for bad_address, bad_work, bad_command in ((address, work, 'FLUSHDB'),
                    ('192.0.2.1:6379', work, 'GET'), (address, work / 'not-owned', 'GET')):
                    try:
                        with deny_redis_command(bad_address, bad_work, bad_command):
                            raise AssertionError('Unsafe fixture accepted')
                    except (ValueError, FileNotFoundError):
                        pass
                report['cases'].append({'unsafe_fixture_inputs_rejected': True})
                with tempfile.TemporaryDirectory(prefix='az-redis-faults-', dir='/tmp') as other:
                    try:
                        with deny_redis_command(address, Path(other), 'GET'):
                            raise AssertionError('Different private fixture directory accepted')
                    except ValueError:
                        pass
                assert owner.get('missing') is None
                report['cases'].append({'different_existing_fixture_rejected': True,
                                        'original_acl_still_allows_get': True})
                owner.execute_command('ACL', 'SETUSER', 'default', '+incr')
                assert owner.dbsize() == 0, 'Helper controls changed unrelated database state'
                report['checks_passed'] = True
            finally:
                for client in clients:
                    client.close()
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                report['private_process_stopped'] = process.poll() is not None


@contextmanager
def _owned_container_control(image, resources, *, tmpfs=True, fixed_port=False, labeled=True, redis_dir="/data"):
    """Create only synthetic control resources; retain IDs for verified cleanup."""
    identifier = 'az-category-image-' + uuid4().hex
    network_id = _docker('network', 'create', '--label', CONTAINER_LABEL + '=' + identifier,
                         identifier + '-net').stdout.strip()
    resource = {'owner_value': identifier, 'network_id': network_id, 'container_id': None,
                'container_removed': False, 'network_removed': False, 'anonymous_volumes': [], 'volumes_removed': False}
    resources.append(resource)
    try:
        port = ''
        if fixed_port:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = str(sock.getsockname()[1])
        container_id = _docker('run', '--detach', '--pull', 'never', '--name', identifier + '-redis',
            *(['--label', CONTAINER_LABEL + '=' + identifier] if labeled else []),
            '--network', network_id, '--publish', '127.0.0.1:' + port + ':6379',
            *(['--tmpfs', '/data'] if tmpfs else []), image,
            'redis-server', '--save', '', '--appendonly', 'no', '--requirepass', PASSWORD, '--dir', redis_dir).stdout.strip()
        resource['container_id'] = container_id
        info = json.loads(_docker('container', 'inspect', container_id).stdout)[0]
        resource['anonymous_volumes'] = [mount['Name'] for mount in info.get('Mounts', []) if mount['Type'] == 'volume']
        host_port = info['NetworkSettings']['Ports']['6379/tcp'][0]['HostPort']
        import redis
        client = redis.Redis(host='127.0.0.1', port=int(host_port), db=1, password=PASSWORD,
                             socket_connect_timeout=2, socket_timeout=2)
        try:
            deadline = time.monotonic() + 10
            while True:
                try:
                    client.ping()
                    break
                except redis.ConnectionError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Owned container Redis did not start') from None
                    time.sleep(.025)
            yield ('127.0.0.1:' + host_port, client, dict(container_id=container_id,
                owner_label=CONTAINER_LABEL, owner_value=identifier, network_id=network_id))
        finally:
            client.close()
    finally:
        try:
            if not resource['container_id']:
                found = _docker('container', 'inspect', identifier + '-redis', check=False)
                if found.returncode == 0:
                    info = json.loads(found.stdout)[0]
                    assert info['Name'] == '/' + identifier + '-redis'
                    resource['container_id'] = info['Id']
                    resource['anonymous_volumes'] = [mount['Name'] for mount in info.get('Mounts', []) if mount['Type'] == 'volume']
            if resource['container_id']:
                _docker('rm', '--force', '--volumes', resource['container_id'])
                resource['container_removed'] = _docker('container', 'inspect', resource['container_id'], check=False).returncode != 0
            else:
                resource['container_removed'] = True
            resource['volumes_removed'] = all(_docker('volume', 'inspect', volume, check=False).returncode != 0
                                              for volume in resource['anonymous_volumes'])
        finally:
            _docker('network', 'rm', network_id)
            resource['network_removed'] = _docker('network', 'inspect', network_id, check=False).returncode != 0


def container_controls(output, image='redis:7-alpine'):
    """Real Docker/Redis positive, rejection and restoration controls, no app HTTP."""
    import redis

    output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'cleanup_verified': False, 'cases': [], 'resources': [],
              'scope': 'Owned container Redis ACL controls; no application HTTP claim',
              'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    receipt_path = output / 'results.json'
    receipt_path.write_text(json.dumps(report, indent=2) + '\n')
    try:
        image_info = json.loads(_docker('image', 'inspect', image).stdout)[0]
        image_id = image_info['Id']
        assert re.fullmatch(r'sha256:[0-9a-f]{64}', image_id)
        report['image'] = {'immutable_id': image_id, 'platform': image_info['Os'] + '/' + image_info['Architecture']}
        with _owned_container_control(image_id, report['resources']) as (address, client, ownership):
            second = redis.Redis(host='127.0.0.1', port=int(address.rsplit(':', 1)[1]), db=1,
                                 password=PASSWORD, socket_connect_timeout=2, socket_timeout=2)
            second.ping()
            try:
                client.execute_command('ACL', 'SETUSER', 'default', '-incr')
                original = _acl(client)
                key = ':1:impersonation:v1:container-control'
                for command in sorted(COMMANDS):
                    client.set(key, b'unchanged', ex=60)
                    with deny_container_redis_command(address, command, **ownership) as measured:
                        for connection in (client, second):
                            try:
                                connection.execute_command(command, key, *(['changed'] if command == 'SET' else []))
                            except redis.exceptions.NoPermissionError:
                                pass
                            else:
                                raise AssertionError('Container command denial was bypassed')
                    assert measured['observed_denials'] >= 2 and measured['acl_restored']
                    assert _acl(client) == original
                    assert client.get(key) == b'unchanged' and 0 < client.ttl(key) <= 60
                    assert client.set(key, 'recovered') and second.get(key) == b'recovered'
                    assert client.delete(key) == 1
                    report['cases'].append({**measured, 'two_existing_clients_rejected': True,
                        'value_and_ttl_preserved': True, 'unrelated_acl_preserved': True,
                        'command_recovery_verified': True})
                class ExpectedFailure(Exception):
                    pass
                try:
                    with deny_container_redis_command(address, 'GET', **ownership) as measured:
                        try:
                            second.get('missing')
                        except redis.exceptions.NoPermissionError:
                            pass
                        raise ExpectedFailure()
                except ExpectedFailure:
                    pass
                assert measured['acl_restored'] and _acl(client) == original
                report['cases'].append({**measured, 'raised_body_cleanup_verified': True})

                def rejected(name, candidate_address=address, candidate_ownership=None):
                    acl_before_rejection = _acl(client)
                    try:
                        with deny_container_redis_command(candidate_address, 'GET',
                                **(ownership if candidate_ownership is None else candidate_ownership)):
                            raise AssertionError('Unsafe container fixture accepted')
                    except (ValueError, redis.AuthenticationError):
                        pass
                    assert _acl(client) == acl_before_rejection, 'Rejected ownership check changed ACL'
                    report['cases'].append({'name': name, 'rejected_before_acl_mutation': True})

                for name, changes in (
                    ('short-container-id', {'container_id': ownership['container_id'][:12]}),
                    ('short-network-id', {'network_id': ownership['network_id'][:12]}),
                    ('wrong-owner-label', {'owner_label': 'unrelated'}),
                    ('wrong-owner-value', {'owner_value': 'az-category-image-' + uuid4().hex}),
                ):
                    rejected(name, candidate_ownership={**ownership, **changes})
                with _owned_container_control(image_id, report['resources']) as (_other_address, _other_client, other):
                    rejected('different-full-container-id', candidate_ownership={**ownership, 'container_id': other['container_id']})
                    rejected('different-full-network-id', candidate_ownership={**ownership, 'network_id': other['network_id']})
                rejected('wrong-loopback-port', candidate_address='127.0.0.1:' + ('12345' if not address.endswith(':12345') else '12346'))
                _docker('network', 'connect', 'bridge', ownership['container_id'])
                try:
                    rejected('additional-shared-network')
                finally:
                    _docker('network', 'disconnect', 'bridge', ownership['container_id'])
                extra = _docker('network', 'create', '--label', CONTAINER_LABEL + '=' + ownership['owner_value'],
                                ownership['owner_value'] + '-unexpected').stdout.strip()
                try:
                    rejected('multiple-owner-networks')
                finally:
                    _docker('network', 'rm', extra)
                for setting, changed, restored in (('save', '3600 1', ''), ('appendonly', 'yes', 'no'),
                                                   ('requirepass', 'synthetic-wrong-password', PASSWORD)):
                    client.config_set(setting, changed)
                    try:
                        rejected('live-' + setting + '-mismatch')
                    finally:
                        client.config_set(setting, restored)
                assert client.dbsize() == 0
            finally:
                second.close()
        for name, options in (('persistent-data-volume', {'tmpfs': False}),
                              ('fixed-published-port', {'fixed_port': True}),
                              ('unlabeled-container', {'labeled': False}),
                              ('wrong-data-directory', {'redis_dir': '/tmp'})):
            with _owned_container_control(image_id, report['resources'], **options) as (address, client, ownership):
                before = _acl(client)
                try:
                    with deny_container_redis_command(address, 'GET', **ownership):
                        raise AssertionError('Unsafe container accepted')
                except ValueError:
                    pass
                assert _acl(client) == before
                report['cases'].append({'name': name, 'rejected_before_acl_mutation': True})
        report['checks_passed'] = True
    except BaseException as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        report['source_stable'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == report['source_sha256']
        report['cleanup_verified'] = bool(report['resources']) and all(
            item['container_removed'] and item['network_removed'] and item['volumes_removed'] for item in report['resources'])
        report['success'] = bool(report.get('checks_passed') and report['source_stable'] and report['cleanup_verified'])
        receipt_path.write_text(json.dumps(report, indent=2) + '\n')
    assert report['success'], 'Container Redis controls incomplete'
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--container-controls', action='store_true')
    parser.add_argument('--redis-image', default='redis:7-alpine')
    args = parser.parse_args()
    result = container_controls(args.output, args.redis_image) if args.container_controls else controls(args.output)
    print(json.dumps({'success': result['success'], 'cases': len(result['cases']),
                      'cleanup_verified': result['cleanup_verified']}))
