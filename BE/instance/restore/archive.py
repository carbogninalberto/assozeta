"""Bounded, non-extracting validation of the Bakney export contract."""
import hashlib
import json
import stat
import zipfile
import zlib
from pathlib import PurePosixPath, PureWindowsPath
from django.conf import settings
from django.db.models import CharField
from django.utils.dateparse import parse_date
from django.core.exceptions import ValidationError
from application.services.import_service import AssociationImportService
from application.services.export_service import AssociationExportService
from application.services.legacy_backup import DEPRECATED_SECTIONS, skipped_records


class RestoreError(ValueError):
    pass


def digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def format_size(size):
    for unit, divisor in (('GiB', 1024**3), ('MiB', 1024**2), ('KiB', 1024)):
        if size >= divisor:
            return f'{size / divisor:.2f} {unit}'
    return f'{size} byte'


class Archive:
    def __init__(self, path):
        self.path = path
        self.records = {}
        self.media = {}
        self.manifest = {}

    def validate(self):
        try:
            return self._validate()
        except RestoreError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, zipfile.BadZipFile, zlib.error, UnicodeError, RuntimeError, ValidationError) as exc:
            raise RestoreError('Il backup ZIP è danneggiato o non rispetta il formato Bakney.') from exc

    def _validate_entries(self, entries):
        max_expanded = getattr(settings, 'DATA_RESTORE_MAX_EXPANDED_BYTES', 20 * 1024**3)
        max_json = getattr(settings, 'DATA_RESTORE_MAX_JSON_BYTES', 512 * 1024**2)
        max_file = getattr(settings, 'DATA_RESTORE_MAX_FILE_BYTES', 5 * 1024**3)
        if len(entries) > 50000:
            raise RestoreError('Il backup contiene più di 50.000 file.')
        names = set()
        for entry in entries:
            name = entry.filename
            if name in names:
                raise RestoreError(f'File duplicato nel backup: {name}.')
            names.add(name)
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or PureWindowsPath(name).drive:
                raise RestoreError(f'Percorso non consentito nel backup: {name}.')
            if '\\' in name:
                # Linux exports may contain a literal backslash in a media
                # basename. Read that exact ZIP member; stage_media replaces the
                # backslash before storage backends can interpret it as a slash.
                parts = name.split('/')
                basename = PureWindowsPath(parts[-1])
                if (len(parts) != 4 or parts[0] != 'files' or not all(parts)
                        or any('\\' in part for part in parts[:-1])
                        or basename.drive or basename.root
                        or '..' in PurePosixPath(parts[-1].replace('\\', '/')).parts):
                    raise RestoreError(f'Percorso non consentito nel backup: {name}.')
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise RestoreError(f'Il backup contiene un collegamento simbolico non consentito: {name}.')
            if entry.flag_bits & 1:
                raise RestoreError(f'Il backup contiene un file cifrato non supportato: {name}.')
            is_json = name.lower().endswith('.json')
            limit = max_json if is_json else max_file
            if entry.file_size > limit:
                setting = 'DATA_RESTORE_MAX_JSON_BYTES' if is_json else 'DATA_RESTORE_MAX_FILE_BYTES'
                raise RestoreError(
                    f'Il file {name} è troppo grande: {format_size(entry.file_size)} non compressi; '
                    f'limite {format_size(limit)} ({setting}).')
        expanded = sum(entry.file_size for entry in entries)
        if expanded > max_expanded:
            raise RestoreError(
                f'Il backup occupa {format_size(expanded)} non compressi; '
                f'limite {format_size(max_expanded)} (DATA_RESTORE_MAX_EXPANDED_BYTES).')
        return names

    def _validate(self):
        with zipfile.ZipFile(self.path) as zf:
            entries = zf.infolist()
            names = self._validate_entries(entries)
            self.manifest = json.loads(zf.read('manifest.json'))
            if self.manifest.get('export_format') != 'bakney_sport_export_v1' or self.manifest.get('version') != '1.0.0':
                raise RestoreError('Formato non supportato. Carica un export ZIP Bakney versione 1.0.0.')
            if self.manifest.get('errors') or self.manifest.get('statistics', {}).get('files_failed', 0):
                raise RestoreError('Il backup segnala errori di esportazione. Genera un export completo.')
            expected = {m.__name__: ('system' if len(item) > 2 else 'data') + '/' + item[1] + '.json'
                        for item in AssociationExportService.EXPORT_ORDER for m in [item[0]]}
            expected.update(DEPRECATED_SECTIONS)
            self.skipped_records = skipped_records(zf, self.manifest)
            expected['SportAssociationInvoices'] = 'data/48_sport_association_invoices.json'
            specs = self.manifest.get('models_exported', [])
            seen = set()
            for spec in specs:
                name, filename = spec['name'], spec['file']
                if name not in expected or name in seen or filename != expected[name]:
                    raise RestoreError(f'Modello o percorso non supportato nel backup: {name}.')
                seen.add(name)
                if filename not in names:
                    raise RestoreError(f'File dichiarato ma mancante: {filename}.')
                rows = json.loads(zf.read(filename))
                if not isinstance(rows, list) or len(rows) != spec['count'] or any(not isinstance(r, dict) for r in rows):
                    raise RestoreError(f'Conteggio o contenuto non valido: {filename}.')
                if name not in DEPRECATED_SECTIONS:
                    self.records[name] = rows
            required = {m.__name__ for _, m in AssociationImportService.IMPORT_ORDER}
            if required - seen:
                raise RestoreError('Export incompleto: mancano ' + ', '.join(sorted(required - seen)) + '.')
            associations = self.records['SportAssociation']
            if len(associations) != 1:
                raise RestoreError('Il backup deve contenere una sola associazione.')
            self.association = associations[0]
            self.association_id = str(self.association['sport_association_id'])
            self.owner_id = str(self.association['user_id'])
            owners = [u for u in self.records['User'] if str(u.get('user_id')) == self.owner_id]
            if len(owners) != 1:
                raise RestoreError('Il titolare del backup è mancante o ambiguo.')
            self.owner = owners[0]
            if (str(self.manifest.get('association', {}).get('sport_association_id')) != self.association_id
                    or self.manifest.get('association', {}).get('denomination') != self.association.get('denomination')):
                raise RestoreError('Il manifest non corrisponde all’associazione esportata.')
            self._normalize_legacy_invoices()
            self._normalize_legacy_tutors()
            self._validate_graph()
            for entry in entries:
                if entry.is_dir() or not entry.filename.startswith('files/'):
                    continue
                parts = entry.filename.split('/')
                if len(parts) != 4 or not parts[-1]:
                    raise RestoreError('Percorso allegato non valido.')
                _, category, identity, _ = parts
                model = 'Subscription' if category in ('signatures', 'subscription_signatures') else 'Document'
                if identity not in self.identities.get(model, set()):
                    raise RestoreError('Un allegato fa riferimento a un record mancante.')
                key = f'{model}:{identity}'
                if key in self.media:
                    raise RestoreError('Allegati duplicati per lo stesso record.')
                self.media[key] = entry.filename
            if self.manifest.get('statistics', {}).get('files_exported', len(self.media)) != len(self.media):
                raise RestoreError('Il numero degli allegati non corrisponde al manifest.')
            if zf.testzip():
                raise RestoreError('Il backup contiene un file danneggiato.')
        self.missing_binary_media = sum(
            bool(row.get('filepath')) and f'Document:{row["document_id"]}' not in self.media
            for row in self.records['Document']) + sum(
            bool(row.get('signature_storage_key'))
            and f'Subscription:{row["subscription_id"]}' not in self.media
            for row in self.records['Subscription'])
        # Legacy Bakney signatures can intentionally be URL-only. Preserve those
        # references and disclose their external dependency, without treating them
        # as lost local objects when validating a recovery snapshot.
        self.missing_media = self.missing_binary_media + sum(
            bool(row.get('signature_url')) and not row.get('signature_storage_key')
            and f'Subscription:{row["subscription_id"]}' not in self.media
            for row in self.records['Subscription'])
        return self

    def _normalize_legacy_invoices(self):
        # Bakney platform invoices no longer have a billing model in Assozeta.
        # Preserve their PDFs as ordinary association documents instead.
        from uuid import UUID
        self.legacy_document_links = []
        for row in self.records.pop('SportAssociationInvoices', []):
            identity = str(UUID(str(row['sport_association_invoice_id'])))
            if str(row['sport_association_id']) != self.association_id:
                raise RestoreError('Una fattura Bakney appartiene a un’altra associazione.')
            date = parse_date(row['invoice_date'])
            if date is None:
                raise RestoreError('Data della fattura Bakney non valida.')
            self.legacy_document_links.append({
                'sport_association_documents_archive_id': identity,
                'sport_association_id': self.association_id,
                'document_id': str(UUID(str(row['document_id']))),
                'date': date.isoformat(),
            })
        self.records['SportAssociationDocumentsArchive'].extend(self.legacy_document_links)

    def _normalize_legacy_tutors(self):
        # Keep the imported member graph self-contained. Unreferenced foreign
        # anagrafiche are irrelevant; referenced tutors become local records.
        from uuid import UUID
        from application.models.user_models import Associate

        associates = self.records['Associate']
        member_ids = {str(row.get('associate_id')) for row in associates
                      if not row.get('sport_association_id')
                      or str(row['sport_association_id']) == self.association_id}
        tutor_ids = {str(row.get('tutor_id')) for row in self.records['AssociateTutorRelation']
                     if str(row.get('associate_id')) in member_ids}
        referenced_ids = set()
        for _, model in AssociationImportService.IMPORT_ORDER:
            fields = [field.attname for field in model._meta.concrete_fields
                      if field.is_relation and field.related_model is Associate]
            m2m_fields = ['_m2m_' + field.name for field in model._meta.many_to_many
                          if field.related_model is Associate]
            for row in self.records.get(model.__name__, []):
                referenced_ids.update(str(row[field]) for field in fields if row.get(field))
                for field in m2m_fields:
                    referenced_ids.update(str(value) for value in row.get(field, []))

        deleted_field = Associate._meta.get_field('deleted')
        tax_field = Associate._meta.get_field('tax_code')
        active_tax_codes = {
            tax_field.to_python(row['tax_code']).upper(): str(row['associate_id'])
            for row in associates if str(row.get('associate_id')) in member_ids
            and not deleted_field.to_python(row.get('deleted', False))
            and row.get('tax_code') is not None
        }
        self.reassigned_tutor_ids = set()
        self.ignored_tutor_ids = set()
        self.tutor_overrides = {}
        self.tutor_reassignments = []
        # Local members retain their tax codes. If foreign tutors share a code,
        # UUID order picks the keeper consistently, independent of ZIP row order.
        for row in sorted(associates, key=lambda row: str(row.get('associate_id'))):
            identity = str(row.get('associate_id'))
            association = row.get('sport_association_id')
            if not association or str(association) == self.association_id:
                continue
            if identity not in referenced_ids:
                self.ignored_tutor_ids.add(identity)
                continue
            if identity not in tutor_ids:
                continue  # Other referenced foreign records still fail validation.
            UUID(str(association))
            overrides = {'sport_association_id': self.association_id}
            detail = {'associate_id': identity, 'source_association_id': str(association),
                      'name': ' '.join(str(row.get(field) or '') for field in ('first_name', 'last_name')).strip()}
            tax_code = tax_field.to_python(row.get('tax_code'))
            if tax_code is not None and not deleted_field.to_python(row.get('deleted', False)):
                conflict = active_tax_codes.get(tax_code.upper())
                if conflict:
                    overrides['tax_code'] = None
                    detail.update(original_tax_code=tax_code, conflicting_associate_id=conflict)
                else:
                    active_tax_codes[tax_code.upper()] = identity
            row.update(overrides)
            self.reassigned_tutor_ids.add(identity)
            self.tutor_overrides[identity] = overrides
            self.tutor_reassignments.append(detail)
        self.records['Associate'] = [row for row in associates
                                     if str(row.get('associate_id')) not in self.ignored_tutor_ids]

    def _validate_graph(self):
        classes = {m.__name__: m for _, m in AssociationImportService.IMPORT_ORDER}
        self.identities = {}
        self.missing_relations = []
        for name, model in classes.items():
            rows = self.records.get(name, [])
            pk = model._meta.pk
            identities = set()
            for row in rows:
                if row.get(pk.name) in (None, ''):
                    raise RestoreError(f'Identificativo mancante in {name}.')
                identity = str(pk.to_python(row[pk.name]))
                if identity in identities:
                    raise RestoreError(f'Identificativo duplicato in {name}.')
                identities.add(identity)
            self.identities[name] = identities
        all_ids = set()
        for name, identities in self.identities.items():
            if all_ids & identities:
                raise RestoreError('Il backup riutilizza un identificativo per tipi di dati differenti.')
            all_ids.update(identities)
        for name, model in classes.items():
            for row in self.records.get(name, []):
                association = row.get('sport_association_id') if name != 'SportAssociation' else None
                if association and str(association) != self.association_id:
                    raise RestoreError('Il backup contiene riferimenti a un’altra associazione.')
                for field in model._meta.concrete_fields:
                    if field.attname in row:
                        value = row[field.attname]
                        parser = field.target_field if field.is_relation else field
                        parser.to_python(value)
                        if value is None and not field.null and not field.has_default() and not getattr(field, 'auto_now', False) and not getattr(field, 'auto_now_add', False):
                            raise RestoreError(f'Valore obbligatorio mancante: {name}.{field.name}.')
                        if isinstance(field, CharField) and isinstance(value, str) and field.max_length and len(value) > field.max_length:
                            raise RestoreError(f'Valore troppo lungo: {name}.{field.name}.')
                    if not field.is_relation or field.related_model.__name__ not in classes:
                        continue
                    value = row.get(field.attname)
                    if value and str(value) not in self.identities[field.related_model.__name__]:
                        # Match setup import for optional links, but keep folder
                        # hierarchy validation and required references strict.
                        if not field.null or name == 'Folder':
                            raise RestoreError(f'Relazione mancante: {name}.{field.name}.')
                        self.missing_relations.append({
                            'model': name, 'record_id': str(row[model._meta.pk.name]),
                            'field': field.attname, 'target_model': field.related_model.__name__,
                            'target_id': str(value),
                        })
                for field in model._meta.many_to_many:
                    values = row.get('_m2m_' + field.name, [])
                    related = field.related_model.__name__
                    if related in classes and any(str(v) not in self.identities[related] for v in values):
                        raise RestoreError(f'Relazione multipla mancante: {name}.{field.name}.')

    def summary(self):
        return {'association': self.association.get('denomination', ''),
                'export_date': self.manifest.get('export_date'),
                'skipped_records': self.skipped_records,
                'records': sum(len(rows) for name, rows in self.records.items()
                               if name not in ('BillingPlan', 'PreviewAndCustomFeatures')),
                'files': len(self.media), 'missing_media': self.missing_media,
                'legacy_invoices': len(self.legacy_document_links),
                'reassigned_tutors': len(self.reassigned_tutor_ids),
                'tutor_tax_code_conflicts': sum('original_tax_code' in row for row in self.tutor_reassignments),
                'tutor_reassignment_details': self.tutor_reassignments,
                'missing_relations': len(self.missing_relations),
                'missing_relation_details': self.missing_relations}
