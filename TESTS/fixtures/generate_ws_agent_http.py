"""Author the AI agent WebSocket sessions and run_agent transcripts (ws-agent.md).

Django serves ``ws/agent/`` (``application.chat.consumers.AgentConsumer``)
through ``JWTAuthMiddleware`` under uvicorn; the consumer runs the agent loop of
``application.agent.core.Agent`` against the configured OpenAI-compatible
endpoint with the AsyncOpenAI 2.54.0 client (httpx 0.28.1). Every session runs
against a per-run stub endpoint that answers scripted streamed completions and
records every request. These sessions pin, in Django's order:
- connect: authentication, AI configuration (instance settings, decrypted key),
  manual availability, the concurrency guard (``ws_concurrent:<user>``), the
  association, the throttle, the welcome text (three variants);
- messages: ping, unknown types, clear_history, cancel, saved reports
  (list, delete, save from the in-memory or cached export), the rate limit
  (``ws_throttle:<user>``), the per-request configuration refresh, the restore
  lease, the timeout and cancellation of a running request;
- the agent: routing (capabilities, conversation, manual-only answers, manual
  FAQ with its section attachment, diagnostics, actions and follow-ups), the
  streamed loop (chunks, tool calls, tool results as the next request's
  messages, the schema cache, unknown tools, invalid arguments, loop
  detection, the iteration limit, deferred exports and saved reports), the
  history cap and every outbound request (headers, exact JSON body, SDK and
  agent retries);
- ``manage.py run_agent``: the CLI transcript (stdout and stderr bytes).
Run with ``python3 -I``.
"""
import ast
import base64
import html
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).with_name('ws_agent_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, FULL, RESTRICTED, ATHLETE = IDS['owner'], IDS['full_collaborator'], IDS['restricted_collaborator'], IDS['athlete']
ADMIN, FOREIGN_OWNER = IDS['admin'], IDS['foreign_owner']
DENOMINATION = 'ASD Agente Fixture'
API_KEY = 'ws-agent-placeholder-key'
MODEL = 'fixture-model'
SDK = '2.54.0'

# Rows the adapter seeds (fixed identifiers, owner's association unless noted).
REPORTS = [
    {'saved_report_id': 'a1000000-0000-4000-8000-000000790001', 'association': OWN, 'user': OWNER, 'name': 'Soci attivi',
     'description': 'Elenco dei soci', 'tool_name': 'query_data', 'params': {'model_name': 'Associate'},
     'ui_config': {}, 'updated_at': '2026-03-01T09:30:00+00:00'},
    {'saved_report_id': 'a1000000-0000-4000-8000-000000790002', 'association': OWN, 'user': OWNER, 'name': 'Pagamenti è',
     'description': '', 'tool_name': 'export_data', 'params': {'model_name': 'Payment', 'format': 'csv'},
     'ui_config': {'fields': []}, 'updated_at': '2026-04-02T10:00:00.123456+00:00'},
    {'saved_report_id': 'a1000000-0000-4000-8000-000000790003', 'association': OWN, 'user': FULL, 'name': 'Del collaboratore',
     'description': 'x', 'tool_name': 'count_data', 'params': {}, 'ui_config': {}, 'updated_at': '2026-02-01T00:00:00+00:00'},
]
TAGS = [{'tag_id': 'a1000000-0000-4000-8000-000000791001', 'tag_name': 'WSAgent Beta'},
        {'tag_id': 'a1000000-0000-4000-8000-000000791002', 'tag_name': 'WSAgent Alfa'}]

# The verified manual corpus the adapter seals (canonical JSON, sorted keys).
SECTION = {
    'id': 'docs/tag#creare', 'page': 'docs/tag', 'section': 'creare', 'title': 'Creare un tag',
    'text': 'Apri la sezione Tag e premi Nuovo tag.', 'status': 'verified',
    'url': 'https://manual.example.test/docs/tag#creare',
    'screenshots': [{'path': 'images/tag.png', 'sha256': None}],  # sha256 filled by the adapter (PNG digest)
    'scenario_ids': ['fixture'], 'page_title': 'Etichette', 'language': 'it', 'intent': 'create',
    'audience': 'public', 'features': [], 'evidence': [], 'runtime_dependencies': {},
    'reader': [{'title': 'Creare un tag', 'text': 'Apri la sezione Tag e premi Nuovo tag.', 'screenshots': []}],
    'page_description': 'Gestione etichette', 'page_order': 1, 'section_order': 1,
}
PNG = b'\x89PNG\r\n\x1a\n' + (13).to_bytes(4, 'big') + b'IHDR' + (1).to_bytes(4, 'big') * 2


def literal(path, name):
    for node in ast.parse((ROOT / path).read_text()).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == name:
            return ast.literal_eval(node.value)
    raise KeyError(name)


SYSTEM = literal('BE/application/agent/prompts.py', 'SYSTEM_PROMPT').format(association_name=DENOMINATION)
STOPWORDS = set('a ad al alla alle ai agli anche che chi con da dal dalla dei del della delle di e ed è gli i il in la le lo l '
                'ma nel nella nelle o per più quale quali se si su sul sulla un una uno come cosa posso devo fare manuale '
                'istruzioni guida tutorial'.split())
assert 'creare' not in STOPWORDS and 'tag' not in STOPWORDS
# TOOLS_FOR_LLM: TOOL_DEFINITIONS (business tools, then the manual tools) as functions.
TOOLS = [{'type': 'function', 'function': {'name': item['name'], 'description': item['description'], 'parameters': item['inputSchema']}}
         for item in json.loads((ROOT / 'BE_GO/internal/handler/businessmcp/tools.json').read_text())]
assert [item['function']['name'] for item in TOOLS][-4:] == ['search_manual', 'get_manual_section', 'get_manual_evidence', 'get_manual_gaps']

WELCOME_MANUAL_ONLY = ("Ciao! Posso cercare istruzioni verificate nel manuale di Assozeta. "
                       "Scrivi la tua domanda oppure [sfoglia il manuale d’uso](/#/manuale). "
                       "Se mancano informazioni verificate, te lo segnalo. "
                       "Le analisi dei dati e gli export richiedono la configurazione AI.")
WELCOME_FULL = ("Ciao! Posso aiutarti a cercare informazioni nell’associazione "
                "e a capire come usare Assozeta.\n\n"
                "- **Cerca nei dati**: per esempio, «Quanti iscritti ho quest’anno?»\n"
                "- **Trova una guida**: per esempio, «Come posso assegnare un tag?»\n"
                "- **Prepara un export** dei dati che ti servono.\n\n"
                "Quando cerchi istruzioni, ti indico la guida disponibile nel manuale. "
                "Se mancano informazioni verificate, te lo segnalo.\n\n"
                "[Sfoglia il manuale d’uso](/#/manuale) oppure scrivi qui la tua domanda.")
WELCOME_DATA = ("Ciao! Posso aiutarti a cercare informazioni nell’associazione "
                "e a preparare export dei dati. Scrivi qui la tua domanda.")

TEMPORARY = "Si e' verificato un problema temporaneo. Riprova tra qualche secondo."
LIMIT = 'Ho raggiunto il limite massimo di operazioni. Prova a riformulare la richiesta in modo più specifico.'
NUDGE = ('[SISTEMA: Stai ripetendo la stessa operazione. '
         'FERMATI e rispondi all\'utente con le informazioni che hai. '
         'Se non hai dati sufficienti, spiega il problema e suggerisci '
         'di riformulare la domanda. NON chiamare strumenti.]')
INTERNAL = 'Si è verificato un errore interno. Riprova.'
TIMEOUT = 'La richiesta ha impiegato troppo tempo. Prova con una richiesta più specifica.'
BUSY_ELABORATING = 'Sto ancora elaborando la richiesta precedente. Attendi o annulla.'
THROTTLED = 'Troppi messaggi. Attendi un momento prima di riprovare.'
RESTORING = 'Ripristino dei dati in corso. Riprova al termine.'
NEEDS_AI = 'Questa funzione richiede la configurazione AI.'
CONFIG_UNAVAILABLE = 'Configurazione AI non disponibile. Contatta il proprietario dell’istanza.'


def capability_answer(ai_enabled, manual_available, saved_reports_enabled):
    if ai_enabled:
        abilities = ["cercare e riepilogare i dati dell'associazione", 'rispondere a domande sui dati', 'preparare export e report']
        if saved_reports_enabled:
            abilities.append('consultare i report salvati')
        if manual_available:
            abilities.append('consultare le istruzioni verificate del manuale')
        return 'In base alle autorizzazioni del tuo account, posso ' f"{', '.join(abilities[:-1])} e {abilities[-1]}."
    if manual_available:
        return ('Posso cercare istruzioni verificate nel manuale di Assozeta. '
                "Le analisi dei dati, gli export e la creazione di report richiedono l'attivazione dell'AI.")
    return 'In questa configurazione non sono disponibili la ricerca nel manuale né le funzioni AI.'


MANUAL_ONLY_ACTION = ("Non posso eseguire analisi dei dati, export o report perché l'AI non è configurata. "
                      'Posso cercare le istruzioni verificate nel manuale: indica la funzione che vuoi usare.')
AMBIGUOUS_MANUAL = ('Non sono riuscito a collegare la richiesta a un’istruzione verificata. '
                    'Indica la funzione del gestionale e cosa vuoi ottenere; '
                    "per analisi ed export dei dati serve l'AI configurata.")


def dumps(value):
    return json.dumps(value)


def tool_text(value):
    # _execute_tool: json.dumps(result, default=str, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def raised(message):
    # an exception inside the tool: json.dumps({'error': str(e)})
    return json.dumps({'error': message})


def frame(value):
    return {'expect': {'text': dumps(value)}}


def frame_text(text):
    return {'expect': {'text': text}}


def send(value):
    return {'send_text': value if isinstance(value, str) else dumps(value)}


def status(name):
    return frame({'type': 'status', 'status': name})


def chunk_frame(text):
    return frame({'type': 'message_chunk', 'content': text})


END = frame({'type': 'message_end'})
DONE = frame({'type': 'done'})
ACCEPTED = {'status': 101, 'protocol': None, 'extensions': None}
FORBIDDEN = {'status': 403, 'reason': 'Forbidden', 'body': '', 'content_type': 'text/plain', 'content_length': '0'}
CLOSE = [{'close': 1000}, {'expect': {'close': 1000, 'reason': ''}}, {'expect': {'eof': True}}]
CRASH = [{'expect': {'eof': True}}]


def error_frame(message):
    return frame({'type': 'error', 'message': message})


def tool_call_frame(name, arguments):
    return frame({'type': 'tool_call', 'tool': name, 'arguments': arguments})


# ---------------------------------------------------------------------------
# The stub endpoint: streamed completions (AsyncOpenAI parses SSE data lines)
# ---------------------------------------------------------------------------

def sse(*chunks, done=True):
    body = ''.join('data: ' + json.dumps(item) + '\n\n' for item in chunks)
    return body + ('data: [DONE]\n\n' if done else '')


def text_chunk(content, finish=None):
    return {'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1767225600, 'model': MODEL,
            'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': finish}]}


