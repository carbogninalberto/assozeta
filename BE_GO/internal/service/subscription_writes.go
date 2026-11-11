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
	// strptime reads the decimal digits of other scripts (ParseDMYDate).
	tutorBorn, ok, err := dto.ParseDMYDate(text)
	if err != nil {
		return err
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
	var byID *subscriptionEditByID
	if write.AssociateID != nil {
		// create() with associate_id updates or creates that row by key.
		if byID, err = s.planByID(ctx, tx, write, group, header); err != nil {
			return err
		}
	}

	// Plan every refusal before the first write.
	var target *domain.SubscriptionEditAssociate
	created := domain.SubscriptionEditAssociate{ID: s.ids(), Sex: "A", CreationDate: now.Truncate(time.Microsecond)}
	tax := write.Text["tax_code"]
	if byID != nil {
		// The associate_id branch matches the tax code as sent (no upper-casing) and the header group is not stored on update.
	} else if tax != nil && *tax != "" {
		upper := dto.AssociateUpper(*tax)
		write.Text["tax_code"] = &upper
		matches, err := tx.TaxCodeMatches(ctx, upper, write.AssociationID, group)
		if err != nil {
			return err
		}
		if len(matches) > 1 {
			// MultipleObjectsReturned: order_by('-creation_date').first(), a tie is broken by the database as in Django.
			newest, err := tx.NewestTaxCodeMatch(ctx, upper, write.AssociationID, group)
			if err != nil {
				return err
			}
			if newest == nil {
				return domain.ErrSubscriptionEditServer
			}
			target = newest
		} else if len(matches) == 1 {
			target = &matches[0]
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
	// The instance the view loaded first: its save() at the end writes these values back.
	stale := *associate
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
	var snapshot string
	collected := false
	if main != nil {
		other, err := tx.OtherReferences(ctx, main.TutorID)
		if err != nil {
			return err
		}
		collected = other
		if main.TutorID == associate.ID {
			// The associate is its own main tutor: the delete removes its row, the final save() inserts it back.
			if snapshot, err = tx.AssociateSnapshot(ctx, associate.ID); err != nil {
				return err
			}
		}
	}

	actor, err := tx.ActiveAssociationOf(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	index := subscriptionEditIndex{tx: tx, actor: actor}
	auditContext, at := paymentCategoryAuditContext(identity, audit), now.Truncate(time.Microsecond)
	record := func(model, key string, action int, changes map[string][2]string, association *string, path string, createdRow bool) error {
		row := ""
		if byID != nil && key == byID.spelling && key != byID.id {
			row = byID.id // object_pk is the spelling the client sent; the stored row is read by its canonical key
		}
		return tx.Audit(ctx, domain.SubscriptionEditAudit{Model: model, Key: key, Row: row, Action: action, Created: createdRow, Changes: changes,
			Association: association, Path: path, Context: auditContext, At: at})
	}
	if byID != nil {
		if target, err = s.saveByID(ctx, tx, byID, write, index, record, now, group); err != nil {
			return err
		}
	} else if target == nil {
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
		if collected {
			// Other tables reference the tutor: Associate.delete() through the Collector port (SET_NULL, payments, receivers).
			if err = tx.DeleteTutorCollected(ctx, main.TutorID, auditContext, group, func() time.Time { return at }); err != nil {
				return err
			}
		} else {
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
	}
	// associate.save() of the instance the view loaded first: every column is written back (undoing a tutor update of the
	// same row) and is_minor is set; a row the main tutor delete removed is inserted again.
	saved := stale
	saved.IsMinor = true
	if snapshot != "" {
		if err = tx.RestoreAssociate(ctx, snapshot); err != nil {
			return err
		}
		// auditlog's pre_save finds no old row (deleted above) and logs an update against None, resolved through the
		// changes; post_save then logs the creation, resolved directly (the row is back).
		found := group == nil || stale.GroupID != nil && *stale.GroupID == *group
		changes := map[string][2]string{"sport_association": {"None", subscriptionEditValue(stale.AssociationID)}}
		association, path, err := index.resolve(ctx, false, nil, "", changes)
		if err != nil {
			return err
		}
		if err = record("associate", stale.ID, 1, nil, association, path, true); err != nil {
			return err
		}
		association, path, err = index.resolve(ctx, found, stale.AssociationID, "direct", changes)
		if err != nil {
			return err
		}
		if err = record("associate", stale.ID, 0, nil, association, path, true); err != nil {
			return err
		}
		return tx.Commit()
	}
	current := stale
	if target != nil && target.ID == stale.ID {
		current = *target
	}
	if changes := associateChanges(current, saved); len(changes) > 0 {
		if err = tx.SaveAssociate(ctx, saved); err != nil {
			return err
		}
		association, path, err := index.resolve(ctx, true, current.AssociationID, "direct", changes)
		if err != nil {
			return err
		}
		if err = record("associate", stale.ID, 1, changes, association, path, false); err != nil {
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

// subscriptionEditByID is AssociateSerializer.create's associate_id branch, planned before the first write.
type subscriptionEditByID struct {
	spelling string                            // the key as sent: object_pk and object_repr of the entries
	id       string                            // the canonical key
	instance *domain.SubscriptionEditAssociate // all_objects() row with that key
	inTarget *domain.SubscriptionEditAssociate // the named association's row with the same tax code (first by key)
	conflict *domain.SubscriptionEditAssociate // create(): the row the unique tax code index points at
	create   bool
	row      *domain.SubscriptionEditAssociate // the row that is saved
}

// planByID looks the rows up and refuses, before any write, what Django's save() would break on (the unique tax code index: a
// 500 after nothing was written).
func (s *SubscriptionEdits) planByID(ctx context.Context, tx domain.SubscriptionEditTransaction, write dto.AssociateTutorWrite, group *string,
	header string) (*subscriptionEditByID, error) {
	parsed, err := paymentInfoParseUUID(*write.AssociateID)
	if err != nil {
		return nil, domain.ErrSubscriptionEditServer // UUIDField.to_python's ValidationError
	}
	plan := &subscriptionEditByID{spelling: *write.AssociateID, id: parsed.String()}
	if plan.instance, err = tx.AssociateAny(ctx, plan.id); err != nil {
		return nil, err
	}
	tax := write.Text["tax_code"]
	named := write.AssociationPresent && write.AssociationID != nil
	taxed := tax != nil && *tax != ""
	// taken: the saved row would break the partial unique index (IntegrityError, unhandled: the 500 page).
	taken := func(row domain.SubscriptionEditAssociate) (bool, error) {
		applyAssociateTutorWrite(&row, write)
		if row.TaxCode == nil || row.AssociationID == nil {
			return false, nil
		}
		return tx.LiveTaxCodeTaken(ctx, *row.TaxCode, *row.AssociationID, row.ID)
	}
	switch {
	case plan.instance != nil:
		if named && taxed {
			if plan.inTarget, err = tx.AssociateByTaxCode(ctx, *tax, *write.AssociationID); err != nil {
				return nil, err
			}
		}
		plan.row = plan.instance
		if plan.inTarget != nil {
			plan.row = plan.inTarget
		}
	default:
		plan.create = true
		if header != "" {
			// Associate.objects.create stores the raw X-Group-Id.
			if group == nil {
				return nil, domain.ErrSubscriptionEditServer
			}
			if exists, err := tx.GroupExists(ctx, *group); err != nil || !exists {
				if err != nil {
					return nil, err
				}
				return nil, domain.ErrSubscriptionEditServer
			}
		}
		if named && taxed {
			conflict, err := tx.LiveTaxCodeTaken(ctx, *tax, *write.AssociationID, plan.id)
			if err != nil {
				return nil, err
			}
			if conflict {
				// create() raises IntegrityError inside its atomic block; the handler then updates the row the tax code names.
				if plan.conflict, err = tx.AssociateByTaxCode(ctx, *tax, *write.AssociationID); err != nil {
					return nil, err
				}
				if plan.conflict == nil {
					return nil, domain.ErrSubscriptionEditServer
				}
				plan.row = plan.conflict
			}
		}
	}
	if plan.row != nil {
		if blocked, err := taken(*plan.row); err != nil || blocked {
			if err != nil {
				return nil, err
			}
			return nil, domain.ErrSubscriptionEditServer
		}
	}
	return plan, nil
}

// saveByID writes the planned row and its audit entry; the saved row is returned (nil after a creation).
func (s *SubscriptionEdits) saveByID(ctx context.Context, tx domain.SubscriptionEditTransaction, plan *subscriptionEditByID,
	write dto.AssociateTutorWrite, index subscriptionEditIndex, record func(model, key string, action int, changes map[string][2]string,
		association *string, path string, createdRow bool) error, now time.Time, group *string) (*domain.SubscriptionEditAssociate, error) {
	if plan.row == nil {
		created := domain.SubscriptionEditAssociate{ID: plan.id, Sex: "A", CreationDate: now.Truncate(time.Microsecond)}
		created.GroupID = group
		applyAssociateTutorWrite(&created, write)
		if err := tx.InsertAssociate(ctx, created); err != nil {
			return nil, err
		}
		found := group == nil || created.GroupID != nil && *created.GroupID == *group
		changes := map[string][2]string{"sport_association": {"None", subscriptionEditValue(created.AssociationID)}}
		association, path, err := index.resolve(ctx, found, created.AssociationID, "direct", changes)
		if err != nil {
			return nil, err
		}
		if err = record("associate", plan.spelling, 0, nil, association, path, true); err != nil {
			return nil, err
		}
		return nil, nil
	}
	row := *plan.row
	old := row
	applyAssociateTutorWrite(&row, write)
	row.Deleted = false
	if err := tx.SaveAssociate(ctx, row); err != nil {
		return nil, err
	}
	key := row.ID
	if row.ID == plan.id {
		key = plan.spelling
	}
	found := !old.Deleted && (group == nil || old.GroupID != nil && *old.GroupID == *group)
	if old.Deleted {
		if err := tx.SetAssociateDeleted(ctx, row.ID, false); err != nil {
			return nil, err
		}
		// The default manager hides the previous state: an update against None.
		changes := map[string][2]string{"sport_association": {"None", subscriptionEditValue(row.AssociationID)}}
		association, path, err := index.resolve(ctx, false, nil, "", changes)
		if err != nil {
			return nil, err
		}
		if err = record("associate", key, 1, nil, association, path, true); err != nil {
			return nil, err
		}
		return &row, nil
	}
	if changes := associateChanges(old, row); len(changes) > 0 {
		association, path, err := index.resolve(ctx, found, old.AssociationID, "direct", changes)
		if err != nil {
			return nil, err
		}
		if err = record("associate", key, 1, changes, association, path, false); err != nil {
			return nil, err
		}
	}
	return &row, nil
}
