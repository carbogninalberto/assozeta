"""Author the StaffBoardMessageInputSerializer corpus from the documented contract.

Expectations are DRF 3.17 English messages and are not observed from Django.
Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('staff-board-inputs.json')
BLANK = 'This field may not be blank.'
NULL = 'This field may not be null.'
STRING = 'Not a valid string.'
MAX = 'Ensure this field has no more than 10000 characters.'
NUL = 'Null characters are not allowed.'
BOOLEAN = 'Must be a valid boolean.'
JSONERR = 'Value must be valid JSON.'
TEXT = 'Il messaggio deve essere un testo.'
EMPTY = 'Scrivi un messaggio o aggiungi un’immagine.'
DOC = 'Documento non valido.'
NODATA = 'No data provided'
DOCUMENT = {'type': 'doc', 'content': [{'type': 'paragraph', 'attrs': {'onclick': 'x'}, 'content': [{'type': 'text', 'text': ' Ciao '}]}]}
CLEANED = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': ' Ciao '}]}]}

cases = []


def add(name, body, *, partial=False, valid=None, errors=None):
    case = {'name': name, 'partial': partial}
    if isinstance(body, list) and body and all(isinstance(item, tuple) for item in body):
        case['form'] = [list(item) for item in body]
    else:
        case['body_json'] = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    case['expect'] = {'valid': valid} if errors is None else {'errors': errors}
    cases.append(case)


add('content-text', {'content': 'hello'}, valid={'content': 'hello', 'document': None})
add('content-trimmed', {'content': ' \t hi 　'}, valid={'content': 'hi', 'document': None})
add('content-empty', {'content': ''}, errors={'content': [BLANK]})
add('content-whitespace', {'content': ' \x1c '}, errors={'content': [BLANK]})
add('content-null', {'content': None}, errors={'content': [NULL]})
add('content-integer', {'content': 5}, errors={'content': [TEXT]})
add('content-float', {'content': 1.5}, errors={'content': [TEXT]})
add('content-true', {'content': True}, errors={'content': [STRING]})
add('content-list', {'content': []}, errors={'content': [STRING]})
add('content-object', {'content': {}}, errors={'content': [STRING]})
add('content-10000', {'content': 'a' * 10000}, valid={'content': 'a' * 10000, 'document': None})
add('content-10001', {'content': 'a' * 10001}, errors={'content': [MAX]})
add('content-null-character', {'content': 'a\x00b'}, errors={'content': [NUL]})
add('content-surrogate', '{"content":"a\\ud800"}', errors={'content': ['Surrogate characters are not allowed: U+D800.']})
add('content-all-validators', '{"content":"\\u0000\\udfff' + 'a' * 10000 + '"}',
    errors={'content': [MAX, NUL, 'Surrogate characters are not allowed: U+DFFF.']})
add('content-duplicate-last-wins', '{"content":5,"content":"b"}', valid={'content': 'b', 'document': None})
add('empty-object', {}, errors={'non_field_errors': [EMPTY]})
add('pinned-only', {'pinned': True}, errors={'non_field_errors': [EMPTY]})
add('unknown-fields-ignored', {'content': 'x', 'author': 'a', 'sport_association': 'b', 'is_owner': True}, valid={'content': 'x', 'document': None})
for label, value, result in (('true', True, True), ('yes-mixed', 'YeS', True), ('on', 'on', True), ('one', 1, True), ('one-float', 1.0, True),
                             ('string-one', '1', True), ('false', False, False), ('off', 'OFF', False), ('zero', 0, False), ('string-f', 'f', False)):
    add(f'pinned-{label}', {'content': 'x', 'pinned': value}, valid={'content': 'x', 'document': None, 'pinned': result})
for label, value in (('maybe', 'maybe'), ('two', 2), ('empty-string', ''), ('null-word', 'null'), ('list', []), ('object', {}), ('half', 0.5)):
    add(f'pinned-invalid-{label}', {'content': 'x', 'pinned': value}, errors={'pinned': [BOOLEAN]})
add('pinned-null', {'content': 'x', 'pinned': None}, errors={'pinned': [NULL]})
add('document-valid', {'document': DOCUMENT}, valid={'document': CLEANED, 'content': 'Ciao'})
add('document-replaces-content', {'content': 'ignored', 'document': DOCUMENT, 'pinned': 'true'},
    valid={'content': 'Ciao', 'document': CLEANED, 'pinned': True})
add('document-invalid-root', {'document': {'type': 'paragraph'}}, errors={'non_field_errors': [DOC]})
add('document-string-json-body', {'document': '{"type":"doc"}'}, errors={'non_field_errors': [DOC]})
add('document-null', {'document': None}, errors={'document': [NULL]})
add('document-empty-text', {'document': {'type': 'doc'}}, errors={'non_field_errors': [EMPTY]})
add('several-field-errors', {'content': '', 'document': None, 'pinned': 'x'},
    errors={'content': [BLANK], 'document': [NULL], 'pinned': [BOOLEAN]})
add('field-error-precedes-validate', {'content': 5}, errors={'content': [TEXT]})
add('blank-content-with-document', {'content': '', 'document': DOCUMENT}, errors={'content': [BLANK]})
add('body-null', 'null', errors={'non_field_errors': [NODATA]})
for label, body, kind in (('list', '[]', 'list'), ('string', '"x"', 'str'), ('integer', '5', 'int'), ('true', 'true', 'bool'), ('float', '1.5', 'float')):
    add(f'body-{label}', body, errors={'non_field_errors': [f'Invalid data. Expected a dictionary, but got {kind}.']})
add('partial-empty', {}, partial=True, valid={})
add('partial-pinned', {'pinned': True}, partial=True, valid={'pinned': True})
add('partial-content', {'content': 'x'}, partial=True, valid={'content': 'x', 'document': None})
add('partial-document', {'document': DOCUMENT}, partial=True, valid={'document': CLEANED, 'content': 'Ciao'})
add('partial-content-empty', {'content': ''}, partial=True, errors={'content': [BLANK]})
add('partial-pinned-null', {'pinned': None}, partial=True, errors={'pinned': [NULL]})
add('partial-body-null', 'null', partial=True, errors={'non_field_errors': [NODATA]})
add('partial-body-list', '[]', partial=True, errors={'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']})
# BooleanField.default_empty_html is False: a non-partial form without pinned
# validates pinned=False, while an empty pinned value is skipped (Field.get_value).
add('form-content', [('content', 'hello')], valid={'content': 'hello', 'pinned': False, 'document': None})
add('form-content-empty-skipped', [('content', '')], errors={'non_field_errors': [EMPTY]})
add('form-content-whitespace', [('content', '   ')], errors={'content': [BLANK]})
add('form-content-last-value', [('content', 'a'), ('content', 'b')], valid={'content': 'b', 'pinned': False, 'document': None})
add('form-document-json', [('document', json.dumps(DOCUMENT))], valid={'document': CLEANED, 'pinned': False, 'content': 'Ciao'})
add('form-document-invalid-json', [('document', 'not json')], errors={'document': [JSONERR]})
add('form-document-empty', [('document', '')], errors={'document': [JSONERR]})
add('form-pinned-on', [('content', 'x'), ('pinned', 'on')], valid={'content': 'x', 'document': None, 'pinned': True})
add('form-pinned-empty-skipped', [('content', 'x'), ('pinned', '')], valid={'content': 'x', 'document': None})
add('form-pinned-invalid', [('content', 'x'), ('pinned', 'maybe')], errors={'pinned': [BOOLEAN]})
add('form-partial-empty', [], partial=True, valid={})
add('form-partial-pinned-empty-skipped', [('pinned', '')], partial=True, valid={})

empty_form = cases[[case['name'] for case in cases].index('form-partial-empty')]
empty_form.pop('body_json')
empty_form['form'] = []
names = [case['name'] for case in cases]
assert len(names) == len(set(names))
lines = ',\n'.join(json.dumps(case, ensure_ascii=False, separators=(',', ':')) for case in cases)
OUTPUT.write_text(f'{{"format":1,"cases":[\n{lines}\n]}}\n')
print(len(cases), 'cases')