def finish_chunk(reason='stop', usage=True):
    value = {'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1767225600, 'model': MODEL,
             'choices': [{'index': 0, 'delta': {}, 'finish_reason': reason}]}
    if usage:
        value['usage'] = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
    return value


def call_chunk(calls):
    """calls: [(index, id or None, name or None, arguments fragment or None)]"""
    deltas = []
    for index, identifier, name, arguments in calls:
        delta = {'index': index}
        if identifier is not None:
            delta['id'] = identifier
            delta['type'] = 'function'
        function = {}
        if name is not None:
            function['name'] = name
        if arguments is not None:
            function['arguments'] = arguments
        if function:
            delta['function'] = function
        deltas.append(delta)
    return {'id': 'chatcmpl-fixture', 'object': 'chat.completion.chunk', 'created': 1767225600, 'model': MODEL,
            'choices': [{'index': 0, 'delta': {'tool_calls': deltas}, 'finish_reason': None}]}


def reply(*parts, delay=0):
    """A streamed text answer split in parts."""
    return {'status': 200, 'headers': [['Content-Type', 'text/event-stream']], 'body': sse(*[text_chunk(p) for p in parts], finish_chunk()),
            'delay': delay}


def tool_reply(*calls, content=(), delay=0):
    """Tool calls: (id, name, arguments text), each argument text sent in two fragments."""
    chunks = [text_chunk(p) for p in content]
    for index, (identifier, name, arguments) in enumerate(calls):
        middle = len(arguments) // 2
        chunks.append(call_chunk([(index, identifier, name, arguments[:middle])]))
        chunks.append(call_chunk([(index, None, None, arguments[middle:])]))
    chunks.append(finish_chunk('tool_calls'))
    return {'status': 200, 'headers': [['Content-Type', 'text/event-stream']], 'body': sse(*chunks), 'delay': delay}


def raw_reply(status, body, headers=(), delay=0):
    return {'status': status, 'headers': [list(item) for item in headers], 'body': body, 'delay': delay}


class Conversation:
    """The agent's message history (Agent.messages) as the authored outbound bodies see it."""

    def __init__(self, cap=50):
        self.messages = [{'role': 'system', 'content': SYSTEM}]
        self.cap = cap
        self.requests = []

    def user(self, text):
        bounded = text.strip()[:2000]
        self.messages.append({'role': 'user', 'content': html.escape(bounded)})
        if len(self.messages) > self.cap:
            self.messages = [self.messages[0]] + self.messages[-(self.cap - 1):]

    def request(self, *, tools=True, temperature=0.1, retry=0, model=MODEL, extra=None, key=API_KEY):
        body = {'messages': list(self.messages) + (extra or []), 'model': model, 'max_tokens': 4096, 'stream': True,
                'temperature': temperature}
        if tools:
            body.update(tool_choice='auto', tools=TOOLS)
        text = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        self.requests.append(outbound(text, retry, key))

    def assistant(self, content):
        self.messages.append({'role': 'assistant', 'content': content})

    def tool_calls(self, content, calls):
        self.messages.append({'role': 'assistant', 'content': content or None, 'tool_calls': [
            {'id': identifier, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}
            for identifier, name, arguments in calls]})

    def tool_result(self, identifier, content):
        if len(content) > 15000:
            content = content[:15000] + '\n\n... [risultato troncato, usa filtri più specifici o limit per ridurre i dati]'
        self.messages.append({'role': 'tool', 'tool_call_id': identifier, 'content': content})

    def clear(self):
        self.messages = self.messages[:1]


def outbound(body, retry=0, key=API_KEY):
    headers = {
        'host': '<<stub_host>>', 'accept-encoding': 'gzip, deflate', 'connection': 'keep-alive',
        'authorization': 'Bearer ' + key, 'accept': 'application/json', 'content-type': 'application/json',
        'user-agent': f'AsyncOpenAI/Python {SDK}', 'x-stainless-lang': 'python', 'x-stainless-package-version': SDK,
        'x-stainless-os': '<<os>>', 'x-stainless-arch': '<<arch>>', 'x-stainless-runtime': 'CPython',
        'x-stainless-runtime-version': '3.13.16', 'x-stainless-async': 'async:asyncio',
        'x-stainless-retry-count': str(retry), 'x-stainless-read-timeout': '600',
        'content-length': '<<length>>'}
    return {'method': 'POST', 'path': '/v1/chat/completions', 'headers': sorted([name, value] for name, value in headers.items()),
            'body': body}


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------
cases = []
AI = {'enabled': True}
NO_AI = {'enabled': False}


