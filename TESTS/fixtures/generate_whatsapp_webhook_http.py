"""Author the communications/whatsapp/webhook HTTP sequence (docs/whatsapp.md).

BE/communications/whatsapp_webhook.py: a plain Django view (csrf_exempt,
require_http_methods(['GET', 'POST']), no DRF, no authentication; the request
middleware leaves its body unread). Every answer is a django HttpResponse, so
it carries 'text/html; charset=utf-8' unless the challenge is echoed as
'text/plain'; other methods get HttpResponseNotAllowed (405, Allow 'GET, POST',
empty body).

- GET: CloudAPI.verify_webhook(hub.mode, hub.verify_token, hub.challenge) read
  with QueryDict.get (the last repeated value; '&' is the only separator): 403
  unless the configured verify token is set, mode is 'subscribe', the challenge
  is non-empty and the token matches; otherwise the challenge as text/plain.
- POST: body read up to 2 MiB + 1 byte (more: 400 before anything else), then
  parse_status_callback: HMAC-SHA256 'sha256=<hex>' of the raw bytes with the
  app secret (no secret or mismatch: 403); json.loads(bytes) (UTF-8 BOM and
  NaN accepted, invalid UTF-8 rejected); data['object'] must exist (KeyError:
  400) and equal 'whatsapp_business_account' or the answer is an empty 200;
  entries are skipped unless their id is the configured WABA id, changes
  unless field 'messages' and metadata.phone_number_id is the configured
  sender; statuses outside sent/delivered/read/failed are skipped, others need
  a non-empty string id (ValueError) and int(timestamp) (int() accepts JSON
  numbers and surrounding whitespace); error_code is str(code) only for an int
  code, error_message the redacted message or title (1000 characters). Any
  ValueError/KeyError/TypeError/IndexError/AttributeError/OSError/OverflowError
  while parsing is a 400 and nothing is recorded (the whole batch is parsed
  first). Then record_status per event: a reference that parses as a UUID
  selects the delivery by pk (even when no row has it), otherwise the provider
  message id; a new (delivery, provider id, status) receipt updates the row
  when it had no provider id or the status ranks higher (queued < sent <
  failed < delivered < read); a repeated receipt changes nothing.
- The integration's enabled flag is not consulted by the webhook.

Configuration is the self-hosted instance integration seeded by the adapter
(WABA/PHONE below, VERIFY_TOKEN, APP_SECRET, no access token so no worker
ever sends). 'config' on a case overrides those settings for that request only.
'signature' is 'valid' (signed at request time with 'signed_with', default the
app secret), 'valid-upper', 'valid-bare' (no 'sha256=' prefix), a literal
header value, or None (no header). Run with ``python3 -I``.
"""
import base64
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('whatsapp_webhook_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
ids = iter(f'a1000000-0000-4000-8000-{1910001 + n:012d}' for n in range(80))
PATH = '/communications/whatsapp/webhook'
WABA, PHONE = '2109876543210', '1098765432109'
VERIFY_TOKEN = 'synthetic-whatsapp-verify-token'
APP_SECRET = 'synthetic-whatsapp-app-secret'
HTML, TEXT = 'text/html; charset=utf-8', 'text/plain'
SEEDED = '2026-11-01T08:00:00+00:00'
FUTURE = '2099-01-01T00:00:00+00:00'
cases = []

# label -> (status, provider message id, error code, error message)
ROWS = {
    'sent': ('sent', 'wamid.SENT', '', ''),
    'queued': ('queued', None, '', ''),
    'provider': ('sent', 'wamid.PROVIDER', '', ''),
    'braced': ('sent', 'wamid.BRACED', '', ''),
    'none_ref': ('sent', 'wamid.NONEREF', '', ''),
    'failed_row': ('failed', 'wamid.FAILED', '131026', 'Message undeliverable'),
    'errors': ('sent', 'wamid.ERRORS', '', ''),
    'title': ('sent', 'wamid.TITLE', '', ''),
    'long': ('sent', 'wamid.LONG', '', ''),
    'batch_one': ('sent', 'wamid.BATCH1', '', ''),
    'batch_two': ('delivered', 'wamid.BATCH2', '', ''),
    'parse_first': ('sent', 'wamid.PARSE', '', ''),
    'disabled': ('sent', 'wamid.DISABLED', '', ''),
    'capital': ('sent', 'wamid.CAPITAL', '', ''),
    'bom': ('sent', 'wamid.BOM', '', ''),
    'int_timestamp': ('sent', 'wamid.INTSTAMP', '', ''),
    'space_timestamp': ('sent', 'wamid.SPACESTAMP', '', ''),
    'string_code': ('sent', 'wamid.STRCODE', '', ''),
    'int_reference': ('sent', 'wamid.INTREF', '', ''),
}
DELIVERIES = {label: {'id': next(ids), 'status': state, 'provider_message_id': provider, 'error_code': code,
                      'error_message': message, 'recipient': '+393331234567', 'body': f'Fixture {label}',
                      'event_type': 'subscription.accepted', 'attempts': 1,
                      'next_attempt_at': FUTURE if state == 'queued' else SEEDED}
              for label, (state, provider, code, message) in ROWS.items()}
UNKNOWN = next(ids)


def ref(label):
    return DELIVERIES[label]['id']


def status(provider, state, stamp='1763700000', *, reference=None, errors=None, **extra):
    item = {'id': provider, 'status': state, 'timestamp': stamp, 'recipient_id': '393331234567'}
    if reference is not None:
        item['biz_opaque_callback_data'] = reference
    if errors is not None:
        item['errors'] = errors
    item.update(extra)
    return item


def payload(statuses, *, waba=WABA, phone=PHONE, field='messages', obj='whatsapp_business_account'):
    return {'object': obj, 'entry': [{'id': waba, 'changes': [{'field': field, 'value': {
        'messaging_product': 'whatsapp', 'metadata': {'display_phone_number': '15550001111', 'phone_number_id': phone},
        'statuses': statuses}}]}]}


def text(value):
    return json.dumps(value, ensure_ascii=False)


def at(stamp):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(stamp), timezone.utc).isoformat()


