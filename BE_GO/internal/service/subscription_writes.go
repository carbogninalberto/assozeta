package service

import (
	"context"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// SubscriptionEditRejected is ValidationError(detail): 400 [detail].
type SubscriptionEditRejected struct{ Detail string }

func (e *SubscriptionEditRejected) Error() string { return e.Detail }

type SubscriptionEdits struct {
	store domain.SubscriptionEditStore
	now   func() time.Time
	ids   func() string
}

func NewSubscriptionEdits(store domain.SubscriptionEditStore, now func() time.Time, ids func() string) *SubscriptionEdits {
	return &SubscriptionEdits{store: store, now: now, ids: ids}
}

// relativeYears is relativedelta(now, born).years for two midnights.
func relativeYears(now, born time.Time) int {
	add := func(months int) time.Time {
		year, month := born.Year(), int(born.Month())-1+months
		year += month / 12
		month %= 12
		if month < 0 {
			month += 12
			year--
		}
		last := time.Date(year, time.Month(month+2), 0, 0, 0, 0, 0, time.UTC).Day()
		day := born.Day()
		if day > last {
			day = last
		}
		return time.Date(year, time.Month(month+1), day, 0, 0, 0, 0, time.UTC)
	}
	months := (now.Year()-born.Year())*12 + int(now.Month()) - int(born.Month())
	if now.Before(born) {
		for now.After(add(months)) {
			months++
		}
	} else {
		for now.Before(add(months)) {
			months--
		}
	}
	if months < 0 {
		return -(-months / 12)
	}
	return months / 12
}

func subscriptionEditGroup(header string) *string {
	if header == "" {
		return nil
	}
	parsed, err := paymentInfoParseUUID(header)
	if err != nil {
		// GroupAwareManager ignores a header the UUID field rejects.
		return nil
	}
	text := parsed.String()
	return &text
}

func subscriptionEditValue(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func subscriptionEditDate(value *time.Time) string {
	if value == nil {
		return "None"
	}
	return value.Format("2006-01-02")
}

// index resolves an Associate entry as the AuditLogIndex signal does: the
// registered resolver when Associate.objects (live, group-aware) still finds
// the row and it has an association, then changes['sport_association'] (a
// live association), then the actor's live association.
type subscriptionEditIndex struct {
	tx    domain.SubscriptionEditTransaction
	actor *string
}

func (x subscriptionEditIndex) resolve(ctx context.Context, found bool, association *string, path string, changes map[string][2]string) (*string, string, error) {
	if found && association != nil {
		return association, path, nil
	}
	if pair, ok := changes["sport_association"]; ok {
		for _, candidate := range pair {
			if candidate == "None" || candidate == "" {
				continue
			}
			exists, err := x.tx.ActiveAssociationExists(ctx, candidate)
			if err != nil {
				return nil, "", err
			}
			if exists {
				value := candidate
				return &value, "changes_json.sport_association", nil
			}
		}
	}
	if x.actor != nil {
		return x.actor, "actor.sport_association", nil
	}
	return nil, "", nil
}

func applyAssociateTutorWrite(row *domain.SubscriptionEditAssociate, write dto.AssociateTutorWrite) {
	fields := map[string]**string{"address": &row.Address, "address_cap": &row.AddressCap, "address_city": &row.AddressCity,
		"born_city": &row.BornCity, "email": &row.Email, "first_name": &row.FirstName, "last_name": &row.LastName,
		"tax_code": &row.TaxCode, "phone": &row.Phone, "family_role": &row.FamilyRole}
	for name, value := range write.Text {
		*fields[name] = value
	}
	born := write.BornDate
	row.BornDate = &born
	if write.Sex != nil {
		row.Sex = *write.Sex
	}
	if write.IsMinor != nil {
		row.IsMinor = *write.IsMinor
	}
	if write.AssociationPresent {
		row.AssociationID = write.AssociationID
	}
	if write.FamilyPresent {
		row.FamilyID = write.FamilyID
	}
}

// associateChanges is auditlog's update diff of the serializer's columns.
func associateChanges(old, row domain.SubscriptionEditAssociate) map[string][2]string {
	changes := map[string][2]string{}
	diff := func(name, before, after string) {
		if before != after {
			changes[name] = [2]string{before, after}
		}
	}
	diff("user", subscriptionEditValue(old.UserID), subscriptionEditValue(row.UserID))
	diff("sport_association", subscriptionEditValue(old.AssociationID), subscriptionEditValue(row.AssociationID))
	diff("first_name", subscriptionEditValue(old.FirstName), subscriptionEditValue(row.FirstName))
	diff("last_name", subscriptionEditValue(old.LastName), subscriptionEditValue(row.LastName))
	diff("sex", old.Sex, row.Sex)
	diff("tax_code", subscriptionEditValue(old.TaxCode), subscriptionEditValue(row.TaxCode))
	diff("born_date", subscriptionEditDate(old.BornDate), subscriptionEditDate(row.BornDate))
	diff("born_city", subscriptionEditValue(old.BornCity), subscriptionEditValue(row.BornCity))
	diff("address_city", subscriptionEditValue(old.AddressCity), subscriptionEditValue(row.AddressCity))
	diff("address", subscriptionEditValue(old.Address), subscriptionEditValue(row.Address))
	diff("address_cap", subscriptionEditValue(old.AddressCap), subscriptionEditValue(row.AddressCap))
	diff("email", subscriptionEditValue(old.Email), subscriptionEditValue(row.Email))
	diff("phone", subscriptionEditValue(old.Phone), subscriptionEditValue(row.Phone))
	diff("is_minor", pythonBool(old.IsMinor), pythonBool(row.IsMinor))
	diff("family", subscriptionEditValue(old.FamilyID), subscriptionEditValue(row.FamilyID))
	diff("family_role", subscriptionEditValue(old.FamilyRole), subscriptionEditValue(row.FamilyRole))
	return changes
}

// Edit follows subscription_edit after is_valid_uuid (id is canonical).
func (s *SubscriptionEdits) Edit(ctx context.Context, identity domain.ResolvedIdentity, id string, body any, form bool, header string,
	audit domain.LoginAuditContext) error {
	tx, err := s.store.BeginSubscriptionEdit(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	group := subscriptionEditGroup(header)
	associate, err := tx.Associate(ctx, id, group, identity.ImpersonationAssociationID)
	if err != nil {
		return err
	}
	if associate == nil {
		// associate.user on None.
		return domain.ErrSubscriptionEditServer
	}
	if associate.UserID == nil || *associate.UserID != identity.ScopeUser.ID {
		return domain.ErrSubscriptionEditDenied
	}
	now := s.now().UTC()
	today := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	if associate.BornDate == nil {
		// strptime(str(None), '%Y-%m-%d').
		return domain.ErrSubscriptionEditServer
	}
	born := associate.BornDate.UTC()
	if relativeYears(today, time.Date(born.Year(), born.Month(), born.Day(), 0, 0, 0, 0, time.UTC)) >= 18 {
		return &SubscriptionEditRejected{Detail: "Cannot edit associate tutor data for associate over 18 years old"}
	}
	// data['associate_tutor_data'] and its initial_data['born_date']: form
	// values are strings, other bodies and values are not subscriptable.
	object, ok := body.(domain.RequestObject)
	if form || !ok {
		return domain.ErrSubscriptionEditServer
	}
	value, present := object.Lookup("associate_tutor_data")
	tutorData, ok := value.(domain.RequestObject)
	if !present || !ok {
		return domain.ErrSubscriptionEditServer
	}
	value, present = tutorData.Lookup("born_date")
	text, ok := value.(string)
	if !present || !ok {
		// str() of None, numbers, booleans, lists and dicts never parses as %d/%m/%Y.
		return domain.ErrSubscriptionEditServer
	}
	if dto.StrptimeUnicodeDigits(text) {
		return domain.ErrSubscriptionEditNotPorted
	}
	tutorBorn, ok, err := dto.ParseDMYDate(text)
	if err != nil {
		return domain.ErrSubscriptionEditNotPorted
	}
	if !ok {
		return domain.ErrSubscriptionEditServer
	}
	if relativeYears(today, tutorBorn) < 18 {
		return &SubscriptionEditRejected{Detail: "Cannot edit associate tutor data for associate tutor under 18 years old"}
	}
	tenant, err := tx.ActiveAssociationOf(ctx, identity.ScopeUser.ID)
	if err != nil {
		return err
	}
	write, invalid, err := dto.ParseAssociateTutor(tutorData, tutorBorn, func(candidate string) (bool, error) {
		exists, err := tx.AssociationExists(ctx, candidate)
		if err != nil || !exists {
			return exists, err
		}
		if tenant != nil && *tenant != candidate {
			return false, domain.ErrSubscriptionEditForeignReference
		}
		return true, nil
	}, func(candidate string) (bool, error) { return tx.FamilyExists(ctx, candidate) })
	if err != nil {
		return err
	}
	if invalid != nil {
		return invalid
	}
	if write.AssociateID != nil {
		// create() with associate_id updates or creates that row: not ported.
		return domain.ErrSubscriptionEditNotPorted
	}

	// Plan every refusal before the first write.
	var target *domain.SubscriptionEditAssociate
	created := domain.SubscriptionEditAssociate{ID: s.ids(), Sex: "A", CreationDate: now.Truncate(time.Microsecond)}
	tax := write.Text["tax_code"]
	if tax != nil && *tax != "" {
		if !dto.AssociateTaxCodeASCII(*tax) {
			return domain.ErrSubscriptionEditNotPorted
		}
		upper := dto.AssociateUpper(*tax)
		write.Text["tax_code"] = &upper
		matches, err := tx.TaxCodeMatches(ctx, upper, write.AssociationID, group)
		if err != nil {
			return err
		}
		if len(matches) > 1 && matches[0].CreationDate.Equal(matches[1].CreationDate) {
			// order_by('-creation_date').first() between equal dates.
			return domain.ErrSubscriptionEditNotPorted
		}
		if len(matches) > 0 {
			target = &matches[0]
			if target.ID == associate.ID {
				return domain.ErrSubscriptionEditNotPorted
			}
		}
	} else if header != "" {
		// Associate.objects.create stores the raw X-Group-Id.
		if group == nil {
			return domain.ErrSubscriptionEditServer
		}
		exists, err := tx.GroupExists(ctx, *group)
		if err != nil {
			return err
		}
		if !exists {
			return domain.ErrSubscriptionEditServer
		}
		created.GroupID = group
	}
	relations, err := tx.Relations(ctx, associate.ID)
	if err != nil {
		return err
	}
	var main *domain.SubscriptionEditRelation
	promote := false
	for index := range relations {
		if relations[index].IsPrimary {
			main = &relations[index]
			break
		}
	}
	if main == nil && len(relations) > 0 {
		main, promote = &relations[0], true
	}
	if main != nil {
		if main.TutorID == associate.ID {
			return domain.ErrSubscriptionEditNotPorted
		}
		other, err := tx.OtherReferences(ctx, main.TutorID)
		if err != nil {
			return err
		}
		if other {
			return domain.ErrSubscriptionEditNotPorted
		}
	}

	actor, err := tx.ActiveAssociationOf(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	index := subscriptionEditIndex{tx: tx, actor: actor}
	auditContext, at := paymentCategoryAuditContext(identity, audit), now.Truncate(time.Microsecond)
	record := func(model, key string, action int, changes map[string][2]string, association *string, path string, createdRow bool) error {
		return tx.Audit(ctx, domain.SubscriptionEditAudit{Model: model, Key: key, Action: action, Created: createdRow, Changes: changes,
			Association: association, Path: path, Context: auditContext, At: at})
	}
	if target == nil {
		applyAssociateTutorWrite(&created, write)
		if err = tx.InsertAssociate(ctx, created); err != nil {
			return err
		}
		found := group == nil || created.GroupID != nil && *created.GroupID == *group
		changes := map[string][2]string{"sport_association": {"None", subscriptionEditValue(created.AssociationID)}}
		association, path, err := index.resolve(ctx, found, created.AssociationID, "direct", changes)
		if err != nil {
			return err
		}
		if err = record("associate", created.ID, 0, nil, association, path, true); err != nil {
			return err
		}
	} else {
		old := *target
		applyAssociateTutorWrite(target, write)
		if err = tx.SaveAssociate(ctx, *target); err != nil {
			return err
		}
		if changes := associateChanges(old, *target); len(changes) > 0 {
			association, path, err := index.resolve(ctx, true, target.AssociationID, "direct", changes)
			if err != nil {
				return err
			}
			if err = record("associate", target.ID, 1, changes, association, path, false); err != nil {
				return err
			}
		}
	}
	if main != nil {
		if promote {
			if err = tx.PromoteRelation(ctx, main.ID); err != nil {
				return err
			}
			association, path, err := index.resolve(ctx, true, associate.AssociationID, "associate.sport_association", nil)
			if err != nil {
				return err
			}
			if err = record("associatetutorrelation", relationKey(main.ID), 1, map[string][2]string{"is_primary": {"False", "True"}},
				association, path, false); err != nil {
				return err
			}
		}
		deletes, changes, err := tx.DeleteTutor(ctx, main.TutorID)
		if err != nil {
			return err
		}
		for _, relation := range deletes {
			association, path, err := index.resolve(ctx, false, nil, "", relation.Changes)
			if err != nil {
				return err
			}
			if err = record("associatetutorrelation", relationKey(relation.ID), 2, relation.Changes, association, path, false); err != nil {
				return err
			}
		}
		association, path, err := index.resolve(ctx, false, nil, "", map[string][2]string{"sport_association": {changes["sport_association"][0], "None"}})
		if err != nil {
			return err
		}
		if err = record("associate", main.TutorID, 2, changes, association, path, false); err != nil {
			return err
		}
	}
	if !associate.IsMinor {
		saved := *associate
		saved.IsMinor = true
		if err = tx.SaveAssociate(ctx, saved); err != nil {
			return err
		}
		association, path, err := index.resolve(ctx, true, associate.AssociationID, "direct", map[string][2]string{"is_minor": {"False", "True"}})
		if err != nil {
			return err
		}
		if err = record("associate", associate.ID, 1, map[string][2]string{"is_minor": {"False", "True"}}, association, path, false); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func relationKey(id int64) string { return strconv.FormatInt(id, 10) }

// SubscriptionEditID is is_valid_uuid(uid): the canonical id, or a
// *PaymentInfoUUIDError (400 [detail]).
func SubscriptionEditID(raw string) (string, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", err
	}
	return id.String(), nil
}