def add(name, steps=(), *, handshake=ACCEPTED, query='token={token:owner}', ai=AI, manual=True, llm=(), requests=(),
        cache=None, cache_seed=None, reports=None, kind='ws'):
    cases.append({'name': f'ws-agent-{name}', 'kind': kind, 'path': '/ws/agent/', 'query': query, 'headers': [],
                  'handshake': handshake, 'ai': ai, 'manual': manual, 'steps': list(steps), 'llm': list(llm),
                  'requests': list(requests), 'cache': cache or {}, 'cache_seed': cache_seed or {},
                  'reports': reports})


def welcome(text=WELCOME_FULL):
    return frame({'type': 'message', 'content': text})


def throttle(count, user=OWNER):
    return {f'ws_throttle:{user}': {'value': str(count), 'ttl': 60}}


LIST = [{key: report[key] for key in ('saved_report_id', 'name', 'description', 'tool_name', 'updated_at')}
        for report in sorted(REPORTS[:2], key=lambda item: item['updated_at'], reverse=True)]
assert LIST[0]['saved_report_id'] == REPORTS[1]['saved_report_id']

# --- connect --------------------------------------------------------------
add('anonymous', handshake=FORBIDDEN, query='')
add('invalid-token', handshake=FORBIDDEN, query='token={token:garbage}')
add('athlete', handshake=FORBIDDEN, query='token={token:athlete}')
# The administrator of the identity fixture owns an association: User.sport_association resolves it.
add('admin-own-association', [welcome(), *CLOSE], query='token={token:admin}')
add('owner-without-association', handshake=FORBIDDEN, query='token={token:owner_without_association}')
add('disabled-without-manual', handshake=FORBIDDEN, ai=NO_AI, manual=False)
add('unreadable-key', handshake=FORBIDDEN, ai={'enabled': True, 'api_key': '<<undecryptable>>'})
add('enabled-without-key', handshake=FORBIDDEN, ai={'enabled': True, 'api_key': ''})
add('concurrent-session', handshake=FORBIDDEN, cache_seed={f'ws_concurrent:{OWNER}': {'value': '1', 'ttl': 300}},
    cache={f'ws_concurrent:{OWNER}': {'value': '1', 'ttl': 300}})
add('impersonation-scoped-restricted', handshake=FORBIDDEN, query='token={token:owner}&impersonation={session:owner_restricted}')
add('welcome-full', [welcome(), *CLOSE])
add('welcome-manual-only', [welcome(WELCOME_MANUAL_ONLY), *CLOSE], ai=NO_AI)
add('welcome-data-only', [welcome(WELCOME_DATA), *CLOSE], manual=False)
add('welcome-environment-disabled', [welcome(WELCOME_MANUAL_ONLY), *CLOSE], ai=None)
add('restricted-collaborator', [welcome(), send({'type': 'list_reports'}), frame({'type': 'report_list', 'reports': [], 'count': 0}),
                                *CLOSE], query='token={token:restricted_collaborator}')
add('impersonated-owner', [welcome(), send({'type': 'list_reports'}),
                           frame({'type': 'report_list', 'reports': LIST, 'count': 2}), *CLOSE],
    query='token={token:admin}&impersonation={session:admin_owner}')

# --- plain messages -------------------------------------------------------
add('ping-unknown-clear-cancel', [
    welcome(),
    send({'type': 'ping', 'timestamp': 1730000000123}), frame({'type': 'pong', 'timestamp': 1730000000123}),
    send({'type': 'ping'}), frame({'type': 'pong', 'timestamp': None}),
    send({'type': 'nope'}), error_frame('Tipo di messaggio sconosciuto: nope'),
    send({}), error_frame('Tipo di messaggio sconosciuto: None'),
    send({'type': 5}), error_frame('Tipo di messaggio sconosciuto: 5'),
    send('{"type": 2.50, "type": false}'), error_frame('Tipo di messaggio sconosciuto: False'),
    send({'type': 'clear_history'}), status('history_cleared'),
    send({'type': 'cancel'}), status('idle'),
    send({'type': 'user_message', 'message': '   '}), send({'type': 'user_message'}),
    send({'type': 'ping', 'timestamp': 'after-empty'}), frame({'type': 'pong', 'timestamp': 'after-empty'}),
    *CLOSE])
# `msg_type in {...}` hashes the type first: a list or object type raises.
add('unhashable-type-crash', [welcome(), send('{"type": [1, "a", null]}'), *CRASH])
add('array-crash', [welcome(), send('[1]'), *CRASH])
add('binary-crash', [welcome(), {'send_binary': '7b7d'}, *CRASH])
add('message-not-string-crash', [welcome(), send({'type': 'user_message', 'message': 5}), *CRASH])

# --- saved reports --------------------------------------------------------
add('reports-disabled', [welcome(WELCOME_MANUAL_ONLY),
                         send({'type': 'list_reports'}), error_frame(NEEDS_AI),
                         send({'type': 'save_report', 'name': 'x'}), error_frame(NEEDS_AI),
                         send({'type': 'delete_report', 'saved_report_id': REPORTS[0]['saved_report_id']}), error_frame(NEEDS_AI),
                         *CLOSE], ai=NO_AI)
add('reports-list-delete', [
    welcome(),
    send({'type': 'list_reports'}), frame({'type': 'report_list', 'reports': LIST, 'count': 2}),
    send({'type': 'delete_report'}), error_frame('ID report mancante.'),
    send({'type': 'delete_report', 'saved_report_id': '  '}), error_frame('ID report mancante.'),
    send({'type': 'delete_report', 'saved_report_id': REPORTS[2]['saved_report_id']}), error_frame('Report non trovato.'),
    send({'type': 'delete_report', 'saved_report_id': 'not-a-uuid'}), error_frame('Errore nella cancellazione del report. Riprova.'),
    send({'type': 'delete_report', 'saved_report_id': ' ' + REPORTS[0]['saved_report_id'] + ' '}),
    frame({'type': 'report_deleted', 'saved_report_id': REPORTS[0]['saved_report_id']}),
    send({'type': 'list_reports'}), frame({'type': 'report_list', 'reports': LIST[:1], 'count': 1}),
    *CLOSE], reports={'deleted': [REPORTS[0]['saved_report_id']], 'created': []})
add('save-report-without-export', [
    welcome(),
    send({'type': 'save_report'}), error_frame('Il nome del report è obbligatorio.'),
    send({'type': 'save_report', 'name': '  '}), error_frame('Il nome del report è obbligatorio.'),
    send({'type': 'save_report', 'name': 'Senza export'}), error_frame('Nessun export disponibile da salvare. Genera prima un export.'),
    *CLOSE])
add('save-report-name-not-string-crash', [welcome(), send({'type': 'save_report', 'name': 7}), *CRASH])

# The cached export of an earlier connection (another worker wrote it).
CACHED_INFO = {'save_info': {'tool_name': 'count_data', 'params': {'model_name': 'Tags', 'filters': {'tag_name__startswith': 'WS'}},
                             'name': 'Conteggio', 'description': '', 'description_hint': '2 righe',
                             'ui_config': {'fields': [{'key': 'format', 'type': 'select'}]}},
               'filename': 'Conteggio.csv'}
