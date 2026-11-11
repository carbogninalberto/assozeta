"""Author the API v2 handoff HTTP sequence (handoff.md).

Go refuses some inputs it has not ported (refusal.ErrNotPorted sentinels) with
the stable 500 page before any write. With DJANGO_HANDOFF_URL set, Go replays
such a request to Django and streams Django's response back. This sequence takes
refused inputs from three families and expects, on every transport, exactly the
response Django gives (and, for writes, Django's effect):

- personas-reads: ported controls only. Searches in a non-Latin script are served
  by Go (no marker); Django's search matches nothing, so the response is the empty
  page of personas-reads-list-search-underscore-escaped. Go keeps no refusal on a
  personas read that this sequence can rely on, so the family carries no hand-off
  case (the `sort[field]` relation paths are kept refused with no fixed result,
  v2-refusals.md, personas-reads).
- course-list: `GET course/list` ordered by a relation path
  (ErrCourseListUnported). With `query[status_flag]=1` one course is listed, so
  the response is the page of course-list-sort-relation-attname.
- workflow-update: `PATCH communications/workflows/<id>/update` with a form or
  multipart `automation_tree` (ErrAutomationWorkflowFormTreeNotPorted). Django
  parses the field with json.loads: a valid tree is saved (200, audited), an
  invalid one is 400 `workflow not valid.`; the audit entry of a saved update
  carries the correlation id the client sent to Go.

Each family also has one ported control case that Go serves itself. Cases marked
`handoff` must be attributed to Django on the Go transports and carry
`X-Assozeta-Handoff: django`; the others must not. Responses and effects reuse the
source families' authored values (and their contracts), not values copied from
a run. Run with ``python3 -I`` after the source families' generators.
"""
import copy
import json
from pathlib import Path
from urllib.parse import urlencode

HERE = Path(__file__).parent
OUTPUT = HERE / 'django_handoff_http.json'
SOURCES = {name: json.loads((HERE / f'{name.replace("-", "_")}_http.json').read_text())
           for name in ('personas-reads', 'course-list', 'workflow-update')}
IDS = SOURCES['workflow-update']['ids']
cases = []


def source_case(family, name):
    found = [case for case in SOURCES[family]['cases'] if case['name'] == name]
    assert len(found) == 1, name
    return found[0]


def add(family, name, *, handoff, path, response_of=None, status=None, response=None, method='GET', headers=None, effect=None,
        body=None, raw_body=None, content_type=None, extra=None):
    if response_of is not None:
        reference = source_case(family, response_of)
        status, response = reference['status'], copy.deepcopy(reference['response'])
        extra = {key: reference[key] for key in ('ordered',) if key in reference}
    case = {'name': f'django-handoff-{name}', 'family': family, 'handoff': handoff, 'actor': 'owner', 'method': method, 'path': path,
            'headers': headers or {}, 'status': status, 'response': response, 'effect': effect, **(extra or {})}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


# personas-reads: ported searches (controls): a Latin term, then Cyrillic terms Go serves itself.
add('personas-reads', 'personas-search-ported', handoff=False, path='/personas/list?query[generalSearch]=presente_',
    response_of='personas-reads-list-search-underscore-escaped')
add('personas-reads', 'personas-search-cyrillic-ported', handoff=False,
    path='/personas/list?query[generalSearch]=%D0%96%D0%B0%D0%BD%D0%BD%D0%B0',
    response_of='personas-reads-list-search-underscore-escaped')
add('personas-reads', 'personas-search-cyrillic-group-ported', handoff=False, headers={'X-Group-Id': IDS['own_group']},
    path='/personas/list?query[generalSearch]=%D0%96%D0%B0%D0%BD%D0%BD%D0%B0&pagination[perpage]=5',
    status=200, response={'data': [], 'meta': {'total': 0, 'page': 1, 'pages': 1, 'perpage': 5, 'rowIds': []}}, extra={'ordered': False})

# course-list: the attname ordering is ported (control); the relation path is refused.
ATTNAME = source_case('course-list', 'course-list-sort-relation-attname')
add('course-list', 'course-sort-attname-ported', handoff=False, path=ATTNAME['path'], response_of='course-list-sort-relation-attname')
add('course-list', 'course-sort-relation-path', handoff=True,
    path='/course/list?sort%5Bfield%5D=sport_association__denomination&sort%5Bsort%5D=asc&query%5Bstatus_flag%5D=1',
    response_of='course-list-sort-relation-attname')
