package service

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// AssociateDrafts ports the subscription/associates-draft list, add, edit,
// delete and bulk-delete function views (subscriptions_views.py).
type AssociateDrafts struct {
	store domain.AssociateDraftStore
	now   func() time.Time
	ids   func() string
}

func NewAssociateDrafts(store domain.AssociateDraftStore, now func() time.Time, ids func() string) *AssociateDrafts {
	return &AssociateDrafts{store: store, now: now, ids: ids}
}

var (
	// ErrAssociateDraftFailed is an exception the views do not handle (a 500).
	ErrAssociateDraftFailed = errors.New("associate import draft request failed")
	// ErrAssociateDraftNotPorted refuses inputs whose Django behaviour is not
	// reproduced (dateutil formats outside the ported subset), before any write.
	ErrAssociateDraftNotPorted = refusal.New("associate import draft input not ported")
)

// associateDraftMandatory is SubscriptionMaps.get_mandatory_fields().
var associateDraftMandatory = []string{"first_name", "last_name", "sex", "tax_code", "born_date", "born_city", "address", "address_cap",
	"address_city"}

func (s *AssociateDrafts) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// today is date.today() (the server runs with TIME_ZONE 'UTC').
func (s *AssociateDrafts) today() time.Time {
	year, month, day := s.now().UTC().Date()
	return time.Date(year, month, day, 0, 0, 0, 0, time.UTC)
}

func (s *AssociateDrafts) begin(ctx context.Context) (domain.AssociateDraftTransaction, error) {
	return s.store.BeginAssociateDrafts(ctx)
}

// List follows subscription_associates_draft_list.
func (s *AssociateDrafts) List(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) (any, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, domain.ErrAssociateDraftUnauthorized
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.Association(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return nil, err
	}
	drafts, err := tx.Drafts(ctx, association)
	if err != nil {
		return nil, err
	}
	data := domain.RequestObject{}
	for index, draft := range drafts {
		if draft.Data == nil {
			return nil, ErrAssociateDraftFailed
		}
		parsed, err := dto.ParseProfileTablesJSON(draft.Data)
		if err != nil {
			return nil, err
		}
		object, ok := parsed.(domain.RequestObject)
		if !ok {
			return nil, ErrAssociateDraftFailed // data[idx]['...'] = ... on a non-dict
		}
		object = dto.SetRequestField(object, "associate_import_draft_id", draft.ID)
		var valid any
		if draft.Valid != nil {
			valid = *draft.Valid
		}
		object = dto.SetRequestField(object, "valid", valid)
		data = append(data, domain.RequestField{Name: strconv.Itoa(index), Value: object})
	}
	// request.user.sport_association (not impersonation-scoped).
	owned, err := tx.Association(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return nil, err
	}
	auditContext := paymentCategoryAuditContext(identity, audit)
	flags := [2]any{false, false}
	for index, rule := range []struct {
		kind  int
		limit time.Duration
	}{{1, time.Hour}, {2, 5 * time.Minute}} {
		status, err := tx.LatestStatus(ctx, owned, rule.kind)
		if err != nil {
			return nil, err
		}
		if status == nil {
			continue
		}
		if s.now().Sub(status.Created) > rule.limit {
			if err = tx.StopStatus(ctx, *status, owned, auditContext, s.at()); err != nil {
				return nil, err
			}
			stopped := false
			status.Doing = &stopped
		}
		if status.Doing == nil {
			flags[index] = nil
		} else {
			flags[index] = *status.Doing
		}
	}
	result := domain.RequestObject{{Name: "data", Value: data}, {Name: "approving", Value: flags[1]}, {Name: "importing", Value: flags[0]}}
	if !dto.AssociateDraftRenderable(result) {
		return nil, ErrAssociateDraftFailed
	}
	return result, tx.Commit()
}

// AddAssociation is the add view's first statement:
// scoped_queryset(SportAssociation.objects).filter(user=request.user).first()
// (none, athletes included, is an empty 401), evaluated before request.data.
func (s *AssociateDrafts) AddAssociation(ctx context.Context, identity domain.ResolvedIdentity) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.FirstAssociation(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err == nil && association == nil {
		err = domain.ErrAssociateDraftUnauthorized
	}
	return err
}

// associateDraftValid is AssociateSerializer(data=value).is_valid() with the
// tenant-scoped related fields (DRF's generic 403 for another association).
func associateDraftValid(ctx context.Context, tx domain.AssociateDraftTransaction, value any, current *string) (bool, error) {
	ok, association, family := dto.AssociateDraftFields(value)
	if association != nil {
		found, err := tx.AssociationExists(ctx, *association)
		if err != nil {
			return false, err
		}
		if !found {
			ok = false
		} else if current != nil && *current != *association {
			return false, &domain.PermissionDenied{}
		}
	}
	if family != nil {
		found, err := tx.FamilyExists(ctx, *family)
		if err != nil {
			return false, err
		}
		ok = ok && found
	}
	return ok, nil
}

