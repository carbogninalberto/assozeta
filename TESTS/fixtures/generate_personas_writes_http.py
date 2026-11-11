"""Author the personas writes HTTP sequence (personas-writes.md).

AssociateViewSet (BE/application/views/personas_views.py) maps, with
`as_view({...})`, `personas/add` (POST add), `personas/<pk>/update` (PATCH
update), `personas/<pk>/delete` (DELETE delete), `personas/<pk>/recover` (POST
recover) and `personas/bulk-delete` (DELETE bulk_delete).
AssociateSubscriptionViewSet maps `personas-subscriptions/<pk>/list` (GET list).
Both classes define their own delete(), so every route also answers DELETE
(bulk-delete maps DELETE to bulk_delete). OPTIONS names the class without
`ViewSet` and an empty description; POST routes add DRF's actions.POST metadata
of AssociateSerializer.

Identity, impersonation and the registry run first. Every action reads
`request.user.sport_association` (athletes: 403 'User is not a sport
association'; an owner whose association is missing or soft-deleted: 500).

- delete (and DELETE on update, recover, info): get_object() through
  get_queryset (association, live, group-aware, query parameters), a value the
  UUID field rejects is a bare 404, a miss 'No Associate matches the given
  query.'; then deleted=True and save(): an auditlog update entry
  {'deleted': ['False', 'True']}; the index signal no longer finds the row
  through the soft-delete manager and resolves the actor's association. 204.
- recover: Associate.objects.all_objects() (no group, no soft-delete filter)
  .filter(pk=pk).first(): a value the UUID field rejects is a ValidationError
  (500), a miss Response(404) without a body; the row's association (None:
  500), request.user.sport_association, then 403 'User not allowed.' for
  another association; deleted=False and save(). auditlog reads the old row
  through the soft-delete, group-aware manager: a deleted (or group-hidden)
  row is not found and the entry carries every field against None.
  {'mgs': 'Ripristinata con successo'}.
- bulk_delete: request.data.get('associate_ids', []) (a list body is a 500),
  len() (non-sized values: 500), Associate.objects (live, group-aware)
  .filter(associate_id__in=..., sport_association=...).update(deleted=True):
  no auditlog entries; ids the UUID field rejects are a 500. 204.
- add: request.data.pop('tutors_data', []) (form bodies are immutable
  QueryDicts and non-dict bodies have no pop: 500), AssociateSerializer
  validation (DATE_INPUT_FORMATS '%d/%m/%Y'; tenant_scope on user,
  sport_association, group and family), save(user=request.user,
  sport_association=...) through Associate.objects.create (the manager stores
  the raw X-Group-Id), then _handle_tutors, then 201 with the serializer data.
- update: get_object(), request.data.get('tutors_data', []) (validated by the
  ListField), partial validation, save() (Model.save writes every field;
  auditlog records only changed fields), _handle_tutors (a missing tutors_data
  is [] and deletes every relation), then get_object() again (its 404s apply
  after the writes) and AssociateSerializer.
- _handle_tutors: relations whose tutor is not listed are deleted (the
  collector posts deletes in descending key order); each listed tutor found by
  Associate.objects (live, group-aware) is partially updated through
  BasicAssociateSerializer, otherwise created; update_or_create of the
  relation with is_primary; without a primary relation the first by key is
  saved primary. Requests are not atomic: exceptions after a save keep it.
- personas-subscriptions list: Subscription.objects (group-aware) of the
  association, not deleted, by -creation_date, filtered by associate_id (a
  value the UUID field rejects is a 500), KTDatatablePagination and
  AssociateSubscriptionSerializer; rowIds are the subscriptions' associate ids.

Personal data is synthetic. Run with ``python3 -I``.
"""
import base64
import copy
import json
import unicodedata
import urllib.parse
import uuid
from pathlib import Path