add('save-report-from-cache', [
    welcome(),
    send({'type': 'save_report', 'name': ' Dalla cache ', 'description': '  '}),
    frame_text('{"type": "report_saved", "saved_report_id": "<<uuid:1>>", "name": "Dalla cache", "message": "Report \\"Dalla cache\\" salvato."}'),
    *CLOSE],
    cache_seed={f'agent_save_info:{OWNER}': {'pickle': CACHED_INFO, 'ttl': 3600}},
    cache={f'agent_save_info:{OWNER}': {'pickle': CACHED_INFO, 'ttl': 3600}},
    reports={'deleted': [], 'created': [{'saved_report_id': '<<uuid:1>>', 'name': 'Dalla cache', 'user': OWNER,
                                          'description': 'Conteggio.csv (2 righe)', 'tool_name': 'count_data',
                                          'params': CACHED_INFO['save_info']['params'], 'ui_config': CACHED_INFO['save_info']['ui_config']}]})

# --- routing without the model -------------------------------------------
CAPS = capability_answer(True, True, True)
add('capabilities-and-conversation', [
    welcome(),
    send({'type': 'user_message', 'message': 'Cosa sai fare?'}), status('thinking'), chunk_frame(CAPS), END, DONE,
    send({'type': 'user_message', 'message': '  Grazie!  '}), status('thinking'), chunk_frame('Prego. ' + CAPS), END, DONE,
    send({'type': 'user_message', 'message': 'Ciao'}), status('thinking'), chunk_frame('Ciao! ' + CAPS), END, DONE,
    send({'type': 'user_message', 'message': 'arrivederci'}), status('thinking'), chunk_frame('A presto. ' + CAPS), END, DONE,
    *CLOSE], cache=throttle(4))
add('manual-only-routes', [
    welcome(WELCOME_MANUAL_ONLY),
    send({'type': 'user_message', 'message': 'Cosa sai fare?'}), status('thinking'), chunk_frame(capability_answer(False, True, True)), END, DONE,
    send({'type': 'user_message', 'message': 'Esporta i pagamenti'}), status('thinking'), chunk_frame(MANUAL_ONLY_ACTION), END, DONE,
    send({'type': 'user_message', 'message': 'Il gatto dorme'}), status('thinking'), status('querying'),
    tool_call_frame('search_manual', {'query': 'Il gatto dorme'}), chunk_frame(AMBIGUOUS_MANUAL), END, DONE,
    *CLOSE], ai=NO_AI, cache=throttle(3))

ATTACHMENT = {'id': SECTION['id'], 'page': SECTION['page'], 'title': SECTION['title'], 'page_title': SECTION['page_title'],
              'reader': [{'screenshots': [], 'text': SECTION['text'], 'title': SECTION['title']}],
              'screenshots': [{'path': 'images/tag.png', 'sha256': '<<png_sha256>>', 'url': '/api/manuale/assets/images/tag.png'}],
              'embedded_url': '/#/manuale?section=docs%2Ftag%23creare'}
GROUNDED = SECTION['text'] + '\n\n[Leggi nel manuale](' + SECTION['url'] + ')'
add('manual-faq', [
    welcome(),
    send({'type': 'user_message', 'message': 'Come posso creare un tag?'}), status('thinking'), status('querying'),
    tool_call_frame('search_manual', {'query': 'Come posso creare un tag?'}), chunk_frame(GROUNDED),
    frame({'type': 'manual_section', 'section': ATTACHMENT}), END, DONE,
    send({'type': 'user_message', 'message': 'Mostrami le lacune del manuale'}), status('thinking'), status('querying'),
    tool_call_frame('get_manual_gaps', {}), chunk_frame('Le lacune del manuale sono riservate al proprietario dell’istanza.'), END, DONE,
    *CLOSE], cache=throttle(2))

# --- the model loop -------------------------------------------------------
c = Conversation()
c.user('Quanti soci ho?')
c.request()
c.assistant('Hai 3 soci attivi.')
c.user('E i corsi <b>?')
c.request()
c.assistant('Ci sono 2 corsi?')
add('loop-text-answers', [
    welcome(),
    send({'type': 'user_message', 'message': 'Quanti soci ho?'}), status('thinking'),
    chunk_frame('Hai 3 '), chunk_frame('soci attivi.'), END, DONE,
    send({'type': 'user_message', 'message': 'E i corsi <b>?'}), status('thinking'),
    chunk_frame('Ci sono 2 corsi?'), END, DONE,
    *CLOSE], llm=[reply('Hai 3 ', 'soci attivi.'), reply('Ci sono 2 corsi?')], requests=c.requests, cache=throttle(2))

c = Conversation()
c.user('Pulisci il nome a/b')
c.request()
calls = [('call_san', 'sanitize_text', {'text': 'a/b: è?'})]
c.tool_calls('', calls)
c.tool_result('call_san', tool_text({'result': 'a-b- è'}))
c.request()
c.assistant('Fatto: a-b- è')
c.clear()
c.user('Ancora')
c.request()
c.assistant('Ok')
add('loop-tool-call-then-clear', [
    welcome(),
    send({'type': 'user_message', 'message': 'Pulisci il nome a/b'}), status('thinking'), status('querying'),
    tool_call_frame('sanitize_text', {'text': 'a/b: è?'}), chunk_frame('Fatto: a-b- è'), END, DONE,
    send({'type': 'clear_history'}), status('history_cleared'),
    send({'type': 'user_message', 'message': 'Ancora'}), status('thinking'), chunk_frame('Ok'), END, DONE,
    *CLOSE], llm=[tool_reply(('call_san', 'sanitize_text', json.dumps({'text': 'a/b: è?'}))), reply('Fatto: a-b- è'), reply('Ok')],
    requests=c.requests, cache=throttle(2))

# Several tool calls in one response; results in call order; tool errors.
c = Conversation()
c.user('Fammi vedere i report salvati')
c.request()
calls = [('c1', 'list_reports', {'user_id': FOREIGN_OWNER}), ('c2', 'nope_tool', {'x': 1}), ('c3', 'sanitize_text', {}),
         ('c4', 'get_schema', {'model_name': 'Nope'}), ('c5', 'count_data', {'model_name': 'Tags', 'filters': {'tag_name__startswith': 'WSAgent'},
                                                                           'include_archived': None})]
c.tool_calls('Controllo.', calls)
c.tool_result('c1', tool_text({'reports': LIST, 'count': 2}))
c.tool_result('c2', json.dumps({'error': 'Unknown tool: nope_tool'}))
c.tool_result('c3', raised("tool_sanitize_text() missing 1 required positional argument: 'text'"))
c.tool_result('c4', tool_text({'error': 'Model Nope not found'}))
c.tool_result('c5', tool_text({'count': 2}))
c.request()
c.assistant('Hai 2 report.')
add('loop-several-tools', [
    welcome(),
    send({'type': 'user_message', 'message': 'Fammi vedere i report salvati'}), status('thinking'),
    chunk_frame('Controllo.'), END,
    status('querying'), tool_call_frame('list_reports', {'user_id': FOREIGN_OWNER}),
    status('querying'), tool_call_frame('nope_tool', {'x': 1}),
    status('querying'), tool_call_frame('sanitize_text', {}),
    status('querying'), tool_call_frame('get_schema', {'model_name': 'Nope'}),
    status('querying'), tool_call_frame('count_data', {'model_name': 'Tags', 'filters': {'tag_name__startswith': 'WSAgent'}, 'include_archived': None}),
    chunk_frame('Hai 2 report.'), END, DONE,
    *CLOSE], llm=[tool_reply(*[(i, n, json.dumps(a)) for i, n, a in calls], content=['Controllo.']), reply('Hai 2 report.')],
    requests=c.requests, cache=throttle(1))