# The group header reaches Django: only the own group's course is listed (as in course-list-subscription-group-own).
GROUP_OWN = source_case('course-list', 'course-list-subscription-group-own')
add('course-list', 'course-sort-relation-path-group', handoff=True, headers=GROUP_OWN['headers'],
    path='/course/list?sort%5Bfield%5D=sport_association__user&sort%5Bsort%5D=desc', response_of='course-list-subscription-group-own')

# workflow-update on the seeded 'plain' workflow; STATE follows the saves.
WORKFLOW = SOURCES['workflow-update']['workflows']['plain']
STATE = {'name': WORKFLOW['name'], 'description': WORKFLOW['description'], 'tree': copy.deepcopy(WORKFLOW['tree']),
         'enabled': WORKFLOW['enabled']}
PATH = f'/communications/workflows/{WORKFLOW["id"]}/update'
UPDATED, INVALID = {'msg': 'workflow updated.'}, {'msg': 'workflow not valid.'}


def text(value):
    return 'None' if value is None else str(value)


def dumps(tree):
    return json.dumps(tree, sort_keys=True)


def saved(actor, fields, *, session=None):
    """workflow-update's effect format: the row after the save and one audit entry when a field changed."""
    columns = {'name': 'name', 'description': 'description', 'automation_tree': 'tree', 'enabled': 'enabled'}
    changes = {}
    for field, value in fields.items():
        old = STATE[columns[field]]
        if field == 'automation_tree':
            if dumps(old) != dumps(value):
                changes[field] = [dumps(old), dumps(value)]
        elif old != value:
            changes[field] = [text(old), text(value)]
        STATE[columns[field]] = copy.deepcopy(value)
    return {'actor': actor, 'session': session, 'label': 'plain',
            'row': {'name': STATE['name'], 'description': STATE['description'], 'automation_tree': copy.deepcopy(STATE['tree']),
                    'enabled': STATE['enabled']}, 'audit': [{'changes': changes}] if changes else []}


def multipart(fields, boundary='v2handoffboundary'):
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n' for name, value in fields]
    return ''.join(parts) + f'--{boundary}--\r\n', f'multipart/form-data; boundary={boundary}'


FORM_TREE = [{'id': 'trigger', 'value': 'new_subscription', 'data': {'z': 'modulo', 'a': [1, None]}}]
JSON_TREE = [{'id': 'trigger', 'value': 'new_subscription', 'data': {'json': True}}]
PART_TREE = [{'id': 'trigger', 'value': 'new_subscription', 'data': {'parte': 'è'}}]
add('workflow-update', 'workflow-json-tree-ported', handoff=False, method='PATCH', path=PATH, status=200, response=UPDATED,
    body={'automation_tree': JSON_TREE}, effect=saved('owner', {'automation_tree': JSON_TREE}))
add('workflow-update', 'workflow-form-tree', handoff=True, method='PATCH', path=PATH, status=200, response=UPDATED,
    raw_body=urlencode({'name': 'Dal modulo', 'automation_tree': json.dumps(FORM_TREE)}),
    content_type='application/x-www-form-urlencoded', effect=saved('owner', {'name': 'Dal modulo', 'automation_tree': FORM_TREE}))
add('workflow-update', 'workflow-form-tree-invalid-json', handoff=True, method='PATCH', path=PATH, status=400, response=INVALID,
    raw_body='automation_tree=%5Bnot+json&name=Mai', content_type='application/x-www-form-urlencoded', effect=None)
BODY, CONTENT_TYPE = multipart([('description', 'Da parti'), ('automation_tree', json.dumps(PART_TREE, ensure_ascii=False))])
add('workflow-update', 'workflow-multipart-tree', handoff=True, method='PATCH', path=PATH, status=200, response=UPDATED,
    raw_body=BODY, content_type=CONTENT_TYPE, effect=saved('owner', {'description': 'Da parti', 'automation_tree': PART_TREE}))

assert len({c['name'] for c in cases}) == len(cases)
assert {c['family'] for c in cases if c['handoff']} == set(SOURCES) - {'personas-reads'}
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'API v2 handoff of refused inputs to Django', 'ids': IDS,
                              'sources': sorted(SOURCES), 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
