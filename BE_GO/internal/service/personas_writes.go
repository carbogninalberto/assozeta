package service

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// PersonaWriteFailure is an exception Django raised after earlier writes of
// the request were saved (requests are not atomic): the writes are committed
// and Err (a *dto.PersonaInvalid, domain.ErrPersonaWriteForeignReference or a
// server error) is the response.
type PersonaWriteFailure struct{ Err error }

func (e *PersonaWriteFailure) Error() string {
	return "persona write failed after saving: " + e.Err.Error()
}
func (e *PersonaWriteFailure) Unwrap() error { return e.Err }

// PersonaWrites ports AssociateViewSet's add, update, delete, recover and
// bulk_delete.
type PersonaWrites struct {
	store    domain.PersonaWritesStore
	personas *Personas
	now      func() time.Time
	ids      func() string
	// pictures is the default storage of picture_path uploads (nil: refused).
	pictures        PersonaPictureObjects
	pictureSettings PersonaPictureSettings
}

func NewPersonaWrites(store domain.PersonaWritesStore, personas *Personas, now func() time.Time, ids func() string) *PersonaWrites {
	return &PersonaWrites{store: store, personas: personas, now: now, ids: ids}
}

// Association is request.user.sport_association (athletes: 403; a missing
// live association: 500).
func (s *PersonaWrites) Association(ctx context.Context, identity domain.ResolvedIdentity) (string, error) {
	return s.personas.association(ctx, identity)
}

// Locate is get_object(): get_queryset with the query parameters, then pk.
func (s *PersonaWrites) Locate(ctx context.Context, identity domain.ResolvedIdentity, rawID string, query domain.RequestObject, header string) (domain.PersonaAssociate, error) {
	row, _, err := s.personas.object(ctx, identity, rawID, query, header)
	return row.PersonaAssociate, err
}

// personaWriteRun carries one request's transaction and audit context.
type personaWriteRun struct {
	tx      domain.PersonaWritesTransaction
	context domain.LoginAuditContext
	at      time.Time
	actor   *string
	group   *string
	header  string
	tenant  *string
	now     time.Time
	ids     func() string
	// saved is set once the request wrote anything (later exceptions keep it).
	saved bool
}

func (s *PersonaWrites) begin(ctx context.Context, identity domain.ResolvedIdentity, header string, audit domain.LoginAuditContext) (*personaWriteRun, error) {
	tx, err := s.store.BeginPersonaWrites(ctx)
	if err != nil {
		return nil, err
	}
	run := &personaWriteRun{tx: tx, context: paymentCategoryAuditContext(identity, audit), group: supplierGroup(header), header: header,
		ids: s.ids}
	run.now = s.now().UTC().Truncate(time.Microsecond)
	run.at = run.now
	if run.actor, err = tx.ActiveAssociationOf(ctx, identity.Actor.ID); err != nil {
		tx.Rollback()
		return nil, err
	}
	if run.tenant, err = tx.ActiveAssociationOf(ctx, identity.ScopeUser.ID); err != nil {
		tx.Rollback()
		return nil, err
	}
	return run, nil
}

// finish commits after a failure that followed saved writes, else rolls back.
func (r *personaWriteRun) finish(err error) error {
	if err == nil {
		return r.tx.Commit()
	}
	if r.saved {
		if commit := r.tx.Commit(); commit != nil {
			return commit
		}
		return &PersonaWriteFailure{Err: err}
	}
	r.tx.Rollback()
	return err
}

func (r *personaWriteRun) record(ctx context.Context, model, key string, action int, changes map[string][2]string, created bool,
	association *string, path string) error {
	return r.tx.Audit(ctx, domain.PersonaWriteAudit{Model: model, Key: key, Action: action, Created: created, Changes: changes,
		Association: association, Path: path, Context: r.context, At: r.at})
}