# Invalid and non-object arguments; a missing tool id and name.
c = Conversation()
c.user('Conta i tag')
c.request()
calls = [('bad', 'sanitize_text', {})]
c.tool_calls('', calls)
c.tool_result('bad', raised("tool_sanitize_text() missing 1 required positional argument: 'text'"))
c.request()
c.assistant('Non riesco.')
add('loop-invalid-arguments', [
    welcome(),
    send({'type': 'user_message', 'message': 'Conta i tag'}), status('thinking'), status('querying'),
    tool_call_frame('sanitize_text', {}), chunk_frame('Non riesco.'), END, DONE,
    *CLOSE], llm=[tool_reply(('bad', 'sanitize_text', '{"text": ')), reply('Non riesco.')], requests=c.requests, cache=throttle(1))
add('loop-list-arguments-internal-error', [
    welcome(),
    send({'type': 'user_message', 'message': 'Conta i tag'}), status('thinking'), status('querying'),
    tool_call_frame('count_data', [1]), error_frame(INTERNAL), DONE,
    *CLOSE], llm=[tool_reply(('lst', 'count_data', '[1]'))], requests=c.requests[:1], cache=throttle(1))

# The full schema is cached for the connection: the second call answers from memory.
c = Conversation()
c.user('Che dati ci sono?')
c.request()
c.tool_calls('', [('s1', 'get_schema', {})])
c.tool_result('s1', '<<schema>>')
c.request()
c.tool_calls('', [('s2', 'get_schema', {'model_name': None})])
c.tool_result('s2', '<<schema>>')
c.request()
c.assistant('Ci sono molti modelli.')
add('loop-schema-cache', [
    welcome(),
    send({'type': 'user_message', 'message': 'Che dati ci sono?'}), status('thinking'),
    status('querying'), tool_call_frame('get_schema', {}),
    status('querying'), tool_call_frame('get_schema', {'model_name': None}),
    chunk_frame('Ci sono molti modelli.'), END, DONE,
    *CLOSE], llm=[tool_reply(('s1', 'get_schema', '{}')), tool_reply(('s2', 'get_schema', '{"model_name": null}')),
                  reply('Ci sono molti modelli.')], requests=c.requests, cache=throttle(1))

# Three identical calls: the forced answer without tools (the nudge is not kept).
c = Conversation()
c.user('Ripeti')
c.request()
same = {'text': 'x'}
c.tool_calls('', [('r1', 'sanitize_text', same), ('r2', 'sanitize_text', same), ('r3', 'sanitize_text', same)])
for identifier in ('r1', 'r2', 'r3'):
    c.tool_result(identifier, tool_text({'result': 'x'}))
c.request(tools=False, extra=[{'role': 'user', 'content': NUDGE}])
c.assistant('Mi fermo qui.')
c.user('Quale formato?')
c.request()
add('loop-detection', [
    welcome(),
    send({'type': 'user_message', 'message': 'Ripeti'}), status('thinking'),
    status('querying'), tool_call_frame('sanitize_text', same),
    status('querying'), tool_call_frame('sanitize_text', same),
    status('querying'), tool_call_frame('sanitize_text', same),
    chunk_frame('Mi fermo qui.'), END, DONE,
    send({'type': 'user_message', 'message': 'Quale formato?'}), status('thinking'), DONE,
    *CLOSE], llm=[tool_reply(*[(i, 'sanitize_text', '{"text": "x"}') for i in ('r1', 'r2', 'r3')]), reply('Mi fermo qui.'),
                  {'status': 200, 'headers': [['Content-Type', 'text/event-stream']], 'body': sse(finish_chunk(usage=False)), 'delay': 0}],
    requests=c.requests, cache=throttle(2))

# The iteration limit (max_iterations=2 in this configuration).
c = Conversation()
c.user('Cerca tutto')
c.request()
c.tool_calls('', [('i1', 'sanitize_text', {'text': 'a'})])
c.tool_result('i1', tool_text({'result': 'a'}))
c.request()
c.tool_calls('', [('i2', 'sanitize_text', {'text': 'b'})])
c.tool_result('i2', tool_text({'result': 'b'}))
add('loop-iteration-limit', [
    welcome(),
    send({'type': 'user_message', 'message': 'Cerca tutto'}), status('thinking'),
    status('querying'), tool_call_frame('sanitize_text', {'text': 'a'}),
    status('querying'), tool_call_frame('sanitize_text', {'text': 'b'}),
    error_frame(LIMIT), DONE, *CLOSE],
    ai={'enabled': True, 'max_iterations': 2},
    llm=[tool_reply(('i1', 'sanitize_text', '{"text": "a"}')), tool_reply(('i2', 'sanitize_text', '{"text": "b"}'))],
    requests=c.requests, cache=throttle(1))

# Provider failures: a 400 (no retry), SDK retries of 500/429, the agent's own retry of tool validation errors.
c = Conversation()
c.user('Prima')
c.request()
c.user('Seconda')
c.request(retry=0)
c.request(retry=1)
c.request(retry=2)
c.user('Terza')
c.request()
c.request(temperature=0.3)
c.request(temperature=0.4)
c.user('Quarta')
c.request()
c.request(retry=1)
c.assistant('Recuperato.')
FAST = [['retry-after-ms', '1']]
add('provider-errors', [
    welcome(),
    send({'type': 'user_message', 'message': 'Prima'}), status('thinking'), error_frame(TEMPORARY), DONE,
    send({'type': 'user_message', 'message': 'Seconda'}), status('thinking'), error_frame(TEMPORARY), DONE,
    send({'type': 'user_message', 'message': 'Terza'}), status('thinking'), error_frame(TEMPORARY), DONE,
    send({'type': 'user_message', 'message': 'Quarta'}), status('thinking'), chunk_frame('Recuperato.'), END, DONE,
    *CLOSE], llm=[
        raw_reply(400, '{"error": {"message": "bad request", "type": "invalid_request_error"}}', [['Content-Type', 'application/json']]),
        raw_reply(500, '{"error": {"message": "boom"}}', [['Content-Type', 'application/json'], *FAST]),
        raw_reply(503, 'unavailable', [*FAST]),
        raw_reply(429, '', [*FAST]),
        raw_reply(400, '{"error": {"message": "Tool call validation failed: x", "code": "tool_use_failed"}}', [['Content-Type', 'application/json']]),
        raw_reply(200, sse({'error': {'message': 'tool_use_failed upstream'}}), [['Content-Type', 'text/event-stream']]),
        raw_reply(400, '{"error": "Tool Call Validation Failed"}', [['x-should-retry', 'false']]),
        raw_reply(409, 'conflict', [*FAST]),
        reply('Recuperato.'),
    ], requests=c.requests, cache=throttle(4))

# Streams the SDK reads leniently, and non-JSON data (an error after the chunks were sent).
c = Conversation()
c.user('Uno')
c.request()
c.assistant('Parziale')
c.user('Due')
c.request()
add('stream-shapes', [
    welcome(),
    send({'type': 'user_message', 'message': 'Uno'}), status('thinking'), chunk_frame('Parz'), chunk_frame('iale'), END, DONE,
    send({'type': 'user_message', 'message': 'Due'}), status('thinking'), chunk_frame('Inizio'), error_frame(TEMPORARY), DONE,
    *CLOSE], llm=[
        raw_reply(200, ': comment\n\nevent: message\ndata: ' + json.dumps({'choices': []}) + '\n\n' +
                  'data: ' + json.dumps({'choices': [{'delta': {'content': 'Parz'}}]}) + '\r\n\r\n' +
                  'data: ' + json.dumps({'choices': None, 'usage': None}) + '\n\n' +
                  'data: ' + json.dumps({'choices': [{'delta': None, 'finish_reason': None}]}) + '\n\n' +
                  'data: ' + json.dumps({'choices': [{'delta': {'content': 'iale', 'tool_calls': None}, 'finish_reason': 'stop'}]}) + '\n\n' +
                  'data: [DONE]\n\ndata: ' + json.dumps({'choices': [{'delta': {'content': 'ignored'}}]}) + '\n\n',
                  [['Content-Type', 'text/event-stream']]),
        raw_reply(200, 'data: ' + json.dumps({'choices': [{'delta': {'content': 'Inizio'}}]}) + '\n\ndata: {not json}\n\n',
                  [['Content-Type', 'text/event-stream']]),
    ], requests=c.requests, cache=throttle(2))