// Add follows subscription_associates_draft_add.
func (s *AssociateDrafts) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.FirstAssociation(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	if association == nil {
		return domain.ErrAssociateDraftUnauthorized
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return ErrAssociateDraftFailed
	}
	values := map[string]any{}
	for _, name := range []string{"new_user_account", "associate_data", "associate_tutor_data", "signature"} {
		value, present := object.Lookup(name)
		if !present {
			return ErrAssociateDraftFailed // KeyError
		}
		values[name] = value
	}
	// tenant_scope.current_association(): the scope user's first active association.
	current, err := tx.FirstAssociation(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return err
	}
	valid, err := associateDraftValid(ctx, tx, values["associate_data"], current)
	if err == nil && valid {
		valid, err = associateDraftValid(ctx, tx, values["associate_tutor_data"], current)
	}
	if err != nil {
		return err
	}
	// data['medical_certificate'] is read only when the draft is created.
	medical, present := object.Lookup("medical_certificate")
	if !present {
		return ErrAssociateDraftFailed
	}
	values["medical_certificate"] = medical
	stored := domain.RequestObject{{Name: "associate", Value: values["associate_data"]}, {Name: "associate_tutor", Value: values["associate_tutor_data"]},
		{Name: "new_user_account", Value: values["new_user_account"]}, {Name: "signature", Value: values["signature"]},
		{Name: "medical_certificate", Value: values["medical_certificate"]}}
	if !dto.AssociateDraftStorable(stored) {
		return ErrAssociateDraftFailed // the jsonb insert fails
	}
	id := s.ids()
	sorted := string(dto.PythonJSONDumpsSorted(stored))
	changes := map[string][2]string{"associate_import_draft_id": {"None", id}, "sport_association": {"None", *association},
		"data": {"None", sorted}, "valid": {"None", associateDraftPythonBool(valid)}}
	if err = tx.CreateDraft(ctx, id, *association, dto.PythonJSONDumps(stored), changes, valid, paymentCategoryAuditContext(identity, audit),
		s.at()); err != nil {
		return err
	}
	return tx.Commit()
}

func associateDraftPythonBool(value bool) string {
	if value {
		return "True"
	}
	return "False"
}

// target is the shared prologue of edit and delete after the athlete check:
// is_valid_uuid(uid), the scoped association .get() and the draft .first().
func (s *AssociateDrafts) target(ctx context.Context, tx domain.AssociateDraftTransaction, identity domain.ResolvedIdentity,
	raw string) (string, string, *domain.AssociateDraftRow, error) {
	parsed, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", "", nil, err
	}
	association, err := tx.Association(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return "", "", nil, err
	}
	draft, err := tx.Draft(ctx, parsed.String(), association)
	return parsed.String(), association, draft, err
}

// associateDraftValidity is the edit view's mandatory-field and age loop.
func (s *AssociateDrafts) associateDraftValidity(editData domain.RequestObject) (bool, error) {
	value, present := editData.Lookup("associate")
	if !present {
		return false, ErrAssociateDraftFailed
	}
	associate, ok := value.(domain.RequestObject)
	if !ok {
		return false, ErrAssociateDraftFailed // .keys() on a non-dict
	}
	valid, parse := true, false
	for _, key := range associateDraftMandatory {
		field, present := associate.Lookup(key)
		if !present {
			valid = false
		}
		if present && (field == nil || field == "") {
			valid = false
		} else {
			parse = true
		}
	}
	if !parse {
		return valid, nil
	}
	born, present := associate.Lookup("born_date")
	if !present {
		return false, ErrAssociateDraftFailed
	}
	date, result := dto.DateutilDate(born)
	switch result {
	case dto.DateutilFailed:
		return false, ErrAssociateDraftFailed
	case dto.DateutilUnsupported:
		return false, ErrAssociateDraftNotPorted
	}
	today := s.today()
	if dto.RelativeYears(today, date) >= 18 {
		return valid, nil
	}
	// Any exception of the tutor's parse is logged and ignored.
	tutor, present := editData.Lookup("associate_tutor")
	if !present {
		return valid, nil
	}
	tutorObject, ok := tutor.(domain.RequestObject)
	if !ok {
		return valid, nil
	}
	tutorBorn, present := tutorObject.Lookup("born_date")
	if !present {
		return valid, nil
	}
	date, result = dto.DateutilDate(tutorBorn)
	switch result {
	case dto.DateutilFailed:
		return valid, nil
	case dto.DateutilUnsupported:
		return false, ErrAssociateDraftNotPorted
	}
	if dto.RelativeYears(today, date) < 18 {
		valid = false
	}
	return valid, nil
}

