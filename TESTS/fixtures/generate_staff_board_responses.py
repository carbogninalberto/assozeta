"""Author StaffBoardMessageSerializer row projections from the documented contract.

Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('staff-board-responses.json')
ASSOCIATION = {'id': 'a1000000-0000-4000-8000-000000703001', 'owner_id': 'a1000000-0000-4000-8000-000000700001',
               'denomination': 'ASD Fixture à'}
OWNER = {'id': 'a1000000-0000-4000-8000-000000700001', 'first_name': '', 'last_name': '', 'username': 'h4sh3d-owner',
         'avatar_image': None}
STAFF = {'id': 'a1000000-0000-4000-8000-000000700002', 'first_name': 'Mario', 'last_name': 'Rossi', 'username': 'mario',
         'avatar_image': 'data:image/png;base64,AAAA'}
MESSAGE = 'a1000000-0000-4000-8000-000000704001'
DOCUMENT = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Ciao'}]}]}
cases = []


def add(name, author, actor, expected, *, document=None, pinned=False, created='2026-10-27T10:11:12.123456+00:00',
        updated='2026-10-27T10:11:13+00:00'):
    row = {'staff_board_message_id': MESSAGE, 'sport_association': ASSOCIATION['id'],
           'author': author['id'] if author else None, 'author_name': expected['author_name'],
           'author_avatar': author['avatar_image'] if author else None, 'is_owner': expected['is_owner'],
           'document': document, 'content': 'Ciao', 'pinned': pinned,
           'created_at': expected.get('created_at', '2026-10-27T10:11:12.123456Z'),
           'updated_at': expected.get('updated_at', '2026-10-27T10:11:13Z')}
    cases.append({'name': name, 'record': {'id': MESSAGE, 'association': ASSOCIATION, 'author': author,
                  'content': 'Ciao', 'document': document, 'pinned': pinned, 'created_at': created,
                  'updated_at': updated}, 'actor_id': actor, 'expect': row})


add('full-name-owner-actor', STAFF, STAFF['id'], {'author_name': 'Mario Rossi', 'is_owner': True}, document=DOCUMENT, pinned=True)
add('other-actor-not-owner', STAFF, OWNER['id'], {'author_name': 'Mario Rossi', 'is_owner': False})
add('association-owner-denomination', OWNER, OWNER['id'], {'author_name': 'ASD Fixture à', 'is_owner': True})
add('username-fallback', {**STAFF, 'first_name': '', 'last_name': ''}, OWNER['id'], {'author_name': 'mario', 'is_owner': False})
add('name-stripped', {**STAFF, 'first_name': ' Anna ', 'last_name': ''}, OWNER['id'], {'author_name': 'Anna', 'is_owner': False})
add('python-whitespace-name-falls-back', {**STAFF, 'first_name': '　', 'last_name': '\x1c'}, OWNER['id'],
    {'author_name': 'mario', 'is_owner': False})
add('last-name-only', {**STAFF, 'first_name': '', 'last_name': 'Bianchi'}, OWNER['id'], {'author_name': 'Bianchi', 'is_owner': False})
add('deleted-author', None, OWNER['id'], {'author_name': 'Utente eliminato', 'is_owner': False})
add('non-utc-timestamps-normalized', STAFF, STAFF['id'],
    {'author_name': 'Mario Rossi', 'is_owner': True, 'created_at': '2026-10-27T08:11:12.000001Z', 'updated_at': '2026-12-31T23:30:00Z'},
    created='2026-10-27T10:11:12.000001+02:00', updated='2027-01-01T00:30:00+01:00')
names = [case['name'] for case in cases]
assert len(names) == len(set(names))
OUTPUT.write_text(json.dumps({'format': 1, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