def receipt(label, provider, state, stamp='1763700000', code='', message=''):
    return {'delivery': label, 'provider_message_id': provider, 'status': state, 'occurred_at': at(stamp),
            'error_code': code, 'error_message': message}


def change(label, **fields):
    return {label: fields}


def add(name, status_code, response, *, method='POST', query='', raw=None, raw_b64=None, raw_parts=None,
        content_type='application/json', signature='valid', signed_with=APP_SECRET, config=None, actor='anonymous',
        deliveries=None, receipts=(), content=HTML, allow=None):
    case = {'name': f'whatsapp-webhook-{name}', 'actor': actor, 'method': method, 'path': PATH + query,
            'headers': {}, 'status': status_code, 'response': response, 'content_type_response': content,
            'allow': allow, 'signature': signature, 'signed_with': signed_with, 'config': config or {},
            'effect': {'deliveries': deliveries or {}, 'receipts': list(receipts)}}
    if raw is not None:
        case['raw_body'], case['content_type'] = raw, content_type
    elif raw_b64 is not None:
        case['raw_b64'], case['content_type'] = raw_b64, content_type
    elif raw_parts is not None:
        case['raw_parts'], case['content_type'] = raw_parts, content_type
    cases.append(case)


def verify(name, status_code, query, response='<empty>', **kwargs):
    add(name, status_code, response, method='GET', query=query, signature=None,
        content=TEXT if status_code == 200 else HTML, **kwargs)


GOOD = f'hub.mode=subscribe&hub.verify_token={VERIFY_TOKEN}'

