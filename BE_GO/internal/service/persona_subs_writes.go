package service

import (
	"context"
	"encoding/json"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
)

type personaSubsRenderStore interface {
	RouterSubscriptionByID(ctx context.Context, id string, group *string, today time.Time) (*domain.PersonaSubscriptionRow, error)
}

// personaSubsValidated is the serializer's validated data after the related
// lookups.
type personaSubsValidated struct {
	write dto.PersonaSubsWrite
	tags  []string // canonical keys, in input order
}

// personaSubsValidate is serializer.is_valid(raise_exception=True): the field
// validation (dto), UniqueValidator on subscription_id, then the related
// fields in field order with tenant_scope (its PermissionDenied escapes the
// collected errors).
func (s *PersonaSubscriptions) personaSubsValidate(ctx context.Context, tx domain.PersonaSubsWriteTransaction, identity domain.ResolvedIdentity,
	body any, partial bool, instance *string, group *string) (personaSubsValidated, error) {
	write, invalid, err := dto.ParsePersonaSubsWrite(body, partial)
	if err != nil {
		return personaSubsValidated{}, ErrPersonaNotPorted
	}
	if _, nonDict := invalid["non_field_errors"]; nonDict {
		return personaSubsValidated{}, &dto.PersonaInvalid{Fields: invalid}
	}
	if write.ID != nil {
		taken, err := tx.SubscriptionIDTaken(ctx, *write.ID, instance, group)
		if err != nil {
			return personaSubsValidated{}, err
		}
		if taken {
			invalid["subscription_id"] = []string{"unique:subscription:subscription id"}
		}
	}
	var tenant *string
	tenantRead := false
	related := func(field, id string, input any) (bool, error) {
		found, owner, err := tx.Related(ctx, field, id, group)
		if err != nil || !found {
			return false, err
		}
		if !tenantRead {
			if tenant, err = tx.TenantAssociation(ctx, identity.ScopeUser.ID); err != nil {
				return false, err
			}
			tenantRead = true
		}
		if tenant != nil && owner != nil && *owner != *tenant {
			return false, domain.ErrPersonaWriteForeignReference
		}
		return true, nil
	}
	for _, field := range dto.PersonaSubsRelationFields {
		id, present := write.Relations[field]
		if !present || id == nil {
			continue
		}
		found, err := related(field, *id, write.RelationInput[field])
		if err != nil {
			return personaSubsValidated{}, err
		}
		if !found {
			invalid[field] = []string{"related-does-not-exist:" + dto.PythonStr(write.RelationInput[field])}
			delete(write.Relations, field)
		}
	}
	validated := personaSubsValidated{write: write}
	if write.TagsPresent {
		validated.tags = []string{}
		for _, item := range write.Tags {
			id, code := dto.PersonaSubsRelated(item)
			if code == "" {
				found, err := related("tags", id, item)
				if err != nil {
					return personaSubsValidated{}, err
				}
				if !found {
					code = "related-does-not-exist:" + dto.PythonStr(item)
				}
			}
			if code != "" {
				invalid["tags"] = []string{code}
				break
			}
			validated.tags = append(validated.tags, id)
		}
	}
	if len(invalid) > 0 {
		return personaSubsValidated{}, &dto.PersonaInvalid{Fields: invalid}
	}
	return validated, nil
}