# A pending action makes a short reply an action; history cap; html escaping and the 2000-character bound.
long_message = 'x' * 1990 + ' & <tag> "q" \'s' + 'y' * 50
c = Conversation(cap=4)
c.user('Esporta i pagamenti')
c.request()
c.assistant('In quale formato preferisci?')
c.user('ok')
c.request()
c.assistant('Ecco fatto.')
# The conversation answer is recorded in history too.
c.user('ok')
c.assistant('Prego. ' + CAPS)
c.user(long_message)
c.request()
c.assistant('Lungo.')
add('pending-action-and-history-cap', [
    welcome(),
    send({'type': 'user_message', 'message': 'Esporta i pagamenti'}), status('thinking'), chunk_frame('In quale formato preferisci?'), END, DONE,
    send({'type': 'user_message', 'message': 'ok'}), status('thinking'), chunk_frame('Ecco fatto.'), END, DONE,
    send({'type': 'user_message', 'message': 'ok'}), status('thinking'), chunk_frame('Prego. ' + CAPS), END, DONE,
    send({'type': 'user_message', 'message': long_message}), status('thinking'), chunk_frame('Lungo.'), END, DONE,
    *CLOSE], ai={'enabled': True, 'history_cap': 4},
    llm=[reply('In quale formato preferisci?'), reply('Ecco fatto.'), reply('Lungo.')], requests=c.requests, cache=throttle(4))

# --- exports, deferred frames and saved reports ---------------------------
EXPORT_ARGS = {'model_name': 'Tags', 'format': 'csv', 'fields': ['tag_name'], 'filters': {'tag_name__startswith': 'WSAgent'},
               'order_by': ['tag_name'], 'title': 'Etichette', 'user_id': 'ignored'}
CSV = '﻿tag_name\r\nWSAgent Alfa\r\nWSAgent Beta\r\n'
DATA = base64.b64encode(CSV.encode('utf-8')).decode()
SAVE_INFO = {
    'tool_name': 'export_data',
    'params': {'model_name': 'Tags', 'format': 'csv', 'title': 'Etichette', 'fields': ['tag_name'],
               'filters': {'tag_name__startswith': 'WSAgent'}, 'order_by': ['tag_name']},
    'name': 'Etichette - <<today_dmy>>', 'description': 'Export Etichette (CSV) - 2 righe, 1 filtro applicato',
    'description_hint': '2 righe, 1 filtro applicato',
    'ui_config': {'fields': [{'key': 'format', 'type': 'select', 'label': 'Formato', 'options': ['csv', 'xlsx', 'pdf']},
                             {'key': 'filters', 'type': 'json', 'label': 'Filtri'}]},
    'model_name': 'Tags'}
EXPORT = {'type': 'export_ready', 'filename': 'Etichette.csv', 'content_type': 'text/csv; charset=utf-8', 'data_base64': DATA,
          'row_count': 2, 'total_count': 2, 'save_info': SAVE_INFO}
EXPORT_TEXT = tool_text(EXPORT)
SAVE_ARGS = {'name': 'Report agente', 'tool_name': 'count_data', 'params': {'model_name': 'Tags'}, 'user_id': FOREIGN_OWNER}
c = Conversation()
c.user('Esporta i tag')
c.request()
c.tool_calls('', [('e1', 'export_data', EXPORT_ARGS), ('e2', 'save_report', SAVE_ARGS)])
c.tool_result('e1', EXPORT_TEXT)
c.tool_result('e2', tool_text({'saved_report_id': '<<uuid:1>>', 'name': 'Report agente', 'message': 'Report "Report agente" salvato.'}))
c.request()
c.assistant('Ecco il file.')
READY = {'type': 'export_ready', 'filename': 'Etichette.csv', 'content_type': 'text/csv; charset=utf-8', 'data_base64': DATA,
         'row_count': 2, 'can_save': True, 'total_count': 2, 'default_name': 'Etichette - <<today_dmy>>',
         'default_description': SAVE_INFO['description'], 'description_hint': SAVE_INFO['description_hint']}
add('export-and-save', [
    welcome(),
    send({'type': 'user_message', 'message': 'Esporta i tag'}), status('thinking'),
    status('querying'), tool_call_frame('export_data', EXPORT_ARGS),
    status('querying'), tool_call_frame('save_report', SAVE_ARGS),
    chunk_frame('Ecco il file.'), END,
    frame(READY),
    frame_text('{"type": "report_saved", "saved_report_id": "<<uuid:1>>", "name": "Report agente"}'),
    DONE,
    send({'type': 'save_report', 'name': 'Etichette salvate', 'description': ' Mie '}),
    frame_text('{"type": "report_saved", "saved_report_id": "<<uuid:2>>", "name": "Etichette salvate", "message": "Report \\"Etichette salvate\\" salvato."}'),
    *CLOSE], llm=[tool_reply(('e1', 'export_data', json.dumps(EXPORT_ARGS)), ('e2', 'save_report', json.dumps(SAVE_ARGS))),
                  reply('Ecco il file.')],
    requests=c.requests, cache={**throttle(1), f'agent_save_info:{OWNER}': {'pickle_export': EXPORT_TEXT, 'ttl': 3600}},
    reports={'deleted': [], 'created': [
        {'saved_report_id': '<<uuid:1>>', 'name': 'Report agente', 'user': OWNER, 'description': '', 'tool_name': 'count_data',
         'params': {'model_name': 'Tags'}, 'ui_config': {}},
        {'saved_report_id': '<<uuid:2>>', 'name': 'Etichette salvate', 'user': OWNER, 'description': 'Mie',
         'tool_name': 'export_data', 'params': SAVE_INFO['params'], 'ui_config': SAVE_INFO['ui_config']}]})

# Manual tools inside the loop: no evidence, then one verified hit (score as ManualIndex.search computes it).
weight = math.log(1 + (1 - 1 + .5) / (1 + .5))
bm25 = weight * 3 * 2.2 / (3 + 1.2 * (.25 + .75 * 1))
lexical = bm25 / (1 + bm25)
score = round(.75 * (.7 * lexical + .3 * 1.0) * 1 + .25 * 1 + .2 * 0, 6)
PUBLIC = {'id': SECTION['id'], 'page': SECTION['page'], 'section': SECTION['section'], 'title': SECTION['title'],
          'text': SECTION['text'], 'status': 'verified', 'url': SECTION['url'],
          'screenshots': ATTACHMENT['screenshots'], 'scenario_ids': ['fixture'], 'page_title': 'Etichette', 'language': 'it',
          'intent': 'create', 'reader': ATTACHMENT['reader'], 'page_description': 'Gestione etichette', 'page_order': 1,
          'section_order': 1, 'embedded_url': ATTACHMENT['embedded_url'], 'version': '<<release>>',
          'application_revision': '<<revision>>', 'manual_revision': 'c' * 40, 'features': [], 'audience': 'public',
          'corpus_identity': '<<corpus_identity>>', 'code_state': 'committed', 'publication_status': 'published', 'score': score}
