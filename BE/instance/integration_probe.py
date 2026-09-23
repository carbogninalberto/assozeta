"""Read-only integration checks; never return provider response bodies or secrets."""
import json
from urllib.parse import urlsplit

import requests

from .diagnostic_probe import result
from .integration_configuration import effective_integration


def get_json(url, headers=None):
    # A redirect must never forward an integration credential to another host.
    with requests.get(url, headers=headers, timeout=(3, 4), allow_redirects=False, stream=True) as response:
        if response.status_code in (401, 403):
            return None, result('failed', 'Credenziale rifiutata o permessi insufficienti. Verifica la chiave e le autorizzazioni del provider.')
        if response.status_code == 429:
            return None, result('warning', 'Il provider limita le richieste. Attendi e ripeti la verifica.')
        if response.status_code != 200:
            return None, result('failed', 'Il provider non ha risposto correttamente. Verifica endpoint, rete e disponibilità del servizio.')
        raw = response.raw.read(262145, decode_content=True)
        if len(raw) > 262144:
            return None, result('failed', 'Risposta del provider troppo grande per la verifica.')
        return json.loads(raw), None


def verify_integration(provider, revision):
    value = effective_integration(provider)
    if value['revision'] != revision:
        return result('not_checked', 'Configurazione cambiata. Ricarica e ripeti la verifica.', 'configuration')
    if not value['enabled']:
        return result('not_applicable', 'Integrazione disattivata. Abilitala e salva prima di verificarla.', 'configuration')
    if provider in ('google', 'apple'):
        if not value.get('client_id'):
            return result('not_configured', 'Client ID mancante.', 'configuration')
        if provider == 'google' and not value['client_id'].endswith('.apps.googleusercontent.com'):
            return result('failed', 'Il client ID Google non ha il formato di un client web.', 'configuration')
        url = 'https://www.googleapis.com/oauth2/v3/certs' if provider == 'google' else 'https://appleid.apple.com/auth/keys'
        data, error = get_json(url)
        if error:
            return error
        if not isinstance(data, dict) or not any(isinstance(key, dict) and key.get('kid') and key.get('kty') == 'RSA' and key.get('n') and key.get('e') for key in data.get('keys', [])):
            return result('failed', 'Il provider non ha restituito chiavi pubbliche valide.')
        return result('warning', 'Provider raggiungibile e chiavi pubbliche disponibili. Validità del client ID, origini autorizzate e accesso utente richiedono una prova di login.' + (' Il pulsante Apple sul web non è disponibile in questa versione.' if provider == 'apple' else ''))
    if provider == 'stripe':
        if not all(value.get(key) for key in ('public_key', 'secret_key', 'webhook_secret')):
            return result('not_configured', 'Completa le chiavi Stripe e la firma webhook.', 'configuration')
        data, error = get_json('https://api.stripe.com/v1/balance', {'Authorization': 'Bearer ' + value['secret_key']})
        if error:
            return error
        if not isinstance(data, dict) or data.get('object') != 'balance':
            return result('failed', 'Risposta Stripe non valida per la verifica.')
        if data.get('livemode') is not value['public_key'].startswith('pk_live_'):
            return result('failed', 'La chiave pubblica e l’account Stripe indicano ambienti diversi.', 'configuration')
        return result('passed', 'Chiave segreta accettata da Stripe e saldo leggibile. Nessun pagamento eseguito; chiave pubblica e consegna webhook non verificate.')
    if provider == 'ai':
        if not value.get('api_key'):
            return result('not_configured', 'Chiave API mancante.', 'configuration')
        base_url = value.get('base_url') or 'https://api.openai.com/v1'
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return result('failed', 'URL del provider non valido.', 'configuration')
        data, error = get_json(base_url.rstrip('/') + '/models', {'Authorization': 'Bearer ' + value['api_key']})
        if error:
            return error
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            return result('failed', 'Il provider non ha restituito un elenco modelli compatibile.')
        models = {item.get('id') for item in data['data'] if isinstance(item, dict)}
        if value['model'] not in models:
            return result('warning', 'Provider raggiungibile, ma il modello principale non compare nell’elenco. Verifica l’identificativo e i permessi. Generazione non provata.')
        return result('passed', 'Elenco modelli accessibile con la chiave salvata e modello principale presente. Nessun messaggio generato; capacità del modello e strumenti non provati.')
    raise ValueError('Unknown integration')