// associateIndex resolves an Associate entry like the AuditLogIndex signal:
// the row as Associate.objects (live, group-aware) finds it when the entry is
// written (the stored row before an update, the new row after a create) with
// an association is direct; then changes['sport_association'] (a live
// association), then the actor's live association.
func (r *personaWriteRun) associateIndex(ctx context.Context, stored *domain.PersonaAssociate, changes map[string][2]string) (*string, string, error) {
	if stored != nil && !stored.Deleted && (r.group == nil || stored.GroupID != nil && *stored.GroupID == *r.group) && stored.AssociationID != nil {
		return stored.AssociationID, "direct", nil
	}
	if pair, ok := changes["sport_association"]; ok {
		for _, candidate := range pair {
			if candidate == "None" || candidate == "" {
				continue
			}
			exists, err := r.tx.ActiveAssociationExists(ctx, candidate)
			if err != nil {
				return nil, "", err
			}
			if exists {
				value := candidate
				return &value, "changes_json.sport_association", nil
			}
		}
	}
	if r.actor != nil {
		return r.actor, "actor.sport_association", nil
	}
	return nil, "", nil
}

// relationIndex: a relation that still exists resolves its associate's
// association; deleted ones (no sport_association in their changes) the actor's.
func (r *personaWriteRun) relationIndex(associate *domain.PersonaAssociate) (*string, string) {
	if associate != nil && associate.AssociationID != nil {
		return associate.AssociationID, "associate.sport_association"
	}
	if r.actor != nil {
		return r.actor, "actor.sport_association"
	}
	return nil, ""
}

// save is Model.save() of every field with auditlog's pre_save entry (written
// before the UPDATE, so it survives a unique violation of the statement).
func (r *personaWriteRun) save(ctx context.Context, old, row domain.PersonaAssociate, oldVisible bool) error {
	var changes map[string][2]string
	created := false
	if oldVisible {
		changes = dto.PersonaChanges(old, row)
	} else {
		created = true // auditlog found no old row: every field against None
	}
	stored := &old
	if !oldVisible {
		stored = nil
	}
	write := func() error {
		err := r.tx.SaveAssociate(ctx, row)
		if err == nil {
			r.saved = true
		}
		return err
	}
	if created {
		// The create-style diff renders the stored row: save first, then audit
		// (the entry is the same when the statement succeeds).
		if err := write(); err != nil {
			return err
		}
		association, path, err := r.associateIndex(ctx, nil, map[string][2]string{"sport_association": {"None", personaValue(row.AssociationID)}})
		if err != nil {
			return err
		}
		return r.record(ctx, "associate", row.ID, 1, nil, true, association, path)
	}
	if len(changes) > 0 {
		association, path, err := r.associateIndex(ctx, stored, changes)
		if err != nil {
			return err
		}
		if err = r.record(ctx, "associate", row.ID, 1, changes, false, association, path); err != nil {
			return err
		}
		r.saved = true
	}
	return write()
}

// create is Associate.objects.create(**validated_data): the manager stores
// the raw X-Group-Id; auditlog's post_save entry.
func (r *personaWriteRun) create(ctx context.Context, row domain.PersonaAssociate) error {
	if err := r.tx.InsertAssociate(ctx, row); err != nil {
		return err
	}
	r.saved = true
	stored := row
	association, path, err := r.associateIndex(ctx, &stored, map[string][2]string{"sport_association": {"None", personaValue(row.AssociationID)}})
	if err != nil {
		return err
	}
	return r.record(ctx, "associate", row.ID, 0, nil, true, association, path)
}

