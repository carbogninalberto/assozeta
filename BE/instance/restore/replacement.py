"""Replace an explicitly scoped export graph while preserving local root identities."""
from django.db import transaction
from django.db.models.deletion import Collector
from application.models import SportAssociation, User
from application.services.import_service import AssociationImportService, ImportOptions
from .archive import Archive, RestoreError
from instance.models import DataRestoreUserDetachment


def require_single(config):
    if (not config or not config.self_hosted or config.support_multiple_associations
            or not config.primary_association_id
            or SportAssociation.original_objects.exclude(pk=config.primary_association_id).exists()):
        raise RestoreError('Il ripristino richiede una istanza self-hosted con una sola associazione.')


def deletion_plan(backup):
    """Only rows in the current association's recovery export can be deleted.

    Check inbound links as well as cascades: SET_NULL must not silently modify an
    unrelated account/document graph. Shared system defaults and users stay intact.
    """
    classes = {m.__name__: m for _, m in AssociationImportService.IMPORT_ORDER}
    scope = {classes[name]: set(values) for name, values in backup.identities.items() if name in classes}
    protected = {User, SportAssociation}
    collector = Collector(using='default')
    for model, identities in scope.items():
        if model in protected or model.__name__ == 'PreviewAndCustomFeatures' or not identities:
            continue
        for relation in model._meta.related_objects:
            if relation.related_model._meta.auto_created:
                continue
            related = relation.related_model
            lookup = relation.field.name + '__pk__in' if relation.many_to_many else relation.field.attname + '__in'
            outsiders = related._base_manager.filter(**{lookup: identities})
            outsiders = outsiders.exclude(pk__in=scope.get(related, set()))
            if outsiders.exists():
                raise RestoreError(f'{model.__name__}: esistono riferimenti esterni ai dati dell’associazione. Nessun dato è stato sostituito.')
        collector.collect(model._base_manager.filter(pk__in=identities))
    for model, objects in collector.data.items():
        if model._meta.auto_created:
            continue
        if model in protected or any(str(obj.pk) not in scope.get(model, set()) for obj in objects):
            raise RestoreError(f'La sostituzione coinvolgerebbe dati non inclusi nel backup di sicurezza: {model.__name__}.')
    for queryset in collector.fast_deletes:
        if not queryset.model._meta.auto_created and queryset.exclude(pk__in=scope.get(queryset.model, set())).exists():
            raise RestoreError('La sostituzione coinvolgerebbe altri dati non esportati.')
    return collector