c = Conversation()
c.user('Dimmi come si fa')
c.request()
c.tool_calls('', [('m1', 'search_manual', {'query': 'zzz', 'limit': 3, 'user_id': 'x'}), ('m2', 'search_manual', {'query': 'tag'}),
                  ('m3', 'get_manual_section', {})])
c.tool_result('m1', tool_text({'status': 'no_evidence', 'results': [], 'message': 'Non ho trovato istruzioni verificate per questa domanda.'}))
c.tool_result('m2', tool_text({'status': 'verified', 'results': [PUBLIC], 'message': ''}))
c.tool_result('m3', raised("tool_get_manual_section() missing 1 required positional argument: 'section_id'"))
c.request()
c.assistant('Vedi il manuale.')
add('loop-manual-tools', [
    welcome(),
    send({'type': 'user_message', 'message': 'Dimmi come si fa'}), status('thinking'),
    status('querying'), tool_call_frame('search_manual', {'query': 'zzz', 'limit': 3, 'user_id': 'x'}),
    status('querying'), tool_call_frame('search_manual', {'query': 'tag'}),
    status('querying'), tool_call_frame('get_manual_section', {}),
    chunk_frame('Vedi il manuale.'), END, DONE, *CLOSE],
    llm=[tool_reply(('m1', 'search_manual', '{"query": "zzz", "limit": 3, "user_id": "x"}'), ('m2', 'search_manual', '{"query": "tag"}'),
                    ('m3', 'get_manual_section', '{}')), reply('Vedi il manuale.')],
    requests=c.requests, cache=throttle(1))

# Tool call deltas arrive by index out of order: the calls run sorted by index.
c = Conversation()
c.user('Due strumenti')
c.request()
c.tool_calls('', [('a0', 'sanitize_text', {'text': 'a'}), ('b1', 'sanitize_text', {'text': 'b'})])
c.tool_result('a0', tool_text({'result': 'a'}))
c.tool_result('b1', tool_text({'result': 'b'}))
c.request()
c.assistant('Ok.')
ORDER_STREAM = sse(call_chunk([(1, 'b1', 'sanitize_text', '{"text": ')]), call_chunk([(0, 'a0', 'sanitize_text', '{"text": "a"}')]),
                   call_chunk([(1, None, None, '"b"}')]), finish_chunk('tool_calls'))
add('stream-tool-index-order', [
    welcome(),
    send({'type': 'user_message', 'message': 'Due strumenti'}), status('thinking'),
    status('querying'), tool_call_frame('sanitize_text', {'text': 'a'}),
    status('querying'), tool_call_frame('sanitize_text', {'text': 'b'}),
    chunk_frame('Ok.'), END, DONE, *CLOSE],
    llm=[raw_reply(200, ORDER_STREAM, [['Content-Type', 'text/event-stream']]), reply('Ok.')], requests=c.requests, cache=throttle(1))

# An export made before a provider failure is still delivered after the error.
ARGS = {k: v for k, v in EXPORT_ARGS.items() if k != 'user_id'}
c = Conversation()
c.user('Esporta e poi fallisci')
c.request()
c.tool_calls('', [('x1', 'export_data', ARGS)])
c.tool_result('x1', EXPORT_TEXT)
c.request()
add('export-then-provider-error', [
    welcome(),
    send({'type': 'user_message', 'message': 'Esporta e poi fallisci'}), status('thinking'),
    status('querying'), tool_call_frame('export_data', ARGS),
    error_frame(TEMPORARY), frame(READY), DONE, *CLOSE],
    llm=[tool_reply(('x1', 'export_data', json.dumps(ARGS))), raw_reply(400, '{"error": {"message": "no"}}')],
    requests=c.requests, cache={**throttle(1), f'agent_save_info:{OWNER}': {'pickle_export': EXPORT_TEXT, 'ttl': 3600}})

# --- concurrency, cancellation, timeout, throttle, configuration ----------
c = Conversation()
c.user('Lenta')
c.request()
c.user('Dopo')
c.request()
c.assistant('Subito.')
add('busy-and-cancel', [
    welcome(),
    send({'type': 'user_message', 'message': 'Lenta'}), status('thinking'), {'wait_requests': 1},
    send({'type': 'user_message', 'message': 'Altra'}), error_frame(BUSY_ELABORATING),
    send({'type': 'ping', 'timestamp': 1}), frame({'type': 'pong', 'timestamp': 1}),
    send({'type': 'cancel'}), status('cancelling'), status('cancelled'), DONE,
    send({'type': 'cancel'}), status('idle'),
    send({'type': 'user_message', 'message': 'Dopo'}), status('thinking'), chunk_frame('Subito.'), END, DONE,
    *CLOSE], llm=[reply('mai', delay=6), reply('Subito.')], requests=c.requests, cache=throttle(2))
c = Conversation()
c.user('Lentissima')
c.request()
add('timeout', [
    welcome(),
    send({'type': 'user_message', 'message': 'Lentissima'}), status('thinking'), error_frame(TIMEOUT), DONE,
    *CLOSE], ai={'enabled': True, 'ws_timeout': 1}, llm=[reply('tardi', delay=4)], requests=c.requests, cache=throttle(1))
c = Conversation()
c.user('Uno')
c.request()
c.assistant('A')
c.user('ciao')
c.assistant('Ciao! ' + CAPS)
c.user('Due')
c.request(model='fixture-model-2')
c.assistant('B')
add('throttle-and-refresh', [
    welcome(),
    send({'type': 'user_message', 'message': 'Uno'}), status('thinking'), chunk_frame('A'), END, DONE,
    send({'type': 'user_message', 'message': 'ciao'}), status('thinking'), chunk_frame('Ciao! ' + CAPS), END, DONE,
    send({'type': 'user_message', 'message': 'grazie'}), error_frame(THROTTLED),
    {'produce': {'ai': {'enabled': True, 'ws_rate_limit': 4, 'model': 'fixture-model-2'}}},
    send({'type': 'user_message', 'message': 'Due'}), status('thinking'), chunk_frame('B'), END, DONE,
    send({'type': 'user_message', 'message': 'Tre'}), error_frame(THROTTLED),
    {'produce': {'ai': {'enabled': True, 'api_key': '<<undecryptable>>'}}},
    send({'type': 'user_message', 'message': 'Quattro'}), error_frame(CONFIG_UNAVAILABLE),
    {'produce': {'ai': {'enabled': False}}},
    send({'type': 'user_message', 'message': 'Esporta i soci'}), error_frame(THROTTLED),
    send({'type': 'list_reports'}), error_frame(NEEDS_AI),
    *CLOSE], ai={'enabled': True, 'ws_rate_limit': 2}, llm=[reply('A'), reply('B')], requests=c.requests, cache=throttle(6))
# The lease is checked when the task (or handler) starts: no status frame, the save and delete never run.
add('restore-lease', [
    welcome(),
    {'produce': {'restore': 'running'}},
    send({'type': 'user_message', 'message': 'Durante il ripristino'}), error_frame(RESTORING), DONE,
    send({'type': 'save_report', 'name': 'Bloccato'}), error_frame(RESTORING), DONE,
    send({'type': 'delete_report', 'saved_report_id': REPORTS[0]['saved_report_id']}), error_frame(RESTORING), DONE,
    send({'type': 'list_reports'}), frame({'type': 'report_list', 'reports': LIST, 'count': 2}),
    {'produce': {'restore': None}},
    *CLOSE], cache=throttle(1))