# --- GET verification ---------------------------------------------------------
verify('verify-challenge', 200, f'?{GOOD}&hub.challenge=1158201444', '1158201444')
verify('verify-wrong-token', 403, '?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=1158201444')
verify('verify-token-prefix', 403, f'?hub.mode=subscribe&hub.verify_token={VERIFY_TOKEN[:-1]}&hub.challenge=1')
verify('verify-wrong-mode', 403, f'?hub.mode=unsubscribe&hub.verify_token={VERIFY_TOKEN}&hub.challenge=1')
verify('verify-missing-challenge', 403, f'?{GOOD}')
verify('verify-empty-challenge', 403, f'?{GOOD}&hub.challenge=')
verify('verify-no-parameters', 403, '')
verify('verify-unicode-challenge', 200, f'?{GOOD}&hub.challenge=%C3%A8+ok%21', 'è ok!')
# QueryDict.get returns the last value of a repeated parameter.
verify('verify-repeated-mode-last-wins', 200, f'?hub.mode=unsubscribe&{GOOD}&hub.challenge=7', '7')
verify('verify-repeated-token-last-wins', 403, f'?{GOOD}&hub.verify_token=wrong&hub.challenge=7')
# Only '&' separates parameters: the semicolon stays in the challenge.
verify('verify-semicolon-in-challenge', 200, f'?{GOOD}&hub.challenge=a;b', 'a;b')
verify('verify-unconfigured-token', 403, '?hub.mode=subscribe&hub.verify_token=&hub.challenge=1',
       config={'webhook_verify_token': ''})
verify('verify-disabled-integration', 200, f'?{GOOD}&hub.challenge=42', '42', config={'enabled': False})
verify('verify-authenticated-owner', 200, f'?{GOOD}&hub.challenge=43', '43', actor='owner')

# --- methods ------------------------------------------------------------------
for verb in ('PUT', 'PATCH', 'DELETE', 'OPTIONS'):
    add(f'{verb.lower()}-not-allowed', 405, '<empty>', method=verb, signature=None, allow=['GET', 'POST'])
add('head-not-allowed', 405, '<empty>', method='HEAD', signature=None, allow=['GET', 'POST'])

# --- signatures -----------------------------------------------------------------
OTHER = text({'object': 'page'})
add('missing-signature', 403, '<empty>', raw=OTHER, signature=None)
add('wrong-signature', 403, '<empty>', raw=OTHER, signature='sha256=' + '0' * 64)
add('signature-without-prefix', 403, '<empty>', raw=OTHER, signature='valid-bare')
add('signature-uppercase-hex', 403, '<empty>', raw=OTHER, signature='valid-upper')
add('signature-other-secret', 403, '<empty>', raw=OTHER, signed_with='another-app-secret')
add('signature-unconfigured-secret', 403, '<empty>', raw=OTHER, signed_with='', config={'app_secret': ''})
# The signature is checked before the payload is parsed.
add('invalid-json-unsigned', 403, '<empty>', raw='{"object": ', signature=None)

# --- payload shapes ---------------------------------------------------------------
add('invalid-json', 400, '<empty>', raw='{"object": ')
add('empty-body', 400, '<empty>', raw='')
add('list-body', 400, '<empty>', raw='[]')
add('missing-object', 400, '<empty>', raw='{}')
add('other-object', 200, '<empty>', raw=OTHER)
add('empty-object', 200, '<empty>', raw=text({'object': ''}))
add('numeric-object', 200, '<empty>', raw=text({'object': 5}))
add('other-object-malformed-entry', 200, '<empty>', raw=text({'object': 'page', 'entry': {'id': WABA}}))
add('nan-in-other-object', 200, '<empty>', raw='{"object": "page", "n": NaN}')
add('invalid-utf8', 400, '<empty>', raw_b64=base64.b64encode(b'{"object": "\xff"}').decode())
add('form-content-type', 200, '<empty>', raw=OTHER, content_type='application/x-www-form-urlencoded')
add('authenticated-owner', 200, '<empty>', raw=OTHER, actor='owner')
add('object-key-is-case-sensitive', 400, '<empty>',
    raw=text({'Object': 'whatsapp_business_account', 'entry': payload([status('wamid.CAPITAL', 'read', reference=ref('capital'))])['entry']}))
