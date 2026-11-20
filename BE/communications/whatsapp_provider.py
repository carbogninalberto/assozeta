"""WhatsApp Business Cloud API adapter. See docs/whatsapp.md for the wire contract."""
import hashlib
import hmac
import json
from datetime import datetime, timezone

import requests
from django.conf import settings
from instance.integration_configuration import effective_integration


class ProviderError(Exception):
    def __init__(self, code, message, transient=False):
        super().__init__(message)
        self.code = str(code)
        self.transient = transient


class CloudAPI:
    def __init__(self):
        self.config = effective_integration('whatsapp')

    def safe_message(self, message):
        text = str(message)
        for key in ('access_token', 'app_secret', 'webhook_verify_token'):
            if self.config[key]:
                text = text.replace(self.config[key], '[redacted]')
        return text[:1000]

    def _request(self, method, resource, edge, *, params=None, payload=None):
        if not resource or not self.config["access_token"]:
            raise ProviderError('configuration', 'Missing WhatsApp resource ID or access token')
        url = f"{settings.WHATSAPP_API_BASE_URL.rstrip('/')}/{settings.WHATSAPP_API_VERSION}/{resource}/{edge}"
        try:
            response = requests.request(
                method, url, params=params, json=payload, timeout=10, allow_redirects=False,
                headers={'Authorization': f'Bearer {self.config["access_token"]}'},
            )
        except requests.RequestException:
            raise ProviderError('network', 'WhatsApp connection failed', True) from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if not 200 <= response.status_code < 300:
            detail = data.get('error') if isinstance(data, dict) else None
            detail = detail if isinstance(detail, dict) else {}
            code = detail.get('code')
            raise ProviderError(code if isinstance(code, int) else response.status_code,
                                self.safe_message(detail.get('message', 'WhatsApp API request failed')),
                                response.status_code == 429 or response.status_code >= 500)
        if not isinstance(data, dict):
            raise ProviderError('invalid_response', 'Invalid WhatsApp API response')
        return data

    def _send(self, recipient, kind, content, reference):
        data = self._request('POST', self.config["phone_number_id"], 'messages', payload={
            'messaging_product': 'whatsapp', 'to': recipient.lstrip('+'), 'type': kind,
            kind: content, 'biz_opaque_callback_data': str(reference),
        })
        try:
            message_id = data['messages'][0]['id']
            if isinstance(message_id, str) and message_id:
                return message_id
        except (KeyError, TypeError, IndexError):
            pass
        raise ProviderError('invalid_response', 'Missing WhatsApp message ID')

    def send_text(self, recipient, body, reference):
        return self._send(recipient, 'text', {'body': body}, reference)

    def send_template(self, recipient, name, language, body_parameter, reference):
        template = {'name': name, 'language': {'code': language}}
        if body_parameter:
            template['components'] = [{'type': 'body', 'parameters': [{'type': 'text', 'text': body_parameter}]}]
        return self._send(recipient, 'template', template, reference)

    def register_template(self, definition):
        return self._request('POST', self.config["business_account_id"], 'message_templates', payload=definition)

    def list_templates(self):
        params = {'status': '["APPROVED"]', 'fields': 'id,name,language,status,components'}
        result, seen = [], set()
        while True:
            data = self._request('GET', self.config["business_account_id"], 'message_templates', params=params)
            if not isinstance(data.get('data'), list):
                raise ProviderError('invalid_response', 'Missing WhatsApp template list')
            result.extend(item for item in data['data'] if isinstance(item, dict) and item.get('status') == 'APPROVED')
            paging = data.get('paging') or {}
            if not isinstance(paging, dict):
                raise ProviderError('invalid_response', 'Invalid WhatsApp template page')
            if not paging.get('next'):
                return result
            cursors = paging.get('cursors')
            cursor = cursors.get('after') if isinstance(cursors, dict) else None
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ProviderError('invalid_response', 'Invalid WhatsApp template cursor')
            seen.add(cursor)
            params['after'] = cursor

    def get_template(self, name, language):
        for item in self.list_templates():
            if item.get('name') == name and item.get('language') == language:
                return item
        raise ProviderError('not_found', 'Approved template not found')

    def verify_webhook(self, mode, token, challenge):
        expected = self.config["webhook_verify_token"]
        if not expected or mode != 'subscribe' or not challenge or not hmac.compare_digest(expected.encode(), token.encode()):
            raise PermissionError
        return challenge

    def parse_status_callback(self, body, signature):
        secret = self.config["app_secret"]
        expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(expected.encode(), signature.encode()):
            raise PermissionError
        result = []
        data = json.loads(body)
        if data['object'] != 'whatsapp_business_account':
            return result
        for entry in data.get('entry', []):
            if not self.config["business_account_id"] or entry.get('id') != self.config["business_account_id"]:
                continue
            for change in entry.get('changes', []):
                value = change.get('value', {})
                sender = value.get('metadata', {}).get('phone_number_id')
                if change.get('field') != 'messages' or not sender or sender != self.config["phone_number_id"]:
                    continue
                for item in value.get('statuses', []):
                    if item.get('status') not in ('sent', 'delivered', 'read', 'failed'):
                        continue
                    if not isinstance(item.get('id'), str) or not item['id']:
                        raise ValueError('Missing provider message ID')
                    detail = (item.get('errors') or [{}])[0]
                    result.append({
                        'provider_message_id': item['id'], 'status': item['status'],
                        'reference': item.get('biz_opaque_callback_data'),
                        'occurred_at': datetime.fromtimestamp(int(item['timestamp']), timezone.utc),
                        'error_code': str(detail['code']) if isinstance(detail.get('code'), int) else '',
                        'error_message': self.safe_message(detail.get('message') or detail.get('title', '')),
                    })
        return result
