"""Sanitized owner diagnostics; optional services do not affect core health."""
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .operation_limits import claim_operation
from .models import InstanceConfiguration
from .permissions import IsInstanceOwner
from .probe_runner import run_probe

CHECKS = (
    ('public_url', 'URL pubblico e HTTPS', True, 'overview'),
    ('api', 'API', True, 'diagnostics'),
    ('database', 'Database', True, 'diagnostics'),
    ('storage', 'Archivio file', True, 'diagnostics'),
    ('worker', 'Worker', True, 'diagnostics'),
    ('scheduler', 'Scheduler', True, 'diagnostics'),
    ('renderer', 'Renderer PDF', True, 'diagnostics'),
    ('email', 'Configurazione email', False, 'email'),
    ('updater', 'Servizio aggiornamenti', False, 'updates'),
    ('backups', 'Preparazione backup', False, 'updates'),
)


def decorate(values):
    return [{**values.get(key, {'status': 'not_checked', 'level': 'connectivity',
                              'message': 'Esegui la diagnostica per verificare questo servizio.', 'checked_at': None}),
             'id': key, 'label': label, 'core': core, 'section': section}
            for key, label, core, section in CHECKS]


def overall(checks):
    core = [check['status'] for check in checks if check['core']]
    if 'failed' in core:
        return 'failed'
    if any(value in ('warning', 'not_configured') for value in core):
        return 'warning'
    return 'passed' if core and all(value == 'passed' for value in core) else 'not_checked'


def integrations(config):
    from .integration_configuration import public_integration, effective_integration
    checks = []
    for key, label in (('stripe', 'Stripe'), ('google', 'Accesso Google'), ('apple', 'Accesso Apple')):
        value = public_integration(key, config)
        state, message = 'not_configured', 'Integrazione facoltativa non configurata.'
        if not value['enabled']:
            if value['source'] == 'instance' or value.get('client_id') or value.get('public_key'):
                state, message = 'not_applicable', 'Integrazione disabilitata. Le credenziali salvate sono conservate.'
        elif value['credential_error']:
            state, message = 'warning', 'Una credenziale salvata non può essere letta. Reinseriscila nella configurazione dell’integrazione.'
        elif key == 'stripe':
            if not all((value['public_key'], value['secret_key_configured'], value['webhook_secret_configured'])):
                state, message = 'warning', 'Completa chiave pubblica, chiave segreta e firma webhook nella configurazione Stripe.'
            else:
                secret = effective_integration(key, config)['secret_key']
                mode = 'sandbox' if value['public_key'].startswith('pk_test_') and secret.startswith(('sk_test_', 'rk_test_')) else 'produzione' if value['public_key'].startswith('pk_live_') and secret.startswith(('sk_live_', 'rk_live_')) else None
                state = 'passed' if mode else 'warning'
                message = f'Credenziali {mode} presenti. Pagamenti e webhook non verificati.' if mode else 'Le chiavi Stripe non indicano lo stesso ambiente. Correggi la configurazione.'
        elif value['client_id']:
            state, message = 'passed', 'Client ID configurato. Un accesso reale con il provider non è stato verificato.'
            legacy = getattr(config, key + '_client_id', '') if config else ''
            if value['source'] == 'environment' and legacy and legacy != value['client_id']:
                state, message = 'warning', 'Il client ID pubblico precedente e quello del server non coincidono. Salva il client ID corretto qui per allinearli.'
            elif key == 'apple':
                state, message = 'warning', 'Client ID configurato per verificare token Apple. Il pulsante di accesso Apple sul web non è disponibile in questa versione.'
        checks.append({'id': key, 'label': label, 'status': state, 'level': 'configuration',
                       'message': message, 'checked_at': timezone.now().isoformat(), 'core': False})
    return checks


def snapshot(config):
    stored = config.diagnostic_results or {}
    checks = decorate({value['id']: value for value in stored.get('checks', [])})
    return {'checks': checks, 'overall': overall(checks), 'checked_at': stored.get('checked_at'),
            'email_test': stored.get('email_test'), 'integrations': integrations(config)}


class DiagnosticsView(APIView):
    permission_classes = [IsInstanceOwner]

    def get(self, request):
        return Response(snapshot(InstanceConfiguration.get_config()), headers={'Cache-Control': 'no-store'})

    def post(self, request):
        if not claim_operation('diagnostics', 45):
            return Response({'error': 'Diagnostica già in corso o appena terminata. Attendi fino a 45 secondi e riprova.'}, status=429, headers={'Retry-After': '45'})
        config = InstanceConfiguration.get_config()
        revision = config.email_revision
        with ThreadPoolExecutor(max_workers=4) as executor:
            values = list(executor.map(run_probe, [check[0] for check in CHECKS]))
        checks = decorate(dict(zip([check[0] for check in CHECKS], values)))
        with transaction.atomic():
            config = InstanceConfiguration.objects.select_for_update().first()
            if config.email_revision != revision:
                checks = [check for check in checks if check['id'] != 'email']
            config.diagnostic_results = {**(config.diagnostic_results or {}), 'checks': checks,
                                         'checked_at': timezone.now().isoformat()}
            config.save(update_fields=['diagnostic_results'])
        return Response(snapshot(config), headers={'Cache-Control': 'no-store'})