add('entry-null', 400, '<empty>', raw=text({'object': 'whatsapp_business_account', 'entry': None}))
add('changes-null', 400, '<empty>', raw=text({'object': 'whatsapp_business_account', 'entry': [{'id': WABA, 'changes': None}]}))
add('metadata-null', 400, '<empty>', raw=text({'object': 'whatsapp_business_account', 'entry': [{'id': WABA, 'changes': [
    {'field': 'messages', 'value': {'metadata': None, 'statuses': []}}]}]}))
add('foreign-entry-malformed-changes', 200, '<empty>',
    raw=text({'object': 'whatsapp_business_account', 'entry': [{'id': 'other-waba', 'changes': 'x'}]}))
add('foreign-waba', 200, '<empty>', raw=text(payload([status('wamid.SENT', 'read', reference=ref('sent'))], waba='other-waba')))
add('foreign-phone', 200, '<empty>', raw=text(payload([status('wamid.SENT', 'read', reference=ref('sent'))], phone='other-phone')))
add('other-field', 200, '<empty>', raw=text(payload([status('wamid.SENT', 'read', reference=ref('sent'))], field='account_update')))
add('unconfigured-waba', 200, '<empty>', raw=text(payload([status('wamid.SENT', 'read', reference=ref('sent'))], waba='')),
    config={'business_account_id': ''})
add('skipped-statuses', 200, '<empty>', raw=text(payload([status('wamid.SENT', 'deleted', reference=ref('sent')),
                                                          status('wamid.SENT', 'queued', reference=ref('sent')),
                                                          status(5, 'pending')])))
add('missing-status-id', 400, '<empty>', raw=text(payload([status('', 'read', reference=ref('sent'))])))
add('numeric-status-id', 400, '<empty>', raw=text(payload([status(7, 'read', reference=ref('sent'))])))
add('missing-timestamp', 400, '<empty>', raw=text(payload([{'id': 'wamid.UNKNOWN', 'status': 'read'}])))
add('fractional-timestamp-text', 400, '<empty>', raw=text(payload([status('wamid.UNKNOWN', 'read', '1.5')])))
add('timestamp-out-of-range', 400, '<empty>', raw=text(payload([status('wamid.UNKNOWN', 'read', '253402300800')])))
add('status-list-item-not-object', 400, '<empty>', raw=text(payload(['wamid.UNKNOWN'])))
# The batch is parsed before anything is recorded.
add('parse-before-record', 400, '<empty>', raw=text(payload([status('wamid.PARSE', 'read', reference=ref('parse_first')),
                                                             {'status': 'read', 'timestamp': '1763700000'}])))
add('body-over-two-mebibytes', 400, '<empty>', signature=None,
    raw_parts=['{"object": "page", "pad": "', {'repeat': 'x', 'count': 2 * 1024 * 1024 - 28}, '"}'])
add('body-of-two-mebibytes', 200, '<empty>',
    raw_parts=['{"object": "page", "pad": "', {'repeat': 'x', 'count': 2 * 1024 * 1024 - 29}, '"}'])

# --- status receipts ----------------------------------------------------------------
add('sent-row-delivered', 200, '<empty>', raw=text(payload([status('wamid.SENT', 'delivered', '1763700100', reference=ref('sent'))])),
    deliveries=change('sent', status='delivered'), receipts=[receipt('sent', 'wamid.SENT', 'delivered', '1763700100')])
add('repeated-receipt-ignored', 200, '<empty>', raw=text(payload([status('wamid.SENT', 'delivered', '1763700200', reference=ref('sent'))])))
add('lower-rank-receipt-recorded-only', 200, '<empty>', raw=text(payload([status('wamid.SENT', 'sent', '1763700050', reference=ref('sent'))])),
    receipts=[receipt('sent', 'wamid.SENT', 'sent', '1763700050')])