OUTPUT = Path(__file__).with_name('personas_writes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER = IDS['own_association'], IDS['foreign_association'], IDS['owner']
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
ADMIN_ASSOCIATION = IDS['admin_association']
SERVER = '<stable-server-error-page>'
EMPTY = '<empty>'
_ids = iter(f'a1000000-0000-4000-8000-{677001 + n:012d}' for n in range(400))
_relation_ids = iter(range(677001, 677200))
UNKNOWN = 'a1000000-0000-4000-8000-000000677999'
F1, F2 = next(_ids), next(_ids)

FIELDS = ('picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_date', 'born_city', 'address_city', 'address',
          'address_cap', 'nationality', 'nationality_residence', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label',
          'phone_2_label', 'phone_3_label', 'phone_4_label', 'is_minor', 'disabled', 'draft', 'deleted', 'family_role', 'id_number',
          'migrated', 'notes')
DEFAULTS = {'picture_path': None, 'first_name': None, 'last_name': None, 'sex': 'A', 'tax_code': None, 'born_date': None,
            'born_city': None, 'address_city': None, 'address': None, 'address_cap': None, 'nationality': 'Italiana',
            'nationality_residence': 'Italia', 'email': None, 'phone': None, 'phone_2': None, 'phone_3': None, 'phone_4': None,
            'phone_label': None, 'phone_2_label': None, 'phone_3_label': None, 'phone_4_label': None, 'is_minor': False,
            'disabled': False, 'draft': False, 'deleted': False, 'family_role': None, 'id_number': None, 'migrated': False,
            'notes': None, 'user': None, 'association': OWN, 'group': None, 'family': None}
_day = iter(range(1, 400))


def person(first, last, **values):
    n = next(_day)
    return {**DEFAULTS, 'first_name': first, 'last_name': last, **values, 'id': next(_ids),
            'created': f'2024-{1 + n // 28:02d}-{1 + n % 28:02d}T07:00:00Z'}


SEED = {
    # delete
    'enzo': person('Enzo', 'Cancellabile', born_date='1985-04-04'),
    'fede': person('Fede', 'Collaborata'),
    'gaia': person('Gaia', 'Amministrata'),
    'dora': person('Dora', 'Gruppo', group=OWN_GROUP),
    'ivan': person('Ivan', 'Viaupdate'),
    'lino': person('Lino', 'Viarecover'),
    'mara': person('Mara', 'Viainfo'),
    'abroad': person('Hans', 'Estero', association=FOREIGN),
    'ghost': person('Gina', 'Cancellata', deleted=True, tax_code='CNCLLT80A01H501C'),
    # recover
    'gone': person('Rita', 'Recuperata', deleted=True, born_date='2001-02-03', tax_code='RCPRTI01B43H501Q', email='rita@example.test',
                   family=F2, notes=''),
    'gone_grp': person('Gelsa', 'Gruppata', deleted=True, group=OWN_GROUP),
    'gone_orphan': person('Orfeo', 'Orfano', deleted=True, association=None),
    'gone_abroad': person('Kurt', 'Lontano', deleted=True, association=FOREIGN),
    'gone_collab': person('Carlo', 'Ripreso', deleted=True),
    'gone_admin': person('Adele', 'Ripresa', deleted=True),
    'alive': person('Viva', 'Presente'),
    # bulk delete
    'b1': person('Bice', 'Uno'),
    'b2': person('Bino', 'Due'),
    'b3': person('Bria', 'Tre'),
    'b_grp': person('Bego', 'Gruppo', group=OWN_GROUP),
    'b_grp2': person('Bera', 'Gruppo', group=OWN_GROUP),
    'b_far': person('Bern', 'Fuori', association=FOREIGN),
    'b_gone': person('Bolo', 'Andato', deleted=True),
    # add: an existing tutor and a tax code
    'tutor_a': person('Teresa', 'Tutrice', born_date='1970-02-01', sex='F', tax_code='TTRTRS70B41H501T', phone='3330000011'),
    'taxed': person('Tito', 'Tassato', tax_code='TXDTTI80A01H501A'),
    # update
    'upd': person('Ugo', 'Aggiornato', born_date='2012-05-06', sex='M', tax_code='GGRUGO12E06H501U', is_minor=True, family=F1,
                  family_role='figlio', email='ugo@example.test'),
    'upd_t1': person('Paola', 'Prima', born_date='1975-03-03', sex='F', family=F1, family_role='madre'),
    'upd_t2': person('Piero', 'Secondo', born_date='1974-04-04', sex='M'),
    'wipe': person('Walter', 'Senzatutori', born_date='2013-01-01'),
    'wipe_t': person('Wanda', 'Tutrice', born_date='1980-01-01'),
    'swap': person('Sara', 'Scambio', born_date='2014-01-01'),
    'swap_t1': person('Silvio', 'Primo', born_date='1981-01-01'),
    'swap_t2': person('Silvia', 'Seconda', born_date='1982-01-01'),
    'clash': person('Cleo', 'Conflitto', born_date='2015-01-01'),
    'clash_t1': person('Ciro', 'Primo', born_date='1983-01-01'),
    'clash_t2': person('Cora', 'Seconda', born_date='1984-01-01'),
    'newtut': person('Nina', 'Nuovotutore', born_date='2016-01-01'),
    'keyless': person('Kira', 'Senzachiave', born_date='2016-02-02'),
    'collab_upd': person('Cesare', 'Collaborato'),
    'grp_upd': person('Greta', 'Gruppo', group=OWN_GROUP),
    'drop': person('Dino', 'Archiviato'),
    'form_upd': person('Furio', 'Modulo'),
    'tax_upd': person('Tea', 'Tassa'),
    'promote': person('Prisco', 'Promosso', born_date='2017-01-01'),
    'promote_t': person('Pia', 'Tutrice', born_date='1979-01-01'),
    'orphaned': person('Olga', 'Senzassociazione'),
    # personas-subscriptions
    'subbed': person('Serena', 'Iscritta', born_date='1990-01-01', tax_code='SRNSCR90A41H501S'),
    'lone': person('Leo', 'Solitario', born_date='1991-01-01', tax_code='LNOSLT91A01H501L'),
    'sub_far': person('Hilde', 'Estera', association=FOREIGN, born_date='1992-01-01', tax_code='HLDSTR92A41Z112H'),
    # picture uploads
    'pic_upd': person('Fosca', 'Fotografata', picture_path='https://cdn.example.test/fosca-old.jpg'),
    'pic_collab': person('Fulvio', 'Fotocollaborato'),
    'pic_form': person('Flora', 'Fotomodulo'),
    'pic_keep': person('Fabio', 'Fotofallita', picture_path='https://cdn.example.test/fabio.jpg'),
    'pic_denied': person('Fermo', 'Fotonegata', picture_path='https://cdn.example.test/fermo.jpg'),
    'pic_other': person('Fedra', 'Fotoaltrui', picture_path='https://cdn.example.test/fedra.jpg'),
    'pic_text': person('Folco', 'Fototesto'),
    # numeric tutor ids: DRF and Django read an integer as UUID(int=n)
    'int_tutor': {**person('Ivo', 'Intero', born_date='1971-01-01'), 'id': '00000000-0000-0000-0000-000000001092'},
    'one_tutor': {**person('Ugo', 'Uno', born_date='1972-01-01'), 'id': '00000000-0000-0000-0000-000000000001'},
    # tutor flags and numeric ids on update
    'prim': person('Prima', 'Flag', born_date='2010-01-01'),
    'prim_t1': person('Pino', 'Primo', born_date='1980-01-01'),
    'prim_t2': person('Pina', 'Seconda', born_date='1981-01-01'),
    'idu': person('Ida', 'Intera', born_date='2011-01-01'),
    'idu2': person('Ines', 'Intera', born_date='2011-02-01'),
    'idu3': person('Ivana', 'Intera', born_date='2011-03-01'),
    'idu4': person('Irma', 'Intera', born_date='2011-04-01'),
    'idu_t': person('Iris', 'Vecchia', born_date='1982-01-01'),
}
A = {label: row['id'] for label, row in SEED.items()}
ACL_DENIED = A['pic_denied']  # the storage stub refuses the object ACL of keys with this id


def relation(associate, tutor, primary):
    return {'id': next(_relation_ids), 'associate': associate, 'tutor': tutor, 'primary': primary}


RELATIONS = {
    'upd_r1': relation('upd', 'upd_t1', True),
    'upd_r2': relation('upd', 'upd_t2', False),
    'wipe_r': relation('wipe', 'wipe_t', True),
    'swap_r1': relation('swap', 'swap_t1', True),
    'swap_r2': relation('swap', 'swap_t2', False),
    'clash_r1': relation('clash', 'clash_t1', True),
    'clash_r2': relation('clash', 'clash_t2', False),
    'promote_r': relation('promote', 'promote_t', False),
    'prim_r1': relation('prim', 'prim_t1', True),
    'prim_r2': relation('prim', 'prim_t2', False),
    'idu_r': relation('idu', 'idu_t', True),
    'idu_r2': relation('idu2', 'idu_t', True),
    'idu_r3': relation('idu3', 'idu_t', True),
}
MEDICAL = {'id': next(_ids), 'expiration': '2030-01-31'}
TAG = {'id': next(_ids), 'name': 'Agonisti'}
SUBSCRIPTIONS = {
    # label: associate, start, end, created, extra
    'cur': ('subbed', '2020-01-01', '2099-12-31', '2025-02-01T09:00:00Z',
            {'medical': True, 'tags': True, 'signature_url': 'https://cdn.example.test/firma.png', 'custom_data': {'k': 'v'},
             'status_flag': 4, 'type': 1, 'role': 2, 'acceptance_date': '2020-01-02', 'subscription_number': '12', 'notes': 'nota',
             'competitive': True, 'draft': False}),
    'old': ('subbed', '2019-01-01', '2019-12-31', '2024-01-01T09:00:00Z', {'signature_storage_key': 'private/key.png'}),
    'gone': ('subbed', '2018-01-01', '2018-12-31', '2023-06-01T09:00:00Z', {'deleted': True}),
    'grouped': ('subbed', '2017-01-01', '2017-12-31', '2023-01-01T09:00:00Z', {'group': OWN_GROUP, 'archived': True}),
    'future': ('subbed', '2099-01-01', '2099-12-31', '2022-01-01T09:00:00Z', {'trial': True}),
    'lone_past': ('lone', '2018-01-01', '2018-12-31', '2024-03-01T09:00:00Z', {'meta': {'n': 1}, 'additional_fields': [1, 'a']}),
    'far': ('subbed', '2020-01-01', '2099-12-31', '2024-04-01T09:00:00Z', {'association': FOREIGN}),
    'hilde': ('sub_far', '2020-01-01', '2099-12-31', '2024-05-01T09:00:00Z', {'association': FOREIGN}),
}
SUB_IDS = {label: next(_ids) for label in SUBSCRIPTIONS}

# --- the world: the state each case starts from, updated by each authored effect ---------------------
WORLD = {'assoc': copy.deepcopy(SEED), 'rel': copy.deepcopy(RELATIONS)}
ACTOR_ASSOCIATION = {'owner': OWN, 'full_collaborator': None, 'admin': ADMIN_ASSOCIATION, 'foreign_owner': FOREIGN}


def row_of(ref):
    return WORLD['assoc'][ref]


def visible(ref, group):
    """Associate.objects (SoftDeleteGroupAwareManager) finds the row."""
    row = row_of(ref)
    return not row['deleted'] and (group is None or row['group'] == group)


# --- serializers, authored from personas_serializers.py ---------------------------------------------------
def age(row):
    return {'$age': row['born_date']} if row['born_date'] else 0


def minor(row):
    return {'$minor': row['born_date']} if row['born_date'] else True


def full_name(row):
    return f"{row['first_name']} {row['last_name']}".strip()


def model_fields(row):
    return {**{k: row[k] for k in FIELDS}, 'creation_date': row['created']}


def relation_fields(row):
    return {'user': row['user'], 'sport_association': row['association'], 'group': row['group'], 'family': row['family']}


def relations_of(ref):
    return sorted((r for r in WORLD['rel'].values() if r['associate'] == ref), key=lambda r: r['id'])


def m2m_tutors(ref, group):
    return [r['tutor'] for r in relations_of(ref) if visible(r['tutor'], group)]


def is_tutor(ref):
    return any(r['tutor'] == ref for r in WORLD['rel'].values())


def basic_tutor(ref, group):
    row = row_of(ref)
    return {'associate_id': row['id'], 'full_name': full_name(row), **model_fields(row), **relation_fields(row),
            'tutors': [row_of(t)['id'] for t in m2m_tutors(ref, group)]}


def basic_associate(ref, group):
    row = row_of(ref)
    return {'associate_id': row['id'], 'full_name': full_name(row), 'tutors': [basic_tutor(t, group) for t in m2m_tutors(ref, group)],
            **model_fields(row), **relation_fields(row)}


def info(ref, group=None):
    row = row_of(ref)
    family = [other['id'] for key, other in WORLD['assoc'].items() if key != ref and row['family'] and other['family'] == row['family']
              and visible(key, group)]
    tutors = [{'tutor': basic_associate(r['tutor'], group), 'is_primary': r['primary']} for r in relations_of(ref)]
    return {'associate_id': row['id'], 'full_name': full_name(row), 'is_minor_now': minor(row), 'is_tutor': is_tutor(ref), 'age': age(row),
            'family_members': family, 'tutors': tutors, **model_fields(row), **relation_fields(row)}


# --- auditlog, authored from auditlog.diff and the AuditLogIndex signal ---------------------------------
REVERSE = {'tutor_relations': 'application.AssociateTutorRelation.None', 'tutored_relations': 'application.AssociateTutorRelation.None'}
COLUMNS = {'user': 'user', 'association': 'sport_association', 'group': 'group', 'family': 'family'}


def audit_text(value):
    return 'True' if value is True else 'False' if value is False else 'None' if value is None else str(value)


def audit_moment(text):
    """str() of the naive UTC datetime auditlog renders for creation_date."""
    return text.replace('T', ' ').replace('Z', '') if text not in ('$now',) else text


def create_changes(row):
    """model_instance_diff(None, row): non-null fields against None, null FKs and DateTimeFields kept, reverse accessors."""
    changes = {'associate_id': ['None', row['id']]}
    for key, name in COLUMNS.items():
        changes[name] = ['None', audit_text(row[key])]
    for name in FIELDS:
        if row[name] is not None:
            changes[name] = ['None', audit_text(row[name])]
    changes['creation_date'] = ['None', audit_moment(row['created'])]
    for name, manager in REVERSE.items():
        changes[name] = ['None', manager]
    return changes


def update_changes(old, new):
    changes = {}
    for key, name in COLUMNS.items():
        if old[key] != new[key]:
            changes[name] = [audit_text(old[key]), audit_text(new[key])]
    for name in FIELDS:
        if audit_text(old[name]) != audit_text(new[name]):
            changes[name] = [audit_text(old[name]), audit_text(new[name])]
    return changes


def associate_index(ref, changes, actor, group, stored=None):
    """resolve_sport_association_for_log_entry for an Associate entry: create entries (post_save) see the new row,
    update entries (auditlog's pre_save) the stored row before the save."""
    row = stored if stored is not None else row_of(ref)
    if not row['deleted'] and (group is None or row['group'] == group) and row['association']:
        return [row['association'], 'direct']
    for candidate in changes.get('sport_association', []):
        if candidate not in ('None', '') and candidate in (OWN, FOREIGN, ADMIN_ASSOCIATION):
            return [candidate, 'changes_json.sport_association']
    association = ACTOR_ASSOCIATION[actor]
    return [association, 'actor.sport_association'] if association else None


def relation_index(rel, actor):
    """AssociateTutorRelation: the associate's association while the relation exists, else the actor's."""
    if rel is not None:
        association = row_of(rel['associate'])['association']
        if association:
            return [association, 'associate.sport_association']
    association = ACTOR_ASSOCIATION[actor]
    return [association, 'actor.sport_association'] if association else None


SERIAL = iter(range(1, 1000))


class Effect:
    """One case's expected writes, applied to the world as they are authored."""

    def __init__(self, actor, group=None):
        self.actor, self.group = actor, group
        self.audit, self.created, self.changed = [], [], {}
        self.rel_created, self.rel_deleted, self.rel_changed = [], [], {}

    def _changed(self, ref, before):
        if ref in self.created_refs():
            return
        columns = self.changed.setdefault(ref, {})
        for key, value in row_of(ref).items():
            if value != before[key]:
                columns[key] = value

    def created_refs(self):
        return [ref for ref, _ in self.created]

    def create(self, sent_group=None, **values):
        ref = f'$a{next(SERIAL)}'
        row = {**DEFAULTS, **values, 'id': ref, 'created': '$now' + ref[2:]}
        WORLD['assoc'][ref] = row
        self.created.append((ref, row))
        # The manager hands the raw X-Group-Id to the instance: auditlog writes it as sent, the column keeps the uuid.
        changes = create_changes({**row, 'group': sent_group} if sent_group else row)
        self.audit.append({'model': 'associate', 'ref': ref, 'action': 0, 'changes': changes,
                           'index': associate_index(ref, changes, self.actor, self.group)})
        return ref

    def save(self, ref, *, audit_old=True, **values):
        """Model.save() of every field: an update entry with the changed fields (or every field when auditlog's
        soft-delete, group-aware lookup of the old row finds nothing)."""
        before = dict(row_of(ref))
        row_of(ref).update(values)
        changes = update_changes(before, row_of(ref)) if audit_old else create_changes(row_of(ref))
        self._changed(ref, before)
        if changes:
            self.audit.append({'model': 'associate', 'ref': ref, 'action': 1, 'changes': changes,
                               'index': associate_index(ref, changes, self.actor, self.group, before)})

    def quiet(self, ref, **values):
        """QuerySet.update(): no signals."""
        before = dict(row_of(ref))
        row_of(ref).update(values)
        self._changed(ref, before)

    def relate(self, associate, tutor, primary, text=None):
        ref = f'$r{next(SERIAL)}'
        rel = {'id': ref, 'associate': associate, 'tutor': tutor, 'primary': primary}
        WORLD['rel'][ref] = rel
        self.rel_created.append(ref)
        self.audit.append({'model': 'associatetutorrelation', 'ref': ref, 'action': 0, 'changes': {
            'id': ['None', ref], 'associate': ['None', row_of(associate)['id']], 'tutor': ['None', row_of(tutor)['id']],
            'is_primary': ['None', audit_text(primary) if text is None else text]}, 'index': relation_index(rel, self.actor)})
        return ref

    def set_primary(self, ref, primary, text=None):
        """The relation is saved with the raw flag: auditlog compares str() of the stored value with str() of it,
        the column keeps what the BooleanField prepares (text: str() of the raw value)."""
        rel = WORLD['rel'][ref]
        text = audit_text(primary) if text is None else text
        if audit_text(rel['primary']) == text:
            return
        changes = {'is_primary': [audit_text(rel['primary']), text]}
        if rel['primary'] != primary:
            rel['primary'] = primary
            if ref not in self.rel_created:
                self.rel_changed[ref] = {'is_primary': primary}
        self.audit.append({'model': 'associatetutorrelation', 'ref': ref, 'action': 1, 'changes': changes,
                           'index': relation_index(rel, self.actor)})

    def unrelate(self, refs):
        """QuerySet.delete(): post_delete entries in descending key order (the row captured before the case)."""
        for ref in sorted(refs, key=lambda r: WORLD['rel'][r]['id'], reverse=True):
            WORLD['rel'].pop(ref)
            self.rel_deleted.append(ref)
            self.audit.append({'model': 'associatetutorrelation', 'ref': ref, 'action': 2, 'changes': None,
                               'index': relation_index(None, self.actor)})

    def document(self):
        return {'actor': self.actor, 'audit': self.audit, 'created': [row for _, row in self.created],
                'changed': self.changed, 'relations_created': [WORLD['rel'][r] if r in WORLD['rel'] else None for r in self.rel_created],
                'relations_deleted': self.rel_deleted, 'relations_changed': self.rel_changed}


def uuid_of(value):
    """UUIDField.to_python of a tutor id: an integer (a bool too) is UUID(int=n)."""
    if isinstance(value, int):
        return str(uuid.UUID(int=value))
    return value


def prepare_primary(raw):
    """BooleanField.to_python for the not-null is_primary column: (stored value, str() of the raw value)."""
    if raw in (True, False):
        return bool(raw), audit_text(raw)
    if isinstance(raw, str) and raw in ('t', 'True', '1'):
        return True, raw
    if isinstance(raw, str) and raw in ('f', 'False', '0'):
        return False, raw
    raise ValueError(raw)


def handle_tutors(effect, associate, tutors_data):
    """AssociateSerializer._handle_tutors for authored, well-formed tutors_data."""
    listed = {uuid_of(data['tutor']['associate_id']) for data in tutors_data if data.get('tutor')}
    listed_refs = {ref for ref, row in WORLD['assoc'].items() if row['id'] in listed}
    effect.unrelate([ref for ref, r in WORLD['rel'].items() if r['associate'] == associate and r['tutor'] not in listed_refs])
    for data in tutors_data:
        info_ = data.get('tutor')
        if not info_:
            continue
        found = [ref for ref, row in WORLD['assoc'].items() if row['id'] == uuid_of(info_.get('associate_id')) and visible(ref, effect.group)]
        values = {k: v for k, v in info_.items() if k not in ('associate_id', 'creation_date', 'tutors', 'full_name')}
        values = {('association' if k == 'sport_association' else k): v for k, v in values.items()}
        if 'born_date' in values and values['born_date']:
            d, m, y = (''.join(str(unicodedata.digit(ch)) for ch in part) for part in values['born_date'].split('/'))
            values['born_date'] = f'{y}-{m}-{d}'
        if found:
            tutor = found[0]
            effect.save(tutor, **values)
        else:
            tutor = effect.create(**{**values, 'association': values.get('association'), 'group': effect.group})
        primary, text = prepare_primary(data['is_primary']) if 'is_primary' in data else (False, 'False')
        existing = [ref for ref, r in WORLD['rel'].items() if r['associate'] == associate and r['tutor'] == tutor]
        if existing:
            effect.set_primary(existing[0], primary, text)
        else:
            effect.relate(associate, tutor, primary, text)
    mine = sorted((ref for ref, r in WORLD['rel'].items() if r['associate'] == associate),
                  key=lambda ref: (isinstance(WORLD['rel'][ref]['id'], str), str(WORLD['rel'][ref]['id']).zfill(12)))
    if mine and not any(WORLD['rel'][ref]['primary'] for ref in mine):
        effect.set_primary(mine[0], True)


# --- cases ---------------------------------------------------------------------------------------
cases = []


def add(name, actor, method, path, status, response, *, allow, body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'personas-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'allow': sorted(allow), 'effect': effect.document() if effect else None}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ADD, BULK = '/personas/add', '/personas/bulk-delete'
delete_path = lambda ref: f'/personas/{A.get(ref, ref)}/delete'
update_path = lambda ref: f'/personas/{A.get(ref, ref)}/update'
recover_path = lambda ref: f'/personas/{A.get(ref, ref)}/recover'
info_path = lambda ref: f'/personas/{A.get(ref, ref)}/info'
subs_path = lambda ref: f'/personas-subscriptions/{A.get(ref, ref)}/list'
ALLOW_ADD, ALLOW_UPDATE, ALLOW_DELETE = {'POST', 'DELETE', 'OPTIONS'}, {'PATCH', 'DELETE', 'OPTIONS'}, {'DELETE', 'OPTIONS'}
ALLOW_RECOVER, ALLOW_INFO, ALLOW_SUBS = {'POST', 'DELETE', 'OPTIONS'}, {'GET', 'HEAD', 'DELETE', 'OPTIONS'}, {'GET', 'HEAD', 'DELETE', 'OPTIONS'}
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
NO_MATCH = {'detail': 'No Associate matches the given query.'}
NOT_FOUND = {'detail': 'Not found.'}
NOT_ALLOWED = {'detail': 'User not allowed.'}
GENERIC = {'detail': 'You do not have permission to perform this action.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
SCOPED_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
missing = lambda key: {'detail': f'Missing permission: {key}'}
method_not_allowed = lambda method: {'detail': f'Method "{method}" not allowed.'}
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']


# --- OPTIONS actions.POST: SimpleMetadata over AssociateSerializer --------------------------------------
LABELS = {'associate_id': 'Associate id'}
TEXT_MAX = {'picture_path': 512, 'first_name': 150, 'last_name': 150, 'tax_code': 25, 'born_city': 150, 'address_city': 150,
            'address': 150, 'address_cap': 5, 'nationality': 150, 'nationality_residence': 150, 'email': 254, 'phone': 30, 'phone_2': 30,
            'phone_3': 30, 'phone_4': 30, 'phone_label': 100, 'phone_2_label': 100, 'phone_3_label': 100, 'phone_4_label': 100,
            'family_role': 100, 'id_number': 100, 'notes': None}
SEX = [{'value': 'A', 'display_name': 'other'}, {'value': 'M', 'display_name': 'male'}, {'value': 'F', 'display_name': 'female'}]


def label(name):
    return name.replace('_', ' ').capitalize()


def meta(kind, name=None, *, read_only=False, required=False, **extra):
    info_ = {'type': kind, 'required': required, 'read_only': read_only}
    if name:
        info_['label'] = label(name)
    info_.update(extra)
    return info_


def model_meta(*, associate_writable, creation_writable):
    fields = {'associate_id': meta('string', 'associate_id', read_only=not associate_writable)}
    for name in ('picture_path', 'first_name', 'last_name'):
        fields[name] = meta('string', name, max_length=TEXT_MAX[name])
    fields['sex'] = meta('choice', 'sex', choices=SEX)
    fields['tax_code'] = meta('string', 'tax_code', max_length=25)
    fields['born_date'] = meta('date', 'born_date')
    for name in ('born_city', 'address_city', 'address', 'address_cap', 'nationality', 'nationality_residence'):
        fields[name] = meta('string', name, max_length=TEXT_MAX[name])
    fields['email'] = meta('email', 'email', max_length=254)
    for name in ('phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label', 'phone_2_label', 'phone_3_label', 'phone_4_label'):
        fields[name] = meta('string', name, max_length=TEXT_MAX[name])
    for name in ('is_minor', 'disabled', 'draft', 'deleted'):
        fields[name] = meta('boolean', name)
    for name in ('family_role', 'id_number'):
        fields[name] = meta('string', name, max_length=100)
    fields['creation_date'] = meta('datetime', 'creation_date', read_only=not creation_writable)
    fields['migrated'] = meta('boolean', 'migrated')
    fields['notes'] = meta('string', 'notes')
    for name in ('user', 'sport_association', 'group', 'family'):
        fields[name] = meta('field', name)
    return fields


def basic_tutors_meta():
    return {**model_meta(associate_writable=True, creation_writable=True), 'full_name': meta('string', 'full_name', read_only=True),
            'tutors': meta('field', 'tutors', read_only=True)}


def nested(name, children):
    return meta('field', name, read_only=True, child={'type': 'nested object', 'required': False, 'read_only': True, 'children': children})


def basic_associate_meta():
    return {**model_meta(associate_writable=True, creation_writable=True), 'full_name': meta('string', 'full_name', read_only=True),
            'tutors': nested('tutors', basic_tutors_meta())}


ACTIONS = {'POST': {
    **model_meta(associate_writable=False, creation_writable=False),
    'full_name': meta('string', 'full_name', read_only=True), 'is_minor_now': meta('boolean', 'is_minor_now', read_only=True),
    'is_tutor': meta('boolean', 'is_tutor', read_only=True), 'age': meta('integer', 'age', read_only=True),
    'family_members': meta('field', 'family_members', read_only=True),
    'tutors': nested('tutors', {'tutor': {**meta('nested object', 'tutor', read_only=True), 'children': basic_associate_meta()},
                                'is_primary': meta('boolean', 'is_primary')}),
    'tutors_data': meta('list', 'tutors_data', child={'type': 'field', 'required': True, 'read_only': False}),
    'incomplete': meta('boolean', 'incomplete', read_only=True),
}}
OPTIONS = {'name': 'Associate', 'description': '', 'renders': ['application/json'], 'parses': PARSES}
OPTIONS_POST = {**OPTIONS, 'actions': ACTIONS}
OPTIONS_SUBS = {'name': 'Associate Subscription', 'description': '', 'renders': ['application/json'], 'parses': PARSES}

# ================================================================= transport and identity, every route
ROUTES = (
    ('add', 'POST', ADD, ALLOW_ADD, 'association.personas.create', OPTIONS_POST, {}),
    ('update', 'PATCH', update_path('upd'), ALLOW_UPDATE, 'association.personas.update', OPTIONS, {'notes': 'x'}),
    ('delete', 'DELETE', delete_path('enzo'), ALLOW_DELETE, 'association.personas.delete', OPTIONS, None),
    ('recover', 'POST', recover_path('gone'), ALLOW_RECOVER, 'association.personas.update', OPTIONS_POST, None),
    ('bulk-delete', 'DELETE', BULK, ALLOW_DELETE, 'association.personas.delete', OPTIONS, {'associate_ids': [A['b1']]}),
    ('subscriptions', 'GET', subs_path('subbed'), ALLOW_SUBS, 'association.personas.read', OPTIONS_SUBS, None),
)
for kind, method, path, allow, permission, options, body in ROUTES:
    add(f'{kind}-anonymous', 'anonymous', method, path, 401, {'detail': 'Authentication credentials were not provided.'}, allow=allow,
        body=body)
    add(f'{kind}-admin-without-session', 'admin', method, path, 403,
        {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, allow=allow, body=body)
    add(f'{kind}-restricted-collaborator', 'restricted_collaborator', method, path, 403, missing(permission), allow=allow, body=body)
    add(f'{kind}-owner-scoped-athlete-denied', 'owner', method, path, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
        headers=SCOPED_ATHLETE, allow=allow, body=body)
    add(f'{kind}-athlete', 'athlete', method, path, 403, NOT_ASSOCIATION, allow=allow, body=body)
    add(f'{kind}-owner-without-association', 'owner_without_association', method, path, 500, SERVER, allow=allow, body=body)
    add(f'{kind}-deleted-association-owner', 'deleted_association_owner', method, path, 500, SERVER, allow=allow, body=body)
    add(f'{kind}-options', 'owner', 'OPTIONS', path, 200, options, allow=allow)
    wrong = 'PUT' if method != 'PUT' else 'POST'
    add(f'{kind}-wrong-method', 'owner', wrong, path, 405, method_not_allowed(wrong), allow=allow)

# ================================================================= delete
add('delete-malformed-pk', 'owner', 'DELETE', delete_path('abc'), 404, NOT_FOUND, allow=ALLOW_DELETE)
add('delete-missing', 'owner', 'DELETE', delete_path(UNKNOWN), 404, NO_MATCH, allow=ALLOW_DELETE)
add('delete-foreign', 'owner', 'DELETE', delete_path('abroad'), 404, NO_MATCH, allow=ALLOW_DELETE)
add('delete-soft-deleted', 'owner', 'DELETE', delete_path('ghost'), 404, NO_MATCH, allow=ALLOW_DELETE)
add('delete-other-group', 'owner', 'DELETE', delete_path('dora'), 404, NO_MATCH, headers={'X-Group-Id': FOREIGN_GROUP}, allow=ALLOW_DELETE)
# get_queryset's parameters apply to get_object().
add('delete-filtered-out', 'owner', 'DELETE', delete_path('enzo') + '?query[type]=tutors', 404, NO_MATCH, allow=ALLOW_DELETE)
add('delete-bad-filter', 'owner', 'DELETE', delete_path('enzo') + '?query[is_tutor]=maybe', 500, SERVER, allow=ALLOW_DELETE)
add('delete-foreign-owner-own-row-missing', 'foreign_owner', 'DELETE', delete_path('enzo'), 404, NO_MATCH, allow=ALLOW_DELETE)


def soft_delete(name, actor, path, ref, allow, *, headers=None, group=None):
    effect = Effect(actor, group)
    effect.save(ref, deleted=True)
    add(name, actor, 'DELETE', path, 204, EMPTY, allow=allow, headers=headers, effect=effect)


soft_delete('delete-owner', 'owner', delete_path('enzo'), 'enzo', ALLOW_DELETE)
add('delete-again-missing', 'owner', 'DELETE', delete_path('enzo'), 404, NO_MATCH, allow=ALLOW_DELETE)
soft_delete('delete-full-collaborator', 'full_collaborator', delete_path('fede'), 'fede', ALLOW_DELETE)
soft_delete('delete-admin-impersonating', 'admin', delete_path('gaia'), 'gaia', ALLOW_DELETE, headers=ADMIN_OWNER)
soft_delete('delete-own-group', 'owner', delete_path('dora'), 'dora', ALLOW_DELETE, headers={'X-Group-Id': OWN_GROUP}, group=OWN_GROUP)
# The class's delete() answers DELETE on the other pk routes.
soft_delete('delete-through-update-route', 'owner', update_path('ivan'), 'ivan', ALLOW_UPDATE)
soft_delete('delete-through-recover-route', 'owner', recover_path('lino'), 'lino', ALLOW_RECOVER)
soft_delete('delete-through-info-route', 'owner', info_path('mara'), 'mara', ALLOW_INFO)
add('delete-through-add-route', 'owner', 'DELETE', ADD, 500, SERVER, allow=ALLOW_ADD)
add('delete-through-add-route-athlete', 'athlete', 'DELETE', ADD, 403, NOT_ASSOCIATION, allow=ALLOW_ADD)

# ================================================================= recover
add('recover-malformed-pk', 'owner', 'POST', recover_path('abc'), 500, SERVER, allow=ALLOW_RECOVER)
add('recover-missing', 'owner', 'POST', recover_path(UNKNOWN), 404, EMPTY, allow=ALLOW_RECOVER)
add('recover-without-association', 'owner', 'POST', recover_path('gone_orphan'), 500, SERVER, allow=ALLOW_RECOVER)
add('recover-foreign', 'owner', 'POST', recover_path('gone_abroad'), 403, NOT_ALLOWED, allow=ALLOW_RECOVER)
add('recover-foreign-live', 'owner', 'POST', recover_path('abroad'), 403, NOT_ALLOWED, allow=ALLOW_RECOVER)
# The lookup runs before request.user.sport_association.
add('recover-athlete-found', 'athlete', 'POST', recover_path('gone'), 403, NOT_ASSOCIATION, allow=ALLOW_RECOVER)
add('recover-athlete-missing', 'athlete', 'POST', recover_path(UNKNOWN), 404, EMPTY, allow=ALLOW_RECOVER)
add('recover-athlete-orphan', 'athlete', 'POST', recover_path('gone_orphan'), 500, SERVER, allow=ALLOW_RECOVER)
RECOVERED = {'mgs': 'Ripristinata con successo'}


def recover(name, actor, ref, *, headers=None, group=None):
    effect = Effect(actor, group)
    old_visible = visible(ref, group)
    effect.save(ref, audit_old=old_visible, deleted=False)
    add(name, actor, 'POST', recover_path(ref), 200, RECOVERED, allow=ALLOW_RECOVER, headers=headers, effect=effect)


recover('recover-owner', 'owner', 'gone')
recover('recover-live-no-entry', 'owner', 'alive')
recover('recover-other-group-header', 'owner', 'gone_grp', headers={'X-Group-Id': FOREIGN_GROUP}, group=FOREIGN_GROUP)
recover('recover-full-collaborator', 'full_collaborator', 'gone_collab')
recover('recover-admin-impersonating', 'admin', 'gone_admin', headers=ADMIN_OWNER)
add('recover-get', 'owner', 'GET', recover_path('gone'), 405, method_not_allowed('GET'), allow=ALLOW_RECOVER)

# ================================================================= bulk delete
add('bulk-delete-list-body', 'owner', 'DELETE', BULK, 500, SERVER, body=[A['b1']], allow=ALLOW_DELETE)
add('bulk-delete-string-ids', 'owner', 'DELETE', BULK, 500, SERVER, body={'associate_ids': 'abc'}, allow=ALLOW_DELETE)
add('bulk-delete-number-ids', 'owner', 'DELETE', BULK, 500, SERVER, body={'associate_ids': 5}, allow=ALLOW_DELETE)
add('bulk-delete-null-ids', 'owner', 'DELETE', BULK, 500, SERVER, body={'associate_ids': None}, allow=ALLOW_DELETE)
add('bulk-delete-malformed-id', 'owner', 'DELETE', BULK, 500, SERVER, body={'associate_ids': [A['b1'], 'zz']}, allow=ALLOW_DELETE)
add('bulk-delete-nested-id', 'owner', 'DELETE', BULK, 500, SERVER, body={'associate_ids': [[A['b1']]]}, allow=ALLOW_DELETE)
add('bulk-delete-athlete-number-ids', 'athlete', 'DELETE', BULK, 500, SERVER, body={'associate_ids': 5}, allow=ALLOW_DELETE)
add('bulk-delete-athlete-malformed-id', 'athlete', 'DELETE', BULK, 403, NOT_ASSOCIATION, body={'associate_ids': ['zz']}, allow=ALLOW_DELETE)
add('bulk-delete-no-body', 'owner', 'DELETE', BULK, 204, EMPTY, allow=ALLOW_DELETE, effect=Effect('owner'))
add('bulk-delete-empty-string', 'owner', 'DELETE', BULK, 204, EMPTY, body={'associate_ids': ''}, allow=ALLOW_DELETE, effect=Effect('owner'))
add('bulk-delete-empty-form', 'owner', 'DELETE', BULK, 204, EMPTY, raw_body='associate_ids=', content_type='application/x-www-form-urlencoded',
    allow=ALLOW_DELETE, effect=Effect('owner'))
add('bulk-delete-form-string', 'owner', 'DELETE', BULK, 500, SERVER, raw_body=f"associate_ids={A['b1']}",
    content_type='application/x-www-form-urlencoded', allow=ALLOW_DELETE)
effect = Effect('owner')
for ref in ('b1', 'b2', 'b_grp'):
    effect.quiet(ref, deleted=True)
add('bulk-delete-owner', 'owner', 'DELETE', BULK, 204, EMPTY,
    body={'associate_ids': [A['b1'], A['b2'].upper(), A['b_grp'], A['b_far'], A['b_gone'], UNKNOWN, None]}, allow=ALLOW_DELETE, effect=effect)
effect = Effect('owner', OWN_GROUP)
effect.quiet('b_grp2', deleted=True)
add('bulk-delete-group', 'owner', 'DELETE', BULK, 204, EMPTY, headers={'X-Group-Id': OWN_GROUP},
    body={'associate_ids': [A['b3'], A['b_grp2']]}, allow=ALLOW_DELETE, effect=effect)
effect = Effect('full_collaborator')
effect.quiet('b3', deleted=True)
add('bulk-delete-full-collaborator', 'full_collaborator', 'DELETE', BULK, 204, EMPTY, body={'associate_ids': {A['b3']: 1}},
    allow=ALLOW_DELETE, effect=effect)

# ================================================================= add
add('add-list-body', 'owner', 'POST', ADD, 500, SERVER, body=[{'first_name': 'X'}], allow=ALLOW_ADD)
add('add-string-body', 'owner', 'POST', ADD, 500, SERVER, body='x', allow=ALLOW_ADD)
add('add-form-body', 'owner', 'POST', ADD, 500, SERVER, raw_body='first_name=Forma', content_type='application/x-www-form-urlencoded',
    allow=ALLOW_ADD)
add('add-malformed-json', 'owner', 'POST', ADD, 400,
    {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    raw_body='{', content_type='application/json', allow=ALLOW_ADD)
add('add-athlete-malformed-json', 'athlete', 'POST', ADD, 403, NOT_ASSOCIATION, raw_body='{', content_type='application/json', allow=ALLOW_ADD)
add('add-validation', 'owner', 'POST', ADD, 400, {
    'first_name': ['Ensure this field has no more than 150 characters.'],
    'sex': ['"X" is not a valid choice.'],
    'tax_code': ['Not a valid string.'],
    'born_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'],
    'address_cap': ['Enter exactly 5 digits'],
    'email': ['Enter a valid email address.'],
    'phone': ['Ensure this field has no more than 30 characters.'],
    'is_minor': ['Must be a valid boolean.'],
    'deleted': ['This field may not be null.'],
    'notes': ['Null characters are not allowed.'],
    'family': ['“abc” is not a valid UUID.'],
    'group': [f'Invalid pk "{UNKNOWN}" - object does not exist.'],
    'user': ['Incorrect type. Expected pk value, received bool.'],
}, body={'first_name': 'x' * 151, 'sex': 'X', 'tax_code': True, 'born_date': '2010-01-01', 'address_cap': '12a', 'email': 'nope',
         'phone': '1' * 31, 'is_minor': 'maybe', 'deleted': None, 'notes': 'a\x00b', 'family': 'abc', 'group': UNKNOWN, 'user': True},
    allow=ALLOW_ADD)
add('add-validation-dates-and-nulls', 'owner', 'POST', ADD, 400, {
    'born_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'],
    'sex': ['This field may not be null.'],
    'migrated': ['This field may not be null.'],
    'address_cap': ['Enter exactly 5 digits', 'Ensure this field has no more than 5 characters.'],
    'sport_association': [f'Invalid pk "{UNKNOWN}" - object does not exist.'],
}, body={'born_date': '31/02/2010', 'sex': None, 'migrated': None, 'address_cap': '123456', 'sport_association': UNKNOWN}, allow=ALLOW_ADD)
add('add-foreign-association', 'owner', 'POST', ADD, 403, GENERIC, body={'sport_association': FOREIGN, 'first_name': 'x' * 151}, allow=ALLOW_ADD)
add('add-foreign-user', 'owner', 'POST', ADD, 403, GENERIC, body={'user': IDS['foreign_owner']}, allow=ALLOW_ADD)
add('add-foreign-group', 'owner', 'POST', ADD, 403, GENERIC, body={'group': FOREIGN_GROUP}, allow=ALLOW_ADD)
# The partial unique index on UPPER(tax_code), sport_association among live rows: the insert fails.
add('add-duplicate-tax-code', 'owner', 'POST', ADD, 500, SERVER, body={'first_name': 'Doppio', 'tax_code': 'txdtti80a01h501a'}, allow=ALLOW_ADD)

effect = Effect('owner')
ref = effect.create(user=OWNER)
add('add-empty', 'owner', 'POST', ADD, 201, info(ref), body={}, allow=ALLOW_ADD, effect=effect)

effect = Effect('owner')
ref = effect.create(user=OWNER, first_name='Alba', last_name='Nuova', sex='F', born_date='2011-09-08', tax_code='nwvlba11p48h501n',
                    born_city='Roma', address='Via Nuova 2', address_cap='00100', address_city='Roma', email='alba@example.test',
                    phone='3331112222', phone_2='0612345', phone_label='casa', is_minor=True, family=F2, family_role='figlia',
                    id_number='AB123', notes='<p>nuova</p>', nationality='Francese', draft=True, disabled=False, migrated=True,
                    picture_path='https://cdn.example.test/alba.jpg')
tutor = effect.create(first_name='Tullio', last_name='Nuovotutore', born_date='1970-02-01', sex='M', association=OWN)
effect.relate(ref, tutor, True)
add('add-full-with-new-tutor', 'owner', 'POST', ADD, 201, info(ref), body={
    'associate_id': UNKNOWN, 'creation_date': '2000-01-01T00:00:00Z', 'full_name': 'ignored', 'is_tutor': True, 'age': 99,
    'family_members': [UNKNOWN], 'incomplete': True, 'tutors': [{'tutor': {}}], 'unknown': 'ignored',
    'user': IDS['athlete'], 'sport_association': OWN,
    'first_name': ' Alba ', 'last_name': 'Nuova', 'sex': 'F', 'born_date': '08/09/2011', 'tax_code': 'nwvlba11p48h501n', 'born_city': 'Roma',
    'address': 'Via Nuova 2', 'address_cap': '00100', 'address_city': 'Roma', 'email': 'alba@example.test', 'phone': 3331112222,
    'phone_2': '0612345', 'phone_label': 'casa', 'is_minor': 'true', 'family': F2, 'family_role': 'figlia', 'id_number': 'AB123',
    'notes': '<p>nuova</p>', 'nationality': 'Francese', 'draft': 1, 'disabled': 'f', 'migrated': True,
    'picture_path': 'https://cdn.example.test/alba.jpg',
    'tutors_data': [{'tutor': {'associate_id': None, 'first_name': 'Tullio', 'last_name': 'Nuovotutore', 'born_date': '01/02/1970',
                               'sex': 'M', 'sport_association': OWN, 'tutors': [], 'creation_date': 'ignored'}, 'is_primary': True}],
}, allow=ALLOW_ADD, effect=effect)

# An existing tutor (Associate.objects by id) is partially updated; without a primary relation the first is promoted.
effect = Effect('owner')
ref = effect.create(user=OWNER, first_name='Bianca', last_name='Contutore', born_date='2012-12-12')
tutor_data = [{'tutor': {'associate_id': A['tutor_a'], 'phone': '3339990000', 'born_date': '01/02/1970', 'creation_date': SEED['tutor_a']['created'],
                         'full_name': 'ignored', 'tutors': [{'x': 1}]}}, {'tutor': None}, {'other': 1}]
handle_tutors(effect, ref, tutor_data)
add('add-existing-tutor-promoted', 'owner', 'POST', ADD, 201, info(ref),
    body={'first_name': 'Bianca', 'last_name': 'Contutore', 'born_date': '12/12/2012', 'tutors_data': tutor_data}, allow=ALLOW_ADD, effect=effect)

effect = Effect('full_collaborator')
ref = effect.create(user=OWNER, first_name='Cola', last_name='Collaboratore')
add('add-full-collaborator', 'full_collaborator', 'POST', ADD, 201, info(ref), body={'first_name': 'Cola', 'last_name': 'Collaboratore'},
    allow=ALLOW_ADD, effect=effect)
effect = Effect('admin')
ref = effect.create(user=OWNER, first_name='Ada', last_name='Impersonata')
add('add-admin-impersonating', 'admin', 'POST', ADD, 201, info(ref), body={'first_name': 'Ada', 'last_name': 'Impersonata'},
    headers=ADMIN_OWNER, allow=ALLOW_ADD, effect=effect)
# The manager's create stores X-Group-Id.
effect = Effect('owner', OWN_GROUP)
ref = effect.create(user=OWNER, first_name='Gemma', last_name='Gruppata', group=OWN_GROUP)
add('add-group-header', 'owner', 'POST', ADD, 201, info(ref, OWN_GROUP), body={'first_name': 'Gemma', 'last_name': 'Gruppata'},
    headers={'X-Group-Id': OWN_GROUP}, allow=ALLOW_ADD, effect=effect)
# A spelling of the group id UUID() reads (upper case, braces, no hyphens, urn) is audited as sent.
for label, spelling in (('upper-case', OWN_GROUP.upper()), ('braces', '{' + OWN_GROUP + '}'), ('no-hyphens', OWN_GROUP.replace('-', '')),
                        ('urn', 'urn:uuid:' + OWN_GROUP)):
    effect = Effect('owner', OWN_GROUP)
    ref = effect.create(sent_group=spelling, user=OWNER, first_name='Gemma', last_name=label.title(), group=OWN_GROUP)
    add(f'add-group-header-{label}', 'owner', 'POST', ADD, 201, {**info(ref, OWN_GROUP), 'group': spelling}, body={'first_name': 'Gemma', 'last_name': label.title()},
        headers={'X-Group-Id': spelling}, allow=ALLOW_ADD, effect=effect)
effect = Effect('owner')
ref = effect.create(user=OWNER, first_name='Gaio', last_name='Corpo', group=OWN_GROUP)
add('add-group-body', 'owner', 'POST', ADD, 201, info(ref), body={'first_name': 'Gaio', 'last_name': 'Corpo', 'group': OWN_GROUP},
    allow=ALLOW_ADD, effect=effect)
# The tax code matches the soft-deleted 'ghost' row: deleted rows are outside the unique index.
effect = Effect('owner')
ref = effect.create(user=OWNER, first_name='Rina', last_name='Omonima', tax_code='cncllt80a01h501c')
add('add-tax-code', 'owner', 'POST', ADD, 201, info(ref), body={'first_name': 'Rina', 'last_name': 'Omonima', 'tax_code': 'CNCLLT80A01H501C'.lower()},
    allow=ALLOW_ADD, effect=effect)

# Non-atomic: the associate is saved before _handle_tutors fails.
effect = Effect('owner')
effect.create(user=OWNER, first_name='Stringa')
add('add-string-tutors-data-saved-then-fails', 'owner', 'POST', ADD, 500, SERVER, body={'first_name': 'Stringa', 'tutors_data': 'x'},
    allow=ALLOW_ADD, effect=effect)
effect = Effect('owner')
effect.create(user=OWNER, first_name='Senzachiave')
add('add-tutor-without-key-saved-then-fails', 'owner', 'POST', ADD, 500, SERVER,
    body={'first_name': 'Senzachiave', 'tutors_data': [{'tutor': {'first_name': 'Anonimo'}}]}, allow=ALLOW_ADD, effect=effect)
effect = Effect('owner')
effect.create(user=OWNER, first_name='Tutorerrato')
add('add-tutor-invalid-saved-then-400', 'owner', 'POST', ADD, 400, {'email': ['Enter a valid email address.']},
    body={'first_name': 'Tutorerrato', 'tutors_data': [{'tutor': {'associate_id': A['tutor_a'], 'email': 'nope'}}]}, allow=ALLOW_ADD,
    effect=effect)
effect = Effect('owner')
ref = effect.create(user=OWNER, first_name='Vuoto')
add('add-empty-tutors-data-dict', 'owner', 'POST', ADD, 201, info(ref), body={'first_name': 'Vuoto', 'tutors_data': {}},
    allow=ALLOW_ADD, effect=effect)

# deleted is writable on create: the instance is rendered without a queryset; the index signal no longer finds it.
effect = Effect('owner')
ref = effect.create(user=OWNER, first_name='Morta', deleted=True)
add('add-deleted', 'owner', 'POST', ADD, 201, info(ref), body={'first_name': 'Morta', 'deleted': True}, allow=ALLOW_ADD, effect=effect)
# A tutor referencing another association: tenant_scope's 403 after the associate was saved.
effect = Effect('owner')
effect.create(user=OWNER, first_name='Tutoreestero')
add('add-foreign-tutor-association-saved-then-403', 'owner', 'POST', ADD, 403, GENERIC,
    body={'first_name': 'Tutoreestero', 'tutors_data': [{'tutor': {'associate_id': None, 'sport_association': FOREIGN}}]},
    allow=ALLOW_ADD, effect=effect)
# New tutors are created through the manager too: they store X-Group-Id.
effect = Effect('owner', OWN_GROUP)
ref = effect.create(user=OWNER, first_name='Gilda', last_name='Gruppotutore', group=OWN_GROUP)
tutor_data = [{'tutor': {'associate_id': None, 'first_name': 'Guido', 'last_name': 'Gruppotutore'}, 'is_primary': False}]
handle_tutors(effect, ref, tutor_data)
add('add-group-header-new-tutor', 'owner', 'POST', ADD, 201, info(ref, OWN_GROUP),
    body={'first_name': 'Gilda', 'last_name': 'Gruppotutore', 'tutors_data': tutor_data}, headers={'X-Group-Id': OWN_GROUP},
    allow=ALLOW_ADD, effect=effect)

# strptime's \\d reads decimal digits of any script (the day's second digit after an ASCII 1 or 2, and the year; the month is ASCII).
effect = Effect('owner')
ref = effect.create(user=OWNER, first_name='Bea', last_name='Cifre', born_date='2010-03-14')
add('add-born-date-unicode-digits', 'owner', 'POST', ADD, 201, info(ref), body={'first_name': 'Bea', 'last_name': 'Cifre', 'born_date': '1٤/03/٢٠١٠'},
    allow=ALLOW_ADD, effect=effect)
effect = Effect('owner')
ref = effect.create(user=OWNER, first_name='Bruna', last_name='Cifre', born_date='2011-05-06')
tutor_data = [{'tutor': {'associate_id': None, 'first_name': 'Tito', 'last_name': 'Cifre', 'born_date': '1२/01/१९७०', 'sport_association': OWN},
               'is_primary': True}]
handle_tutors(effect, ref, tutor_data)
add('add-tutor-born-date-unicode-digits', 'owner', 'POST', ADD, 201, info(ref),
    body={'first_name': 'Bruna', 'last_name': 'Cifre', 'born_date': '06/05/2011', 'tutors_data': tutor_data}, allow=ALLOW_ADD, effect=effect)

# ================================================================= update
add('update-malformed-pk', 'owner', 'PATCH', update_path('abc'), 404, NOT_FOUND, body={'notes': 'x'}, allow=ALLOW_UPDATE)
add('update-missing', 'owner', 'PATCH', update_path(UNKNOWN), 404, NO_MATCH, raw_body='{', content_type='application/json', allow=ALLOW_UPDATE)
add('update-foreign', 'owner', 'PATCH', update_path('abroad'), 404, NO_MATCH, body={'notes': 'x'}, allow=ALLOW_UPDATE)
add('update-other-group', 'owner', 'PATCH', update_path('grp_upd'), 404, NO_MATCH, body={'notes': 'x'},
    headers={'X-Group-Id': FOREIGN_GROUP}, allow=ALLOW_UPDATE)
add('update-malformed-json', 'owner', 'PATCH', update_path('upd'), 400,
    {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    raw_body='{', content_type='application/json', allow=ALLOW_UPDATE)
add('update-list-body', 'owner', 'PATCH', update_path('upd'), 500, SERVER, body=[1], allow=ALLOW_UPDATE)
add('update-validation', 'owner', 'PATCH', update_path('upd'), 400, {
    'tutors_data': ['Expected a list of items but got type "str".'],
    'last_name': ['Ensure this field has no more than 150 characters.'],
    'disabled': ['Must be a valid boolean.'],
    'born_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'],
}, body={'tutors_data': 'x', 'last_name': 'y' * 151, 'disabled': 'maybe', 'born_date': 5}, allow=ALLOW_UPDATE)
add('update-tutors-data-dict', 'owner', 'PATCH', update_path('upd'), 400, {'tutors_data': ['Expected a list of items but got type "dict".']},
    body={'tutors_data': {}}, allow=ALLOW_UPDATE)
add('update-tutors-data-null', 'owner', 'PATCH', update_path('upd'), 400, {'tutors_data': ['This field may not be null.']},
    body={'tutors_data': None}, allow=ALLOW_UPDATE)
add('update-foreign-user', 'owner', 'PATCH', update_path('upd'), 403, GENERIC, body={'user': IDS['foreign_owner']},
    allow=ALLOW_UPDATE)
# auditlog's pre_save entry is written (autocommit) before the UPDATE violates the unique index.
effect = Effect('owner')
effect.audit.append({'model': 'associate', 'ref': 'tax_upd', 'action': 1,
                     'changes': {'tax_code': ['None', 'TXDTTI80A01H501A'], 'notes': ['None', 'x']}, 'index': [OWN, 'direct']})
add('update-duplicate-tax-code-audited', 'owner', 'PATCH', update_path('tax_upd'), 500, SERVER,
    body={'tax_code': 'TXDTTI80A01H501A', 'notes': 'x'}, allow=ALLOW_UPDATE, effect=effect)

# The frontend sends the tutors back: unchanged tutors and relations write no entries.
effect = Effect('owner')
tutor_data = [{'tutor': {'associate_id': A['upd_t1'], 'first_name': 'Paola', 'born_date': '03/03/1975', 'creation_date': SEED['upd_t1']['created'],
                         'family': F1, 'sport_association': OWN, 'deleted': False}, 'is_primary': True},
              {'tutor': {'associate_id': A['upd_t2'], 'phone': '3334445555'}, 'is_primary': False}]
effect.save('upd', first_name='Ugone', email=None, born_date='2012-05-07')
handle_tutors(effect, 'upd', tutor_data)
add('update-with-tutors', 'owner', 'PATCH', update_path('upd'), 200, info('upd'),
    body={'first_name': 'Ugone', 'email': None, 'born_date': '07/05/2012', 'associate_id': UNKNOWN, 'creation_date': '2000-01-01T00:00:00Z',
          'tutors': [], 'full_name': 'ignored', 'tutors_data': tutor_data}, allow=ALLOW_UPDATE, effect=effect)

# Without tutors_data the context default [] deletes every relation.
effect = Effect('owner')
effect.save('wipe', notes='senza tutori')
handle_tutors(effect, 'wipe', [])
add('update-without-tutors-data-wipes', 'owner', 'PATCH', update_path('wipe'), 200, info('wipe'), body={'notes': 'senza tutori'},
    allow=ALLOW_UPDATE, effect=effect)

# Dropping the primary tutor and making the other primary.
effect = Effect('owner')
effect.save('swap')
tutor_data = [{'tutor': {'associate_id': A['swap_t2']}, 'is_primary': True}]
handle_tutors(effect, 'swap', tutor_data)
add('update-swap-primary', 'owner', 'PATCH', update_path('swap'), 200, info('swap'), body={'tutors_data': tutor_data}, allow=ALLOW_UPDATE,
    effect=effect)

# A second primary before the first is cleared violates unique_primary_tutor inside update_or_create's atomic
# block (its auditlog entry rolls back with it); the earlier writes (none change here) stay.
effect = Effect('owner')
effect.save('clash', notes='conflitto')
add('update-second-primary-fails-after-writes', 'owner', 'PATCH', update_path('clash'), 500, SERVER,
    body={'notes': 'conflitto', 'tutors_data': [{'tutor': {'associate_id': A['clash_t2']}, 'is_primary': True},
                          {'tutor': {'associate_id': A['clash_t1']}, 'is_primary': False}]}, allow=ALLOW_UPDATE, effect=effect)

# A tutor id Associate.objects does not find creates a new tutor (its associate_id is popped).
effect = Effect('owner')
effect.save('newtut')
tutor_data = [{'tutor': {'associate_id': UNKNOWN, 'first_name': 'Nuovo', 'last_name': 'Tutore', 'email': 'nuovo@example.test'}}]
handle_tutors(effect, 'newtut', tutor_data)
add('update-creates-tutor', 'owner', 'PATCH', update_path('newtut'), 200, info('newtut'), body={'tutors_data': tutor_data},
    allow=ALLOW_UPDATE, effect=effect)

effect = Effect('owner')
effect.save('keyless', notes='prima')
add('update-tutor-without-key-fails-after-save', 'owner', 'PATCH', update_path('keyless'), 500, SERVER,
    body={'notes': 'prima', 'tutors_data': [{'tutor': {'first_name': 'Senza'}}]}, allow=ALLOW_UPDATE, effect=effect)

effect = Effect('owner')
effect.save('promote')
tutor_data = [{'tutor': {'associate_id': A['promote_t']}}]
handle_tutors(effect, 'promote', tutor_data)
add('update-promotes-first-relation', 'owner', 'PATCH', update_path('promote'), 200, info('promote'), body={'tutors_data': tutor_data},
    allow=ALLOW_UPDATE, effect=effect)

effect = Effect('owner')
effect.save('collab_upd', born_date='2013-02-13')
add('update-born-date-unicode-digits', 'owner', 'PATCH', update_path('collab_upd'), 200, info('collab_upd'),
    body={'born_date': '1३/02/२०१३'}, allow=ALLOW_UPDATE, effect=effect)
effect = Effect('full_collaborator')
effect.save('collab_upd', phone='3330001111', sex='M')
add('update-full-collaborator', 'full_collaborator', 'PATCH', update_path('collab_upd'), 200, info('collab_upd'),
    body={'phone': '3330001111', 'sex': 'M'}, allow=ALLOW_UPDATE, effect=effect)

effect = Effect('owner', OWN_GROUP)
effect.save('grp_upd', last_name='Gruppone', group=None)
add('update-own-group-moves-out', 'owner', 'PATCH', update_path('grp_upd'), 404, NO_MATCH, body={'last_name': 'Gruppone', 'group': None},
    headers={'X-Group-Id': OWN_GROUP}, allow=ALLOW_UPDATE, effect=effect)

# deleted=True is writable: the second get_object() no longer finds the row.
effect = Effect('owner')
effect.save('drop', deleted=True)
add('update-deleted-then-missing', 'owner', 'PATCH', update_path('drop'), 404, NO_MATCH, body={'deleted': True}, allow=ALLOW_UPDATE, effect=effect)

effect = Effect('admin')
effect.save('form_upd', first_name='Formato', is_minor=True, notes='')
add('update-form-body', 'admin', 'PATCH', update_path('form_upd'), 200, info('form_upd'), raw_body='first_name=Formato&is_minor=on&notes=',
    content_type='application/x-www-form-urlencoded', headers=ADMIN_OWNER, allow=ALLOW_UPDATE, effect=effect)
add('update-post', 'owner', 'POST', update_path('upd'), 405, method_not_allowed('POST'), allow=ALLOW_UPDATE)
effect = Effect('owner')
effect.save('form_upd', notes='y')
add('update-form-tutors-string-fails-after-save', 'owner', 'PATCH', update_path('form_upd'), 500, SERVER, raw_body='notes=y&tutors_data=x',
    content_type='application/x-www-form-urlencoded', allow=ALLOW_UPDATE, effect=effect)
# A new tutor's relation as a second primary: get_or_create's create violates unique_primary_tutor (its entry rolls back
# with update_or_create's atomic block); the created tutor stays.
effect = Effect('owner')
effect.create(first_name='Terzo', last_name='Tutore', association=None)
add('update-new-primary-conflict-keeps-tutor', 'owner', 'PATCH', update_path('upd'), 500, SERVER, body={'tutors_data': [
    {'tutor': {'associate_id': A['upd_t1']}, 'is_primary': True}, {'tutor': {'associate_id': A['upd_t2']}, 'is_primary': False},
    {'tutor': {'associate_id': None, 'first_name': 'Terzo', 'last_name': 'Tutore'}, 'is_primary': True}]}, allow=ALLOW_UPDATE, effect=effect)
# sport_association set to None: the second get_object() no longer finds the row.
effect = Effect('owner')
effect.save('orphaned', association=None)
add('update-association-null-then-missing', 'owner', 'PATCH', update_path('orphaned'), 404, NO_MATCH, body={'sport_association': None},
    allow=ALLOW_UPDATE, effect=effect)

# ================================================================= personas-subscriptions
def subscription(label, group=None):
    associate, start, end, created, extra = SUBSCRIPTIONS[label]
    row = row_of(associate)
    current = True
    status = 'corrente'
    # start <= today <= end holds for every authored current subscription; past ones end before 2020.
    if end < '2020-01-01':
        current, status = False, 'precedente'
    elif start > '2098-12-31':
        current, status = False, 'successivo'
    renewal = False
    if not current:
        later = [k for k, (a, s, e, c, x) in SUBSCRIPTIONS.items() if row_of(a)['tax_code'] and row_of(a)['tax_code'].upper() ==
                 (row['tax_code'] or '').upper() and s >= end and not x.get('archived') and x.get('association', OWN) == OWN
                 and (group is None or x.get('group') == group)]
        renewal = not later
    return {
        'subscription_id': SUB_IDS[label], 'active': current, 'is_current': current, 'is_next_year': status == 'successivo',
        'current_year': status, 'medical': MEDICAL['id'] if extra.get('medical') else None,
        'medical_expiration_date': MEDICAL['expiration'] if extra.get('medical') else None, 'age': age(row),
        'renewal_available': renewal, 'signature_present': bool(extra.get('signature_url') or extra.get('signature_storage_key')),
        'associate': basic_associate(associate, group), 'custom_data': extra.get('custom_data'),
        'additional_fields': extra.get('additional_fields'), 'type': extra.get('type', 2), 'role': extra.get('role', 1),
        'acceptance_date': extra.get('acceptance_date'), 'resignation_date': None, 'status_flag': extra.get('status_flag', 1),
        'creation_date': created, 'start_date': start, 'end_date': end, 'signature_url': extra.get('signature_url'),
        'deleted': extra.get('deleted', False), 'archived': extra.get('archived', False), 'draft': extra.get('draft', True),
        'trial': extra.get('trial', False), 'meta': extra.get('meta'), 'subscription_number': extra.get('subscription_number', '0'),
        'subscription_type': None, 'competitive': extra.get('competitive', False), 'notes': extra.get('notes'),
        'sport_association': extra.get('association', OWN), 'supplier': None, 'instructor': None, 'user': OWNER, 'document_pdf': None,
        'payment': None, 'group': extra.get('group'), 'tags': [TAG['id']] if extra.get('tags') else []}


def sub_page(labels, *, number=1, size=10, total=None, group=None):
    total = len(labels) if total is None else total
    pages = max(1, -(-total // size))
    return {'data': [subscription(label, group) for label in labels],
            'meta': {'total': total, 'page': number, 'pages': pages, 'perpage': size,
                     'rowIds': [row_of(SUBSCRIPTIONS[label][0])['id'] for label in labels]}}


# Ordered by -creation_date.
add('subscriptions-owner', 'owner', 'GET', subs_path('subbed'), 200, sub_page(['cur', 'old', 'grouped', 'future']), allow=ALLOW_SUBS)
add('subscriptions-full-collaborator', 'full_collaborator', 'GET', subs_path('subbed'), 200, sub_page(['cur', 'old', 'grouped', 'future']),
    allow=ALLOW_SUBS)
add('subscriptions-admin-impersonating', 'admin', 'GET', subs_path('subbed'), 200, sub_page(['cur', 'old', 'grouped', 'future']),
    headers=ADMIN_OWNER, allow=ALLOW_SUBS)
add('subscriptions-lone-renewal', 'owner', 'GET', subs_path('lone'), 200, sub_page(['lone_past']), allow=ALLOW_SUBS)
add('subscriptions-group', 'owner', 'GET', subs_path('subbed'), 200, sub_page(['grouped'], group=OWN_GROUP), headers={'X-Group-Id': OWN_GROUP}, allow=ALLOW_SUBS)
add('subscriptions-page-two', 'owner', 'GET', subs_path('subbed') + '?pagination[perpage]=2&pagination[page]=2', 200,
    sub_page(['grouped', 'future'], number=2, size=2, total=4), allow=ALLOW_SUBS)
add('subscriptions-last-page', 'owner', 'GET', subs_path('subbed') + '?pagination[perpage]=3&pagination[page]=last', 200,
    sub_page(['future'], number=2, size=3, total=4), allow=ALLOW_SUBS)
add('subscriptions-invalid-page', 'owner', 'GET', subs_path('subbed') + '?pagination[page]=9', 404, {'detail': 'Invalid page.'}, allow=ALLOW_SUBS)
add('subscriptions-foreign-associate', 'owner', 'GET', subs_path('sub_far'), 200, sub_page([]), allow=ALLOW_SUBS)
add('subscriptions-foreign-owner', 'foreign_owner', 'GET', subs_path('subbed'), 200, sub_page(['far']), allow=ALLOW_SUBS)
add('subscriptions-unknown', 'owner', 'GET', subs_path(UNKNOWN), 200, sub_page([]), allow=ALLOW_SUBS)
add('subscriptions-malformed-pk', 'owner', 'GET', subs_path('abc'), 500, SERVER, allow=ALLOW_SUBS)
add('subscriptions-delete-associate-id', 'owner', 'DELETE', subs_path('subbed'), 404, {'detail': 'No Subscription matches the given query.'},
    allow=ALLOW_SUBS)
add('subscriptions-delete-malformed', 'owner', 'DELETE', subs_path('abc'), 404, NOT_FOUND, allow=ALLOW_SUBS)
add('subscriptions-delete-athlete', 'athlete', 'DELETE', subs_path('subbed'), 403, NOT_ASSOCIATION, allow=ALLOW_SUBS)

# ================================================================= picture uploads
# AssociateSerializer.to_internal_value: a picture_path containing 'base64' is decoded (base64.b64decode, the text
# after the first comma) before the field validation and left out of it. create() / update() then save it through the
# default storage as associates/<the associate's id>/picture_<timestamp>.jpg (a new associate gets its id first),
# put_object_acl public-read (AWS_S3_USE_OBJECT_ACL, on for the run) and store the object's URL under the storage's
# public address (the run has no public base URL: <endpoint>/<bucket>).
PICTURE = base64.b64encode(b'\x89PNG\r\n\x1a\n personas picture fixture \xff\xfe').decode()
PICTURE_2 = base64.b64encode(b'\xff\xd8\xff\xe0 second picture ?>~').decode()
assert '+' in PICTURE_2 or '/' in PICTURE_2
_pictures = iter(range(1, 100))


def picture(folder, body, *, denied=False, case=None):
    """A stored picture: its placeholder (the object's URL) and the storage requests of the upload.
    folder: the associate's id, or None until created_picture names the new associate."""
    ref = f'$pic{next(_pictures)}'
    return ref, {'pictures': {ref: {'folder': folder, 'body_b64': body}},
                 'storage': [f'HEAD {ref}', f'PUT {ref}', f'ACL {ref}' + (' denied' if denied else '')]}


def created_picture(effect, body, **values):
    """A new associate with an uploaded picture: the object's folder is the new associate's id."""
    pic, upload = picture(None, body)
    ref = effect.create(picture_path=pic, **values)
    upload['pictures'][pic]['folder'] = ref
    return ref, upload


def add_picture(name, *args, upload=None, **kwargs):
    add(name, *args, **kwargs)
    if upload:
        cases[-1].update(upload)


effect = Effect('owner')
ref, upload = created_picture(effect, PICTURE, user=OWNER, first_name='Fiamma', last_name='Foto')
add_picture('add-picture', 'owner', 'POST', ADD, 201, info(ref),
            body={'first_name': 'Fiamma', 'last_name': 'Foto', 'picture_path': 'data:image/png;base64,' + PICTURE}, allow=ALLOW_ADD,
            effect=effect, upload=upload)
# associate_id is read-only: a body value is not used, the folder is the new associate's id.
effect = Effect('owner')
ref, upload = created_picture(effect, PICTURE_2, user=OWNER, first_name='Fedra')
add_picture('add-picture-body-associate-id', 'owner', 'POST', ADD, 201, info(ref),
            body={'associate_id': UNKNOWN, 'first_name': 'Fedra', 'picture_path': 'data:image/jpeg;base64,' + PICTURE_2},
            allow=ALLOW_ADD, effect=effect, upload=upload)
effect = Effect('owner')
ref, upload = created_picture(effect, PICTURE, user=OWNER, first_name='Fedora')
add_picture('add-picture-body-associate-id-seeded', 'owner', 'POST', ADD, 201, info(ref),
            body={'associate_id': A['pic_keep'], 'first_name': 'Fedora', 'picture_path': 'data:image/png;base64,' + PICTURE},
            allow=ALLOW_ADD, effect=effect, upload=upload)
effect = Effect('owner')
ref, upload = created_picture(effect, PICTURE_2, user=OWNER, first_name='Felice')
add_picture('add-picture-body-associate-id-path-text', 'owner', 'POST', ADD, 201, info(ref),
            body={'associate_id': '../../other/folder', 'first_name': 'Felice', 'picture_path': 'data:image/jpeg;base64,' + PICTURE_2},
            allow=ALLOW_ADD, effect=effect, upload=upload)
effect = Effect('owner')
ref, upload = created_picture(effect, PICTURE, user=OWNER, first_name='Fedele')
add_picture('add-picture-body-associate-id-number', 'owner', 'POST', ADD, 201, info(ref),
            body={'associate_id': 7, 'first_name': 'Fedele', 'picture_path': 'data:image/png;base64,' + PICTURE},
            allow=ALLOW_ADD, effect=effect, upload=upload)
# The field validation precedes the upload: nothing is stored and nothing is saved.
add('add-picture-then-invalid', 'owner', 'POST', ADD, 400, {'email': ['Enter a valid email address.']},
    body={'first_name': 'Falco', 'email': 'nope', 'picture_path': 'data:image/png;base64,' + PICTURE}, allow=ALLOW_ADD)
add('add-picture-then-invalid-with-associate-id', 'owner', 'POST', ADD, 400, {'email': ['Enter a valid email address.']},
    body={'associate_id': A['pic_keep'], 'first_name': 'Falco', 'email': 'nope', 'picture_path': 'data:image/png;base64,' + PICTURE},
    allow=ALLOW_ADD)
# b64decode's binascii.Error becomes a ValidationError that the surrounding handler wraps again.
PICTURE_INVALID = {'picture_path': "Error processing image: {'picture_path': ErrorDetail(string='Invalid base64 string', code='invalid')}"}
add('add-picture-incorrect-padding', 'owner', 'POST', ADD, 400, PICTURE_INVALID,
    body={'first_name': 'Fausto', 'picture_path': 'data:image/png;base64,QUJD' + 'QQ'}, allow=ALLOW_ADD)
add('add-picture-one-character-quad', 'owner', 'POST', ADD, 400, PICTURE_INVALID,
    body={'first_name': 'Fausto', 'picture_path': 'data:image/png;base64,QUJDQ'}, allow=ALLOW_ADD)
add('add-picture-non-ascii', 'owner', 'POST', ADD, 400,
    {'picture_path': 'Error processing image: string argument should contain only ASCII characters'},
    body={'first_name': 'Fausto', 'picture_path': 'data:image/png;base64,QUJDè'}, allow=ALLOW_ADD)
# Characters outside the alphabet are skipped by b64decode.
effect = Effect('owner')
ref, upload = created_picture(effect, PICTURE, user=OWNER, first_name='Frida')
add_picture('add-picture-spaced-base64', 'owner', 'POST', ADD, 201, info(ref),
            body={'first_name': 'Frida', 'picture_path': 'data:image/png;base64,' + PICTURE[:10] + ' \n' + PICTURE[10:20] + '!' + PICTURE[20:]},
            allow=ALLOW_ADD, effect=effect, upload=upload)
# Without a comma the whole value is decoded.
effect = Effect('owner')
ref, upload = created_picture(effect, 'base64AAAAAA', user=OWNER, first_name='Fulgenzio')
add_picture('add-picture-without-comma', 'owner', 'POST', ADD, 201, info(ref), body={'first_name': 'Fulgenzio', 'picture_path': 'base64AAAAAA'},
            allow=ALLOW_ADD, effect=effect, upload=upload)
# Nothing after the comma: an empty object.
effect = Effect('owner')
ref, upload = created_picture(effect, '', user=OWNER, first_name='Filippo')
add_picture('add-picture-empty', 'owner', 'POST', ADD, 201, info(ref), body={'first_name': 'Filippo', 'picture_path': 'data:image/png;base64,'},
            allow=ALLOW_ADD, effect=effect, upload=upload)
add('add-picture-athlete', 'athlete', 'POST', ADD, 403, NOT_ASSOCIATION,
    body={'first_name': 'Atleta', 'picture_path': 'data:image/png;base64,' + PICTURE}, allow=ALLOW_ADD)
# A new tutor's picture goes through BasicAssociateSerializer: stored as sent.
effect = Effect('owner')
ref, upload = created_picture(effect, PICTURE, user=OWNER, first_name='Fiore')
tutor_data = [{'tutor': {'associate_id': None, 'first_name': 'Fosco', 'picture_path': 'data:image/png;base64,QUJD'}, 'is_primary': True}]
handle_tutors(effect, ref, tutor_data)
add_picture('add-picture-tutor-picture-kept', 'owner', 'POST', ADD, 201, info(ref),
            body={'first_name': 'Fiore', 'picture_path': 'data:image/png;base64,' + PICTURE, 'tutors_data': tutor_data}, allow=ALLOW_ADD,
            effect=effect, upload=upload)

# update: the edit form sends the associate back with its id.
effect = Effect('owner')
pic, upload = picture(A['pic_upd'], PICTURE_2)
effect.save('pic_upd', picture_path=pic, notes='nuova foto')
handle_tutors(effect, 'pic_upd', [])
add_picture('update-picture', 'owner', 'PATCH', update_path('pic_upd'), 200, info('pic_upd'),
            body={'associate_id': A['pic_upd'], 'notes': 'nuova foto', 'picture_path': 'data:image/jpeg;base64,' + PICTURE_2, 'incomplete': False,
                  'tutors_data': []}, allow=ALLOW_UPDATE, effect=effect, upload=upload)
# A stored URL sent back is not an upload.
effect = Effect('owner')
effect.save('pic_keep', first_name='Fabio Maria', picture_path='https://cdn.example.test/fabio-new.jpg')
handle_tutors(effect, 'pic_keep', [])
add('update-picture-url-not-uploaded', 'owner', 'PATCH', update_path('pic_keep'), 200, info('pic_keep'),
    body={'associate_id': A['pic_keep'], 'first_name': 'Fabio Maria', 'picture_path': 'https://cdn.example.test/fabio-new.jpg'},
    allow=ALLOW_UPDATE, effect=effect)
# The folder is the associate's id, with or without associate_id in the body.
effect = Effect('full_collaborator')
pic, upload = picture(A['pic_collab'], PICTURE)
effect.save('pic_collab', picture_path=pic)
handle_tutors(effect, 'pic_collab', [])
add_picture('update-picture-full-collaborator', 'full_collaborator', 'PATCH', update_path('pic_collab'), 200, info('pic_collab'),
            body={'picture_path': 'data:image/png;base64,' + PICTURE}, allow=ALLOW_UPDATE, effect=effect, upload=upload)
effect = Effect('admin')
pic, upload = picture(A['pic_form'], PICTURE_2)
effect.save('pic_form', picture_path=pic, first_name='Flora Anna')
handle_tutors(effect, 'pic_form', [])
add_picture('update-picture-form-body', 'admin', 'PATCH', update_path('pic_form'), 200, info('pic_form'),
            raw_body=urllib.parse.urlencode({'associate_id': A['pic_form'], 'first_name': 'Flora Anna',
                                             'picture_path': 'data:image/jpeg;base64,' + PICTURE_2}),
            content_type='application/x-www-form-urlencoded', headers=ADMIN_OWNER, allow=ALLOW_UPDATE, effect=effect, upload=upload)
# A body associate_id of another associate, or any text, does not move the object.
effect = Effect('owner')
pic, upload = picture(A['pic_other'], PICTURE)
effect.save('pic_other', picture_path=pic)
handle_tutors(effect, 'pic_other', [])
add_picture('update-picture-body-associate-id-other', 'owner', 'PATCH', update_path('pic_other'), 200, info('pic_other'),
            body={'associate_id': A['pic_keep'], 'picture_path': 'data:image/png;base64,' + PICTURE}, allow=ALLOW_UPDATE, effect=effect,
            upload=upload)
effect = Effect('owner')
pic, upload = picture(A['pic_text'], PICTURE_2)
effect.save('pic_text', picture_path=pic)
handle_tutors(effect, 'pic_text', [])
add_picture('update-picture-body-associate-id-path-text', 'owner', 'PATCH', update_path('pic_text'), 200, info('pic_text'),
            body={'associate_id': '../' + A['pic_keep'], 'picture_path': 'data:image/jpeg;base64,' + PICTURE_2}, allow=ALLOW_UPDATE,
            effect=effect, upload=upload)
# The field validation precedes the upload: nothing is stored.
add('update-picture-then-invalid', 'owner', 'PATCH', update_path('pic_keep'), 400, {'sex': ['"X" is not a valid choice.']},
    body={'associate_id': A['pic_keep'], 'sex': 'X', 'picture_path': 'data:image/png;base64,' + PICTURE}, allow=ALLOW_UPDATE)
# The storage refuses the ACL: the saved object stays, the row is unchanged and the error text is the answer.
pic, upload = picture(A['pic_denied'], PICTURE, denied=True)
add_picture('update-picture-acl-refused', 'owner', 'PATCH', update_path('pic_denied'), 400,
            {'picture_path': 'Error processing image: An error occurred (AccessDenied) when calling the PutObjectAcl operation: Access Denied.'},
            body={'first_name': 'Fermo Maria', 'picture_path': 'data:image/png;base64,' + PICTURE}, allow=ALLOW_UPDATE, upload=upload)
add('update-picture-incorrect-padding', 'owner', 'PATCH', update_path('pic_keep'), 400, PICTURE_INVALID,
    body={'picture_path': 'data:image/png;base64,QUJDQQ'}, allow=ALLOW_UPDATE)
# get_object() precedes the serializer: nothing is uploaded for a row that is not found.
add('update-picture-missing-row', 'owner', 'PATCH', update_path(UNKNOWN), 404, NO_MATCH,
    body={'picture_path': 'data:image/png;base64,' + PICTURE}, allow=ALLOW_UPDATE)
effect = Effect('owner')
effect.save('pic_keep', picture_path=None)
handle_tutors(effect, 'pic_keep', [])
add('update-picture-null', 'owner', 'PATCH', update_path('pic_keep'), 200, info('pic_keep'), body={'picture_path': None},
    allow=ALLOW_UPDATE, effect=effect)

# === tutor flags and numeric tutor ids (_handle_tutors on the raw JSON values)
# is_primary reaches AssociateTutorRelation.is_primary (a not-null BooleanField) unconverted: auditlog renders str() of the
# raw value, the column keeps what BooleanField.to_python prepares (True/False, 1/0 and equal floats, t/True/1 and f/False/0)
# and anything else is a ValidationError inside update_or_create's atomic block (the 500 page; earlier writes stay).
_flag = iter(range(1, 100))


def add_flag(label, value):
    n = next(_flag)
    effect = Effect('owner')
    ref = effect.create(user=OWNER, first_name=f'Flag{n}')
    tutor_data = [{'tutor': {'associate_id': None, 'first_name': f'Tut{n}'}, 'is_primary': value}]
    try:
        prepare_primary(value)
    except ValueError:
        effect.create(first_name=f'Tut{n}', association=None, group=None)
        add(f'add-primary-{label}-saved-then-fails', 'owner', 'POST', ADD, 500, SERVER,
            body={'first_name': f'Flag{n}', 'tutors_data': tutor_data}, allow=ALLOW_ADD, effect=effect)
        return
    handle_tutors(effect, ref, tutor_data)
    add(f'add-primary-{label}', 'owner', 'POST', ADD, 201, info(ref), body={'first_name': f'Flag{n}', 'tutors_data': tutor_data},
        allow=ALLOW_ADD, effect=effect)


for label, value in (('int-one', 1), ('int-zero', 0), ('float-one', 1.0), ('float-zero', 0.0), ('text-t', 't'), ('text-true', 'True'),
                     ('text-one', '1'), ('text-f', 'f'), ('text-false', 'False'), ('text-zero', '0'), ('int-two', 2),
                     ('float-fraction', 1.5), ('text-lower-true', 'true'), ('text-yes', 'yes'), ('text-empty', ''), ('null', None),
                     ('list', []), ('dict', {}), ('huge-int', 10 ** 30), ('negative', -1)):
    add_flag(label, value)


def update_flags(label, first, second, status=200, saved=()):
    """PATCH prim with the two tutors flagged `first` and `second`."""
    tutor_data = [{'tutor': {'associate_id': A['prim_t1']}, 'is_primary': first},
                  {'tutor': {'associate_id': A['prim_t2']}, 'is_primary': second}]
    effect = Effect('owner')
    effect.save('prim')
    if status == 200:
        handle_tutors(effect, 'prim', tutor_data)
        add(f'update-primary-{label}', 'owner', 'PATCH', update_path('prim'), 200, info('prim'), body={'tutors_data': tutor_data},
            allow=ALLOW_UPDATE, effect=effect)
        return
    for ref, value in saved:
        effect.set_primary(ref, *prepare_primary(value))
    add(f'update-primary-{label}', 'owner', 'PATCH', update_path('prim'), 500, SERVER, body={'tutors_data': tutor_data},
        allow=ALLOW_UPDATE, effect=effect)


# The unchanged column value still gets an entry when str() of the raw value differs.
update_flags('int-text-pair', 1, '0')
update_flags('t-and-false', 't', 'False')
update_flags('floats', 1.0, 0.0)
update_flags('same-text', 'True', 'False')
update_flags('invalid-first-changes-nothing', 2, False, status=500)
_first = [ref for ref, r in WORLD['rel'].items() if r['associate'] == 'prim' and r['tutor'] == 'prim_t1'][0]
update_flags('invalid-second-keeps-first', '1', 'yes', status=500, saved=[(_first, '1')])
# A cleared primary: the first relation (lowest key) is promoted.
update_flags('zero-promotes-first', 0, 'f')

# Tutor ids: an integer (a bool too) is UUID(int=n); one no row has creates a new tutor, one a row has updates it.
_ids_n = iter(range(1, 100))


def tutor_id_reads(value):
    """Whether the exclude() accepts the id: UUIDField.to_python takes an int below 2**128 (a bool too), not a float or a list."""
    return isinstance(value, int) and 0 <= value < 2 ** 128


def add_tutor_id(label, value, **info_fields):
    n = next(_ids_n)
    effect = Effect('owner')
    ref = effect.create(user=OWNER, first_name=f'Pid{n}')
    tutor_data = [{'tutor': {'associate_id': value, 'first_name': f'Nt{n}', 'phone': '3331112222', **info_fields}}]
    body = {'first_name': f'Pid{n}', 'tutors_data': tutor_data}
    if not tutor_id_reads(value):
        add(f'add-tutor-id-{label}-saved-then-fails', 'owner', 'POST', ADD, 500, SERVER, body=body, allow=ALLOW_ADD, effect=effect)
        return
    handle_tutors(effect, ref, tutor_data)
    add(f'add-tutor-id-{label}', 'owner', 'POST', ADD, 201, info(ref), body=body, allow=ALLOW_ADD, effect=effect)


for label, value in (('int-new', 5), ('bool-true-existing', True), ('bool-false-new', False), ('zero-new', 0), ('int-existing', 4242),
                     ('int-one-existing', 1), ('int-large-new', 2 ** 127 + 7), ('negative', -1), ('float', 1.5), ('float-whole', 4242.0),
                     ('float-exponent', 1e3), ('int-too-large', 2 ** 128), ('text-number', '4242'), ('list', [1]), ('dict', {'a': 1})):
    add_tutor_id(label, value)

# On update the old relations are dropped and the tutor an integer names is updated through BasicAssociateSerializer.
for seed, label, value in (('idu', 'int-existing', 4242), ('idu2', 'bool-existing', True), ('idu3', 'int-new', 77)):
    tutor_data = [{'tutor': {'associate_id': value, 'phone': '3334445555'}, 'is_primary': True}]
    effect = Effect('owner')
    effect.save(seed)
    handle_tutors(effect, seed, tutor_data)
    add(f'update-tutor-id-{label}', 'owner', 'PATCH', update_path(seed), 200, info(seed), body={'tutors_data': tutor_data},
        allow=ALLOW_UPDATE, effect=effect)
effect = Effect('owner')
effect.save('idu4')
add('update-tutor-id-float-fails-after-save', 'owner', 'PATCH', update_path('idu4'), 500, SERVER,
    body={'tutors_data': [{'tutor': {'associate_id': 1.5, 'phone': '3330000000'}}]}, allow=ALLOW_UPDATE, effect=effect)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'personas writes authored sequence with associate, tutor relation and audit effects',
    'ids': IDS, 'families': [F1, F2], 'associates': SEED, 'relations': RELATIONS, 'medical': MEDICAL, 'tag': TAG,
    'subscriptions': {label: {'id': SUB_IDS[label], 'associate': A[a], 'start': s, 'end': e, 'created': c, **x}
                      for label, (a, s, e, c, x) in SUBSCRIPTIONS.items()},
    'reverse': REVERSE, 'acl_denied': ACL_DENIED,
    'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
