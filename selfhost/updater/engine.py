"""Execute the fixed lifecycle command and verify the resulting running services."""
import json
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import traceback
from uuid import UUID

from common import compose_command, compose_environment, read_env, write_json, update_eligibility_reason
from journal import now
from release_catalog import fetch_release, image_version, version_tuple, ReleaseError

STAGES = {'checking', 'backup', 'downloading', 'migrating', 'restarting', 'health_check'}


class VerificationError(ReleaseError):
    """Safe public explanation; underlying exceptions belong only in private logs."""


class Engine:
    def __init__(self, root, env_file, journal, resolver=fetch_release):
        self.root = Path(root).resolve()
        self.env_file = Path(env_file).resolve()
        self.journal = journal
        self.resolver = resolver

    def launch_command(self, operation):
        return [sys.executable, str(Path(__file__).with_name('bootstrap.py')),
                str(self.root), str(self.env_file), image_version(operation['tag'])]

    def execute(self, operation):
        operation_id = operation['id']
        stage = 'checking'
        log = self.root / '.updater' / 'logs' / f'{operation_id}.log'
        self.journal.update(operation_id, status='running', stage=stage)
        try:
            if self.journal.requiring_recovery() or (self.root / '.updater/pending-distribution').exists():
                raise ReleaseError('Un aggiornamento richiede una verifica di ripristino prima di un nuovo tentativo.')
            release = self.resolver(operation['release_id'])
            values = read_env(self.env_file)
            current = version_tuple(values.get('ASSOZETA_VERSION'))
            if release['tag'] != operation['tag'] or not release['artifacts_ready']:
                raise ReleaseError('La release selezionata non è più disponibile come distribuzione completa.')
            if current is None:
                raise ReleaseError('La versione configurata non è una release stabile valida.')
            if version_tuple(operation['source_version']) != current:
                raise ReleaseError(f'Versione di partenza non coerente: richiesta {image_version(operation["source_version"])}, '
                                   f'configurata {image_version(values["ASSOZETA_VERSION"])}. Verifica l’installazione prima di riprovare.')
            if version_tuple(release['tag']) <= current:
                raise ReleaseError('La release di destinazione deve essere successiva alla versione configurata.')
            reason = update_eligibility_reason(values)
            if reason:
                raise ReleaseError(reason)
            log.parent.mkdir(parents=True, exist_ok=True)
            environment = {**os.environ, 'ASSOZETA_ENV_FILE': str(self.env_file), 'ASSOZETA_RUNNER_ACTIVE': '1',
                           'ASSOZETA_EXPECTED_SOURCE': operation['source_version'], 'ASSOZETA_OPERATION_ID': operation_id}
            # Only a canonical validated version reaches argv. Never execute client commands.
            with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as output:
                os.fchmod(output.fileno(), 0o600)
                with subprocess.Popen(
                    self.launch_command(operation),
                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                ) as process:
                    for line in process.stdout:
                        output.write(line)
                        output.flush()
                        if line.startswith('@@ASSOZETA_STAGE '):
                            candidate = line.strip().split(' ', 1)[1]
                            if candidate in STAGES:
                                stage = candidate
                                self.journal.update(operation_id, stage=stage)
                    if process.wait() != 0:
                        raise RuntimeError('Il comando di aggiornamento non è terminato correttamente.')
            stage = 'health_check'
            self.journal.update(operation_id, stage=stage)
            verification = self.require_verification(operation)
            self.journal.update(operation_id, status='succeeded', stage='completed', verified_at=verification['verified_at'])
            try:
                self.refresh_runner()
            except (OSError, RuntimeError, subprocess.SubprocessError):
                self.journal.update(operation_id, recovery='La nuova versione è attiva. Il rinnovo del servizio di aggiornamento non è riuscito; verifica il servizio prima del prossimo aggiornamento.')
        except Exception as exc:
            # Includes failures before subprocess startup and receipt verification.
            try:
                log.parent.mkdir(parents=True, exist_ok=True)
                with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'w') as output:
                    os.fchmod(output.fileno(), 0o600)
                    traceback.print_exc(file=output)
            except OSError:
                # A log permission/disk error must not strand the journal in running.
                pass
            needs_recovery = stage in ('migrating', 'restarting', 'health_check')
            # Raw subprocess logs stay private; API responses contain no environment values.
            detail = str(exc) if isinstance(exc, ReleaseError) else f'Aggiornamento non riuscito durante: {stage}.'
            self.journal.update(operation_id,
                status='recovery_required' if needs_recovery else 'failed',
                stage='recovery_required' if needs_recovery else 'failed', failed_stage=stage, error=detail,
                recovery=('Conserva il backup creato prima dell’aggiornamento. L’operatore del server può usare recover-upgrade con quel backup per ripristinare insieme versione, configurazione e dati; cambiare solo le immagini non ripristina il database.' if needs_recovery
                          else 'La fase precedente alle migrazioni non è stata completata. Verifica i log privati prima di riprovare.'))

    def refresh_runner(self):
        values = read_env(self.env_file)
        reference = values.get('ASSOZETA_UPDATER_REF')
        if not reference:
            raise RuntimeError('Verified updater image is missing')
        command = ['docker', 'run', '--rm', '--detach', '--volume', f'{self.root}:{self.root}',
                   '--volume', '/var/run/docker.sock:/var/run/docker.sock']
        if not self.env_file.is_relative_to(self.root):
            command += ['--volume', f'{self.env_file.parent}:{self.env_file.parent}']
        # This helper lives outside the old controller and can replace it without
        # killing the process responsible for finishing the replacement.
        subprocess.run(command + [reference, '/runner/provision.py', str(self.root), str(self.env_file)],
                       check=True, stdout=subprocess.DEVNULL)

    def verification_path(self, operation_id):
        return self.root / '.updater/verifications' / f'{UUID(operation_id)}.json'

    def require_verification(self, operation):
        # The CLI verified health/images/version while holding the lifecycle lock.
        # Checking live services again here races a subsequent CLI backup/stop.
        try:
            result = json.loads(self.verification_path(operation['id']).read_text())
        except FileNotFoundError as exc:
            raise VerificationError('Verifica della release non completata: manca la ricevuta dell’aggiornamento.') from exc
        except (ValueError, UnicodeError) as exc:
            raise VerificationError('Verifica della release non completata: ricevuta non valida.') from exc
        except OSError as exc:
            raise VerificationError('Verifica della release non completata: ricevuta non leggibile.') from exc
        try:
            if not isinstance(result, dict) or type(result.get('schema_version')) is not int or result['schema_version'] != 1:
                raise ValueError('Invalid receipt schema')
            if datetime.fromisoformat(result['verified_at']).utcoffset() is None:
                raise ValueError('Verification time requires a timezone')
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationError('Verifica della release non completata: ricevuta non valida.') from exc
        if result.get('operation_id') != operation['id'] or result.get('version') != image_version(operation['tag']):
            raise VerificationError('Verifica della release non completata: la ricevuta non corrisponde all’operazione e alla versione richieste.')
        return result

    def record_verification(self, operation_id, target):
        write_json(self.verification_path(operation_id), {
            'schema_version': 1, 'operation_id': operation_id,
            'version': image_version(target), 'verified_at': now(),
        })

    def verify_running(self, target, allow_configured_images=False):
        values = read_env(self.env_file)
        if image_version(values.get('ASSOZETA_VERSION')) != image_version(target):
            raise RuntimeError('Configured target does not match requested release')
        command = compose_command(self.root, self.env_file)
        environment = compose_environment(self.root, self.env_file)
        configured = {}
        if allow_configured_images:
            configured = json.loads(subprocess.check_output(command + ['config', '--format', 'json'], env=environment, text=True))['services']
        for service, image in (('api', 'BACKEND'), ('worker', 'BACKEND'), ('beat', 'BACKEND'), ('web', 'WEB'), ('renderer', 'RENDERER')):
            container = subprocess.check_output(command + ['ps', '-q', service], env=environment, text=True).strip()
            if not container:
                raise RuntimeError(f'Missing service: {service}')
            details = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
            if not details['State']['Running'] or details['State'].get('Health', {}).get('Status', 'healthy') != 'healthy':
                raise RuntimeError(f'Unhealthy service: {service}')
            expected = configured[service]['image'] if allow_configured_images else values.get(f'ASSOZETA_{image}_REF')
            if not expected or details['Config']['Image'] != expected:
                raise RuntimeError(f'Running image differs from verified release: {service}')
        running = subprocess.check_output(command + ['exec', '-T', 'api', 'cat', '/app/VERSION'], env=environment, text=True).strip()
        if version_tuple(running) != version_tuple(target):
            raise RuntimeError('Running backend reports a different release')


if __name__ == '__main__':
    root, env_file, target, *operation_ids = sys.argv[1:]
    engine = Engine(root, env_file, None)
    engine.verify_running(target)
    if operation_ids and operation_ids[0]:
        engine.record_verification(operation_ids[0], target)