// RouterCreate is the stock create(): validation, then serializer.save()
// calls Subscription.objects.create(**validated_data), whose
// SubscriptionManager.subscription_exists() raises a Django ValidationError
// without associate or associate_data (read-only, never validated): a 500
// with nothing written.
func (s *PersonaSubscriptions) RouterCreate(ctx context.Context, identity domain.ResolvedIdentity, header string, body any, form bool) error {
	if form {
		return ErrPersonaNotPorted
	}
	tx, err := s.store.(domain.PersonaSubsWriteStore).BeginPersonaSubsWrite(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = s.personaSubsValidate(ctx, tx, identity, body, false, nil, supplierGroup(header)); err != nil {
		return err
	}
	return ErrPersonaUnhandled
}

func personaSubsAuditText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func personaSubsAuditDate(value *time.Time) string {
	if value == nil {
		return "None"
	}
	return value.Format("2006-01-02")
}

func personaSubsStoredJSON(value *json.RawMessage) (any, error) {
	if value == nil {
		return nil, nil
	}
	text := string(*value)
	return dto.CourseWritesStoredJSON(&text)
}

// personaSubsChanges is auditlog's model_instance_diff of the stored row and
// the in-memory instance (JSON fields compared as json.dumps(sort_keys=True)).
func personaSubsChanges(old, new domain.PersonaSubscriptionRow, inMemory map[string]any) (map[string][2]string, error) {
	changes := map[string][2]string{}
	compare := func(name, before, after string) {
		if before != after {
			changes[name] = [2]string{before, after}
		}
	}
	compare("sport_association", old.AssociationID, new.AssociationID)
	compare("user", old.UserID, new.UserID)
	for _, field := range []struct {
		name        string
		before, now *string
	}{{"supplier", old.SupplierID, new.SupplierID}, {"instructor", old.InstructorID, new.InstructorID},
		{"document_pdf", old.DocumentID, new.DocumentID}, {"payment", old.PaymentID, new.PaymentID}, {"group", old.GroupID, new.GroupID},
		{"signature_url", old.SignatureURL, new.SignatureURL}, {"subscription_number", old.SubscriptionNumber, new.SubscriptionNumber},
		{"subscription_type", old.SubscriptionType, new.SubscriptionType}, {"notes", old.Notes, new.Notes}} {
		compare(field.name, personaSubsAuditText(field.before), personaSubsAuditText(field.now))
	}
	compare("type", strconv.FormatInt(old.Type, 10), strconv.FormatInt(new.Type, 10))
	compare("role", strconv.FormatInt(old.Role, 10), strconv.FormatInt(new.Role, 10))
	compare("status_flag", strconv.FormatInt(old.StatusFlag, 10), strconv.FormatInt(new.StatusFlag, 10))
	for _, field := range []struct {
		name        string
		before, now *time.Time
	}{{"acceptance_date", old.AcceptanceDate, new.AcceptanceDate}, {"resignation_date", old.ResignationDate, new.ResignationDate},
		{"start_date", old.StartDate, new.StartDate}, {"end_date", old.EndDate, new.EndDate}} {
		compare(field.name, personaSubsAuditDate(field.before), personaSubsAuditDate(field.now))
	}
	compare("creation_date", dto.PersonaAuditMoment(old.CreationDate), dto.PersonaAuditMoment(new.CreationDate))
	for _, field := range []struct {
		name  string
		value bool
		now   bool
	}{{"deleted", old.Deleted, new.Deleted}, {"archived", old.Archived, new.Archived}, {"draft", old.Draft, new.Draft},
		{"trial", old.Trial, new.Trial}, {"competitive", old.Competitive, new.Competitive}} {
		compare(field.name, pythonBoolText(field.value), pythonBoolText(field.now))
	}
	for _, field := range []struct {
		name   string
		stored *json.RawMessage
	}{{"custom_data", old.CustomData}, {"additional_fields", old.AdditionalFields}, {"meta", old.Meta}} {
		value, present := inMemory[field.name]
		if !present {
			continue
		}
		stored, err := personaSubsStoredJSON(field.stored)
		if err != nil {
			return nil, err
		}
		compare(field.name, dto.CourseWritesAuditJSON(stored), dto.CourseWritesAuditJSON(value))
	}
	return changes, nil
}

func pythonBoolText(value bool) string {
	if value {
		return "True"
	}
	return "False"
}

// RouterUpdate is the stock update() after get_object(): validation
// (partial for PATCH), setattr of each validated field, Subscription.save()
// (missing dates from the owner's settings, auditlog's update entry, every
// column), tags.set(), then the serializer over the in-memory instance.
// Refused before any write: form bodies and a different primary key (Model.save
// would insert a copy). JSON values PostgreSQL rejects are the 500 page; several
// new tag links are created in the iteration order of Django's set of ids.
func (s *PersonaSubscriptions) RouterUpdate(ctx context.Context, identity domain.ResolvedIdentity, id, header string, body any, form, partial bool,
	login domain.LoginAuditContext) (any, error) {
	if form {
		return nil, ErrPersonaNotPorted
	}
	group := supplierGroup(header)
	tx, err := s.store.(domain.PersonaSubsWriteStore).BeginPersonaSubsWrite(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	stored, err := tx.Subscription(ctx, id)
	if err != nil {
		return nil, err
	}
	if stored == nil {
		return nil, domain.ErrPersonaSubscriptionNotFound
	}
	validated, err := s.personaSubsValidate(ctx, tx, identity, body, partial, &stored.ID, group)
	if err != nil {
		return nil, err
	}
	write := validated.write
	if write.ID != nil && *write.ID != stored.ID {
		return nil, ErrPersonaNotPorted
	}
	for _, value := range write.JSON {
		if !dto.TextStorable(value) || !jsonFinite(value) {
			// PostgreSQL rejects the jsonb text (NUL escape, NaN): the UPDATE fails, the 500 page, nothing written.
			return nil, ErrPersonaUnhandled
		}
	}
	row := *stored
	for name, value := range write.JSON {
		var raw *json.RawMessage
		if text := dto.CourseWritesJSONText(value); text != nil {
			message := json.RawMessage(*text)
			raw = &message
		}
		switch name {
		case "custom_data":
			row.CustomData = raw
		case "additional_fields":
			row.AdditionalFields = raw
		case "meta":
			row.Meta = raw
		}
	}
	for name, value := range write.Choices {
		switch name {
		case "type":
			row.Type = value
		case "role":
			row.Role = value
		case "status_flag":
			row.StatusFlag = value
		}
	}
	for name, value := range write.Dates {
		switch name {
		case "acceptance_date":
			row.AcceptanceDate = value
		case "resignation_date":
			row.ResignationDate = value
		case "start_date":
			row.StartDate = value
		case "end_date":
			row.EndDate = value
		}
	}
	if write.Created != nil {
		row.CreationDate = *write.Created
	}
	for name, value := range write.Text {
		switch name {
		case "signature_url":
			row.SignatureURL = value
		case "subscription_number":
			row.SubscriptionNumber = value
		case "subscription_type":
			row.SubscriptionType = value
		case "notes":
			row.Notes = value
		}
	}
	for name, value := range write.Booleans {
		switch name {
		case "archived":
			row.Archived = value
		case "draft":
			row.Draft = value
		case "trial":
			row.Trial = value
		case "deleted":
			row.Deleted = value
		case "competitive":
			row.Competitive = value
		}
	}
	for name, value := range write.Relations {
		switch name {
		case "sport_association":
			row.AssociationID = *value
		case "user":
			row.UserID = *value
		case "supplier":
			row.SupplierID = value
		case "instructor":
			row.InstructorID = value
		case "document_pdf":
			row.DocumentID = value
		case "payment":
			row.PaymentID = value
		case "group":
			row.GroupID = value
		}
	}
	if err = personaSubsSeason(ctx, tx, &row); err != nil {
		return nil, err
	}
	// tags.set(): links not listed are removed, listed ones not linked added.
	var removed, added []string
	if validated.tags != nil {
		current, err := tx.TagIDs(ctx, row.ID)
		if err != nil {
			return nil, err
		}
		wanted, linked := map[string]bool{}, map[string]bool{}
		for _, tag := range validated.tags {
			wanted[tag] = true
		}
		for _, tag := range current {
			linked[tag] = true
			if !wanted[tag] {
				removed = append(removed, tag)
			}
		}
		seen := map[string]bool{}
		for _, tag := range validated.tags {
			if !linked[tag] && !seen[tag] {
				seen[tag] = true
				added = append(added, tag)
			}
		}
		// bulk_create follows the iteration order of the set of new target ids.
		if len(added) > 1 {
			added = importeffects.PySetOrder(added)
		}
	}
	changes, err := personaSubsChanges(*stored, row, write.JSON)
	if err != nil {
		return nil, err
	}
	audit := domain.CourseSubscriptionAudit{Context: paymentCategoryAuditContext(identity, login), At: s.personas.now().UTC().Truncate(time.Microsecond),
		Group: group}
	if err = tx.SaveSubscription(ctx, row, changes, audit); err != nil {
		return nil, err
	}
	if len(removed) > 0 {
		if err = tx.RemoveTags(ctx, row.ID, removed); err != nil {
			return nil, err
		}
	}
	for _, tag := range added {
		if err = tx.AddTag(ctx, row.ID, tag); err != nil {
			return nil, err
		}
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	// serializer.data over the saved instance: JSON fields keep the request's values.
	today := s.personas.today()
	rendered, err := s.store.(personaSubsRenderStore).RouterSubscriptionByID(ctx, row.ID, group, today)
	if err != nil {
		return nil, err
	}
	if rendered == nil {
		return nil, ErrPersonaUnhandled
	}
	data, _, err := s.routerItems(ctx, []domain.PersonaSubscriptionRow{*rendered}, group, today)
	if err != nil {
		return nil, err
	}
	item := data[0].(domain.RequestObject)
	for index, field := range item {
		if value, present := write.JSON[field.Name]; present {
			item[index].Value = value
		}
	}
	return item, nil
}

// personaSubsSeason is Subscription.save(): the season of creation_date from
// the association owner's settings fills a missing start or end date.
func personaSubsSeason(ctx context.Context, tx domain.PersonaSubsWriteTransaction, row *domain.PersonaSubscriptionRow) error {
	rule, err := tx.DateRule(ctx, row.AssociationID)
	if err != nil {
		return err
	}
	existing := domain.SubscriptionDates{}
	marker := &domain.CalendarDate{Year: 1, Month: 1, Day: 1}
	if row.StartDate != nil {
		existing.Start = marker
	}
	if row.EndDate != nil {
		existing.End = marker
	}
	filled, err := ResolveSubscriptionDates(ctx, row.CreationDate.UTC(), rule, existing)
	if err != nil {
		return err
	}
	asTime := func(date *domain.CalendarDate) *time.Time {
		if date == nil {
			return nil
		}
		value := time.Date(date.Year, time.Month(date.Month), date.Day, 0, 0, 0, 0, time.UTC)
		return &value
	}
	if row.StartDate == nil {
		row.StartDate = asTime(filled.Start)
	}
	if row.EndDate == nil {
		row.EndDate = asTime(filled.End)
	}
	return nil
}