class ReplacementImporter(AssociationImportService):
    def __init__(self, archive, config, media, progress=None):
        super().__init__(archive.path, ImportOptions())
        self.progress = progress
        self.processed = set()
        self.total_records = archive.summary()['records']
        self.archive = archive
        self.target = config.primary_association
        self.local_owner = self.target.user
        self.staged_media = media
        self.missing_fields = {}
        for relation in archive.missing_relations:
            key = (relation['model'], relation['record_id'])
            self.missing_fields.setdefault(key, set()).add(relation['field'])

    def _register_imported_object(self, model_name, old_pk, obj):
        super()._register_imported_object(model_name, old_pk, obj)
        if old_pk is not None and obj is not None:
            self.processed.add((model_name, str(old_pk)))
            if self.progress:
                self.progress.publish('restoring', completed=len(self.processed),
                                      total=self.total_records, unit='records', detail=model_name)

    def _import_folders(self, records):
        result = super()._import_folders(records)
        self.processed.update(('Folder', str(row['id'])) for row in records)
        return result

    def _resolve_deferred_fks(self):
        if self.progress:
            self.progress.publish('finalizing', detail='Ricostruzione dei collegamenti')
        return super()._resolve_deferred_fks()

    def _normalize_optional_links(self, model_class, data):
        data = dict(data)
        key = (model_class.__name__, str(data.get(model_class._meta.pk.name)))
        for field in self.missing_fields.get(key, ()):
            data[field] = None
        return data

    def _generate_uuid(self, old_uuid, model_name):
        value = str(old_uuid)
        if model_name == 'SportAssociation' and value == self.source_association_id:
            value = str(self.target.pk)
        elif model_name == 'User' and value == self.source_owner_user_id:
            value = str(self.local_owner.pk)
        self.uuid_mapping[str(old_uuid)] = value
        return value

    def _create_owner_user(self, zf):
        self._load_source_identity(zf)
        self.owner_user = self.local_owner
        self.uuid_mapping[self.source_owner_user_id] = str(self.local_owner.pk)
        self.reused_user_pks.add(self.source_owner_user_id)
        self._register_imported_object('User', self.source_owner_user_id, self.local_owner)
        self.imported_models.add('User')
        return self.local_owner

    def _import_sport_association(self, zf):
        data, _ = self._load_source_identity(zf)
        data = self._normalize_optional_links(SportAssociation, data)
        values = self._build_model_kwargs(SportAssociation, {
            **data, 'sport_association_id': str(self.target.pk), 'user_id': str(self.local_owner.pk),
            'deleted': self.target.deleted,
        })
        # Construct a fresh business record so omitted legacy fields get defaults;
        # keep the existing root PK, billing relationships and installation linkage.
        self.association = SportAssociation(**values)
        self.association.save(force_update=True)
        self.uuid_mapping[self.source_association_id] = str(self.target.pk)
        self._register_imported_object('SportAssociation', self.source_association_id, self.association)
        self.imported_models.add('SportAssociation')
        return self.association

    def _create_model_instance(self, model_class, data):
        data = self._normalize_optional_links(model_class, data)
        if model_class is User:
            # Global feature definitions remain local. The setup importer also
            # omits system rows; don't silently claim their M2M links were imported.
            data.pop('_m2m_preview_and_custom_features', None)
        return super()._create_model_instance(model_class, data)

    def _import_files(self, zf):
        # All bytes were staged under new, operation-specific keys before deletion.
        for identity, doc in self.document_mapping.items():
            doc.filepath = self.staged_media.get(f'Document:{identity}', '')
            doc.save(update_fields=['filepath'])
        for identity, subscription in self.imported_objects.get('Subscription', {}).items():
            key = self.staged_media.get(f'Subscription:{identity}', '')
            subscription.signature_storage_key = key
            if key:
                subscription.signature_url = subscription._signature_public_url(key)
            # URL-only legacy signatures remain fallback links; no foreign storage
            # key is ever treated as a local object.
            subscription.save(update_fields=['signature_storage_key', 'signature_url'])
        self.stats['files_imported'] = len(self.staged_media)
        return len(self.staged_media)

    def restore_user_links(self, old_user_ids):
        # Revoke old collaborator linkage; retain accounts/passwords. Restore only
        # relationships present in this archive, mapping the source owner locally.
        incoming_users = {str(row['user_id']) for row in self.archive.records['User']}
        removed = User.original_objects.filter(pk__in=old_user_ids - incoming_users, connected_user=self.local_owner).exclude(pk=self.local_owner.pk)
        for user_id in removed.values_list('pk', flat=True):
            DataRestoreUserDetachment.objects.update_or_create(user_id=user_id, defaults={
                'owner': self.local_owner, 'association_id': self.target.pk,
            })
        removed.update(connected_user=None, is_active=False)
        for row in self.archive.records['User']:
            row = self._normalize_optional_links(User, row)
            source_id = str(row['user_id'])
            if source_id == self.source_owner_user_id:
                # Retain local identity, credentials and operational settings;
                # repair business references whose target rows were replaced.
                fields = {}
                for name in ('default_payment_category_id', 'default_payment_category_courses_id'):
                    raw = row.get(name)
                    fields[name] = self._resolve_fk(raw, 'PaymentCategory') if raw else None
                User.original_objects.filter(pk=self.local_owner.pk).update(**fields)
                continue
            obj = self.imported_objects['User'][source_id]
            detachment = DataRestoreUserDetachment.objects.filter(
                user=obj, owner=self.local_owner, association_id=self.target.pk,
            )
            recover_detached = (
                obj.connected_user_id is None and not obj.is_active
                and str(row.get('connected_user_id')) == self.source_owner_user_id
                and detachment.exists()
            )
            if source_id in self.reused_user_pks and source_id not in old_user_ids and not recover_detached:
                continue  # Shared accounts outside the replaced graph stay intact.
            if obj.pk == self.local_owner.pk:
                raise RestoreError('Il titolare locale compare anche come altro utente nel backup.')
            fields = {}
            if recover_detached:
                fields['is_active'] = row.get('is_active', True)
            for field in User._meta.concrete_fields:
                if field.is_relation and field.related_model.__name__ in self.imported_objects:
                    raw = row.get(field.attname)
                    fields[field.attname] = self._resolve_fk(raw, field.related_model.__name__) if raw else None
            if fields:
                User.original_objects.filter(pk=obj.pk).update(**fields)
            if recover_detached:
                detachment.delete()


def replace(archive, backup, config, media, complete, progress=None):
    require_single(config)
    importer = ReplacementImporter(archive, config, media, progress=progress)
    with transaction.atomic():
        deletion_plan(backup).delete()
        importer.import_all()
        # The setup importer intentionally knows nothing about the retired
        # platform billing model. Restore its documents through the existing
        # business archive model, inside the same replacement transaction.
        from application.models.user_models import SportAssociationDocumentsArchive
        for row in archive.legacy_document_links:
            document_link = importer._create_model_instance(SportAssociationDocumentsArchive, dict(row))
            document_link.save(force_insert=True)
        importer.restore_user_links(backup.identities['User'])
        if importer.errors or importer.stats.get('deferred_fks_failed') or importer.stats.get('m2m_failed'):
            raise RestoreError('Non è stato possibile ricostruire tutte le relazioni. I dati precedenti sono stati conservati.')
        complete(importer)
    return importer