func personaValue(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// headerGroup is the X-Group-Id the manager's create stores: none, or a
// canonical existing group (other values are not ported, or a Django 500).
func (r *personaWriteRun) headerGroup(ctx context.Context) (*string, error) {
	if r.header == "" {
		return nil, nil
	}
	if r.group == nil || *r.group != r.header {
		return nil, ErrPersonaNotPorted
	}
	if _, found, err := r.tx.GroupAssociation(ctx, *r.group); err != nil || !found {
		if err == nil {
			err = ErrPersonaNotPorted
		}
		return nil, err
	}
	return r.group, nil
}

// related is the serializer's related-field lookup with tenant_scope.
func (r *personaWriteRun) related(ctx context.Context) func(name, id string) (bool, error) {
	return func(name, id string) (bool, error) {
		var exists bool
		var owner *string
		var err error
		switch name {
		case "user":
			if exists, err = r.tx.ActiveUserExists(ctx, id); err == nil && exists {
				owner, err = r.tx.ActiveAssociationOf(ctx, id)
			}
		case "sport_association":
			if exists, err = r.tx.ActiveAssociationExists(ctx, id); exists {
				owner = &id
			}
		case "group":
			var association string
			if association, exists, err = r.tx.GroupAssociation(ctx, id); exists {
				owner = &association
			}
		case "family":
			exists, err = r.tx.FamilyExists(ctx, id)
		}
		if err != nil || !exists {
			return exists, err
		}
		if r.tenant != nil && owner != nil && *owner != *r.tenant {
			return false, domain.ErrPersonaWriteForeignReference
		}
		return true, nil
	}
}

// pictureUnported: to_internal_value's base64 branch uploads to storage (not
// ported); `'base64' in value` on numbers is a TypeError (500).
func pictureUnported(body domain.RequestObject) error {
	value, present := body.Lookup("picture_path")
	if !present || value == nil || value == "" {
		return nil
	}
	switch v := value.(type) {
	case string:
		if strings.Contains(v, "base64") {
			return ErrPersonaNotPorted
		}
	case []any:
		for _, item := range v {
			if item == "base64" {
				return ErrPersonaNotPorted
			}
		}
	case domain.RequestObject:
		if _, found := v.Lookup("base64"); found {
			return ErrPersonaNotPorted
		}
	default:
		return ErrPersonaUnhandled
	}
	return nil
}

// planTutors refuses tutors_data the port does not handle, before any write:
// born dates with Unicode digits and a tutor create under an X-Group-Id the
// manager would store raw.
func planTutors(tutors any, header string, group *string) error {
	items, ok := tutors.([]any)
	if !ok {
		return nil
	}
	creates := false
	for _, item := range items {
		data, ok := item.(domain.RequestObject)
		if !ok {
			continue
		}
		info, ok := data.Lookup("tutor")
		object, isObject := info.(domain.RequestObject)
		if !ok || !isObject || !dto.PersonaTruthy(info) {
			continue
		}
		creates = true
		if value, present := object.Lookup("born_date"); present && dto.PersonaDMYUnported(value) {
			return ErrPersonaNotPorted
		}
	}
	if creates && header != "" && (group == nil || *group != header) {
		return ErrPersonaNotPorted
	}
	return nil
}

// pythonIterate is `for item in value` over a decoded request value.
func pythonIterate(value any) ([]any, bool) {
	switch v := value.(type) {
	case []any:
		return v, true
	case string:
		items := []any{}
		for _, r := range v {
			items = append(items, string(r))
		}
		return items, true
	case domain.RequestObject:
		items := []any{}
		for _, field := range v {
			items = append(items, field.Name)
		}
		return items, true
	}
	return nil, false
}

// handleTutors is AssociateSerializer._handle_tutors.
func (r *personaWriteRun) handleTutors(ctx context.Context, associate domain.PersonaAssociate, tutors any) error {
	items, ok := pythonIterate(tutors)
	if !ok {
		return ErrPersonaUnhandled // TypeError: not iterable
	}
	// new_tutor_ids = {data['tutor']['associate_id'] for data in tutors_data if data.get('tutor')}
	listed := map[string]bool{}
	for _, item := range items {
		data, ok := item.(domain.RequestObject)
		if !ok {
			return ErrPersonaUnhandled // AttributeError: no .get
		}
		info, _ := data.Lookup("tutor")
		if !dto.PersonaTruthy(info) {
			continue
		}
		object, ok := info.(domain.RequestObject)
		if !ok {
			return ErrPersonaUnhandled // TypeError: not subscriptable by a string
		}
		id, present := object.Lookup("associate_id")
		if !present {
			return ErrPersonaUnhandled // KeyError
		}
		switch id.(type) {
		case nil:
		case string, bool, json.Number:
			// An integer (or bool) reads as UUID(int=n); floats and out-of-range
			// integers are the UUIDField ValidationError in the exclude().
			parsed, ok := dto.PersonaUUIDValue(id)
			if !ok {
				return ErrPersonaUnhandled
			}
			listed[parsed] = true
		default:
			return ErrPersonaUnhandled // TypeError: unhashable
		}
	}
	relations, err := r.tx.Relations(ctx, associate.ID)
	if err != nil {
		return err
	}
	stale := []domain.PersonaRelationRow{}
	for _, relation := range relations {
		if !listed[relation.TutorID] {
			stale = append(stale, relation)
		}
	}
	for index := len(stale) - 1; index >= 0; index-- {
		changes, err := r.tx.DeleteRelation(ctx, stale[index].ID)
		if err != nil {
			return err
		}
		r.saved = true
		association, path := r.relationIndex(nil)
		if err = r.record(ctx, "associatetutorrelation", strconv.FormatInt(stale[index].ID, 10), 2, changes, false, association, path); err != nil {
			return err
		}
	}
	for _, item := range items {
		data := item.(domain.RequestObject)
		info, _ := data.Lookup("tutor")
		if !dto.PersonaTruthy(info) {
			continue
		}
		object := info.(domain.RequestObject)
		id, _ := object.Lookup("associate_id")
		var tutor *domain.PersonaAssociate
		if id != nil {
			parsed, _ := dto.PersonaUUIDValue(id)
			if tutor, err = r.tx.Associate(ctx, parsed, true, r.group); err != nil {
				return err
			}
		}
		var tutorRow domain.PersonaAssociate
		if tutor != nil {
			fields, invalid, err := dto.ParsePersonaFields(object, dto.PersonaFieldOptions{Partial: true, Basic: true}, r.related(ctx))
			if err != nil {
				return err
			}
			if invalid != nil {
				return invalid
			}
			tutorRow = *tutor
			dto.ApplyPersonaFields(&tutorRow, fields)
			if tutorRow.ID != tutor.ID {
				return ErrPersonaNotPorted
			}
			if err = r.save(ctx, *tutor, tutorRow, true); err != nil {
				return err
			}
		} else {
			fields, invalid, err := dto.ParsePersonaFields(object, dto.PersonaFieldOptions{Basic: true,
				Omit: []string{"associate_id", "creation_date", "tutors"}}, r.related(ctx))
			if err != nil {
				return err
			}
			if invalid != nil {
				return invalid
			}
			group, err := r.headerGroup(ctx)
			if err != nil {
				return err
			}
			tutorRow = personaNewRow(r.ids(), r.now)
			dto.ApplyPersonaFields(&tutorRow, fields)
			if group != nil {
				tutorRow.GroupID = group
			}
			if err = r.create(ctx, tutorRow); err != nil {
				return err
			}
		}
		primary, primaryText := false, "False"
		if value, present := data.Lookup("is_primary"); present {
			var valid bool
			if primary, primaryText, valid = personaPrimary(value); !valid {
				// BooleanField.get_prep_value raises ValidationError inside update_or_create's atomic block.
				return ErrPersonaUnhandled
			}
		}
		relations, err := r.tx.Relations(ctx, associate.ID)
		if err != nil {
			return err
		}
		matches := []domain.PersonaRelationRow{}
		for _, relation := range relations {
			if relation.TutorID == tutorRow.ID {
				matches = append(matches, relation)
			}
		}
		association, path := r.relationIndex(&associate)
		switch len(matches) {
		case 0:
			_, err = r.tx.InsertRelation(ctx, associate.ID, tutorRow.ID, primary, func(id int64) error {
				key := strconv.FormatInt(id, 10)
				return r.record(ctx, "associatetutorrelation", key, 0, map[string][2]string{"id": {"None", key},
					"associate": {"None", associate.ID}, "tutor": {"None", tutorRow.ID}, "is_primary": {"None", primaryText}},
					false, association, path)
			})
		case 1:
			// auditlog compares str() of the stored value with str() of the raw one; the
			// column keeps the value the BooleanField prepares.
			recordChange := func() error {
				if personaFlag(matches[0].IsPrimary) == primaryText {
					return nil
				}
				return r.record(ctx, "associatetutorrelation", strconv.FormatInt(matches[0].ID, 10), 1,
					map[string][2]string{"is_primary": {personaFlag(matches[0].IsPrimary), primaryText}}, false, association, path)
			}
			if matches[0].IsPrimary != primary {
				err = r.tx.SetRelationPrimary(ctx, matches[0].ID, primary, recordChange)
			} else {
				err = recordChange()
			}
		default:
			return ErrPersonaUnhandled // MultipleObjectsReturned
		}
		if err != nil {
			return err
		}
		r.saved = true
	}
	relations, err = r.tx.Relations(ctx, associate.ID)
	if err != nil {
		return err
	}
	for _, relation := range relations {
		if relation.IsPrimary {
			return nil
		}
	}
	if len(relations) == 0 {
		return nil
	}
	first := relations[0]
	association, path := r.relationIndex(&associate)
	return r.tx.SetRelationPrimary(ctx, first.ID, true, func() error {
		r.saved = true
		return r.record(ctx, "associatetutorrelation", strconv.FormatInt(first.ID, 10), 1,
			map[string][2]string{"is_primary": {"False", "True"}}, false, association, path)
	})
}

// personaPrimary is BooleanField.to_python for AssociateTutorRelation.is_primary
// (not null): booleans, the numbers equal to 0 or 1, and the strings
// t/True/1 and f/False/0. text is str() of the raw value, which auditlog
// renders; ok is false for every value the column refuses.
func personaPrimary(value any) (stored bool, text string, ok bool) {
	switch v := value.(type) {
	case bool:
		return v, personaFlag(v), true
	case json.Number:
		number, err := strconv.ParseFloat(string(v), 64)
		if err == nil && (number == 0 || number == 1) {
			return number == 1, dto.PythonStr(v), true
		}
	case string:
		switch v {
		case "t", "True", "1":
			return true, v, true
		case "f", "False", "0":
			return false, v, true
		}
	}
	return false, "", false
}

func personaFlag(value bool) string {
	if value {
		return "True"
	}
	return "False"
}

// personaNewRow is Associate() with the model defaults.
func personaNewRow(id string, now time.Time) domain.PersonaAssociate {
	italian, italy := "Italiana", "Italia"
	return domain.PersonaAssociate{ID: id, Sex: "A", Nationality: &italian, NationalityResidence: &italy, CreationDate: now}
}

// Delete is the viewset's delete() once get_object() found the row (the
// queryset is the association's: the ownership check passes): deleted=True
// and save().
func (s *PersonaWrites) Delete(ctx context.Context, identity domain.ResolvedIdentity, row domain.PersonaAssociate, header string,
	audit domain.LoginAuditContext) error {
	run, err := s.begin(ctx, identity, header, audit)
	if err != nil {
		return err
	}
	saved := row
	saved.Deleted = true
	return run.finish(run.save(ctx, row, saved, true))
}

// Recover is the viewset's recover().
func (s *PersonaWrites) Recover(ctx context.Context, identity domain.ResolvedIdentity, rawID string, header string, audit domain.LoginAuditContext) error {
	id, ok := dto.PersonaUUIDValue(rawID)
	if !ok {
		return ErrPersonaUnhandled // UUIDField ValidationError
	}
	run, err := s.begin(ctx, identity, header, audit)
	if err != nil {
		return err
	}
	row, err := run.tx.Associate(ctx, id, false, nil)
	if err == nil && row == nil {
		err = domain.ErrPersonaRecoverMissing
	}
	if err == nil && row.AssociationID == nil {
		err = ErrPersonaUnhandled // instance.sport_association.sport_association_id on None
	}
	var association string
	if err == nil {
		association, err = s.Association(ctx, identity)
	}
	if err == nil && *row.AssociationID != association {
		err = domain.ErrPersonaWriteDenied
	}
	if err != nil {
		run.tx.Rollback()
		return err
	}
	visible := !row.Deleted && (run.group == nil || row.GroupID != nil && *row.GroupID == *run.group)
	saved := *row
	saved.Deleted = false
	return run.finish(run.save(ctx, *row, saved, visible))
}

// BulkDelete is the viewset's bulk_delete().
func (s *PersonaWrites) BulkDelete(ctx context.Context, identity domain.ResolvedIdentity, body any, header string) error {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return ErrPersonaUnhandled // .get on a non-dict body
	}
	value, present := object.Lookup("associate_ids")
	if !present {
		value = []any{}
	}
	items, ok := pythonIterate(value)
	if !ok {
		return ErrPersonaUnhandled // len() of a non-sized value
	}
	association, err := s.Association(ctx, identity)
	if err != nil {
		return err
	}
	ids := []string{}
	for _, item := range items {
		if item == nil {
			continue
		}
		if number, ok := item.(json.Number); ok && strings.ContainsAny(string(number), ".eE") {
			return ErrPersonaUnhandled
		}
		id, ok := dto.PersonaUUIDValue(item)
		if !ok {
			return ErrPersonaUnhandled // UUIDField ValidationError
		}
		ids = append(ids, id)
	}
	tx, err := s.store.BeginPersonaWrites(ctx)
	if err != nil {
		return err
	}
	if err = tx.BulkDelete(ctx, ids, association, supplierGroup(header)); err != nil {
		tx.Rollback()
		return err
	}
	return tx.Commit()
}