add('sent-row-read', 200, '<empty>', raw=text(payload([status('wamid.SENT', 'read', '1763700300', reference=ref('sent'))])),
    deliveries=change('sent', status='read'), receipts=[receipt('sent', 'wamid.SENT', 'read', '1763700300')])
add('queued-row-gets-provider-id', 200, '<empty>', raw=text(payload([status('wamid.QUEUED', 'sent', '1763700400', reference=ref('queued'))])),
    deliveries=change('queued', status='sent', provider_message_id='wamid.QUEUED'),
    receipts=[receipt('queued', 'wamid.QUEUED', 'sent', '1763700400')])
add('queued-row-failed-with-error', 200, '<empty>', raw=text(payload([status(
    'wamid.QUEUED', 'failed', '1763700500', reference=ref('queued'),
    errors=[{'code': 131047, 'title': 'Re-engagement message', 'message': f'Outside window for {APP_SECRET}'}])])),
    deliveries=change('queued', status='failed', error_code='131047', error_message='Outside window for [redacted]'),
    receipts=[receipt('queued', 'wamid.QUEUED', 'failed', '1763700500', '131047', 'Outside window for [redacted]')])
add('failed-row-delivered-clears-error', 200, '<empty>', raw=text(payload([status('wamid.FAILED', 'delivered', '1763700600', reference=ref('failed_row'))])),
    deliveries=change('failed_row', status='delivered', error_code='', error_message=''),
    receipts=[receipt('failed_row', 'wamid.FAILED', 'delivered', '1763700600')])
add('provider-id-when-reference-invalid', 200, '<empty>', raw=text(payload([status('wamid.PROVIDER', 'delivered', '1763700700', reference='not-a-uuid')])),
    deliveries=change('provider', status='delivered'), receipts=[receipt('provider', 'wamid.PROVIDER', 'delivered', '1763700700')])
add('unknown-reference-ignores-provider-id', 200, '<empty>', raw=text(payload([status('wamid.PROVIDER', 'read', '1763700800', reference=UNKNOWN)])))
add('braced-uppercase-reference', 200, '<empty>', raw=text(payload([status('wamid.BRACED', 'read', '1763700900',
                                                                           reference='{' + ref('braced').upper() + '}')])),
    deliveries=change('braced', status='read'), receipts=[receipt('braced', 'wamid.BRACED', 'read', '1763700900')])
add('null-reference-uses-provider-id', 200, '<empty>', raw=text(payload([status('wamid.NONEREF', 'delivered', '1763701000',
                                                                               biz_opaque_callback_data=None)])),
    deliveries=change('none_ref', status='delivered'), receipts=[receipt('none_ref', 'wamid.NONEREF', 'delivered', '1763701000')])
add('unknown-provider-id', 200, '<empty>', raw=text(payload([status('wamid.UNKNOWN', 'read', '1763701100')])))
add('failed-error-code-and-message', 200, '<empty>', raw=text(payload([status(
    'wamid.ERRORS', 'failed', '1763701200', reference=ref('errors'),
    errors=[{'code': 131026, 'title': 'Undeliverable', 'message': f'Token {VERIFY_TOKEN} rejected'}, {'code': 1}])])),
    deliveries=change('errors', status='failed', error_code='131026', error_message='Token [redacted] rejected'),
    receipts=[receipt('errors', 'wamid.ERRORS', 'failed', '1763701200', '131026', 'Token [redacted] rejected')])
add('failed-title-when-message-empty', 200, '<empty>', raw=text(payload([status(
    'wamid.TITLE', 'failed', '1763701300', reference=ref('title'), errors=[{'code': 131000, 'message': '', 'title': 'Generic failure'}])])),
    deliveries=change('title', status='failed', error_code='131000', error_message='Generic failure'),
    receipts=[receipt('title', 'wamid.TITLE', 'failed', '1763701300', '131000', 'Generic failure')])