# ---------------------------------------------------------------------------
# run_agent (CLI transcripts)
# ---------------------------------------------------------------------------
GREY, CYAN, GREEN, YELLOW, RED, RESET = '\x1b[90m', '\x1b[36m', '\x1b[32m', '\x1b[33m', '\x1b[31m', '\x1b[0m'
BANNER = f'\nAgent CLI per "{DENOMINATION}"\nScrivi un messaggio o "exit" per uscire.\n'
PROMPT = '\n> '


def cli(name, stdin, stdout, stderr='', *, association=OWN, ai=AI, manual=True, llm=(), requests=(), exit_code=0, reports=None):
    cases.append({'name': f'run-agent-{name}', 'kind': 'cli', 'association': association, 'stdin': stdin, 'stdout': stdout,
                  'stderr': stderr, 'exit': exit_code, 'ai': ai, 'manual': manual, 'llm': list(llm), 'requests': list(requests),
                  'cache': {}, 'cache_seed': {}, 'reports': reports})


def status_line(name):
    return f'{GREY}[{name}]{RESET}\n'


def tool_line(name, arguments):
    text = json.dumps(arguments, ensure_ascii=False, indent=2)
    if len(text) > 500:
        text = text[:500] + '...'
    return f'{CYAN}>> {name}{RESET}\n{GREY}{text}{RESET}\n'


CLI_CAPS = capability_answer(True, True, False)
cli('missing-association', 'ciao\n', '', 'Sport association a1000000-0000-4000-8000-000000799999 not found\n',
    association='a1000000-0000-4000-8000-000000799999')
cli('disabled', 'ciao\n', '', 'Il bot AI è disattivato per questa istanza.\n', ai=NO_AI)
cli('direct-answers', 'Cosa sai fare?\n\nclear\nCome posso creare un tag?\nESCI\nnon letto\n',
    BANNER + PROMPT + status_line('thinking') + f'{GREEN}{CLI_CAPS}{RESET}\n' + PROMPT + PROMPT + 'Cronologia cancellata.\n' + PROMPT
    + status_line('thinking') + status_line('querying') + tool_line('search_manual', {'query': 'Come posso creare un tag?'})
    + f'{GREEN}{GROUNDED}{RESET}\n' + PROMPT)
c = Conversation()
c.user('Pulisci a/b')
c.request()
c.tool_calls('', [('k1', 'sanitize_text', {'text': 'a/b'}), ('k2', 'list_reports', {}), ('k3', 'save_report', {'name': 'x', 'tool_name': 't', 'params': {}})])
c.tool_result('k1', tool_text({'result': 'a-b'}))
c.tool_result('k2', tool_text({'error': 'user_id is required to list reports'}))
c.tool_result('k3', tool_text({'error': 'user_id is required to save a report'}))
c.request()
c.assistant('Fatto.')
c.user('Esporta i tag')
c.request()
c.tool_calls('', [('k4', 'export_data', {k: v for k, v in EXPORT_ARGS.items() if k != 'user_id'})])
c.tool_result('k4', EXPORT_TEXT)
c.request()
c.assistant('Pronto.')
c.user('Errore')
c.request()
cli('loop', 'Pulisci a/b\nEsporta i tag\nErrore\n',
    BANNER + PROMPT + status_line('thinking')
    + status_line('querying') + tool_line('sanitize_text', {'text': 'a/b'})
    + status_line('querying') + tool_line('list_reports', {})
    + status_line('querying') + tool_line('save_report', {'name': 'x', 'tool_name': 't', 'params': {}})
    + f'{GREEN}Fatto.{RESET}\n' + PROMPT
    + status_line('thinking') + status_line('querying') + tool_line('export_data', {k: v for k, v in EXPORT_ARGS.items() if k != 'user_id'})
    + f'{GREEN}Pronto.{RESET}\n' + f'{YELLOW}[Export] Etichette.csv (2 righe){RESET}\n' + PROMPT
    + status_line('thinking') + PROMPT,
    f'{RED}[Errore] {TEMPORARY}{RESET}\n',
    llm=[tool_reply(('k1', 'sanitize_text', '{"text": "a/b"}'), ('k2', 'list_reports', '{}'),
                    ('k3', 'save_report', '{"name": "x", "tool_name": "t", "params": {}}')),
         reply('Fatto.'),
         tool_reply(('k4', 'export_data', json.dumps({k: v for k, v in EXPORT_ARGS.items() if k != 'user_id'}))),
         reply('Pronto.'),
         raw_reply(400, '{"error": "no"}')],
    requests=c.requests)
c = Conversation()
c.user('Uno')
c.request()
# The stub disables the AI configuration when it receives the first request.
cli('disabled-mid-session', 'Uno\nDue\n', BANNER + PROMPT + status_line('thinking') + f'{GREEN}A{RESET}\n' + PROMPT + PROMPT,
    'Il bot AI è disattivato per questa istanza.\n', llm=[{**reply('A'), 'disable_ai': True}], requests=c.requests)

# An exception in the consumer ends the connection without websocket.disconnect:
# the concurrency slot is not released and expires with its TTL.
# The CLI agent has no user: a user_id argument from the model is used as given.
CLI_SAVE = {'name': 'Dal CLI', 'tool_name': 'count_data', 'params': {}, 'user_id': OWNER}
c = Conversation()
c.user('Salva il conteggio')
c.request()
c.tool_calls('', [('s1', 'save_report', CLI_SAVE)])
c.tool_result('s1', tool_text({'saved_report_id': '<<uuid:1>>', 'name': 'Dal CLI', 'message': 'Report "Dal CLI" salvato.'}))
c.request()
c.assistant('Salvato.')
cli('save-with-argument-user', 'Salva il conteggio\n',
    BANNER + PROMPT + status_line('thinking') + status_line('querying') + tool_line('save_report', CLI_SAVE)
    + f'{GREEN}Salvato.{RESET}\n' + f'{YELLOW}[Report salvato] Dal CLI{RESET}\n' + PROMPT,
    llm=[tool_reply(('s1', 'save_report', json.dumps(CLI_SAVE))), reply('Salvato.')], requests=c.requests,
    reports={'deleted': [], 'created': [{'saved_report_id': '<<uuid:1>>', 'name': 'Dal CLI', 'user': OWNER, 'description': '',
                                          'tool_name': 'count_data', 'params': {}, 'ui_config': {}}]})

for case in cases:
    if case['kind'] == 'ws' and case['steps'][-1:] == CRASH and len(case['steps']) > 1 and case['steps'][-2] != {'expect': {'close': 1000, 'reason': ''}}:
        case['cache'] = {**case['cache'], f'ws_concurrent:{OWNER}': {'value': '1', 'ttl': 300}}
for case in cases:
    if case['kind'] == 'ws':
        assert case['handshake']['status'] != 101 or case['steps'][-1] == {'expect': {'eof': True}}, case['name']
names = [case['name'] for case in cases]
assert len(names) == len(set(names))
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'ws/agent/ (AgentConsumer) and manage.py run_agent against a scripted OpenAI-compatible endpoint',
    'ids': {**IDS, 'admin': ADMIN}, 'denomination': DENOMINATION, 'api_key': API_KEY, 'model': MODEL,
    'ai_defaults': {'model': MODEL, 'cheap_model': 'fixture-cheap', 'max_iterations': 3, 'max_results': 5000, 'query_timeout': 10,
                    'ws_rate_limit': 5, 'ws_timeout': 20, 'history_cap': 50},
    'reports': REPORTS, 'tags': TAGS, 'section': SECTION, 'png_hex': PNG.hex(),
    'cases': cases}, indent=1, ensure_ascii=False) + '\n')
print(len(cases), 'cases')