// Add is the viewset's add() after request.user.sport_association; it
// returns the new associate's id.
func (s *PersonaWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, association string, body any, form bool, header string,
	audit domain.LoginAuditContext) (string, error) {
	object, ok := body.(domain.RequestObject)
	if !ok || form {
		return "", ErrPersonaUnhandled // request.data.pop on a non-dict body or an immutable QueryDict
	}
	tutors, present := object.Lookup("tutors_data")
	if !present {
		tutors = []any{}
	}
	rest := domain.RequestObject{}
	for _, field := range object {
		if field.Name != "tutors_data" {
			rest = append(rest, field)
		}
	}
	picture, upload, err := personaPicturePlan(rest)
	if err != nil {
		return "", err
	}
	if upload {
		rest = personaWithoutPicture(rest)
	}
	if value, present := rest.Lookup("born_date"); present && dto.PersonaDMYUnported(value) {
		return "", ErrPersonaNotPorted
	}
	if err := planTutors(tutors, header, supplierGroup(header)); err != nil {
		return "", err
	}
	run, err := s.begin(ctx, identity, header, audit)
	if err != nil {
		return "", err
	}
	fields, invalid, err := dto.ParsePersonaFields(rest, dto.PersonaFieldOptions{Omit: []string{"associate_id", "creation_date"}}, run.related(ctx))
	if err == nil && invalid != nil {
		err = invalid
	}
	var group *string
	if err == nil {
		group, err = run.headerGroup(ctx)
		if errors.Is(err, ErrPersonaNotPorted) && run.group == nil {
			err = ErrPersonaUnhandled // the raw header is not a UUID
		}
	}
	if err != nil {
		run.tx.Rollback()
		return "", err
	}
	row := personaNewRow(s.ids(), run.now)
	dto.ApplyPersonaFields(&row, fields)
	if upload {
		// create(): the valid data's picture is stored under the new associate's id.
		url, err := s.uploadPicture(ctx, picture, row.ID)
		if err != nil {
			run.tx.Rollback()
			return "", err
		}
		row.PicturePath = &url
	}
	user, own := identity.ScopeUser.ID, association
	row.UserID, row.AssociationID = &user, &own
	if group != nil {
		row.GroupID = group
	}
	err = run.create(ctx, row)
	if err == nil {
		err = run.handleTutors(ctx, row, tutors)
	}
	if err = run.finish(err); err != nil {
		return row.ID, err
	}
	return row.ID, nil
}