LONG = 'é' * 1200
add('failed-message-truncated', 200, '<empty>', raw=text(payload([status(
    'wamid.LONG', 'failed', '1763701400', reference=ref('long'), errors=[{'code': 2, 'message': LONG}])])),
    deliveries=change('long', status='failed', error_code='2', error_message=LONG[:1000]),
    receipts=[receipt('long', 'wamid.LONG', 'failed', '1763701400', '2', LONG[:1000])])
add('batch-several-rows-and-repeat', 200, '<empty>', raw=text(payload([
    status('wamid.BATCH1', 'delivered', '1763701500', reference=ref('batch_one')),
    status('wamid.BATCH2', 'read', '1763701501', reference=ref('batch_two')),
    status('wamid.BATCH1', 'delivered', '1763701502', reference=ref('batch_one')),
    status('wamid.BATCH1', 'read', '1763701503', reference=ref('batch_one'))])),
    deliveries={'batch_one': {'status': 'read'}, 'batch_two': {'status': 'read'}},
    receipts=[receipt('batch_one', 'wamid.BATCH1', 'delivered', '1763701500'), receipt('batch_two', 'wamid.BATCH2', 'read', '1763701501'),
              receipt('batch_one', 'wamid.BATCH1', 'read', '1763701503')])
add('receipt-with-disabled-integration', 200, '<empty>', config={'enabled': False},
    raw=text(payload([status('wamid.DISABLED', 'delivered', '1763701600', reference=ref('disabled'))])),
    deliveries=change('disabled', status='delivered'), receipts=[receipt('disabled', 'wamid.DISABLED', 'delivered', '1763701600')])
# json.loads(bytes) detects the UTF-8 BOM.
add('utf8-bom-body', 200, '<empty>', raw='﻿' + text(payload([status('wamid.BOM', 'delivered', '1763701700', reference=ref('bom'))])),
    deliveries=change('bom', status='delivered'), receipts=[receipt('bom', 'wamid.BOM', 'delivered', '1763701700')])
# int() accepts a JSON number and surrounding whitespace.
add('integer-timestamp', 200, '<empty>', raw=text(payload([status('wamid.INTSTAMP', 'delivered', 1763701800, reference=ref('int_timestamp'))])),
    deliveries=change('int_timestamp', status='delivered'),
    receipts=[receipt('int_timestamp', 'wamid.INTSTAMP', 'delivered', '1763701800')])
add('timestamp-with-spaces', 200, '<empty>', raw=text(payload([status('wamid.SPACESTAMP', 'delivered', ' 1763701900 ',
                                                                     reference=ref('space_timestamp'))])),
    deliveries=change('space_timestamp', status='delivered'),
    receipts=[receipt('space_timestamp', 'wamid.SPACESTAMP', 'delivered', '1763701900')])
# Only an int code is kept.
add('string-error-code-dropped', 200, '<empty>', raw=text(payload([status(
    'wamid.STRCODE', 'failed', '1763702000', reference=ref('string_code'), errors=[{'code': '131026', 'message': 'Text code'}])])),
    deliveries=change('string_code', status='failed', error_code='', error_message='Text code'),
    receipts=[receipt('string_code', 'wamid.STRCODE', 'failed', '1763702000', '', 'Text code')])
# str(5) is not a UUID, so the provider message id selects the row.
add('integer-reference-uses-provider-id', 200, '<empty>', raw=text(payload([status('wamid.INTREF', 'read', '1763702100', reference=5)])),
    deliveries=change('int_reference', status='read'), receipts=[receipt('int_reference', 'wamid.INTREF', 'read', '1763702100')])

assert len({case['name'] for case in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'WhatsApp webhook verification and signed status callbacks with delivery/receipt effects',
    'ids': IDS, 'whatsapp': {'phone_number_id': PHONE, 'business_account_id': WABA, 'webhook_verify_token': VERIFY_TOKEN,
                             'app_secret': APP_SECRET, 'seeded_at': SEEDED},
    'deliveries': DELIVERIES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