// Edit follows subscription_associates_draft_edit. Requests are not atomic:
// a failure after a write commits the writes before it and answers 500.
func (s *AssociateDrafts) Edit(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, audit domain.LoginAuditContext) (any, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, domain.ErrAssociateDraftUnauthorized
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	id, association, draft, err := s.target(ctx, tx, identity, raw)
	if err != nil {
		return nil, err
	}
	if draft == nil {
		return nil, domain.ErrAssociateDraftNotFound
	}
	data, ok := body.(domain.RequestObject)
	if !ok {
		return nil, ErrAssociateDraftFailed // data.keys()
	}
	respond := func() (any, error) {
		result := domain.RequestObject{{Name: "data", Value: data}}
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		if !dto.AssociateDraftRenderable(result) {
			return nil, ErrAssociateDraftFailed
		}
		return result, nil
	}
	value, present := data.Lookup("edit_data")
	if !present {
		return respond()
	}
	editData, ok := value.(domain.RequestObject)
	if !ok {
		return nil, ErrAssociateDraftFailed // data['edit_data']['associate'] on a non-dict
	}
	valid, err := s.associateDraftValidity(editData)
	if err != nil {
		return nil, err
	}
	auditContext := paymentCategoryAuditContext(identity, audit)
	// fail commits the writes so far (autocommit) and answers 500.
	fail := func() (any, error) {
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return nil, ErrAssociateDraftFailed
	}
	save := func() (bool, error) {
		if !dto.AssociateDraftStorable(editData) {
			return false, nil
		}
		return true, tx.SaveDraft(ctx, id, association, dto.PythonJSONDumps(editData), dto.PythonJSONDumpsSorted(editData), valid, auditContext, s.at())
	}
	expiration, _ := data.Lookup("certificate_expiring_date")
	if _, present := editData.Lookup("medical_certificate"); !present {
		editData = dto.SetRequestField(editData, "medical_certificate", domain.RequestObject{
			{Name: "medical_id", Value: nil}, {Name: "filename", Value: nil}, {Name: "certificate_expring_date", Value: nil}})
		data = dto.SetRequestField(data, "edit_data", editData)
		if stored, err := save(); err != nil || !stored {
			if err != nil {
				return nil, err
			}
			return fail()
		}
	}
	medicalValue, _ := editData.Lookup("medical_certificate")
	medical, ok := medicalValue.(domain.RequestObject)
	if !ok {
		return nil, ErrAssociateDraftFailed // .keys() on a non-dict
	}
	var certificate *domain.AssociateDraftCertificate
	if medicalID, present := medical.Lookup("medical_id"); present && medicalID != nil {
		lookup, err := djangoUUIDValue(medicalID)
		if err != nil {
			return fail()
		}
		if certificate, err = tx.Certificate(ctx, lookup); err != nil {
			return nil, err
		}
	}
	actorAssociation, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return nil, err
	}
	if certificate == nil {
		certificate = &domain.AssociateDraftCertificate{ID: s.ids()}
		if err = tx.CreateCertificate(ctx, certificate.ID, identity.ScopeUser.ID, actorAssociation, auditContext, s.at()); err != nil {
			return nil, err
		}
	}
	truthy := dto.PythonTruthy(expiration)
	if truthy {
		text, isText := expiration.(string)
		if !isText {
			return fail() // strptime() argument must be str
		}
		date, parsed := dto.ParseSubscriptionMembershipDate(text)
		if !parsed {
			return fail()
		}
		// make_aware(..., UTC) assigned to the DateField: auditlog renders str() of the datetime.
		if err = tx.SaveCertificateExpiration(ctx, *certificate, date, fmt.Sprintf("%s 00:00:00+00:00", date), actorAssociation, auditContext,
			s.at()); err != nil {
			return nil, err
		}
	}
	medical = dto.SetRequestField(medical, "medical_id", certificate.ID)
	if truthy {
		medical = dto.SetRequestField(medical, "certificate_expring_date", expiration)
	}
	editData = dto.SetRequestField(editData, "medical_certificate", medical)
	data = dto.SetRequestField(data, "edit_data", editData)
	if stored, err := save(); err != nil || !stored {
		if err != nil {
			return nil, err
		}
		return fail()
	}
	return respond()
}

// Delete follows subscription_associates_draft_delete (request.data is
// parsed by the handler before the athlete check and otherwise unused).
func (s *AssociateDrafts) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return domain.ErrAssociateDraftUnauthorized
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	id, association, draft, err := s.target(ctx, tx, identity, raw)
	if err != nil {
		return err
	}
	if draft != nil {
		if _, err = tx.DeleteDrafts(ctx, association, []string{id}, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// BulkDelete follows subscription_associates_draft_bulk_delete after the
// athlete check: it returns the deleted and requested counts.
func (s *AssociateDrafts) BulkDelete(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (int, int, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return 0, 0, ErrAssociateDraftFailed // request.data.get on a non-dict
	}
	items := []any{}
	if value, present := object.Lookup("associate_import_draft_ids"); present {
		if items, ok = value.([]any); !ok {
			return 0, 0, domain.ErrAssociateDraftNotList
		}
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return 0, 0, err
	}
	defer tx.Rollback()
	association, err := tx.Association(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return 0, 0, err
	}
	// The __in lookup drops None and runs UUIDField.to_python on the rest.
	ids := []string{}
	for _, item := range items {
		if item == nil {
			continue
		}
		id, err := djangoUUIDValue(item)
		if err != nil {
			return 0, 0, ErrAssociateDraftFailed
		}
		ids = append(ids, id)
	}
	deleted := 0
	if len(ids) > 0 {
		if deleted, err = tx.DeleteDrafts(ctx, association, ids, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
			return 0, 0, err
		}
	}
	return deleted, len(items), tx.Commit()
}