// Update is the viewset's update() after get_object() found row.
func (s *PersonaWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, row domain.PersonaAssociate, body any, form bool,
	header string, audit domain.LoginAuditContext) error {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return ErrPersonaUnhandled // request.data.get on a non-dict body
	}
	tutors, present := object.Lookup("tutors_data")
	if !present {
		tutors = []any{}
	}
	picture, upload, err := personaPicturePlan(object)
	if err != nil {
		return err
	}
	if upload {
		object = personaWithoutPicture(object)
	}
	if value, present := object.Lookup("born_date"); present && dto.PersonaDMYUnported(value) {
		return ErrPersonaNotPorted
	}
	if err := planTutors(tutors, header, supplierGroup(header)); err != nil {
		return err
	}
	run, err := s.begin(ctx, identity, header, audit)
	if err != nil {
		return err
	}
	fields, invalid, err := dto.ParsePersonaFields(object, dto.PersonaFieldOptions{Partial: true, Form: form, TutorsData: true,
		Omit: []string{"associate_id", "creation_date"}}, run.related(ctx))
	if err == nil && invalid != nil {
		err = invalid
	}
	if err != nil {
		run.tx.Rollback()
		return err
	}
	saved := row
	dto.ApplyPersonaFields(&saved, fields)
	if upload {
		// update(): the valid data's picture is stored under the associate's id.
		url, err := s.uploadPicture(ctx, picture, row.ID)
		if err != nil {
			run.tx.Rollback()
			return err
		}
		saved.PicturePath = &url
	}
	err = run.save(ctx, row, saved, true)
	if err == nil {
		err = run.handleTutors(ctx, saved, tutors)
	}
	return run.finish(err)
}

