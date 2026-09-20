"""Isolated, fixed-purpose probes. Never return exception text or connection details."""
import json
import sys
import time
from email.utils import formataddr
from urllib.parse import urlsplit


def result(status, message, level='connectivity'):
    return {'status': status, 'level': level, 'message': message}


def health_response(url, expected_version=None):
    import requests
    # No redirects: a different host must not masquerade as the configured service.
    with requests.get(url, timeout=(3, 3), allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            return False
        data = json.loads(response.raw.read(8193, decode_content=True))
        return data.get('status') == 'ok' and (expected_version is None or data.get('version') == expected_version)


def probe(name, payload):
    from django.conf import settings
    from django.core.cache import cache
    from django.db import connection

    if name == 'database':
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            assert cursor.fetchone()[0] == 1
        return result('passed', 'Il database risponde a una query di lettura. Nessun dato è stato modificato.')
    if name in ('public_url', 'api'):
        url = settings.APP_URL if name == 'public_url' else 'http://127.0.0.1:8000'
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return result('failed', 'Configura un URL pubblico valido in APP_URL.', 'configuration')
        if not health_response(url.rstrip('/') + ('/api/healthz' if name == 'public_url' else '/healthz'), settings.RUNNING_VERSION):
            return result('failed', 'L’endpoint di salute non risponde con la versione attesa. Verifica URL, rete e reverse proxy.')
        if name == 'public_url' and parsed.scheme != 'https':
            return result('warning', 'L’URL risponde, ma HTTPS non è attivo. Configura dominio e certificato prima dell’uso pubblico.')
        return result('passed', 'URL pubblico raggiungibile con certificato HTTPS valido.' if name == 'public_url' else 'L’API risponde con la versione in esecuzione.')
    if name == 'storage':
        import boto3
        from botocore.config import Config
        client = boto3.client('s3', endpoint_url=settings.AWS_S3_ENDPOINT_URL,
                              aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                              aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                              region_name=settings.AWS_S3_REGION_NAME,
                              config=Config(connect_timeout=3, read_timeout=3, retries={'max_attempts': 0}))
        client.list_objects_v2(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Prefix=settings.AWS_LOCATION, MaxKeys=1)
        return result('passed', 'Archivio accessibile in lettura. Scrittura e caricamento file non sono stati provati.')
    if name == 'worker':
        from core.celery import app
        replies = app.control.inspect(timeout=2).ping()
        if replies and any(reply.get('ok') == 'pong' for reply in replies.values()):
            return result('passed', 'Un worker risponde tramite il broker. Non sono stati eseguiti task applicativi.')
        return result('failed', 'Nessun worker risponde. Verifica worker e broker e riprova.')
    if name == 'scheduler':
        heartbeat = cache.get('instance:scheduler:heartbeat')
        if isinstance(heartbeat, (float, int)) and 0 <= time.time() - heartbeat <= 90:
            return result('passed', 'Il ciclo dello scheduler è attivo (segnale negli ultimi 90 secondi). L’esito dei task non è verificato.', 'functional')
        return result('failed', 'Segnale dello scheduler assente o scaduto. Avvia o riavvia il servizio beat e ripeti dopo un minuto.')
    if name == 'renderer':
        url = f'http://{settings.PUPPETEER_HOST}:{settings.PUPPETEER_PORT}/healthz'
        if health_response(url):
            return result('passed', 'Il renderer apre e chiude una pagina nel browser. La generazione di un documento PDF non è stata provata.')
        return result('failed', 'Il renderer non supera il controllo di salute. Verifica il servizio PDF.')
    if name.startswith('email'):
        from .email_configuration import effective_email, smtp_connection
        from .models import InstanceConfiguration
        config = InstanceConfiguration.get_config()
        value = effective_email(config)
        if not value['host'] or not value['from_email']:
            return result('not_configured', 'Configura server SMTP e indirizzo mittente nella sezione Email.', 'configuration')
        if value['security'] == 'invalid':
            return result('failed', 'TLS e SSL sono entrambi attivi nell’ambiente. Scegli una sola modalità nella configurazione email.', 'configuration')
        if value['security'] == 'none' and (name == 'email' or value['username'] or value['password']):
            return result('warning', 'SMTP senza cifratura. Configura TLS o SSL prima di inviare credenziali o messaggi.', 'configuration')
        if bool(value['username']) != bool(value['password']):
            return result('warning', 'Nome utente e password SMTP incompleti. Completa entrambe le credenziali oppure rimuovile per un relay senza autenticazione.', 'configuration')
        if name == 'email':
            return result('passed', 'Campi SMTP e mittente presenti. Connessione e invio non verificati: usa i test espliciti nella sezione Email.', 'configuration')
        if payload.get('revision') != value['revision']:
            return result('not_checked', 'Configurazione cambiata. Ricarica e ripeti il test.', 'configuration')
        with smtp_connection(value) as smtp:
            if name == 'email_connect':
                return result('passed', 'Connessione SMTP e autenticazione (se configurata) riuscite. Nessun messaggio inviato.')
            from django.core.mail import EmailMessage
            message = EmailMessage('Assozeta — verifica email',
                                   'Questo messaggio è stato richiesto dal proprietario dell’istanza per verificare la configurazione email.',
                                   formataddr((value['sender_name'], value['from_email'])),
                                   [payload['recipient']], connection=smtp)
            if message.send() == 1:
                return result('passed', 'Il server SMTP ha accettato il messaggio. Verifica la casella del destinatario e lo spam; la consegna non è garantita.', 'functional')
            return result('failed', 'Il server SMTP non ha accettato il messaggio. Verifica mittente e destinatario.', 'functional')
    if name in ('updater', 'backups'):
        from .updater_client import call_runner, deployment_mode
        if deployment_mode() != 'production':
            return result('not_applicable', 'Disponibile nelle installazioni self-hosted in modalità produzione.', 'configuration')
        value = call_runner('GET', '/status' if name == 'updater' else '/diagnostics')
        if name == 'backups':
            return value['backup']
        if value.get('available') and value.get('protocol') == 1:
            if value.get('can_update') is False:
                return result('warning', 'Il servizio risponde, ma richiede un ripristino. Consulta Aggiornamenti e backup.')
            return result('passed', 'Il servizio di aggiornamento risponde con un protocollo compatibile. Le immagini di una release non sono state scaricate.')
        return result('failed', 'Servizio di aggiornamento non disponibile o incompatibile. Esegui il normale aggiornamento dal server.')
    raise ValueError('Unknown probe')


def main():
    import contextlib
    import logging
    import os
    logging.disable(logging.CRITICAL)
    name = sys.argv[1]
    from .probe_runner import PROBE_IDS
    if name not in PROBE_IDS:
        raise ValueError('Unknown probe')
    payload = json.loads(sys.stdin.read(4096) or '{}')
    try:
        # Startup libraries must not contaminate the small, sanitized result channel.
        with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink):
            # Service probes need settings and their client libraries, not the
            # application's model registry/task imports. Booting the whole app
            # four times in parallel exhausts the deadline on small servers.
            # Only email probes read the instance model and require setup.
            if name.startswith('email'):
                import django
                django.setup()
            value = probe(name, payload)
    except Exception:
        value = result('failed', 'Verifica non riuscita. Controlla configurazione e disponibilità del servizio e riprova.')
    print(json.dumps(value))


if __name__ == '__main__':
    main()