// StockCreate is the router list route's CreateModelMixin.create on
// AssociateViewSet: request.data is not popped, so AssociateSerializer
// validates tutors_data as its ListField and a non-dict body is the 400;
// perform_create then reads request.user.sport_association (athletes 403,
// owners without an association 500) and save() reaches ModelSerializer.create
// with tutors_data among the model's keyword arguments (TypeError: 500,
// nothing written). Without tutors_data it is add with no tutors. HTML form
// input is not ported (refused before any write).
func (s *PersonaWrites) StockCreate(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, header string,
	audit domain.LoginAuditContext) (string, error) {
	if form {
		return "", ErrPersonaNotPorted
	}
	if body == nil {
		body = domain.RequestObject{}
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		// AssociateSerializer.to_internal_value starts with data.copy(): only a
		// list (of the non-dict bodies) has one, and then fails the dict check.
		if _, list := body.([]any); list {
			return "", &dto.PersonaInvalid{Fields: map[string][]string{"non_field_errors": {"dictionary:list"}}}
		}
		return "", ErrPersonaUnhandled
	}
	if err := pictureUnported(object); err != nil {
		return "", err
	}
	if value, present := object.Lookup("born_date"); present && dto.PersonaDMYUnported(value) {
		return "", ErrPersonaNotPorted
	}
	run, err := s.begin(ctx, identity, header, audit)
	if err != nil {
		return "", err
	}
	_, invalid, err := dto.ParsePersonaFields(object, dto.PersonaFieldOptions{TutorsData: true, Omit: []string{"associate_id", "creation_date"}},
		run.related(ctx))
	run.tx.Rollback()
	if err != nil {
		return "", err
	}
	if invalid != nil {
		return "", invalid
	}
	association, err := s.Association(ctx, identity)
	if err != nil {
		return "", err
	}
	if _, present := object.Lookup("tutors_data"); present {
		return "", ErrPersonaUnhandled // Associate() got unexpected keyword arguments: 'tutors_data'
	}
	return s.Add(ctx, identity, association, object, false, header, audit)
}
