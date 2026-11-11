package service

import (
	"context"
	"errors"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

var (
	// errInstructorWritesServer is an exception Django answers with the 500 page.
	errInstructorWritesServer = errors.New("instructor writes server error")
	// ErrInstructorHoursMissing is the compensation's 400 {'error': 'hours not found'}.
	ErrInstructorHoursMissing = errors.New("compensation hours missing")
)

// InstructorWritesValidationError is the serializer's 400 field errors.
type InstructorWritesValidationError struct{ Fields map[string]any }

func (e *InstructorWritesValidationError) Error() string { return "invalid instructor input" }

type InstructorWrites struct {
	store domain.InstructorWritesStore
	now   func() time.Time
	newID func() string
}

func NewInstructorWrites(store domain.InstructorWritesStore, now func() time.Time, newID func() string) *InstructorWrites {
	return &InstructorWrites{store: store, now: now, newID: newID}
}

func (s *InstructorWrites) moment() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// instructorWritesLate commits the work Django saved before an exception
// (requests are not atomic) and answers the 500 page.
func instructorWritesLate(tx domain.InstructorWritesTransaction) error {
	if err := tx.Commit(); err != nil {
		return err
	}
	return errInstructorWritesServer
}

// actorIndex is the index fallback: SportAssociation.objects.filter(user=actor).first().
func (s *InstructorWrites) actorIndex(ctx context.Context, tx domain.InstructorWritesTransaction, audit domain.LoginAuditContext) (*string, string, error) {
	if audit.ActorID == nil {
		return nil, "", nil
	}
	association, err := tx.FirstAssociation(ctx, *audit.ActorID)
	return association, "actor.sport_association", err
}

// instructorIndex is instructor_resolver on a stored instructor (its owner's
// association), else the actor's.
func (s *InstructorWrites) instructorIndex(ctx context.Context, tx domain.InstructorWritesTransaction, owner string,
	audit domain.LoginAuditContext) (*string, string, error) {
	association, err := tx.FirstAssociation(ctx, owner)
	if err != nil || association != nil {
		return association, "instructor.user.sport_association", err
	}
	return s.actorIndex(ctx, tx, audit)
}

func (s *InstructorWrites) append(ctx context.Context, tx domain.InstructorWritesTransaction, event domain.InstructorWritesAudit,
	association *string, path string, audit domain.LoginAuditContext) error {
	event.AssociationID, event.Path = association, path
	return tx.AppendAudit(ctx, event, audit, s.moment())
}

// instructorObject is request.data for a view that sets data['user']: a
// QueryDict (form), a list, a str or None raises.
func instructorObject(body any, form bool) (domain.RequestObject, error) {
	object, ok := body.(domain.RequestObject)
	if !ok || form {
		return nil, errInstructorWritesServer
	}
	return object, nil
}

// related adds the document existence errors.
func (s *InstructorWrites) related(ctx context.Context, tx domain.InstructorWritesTransaction, write dto.InstructorWrite, errs map[string]any) error {
	if _, failed := errs["documents"]; !failed {
		for index, id := range write.Documents {
			found, err := tx.DocumentExists(ctx, id)
			if err != nil {
				return err
			}
			if !found {
				errs["documents"] = []string{"related-does-not-exist:" + write.DocumentText[index]}
				break
			}
		}
	}
	return nil
}

// Add follows instructor_add.
func (s *InstructorWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, load InstructorHoursBody, audit domain.LoginAuditContext) error {
	audit = paymentCategoryAuditContext(identity, audit)
	owner := identity.ScopeUser.ID
	tx, err := s.store.BeginInstructorWrites(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// SportAssociation.objects.get(user=request.user).
	if associations, err := tx.Associations(ctx, owner); err != nil {
		return err
	} else if len(associations) != 1 {
		return errInstructorWritesServer
	}
	body, form, err := load()
	if err != nil {
		return err
	}
	object, err := instructorObject(body, form)
	if err != nil {
		return err
	}
	if value, present := object.Lookup("associated_user_id"); present && value == "" {
		object = instructorReplaced(object, "associated_user_id", nil)
	}
	write, errs := dto.ParseInstructorWrite(object)
	if errs == nil {
		errs = map[string]any{}
	}
	if err := s.related(ctx, tx, write, errs); err != nil {
		return err
	}
	if len(errs) > 0 {
		return &InstructorWritesValidationError{Fields: errs}
	}
	if write.DocumentsPresent {
		// Instructor.objects.create(documents=...): direct assignment to a many-to-many set.
		return errInstructorWritesServer
	}
	role := "Istruttore"
	zero := "0.00"
	row := domain.InstructorRecord{ID: s.newID(), UserID: owner, Role: &role, DefaultHourlyBilling: zero, DefaultPercentageBilling: &zero}
	dto.ApplyInstructorWrite(&row, write)
	if err := tx.InsertInstructor(ctx, row); err != nil {
		return err
	}
	changes, err := tx.CreatedChanges(ctx, "Instructor", row.ID)
	if err != nil {
		return err
	}
	// Model defaults the serializer did not provide are the float 0.0 in memory.
	for _, name := range []string{"default_hourly_billing", "default_percentage_billing"} {
		if _, provided := write.Decimals[name]; !provided {
			changes[name] = [2]string{"None", "0.0"}
		}
	}
	association, path, err := s.instructorIndex(ctx, tx, owner, audit)
	if err != nil {
		return err
	}
	if err := s.append(ctx, tx, domain.InstructorWritesAudit{Model: "Instructor", Key: row.ID, Action: 0, Changes: changes}, association, path,
		audit); err != nil {
		return err
	}
	return tx.Commit()
}

func instructorReplaced(object domain.RequestObject, name string, value any) domain.RequestObject {
	result := make(domain.RequestObject, 0, len(object))
	for _, field := range object {
		if field.Name == name {
			field.Value = value
		}
		result = append(result, field)
	}
	return result
}

// Update follows instructor_update: a full serializer save.
func (s *InstructorWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, load InstructorHoursBody,
	audit domain.LoginAuditContext) error {
	audit = paymentCategoryAuditContext(identity, audit)
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	owner := identity.ScopeUser.ID
	tx, err := s.store.BeginInstructorWrites(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	current, err := tx.OwnedInstructor(ctx, id, owner)
	if err != nil {
		return err
	}
	if current == nil {
		return ErrInstructorNotFound
	}
	body, form, err := load()
	if err != nil {
		return err
	}
	object, err := instructorObject(body, form)
	if err != nil {
		return err
	}
	write, errs := dto.ParseInstructorWrite(object)
	if errs == nil {
		errs = map[string]any{}
	}
	if err := s.related(ctx, tx, write, errs); err != nil {
		return err
	}
	if len(errs) > 0 {
		return &InstructorWritesValidationError{Fields: errs}
	}
	row := *current
	row.UserID = owner
	dto.ApplyInstructorWrite(&row, write)
	// The update entry precedes the save: the stored instructor's owner.
	changes := dto.InstructorChanges(*current, row)
	if len(changes) > 0 {
		association, path, err := s.instructorIndex(ctx, tx, current.UserID, audit)
		if err != nil {
			return err
		}
		if err := s.append(ctx, tx, domain.InstructorWritesAudit{Model: "Instructor", Key: row.ID, Action: 1, Changes: changes}, association,
			path, audit); err != nil {
			return err
		}
	}
	if err := tx.UpdateInstructor(ctx, row); err != nil {
		return err
	}
	if write.DocumentsPresent {
		if err := tx.SetDocuments(ctx, row.ID, write.Documents); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// Delete follows instructor_delete.
func (s *InstructorWrites) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	audit = paymentCategoryAuditContext(identity, audit)
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginInstructorWrites(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	current, err := tx.OwnedInstructor(ctx, id, identity.ScopeUser.ID)
	if err != nil {
		return err
	}
	if current == nil {
		return ErrInstructorNotFound
	}
	events, err := tx.DeleteInstructor(ctx, id)
	if err != nil {
		return err
	}
	for _, event := range events {
		// post_delete: the row is gone, so the index uses the diff's
		// sport_association (Reminders), else the actor's association.
		var association *string
		path := ""
		if pair, ok := event.Changes["sport_association"]; ok && pair[0] != "None" {
			live, err := tx.LiveAssociation(ctx, pair[0])
			if err != nil {
				return err
			}
			if live {
				value := pair[0]
				association, path = &value, "changes_json.sport_association"
			}
		}
		if association == nil {
			if association, path, err = s.actorIndex(ctx, tx, audit); err != nil {
				return err
			}
		}
		if err := s.append(ctx, tx, event, association, path, audit); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// compensationHourIDs is instructor_hours_id__in=data['hours']: the value is
// iterated (a str by character, a dict by key) and each item goes through
// UUIDField.to_python (None is dropped); anything else raises.
func compensationHourIDs(value any) ([]string, error) {
	var items []any
	switch v := value.(type) {
	case []any:
		items = v
	case string:
		for _, r := range v {
			items = append(items, string(r))
		}
	case domain.RequestObject:
		for _, field := range v {
			items = append(items, field.Name)
		}
	default:
		return nil, errInstructorWritesServer
	}
	ids := []string{}
	for _, item := range items {
		if item == nil {
			continue
		}
		id, ok := dto.DRFUUIDValue(item)
		if !ok {
			return nil, errInstructorWritesServer
		}
		ids = append(ids, id)
	}
	return ids, nil
}

// compensationNotes composes the notes for hours: the text, the index of the
// first hour whose composition raises (-1 for none), or not ported.
func compensationNotes(hours []domain.InstructorHour) (string, int, error) {
	var notes strings.Builder
	for index, hour := range hours {
		if hour.CompensationType == "hourly" {
			notes.WriteString("Compenso per il giorno " + hour.Date + ", ore " + hour.Hours + " con importo totale di " + hour.Amount + "€\n")
		} else {
			notes.WriteString("Compenso per il giorno " + hour.Date + ", importo di " + hour.Amount + "€\n")
			text := "-"
			if hour.Notes != nil && *hour.Notes != "" {
				text = *hour.Notes
			}
			notes.WriteString("Note: " + text + "\n")
			notes.WriteString("Nel dettaglio:\n\n")
			failed, err := compensationDetails(&notes, hour)
			if err != nil {
				return "", 0, err
			}
			if failed {
				return "", index, nil
			}
		}
		if index != len(hours)-1 {
			notes.WriteString("\n ---------------- \n\n")
		}
	}
	return notes.String(), -1, nil
}

// compensationDetails writes one line per calculation_data entry; failed is
// the exception Django raises in the loop.
func compensationDetails(notes *strings.Builder, hour domain.InstructorHour) (bool, error) {
	if hour.CalculationData == nil {
		return true, nil
	}
	parsed, err := dto.ParseProfileTablesJSON([]byte(*hour.CalculationData))
	if err != nil {
		return false, err
	}
	var items []any
	switch v := parsed.(type) {
	case []any:
		items = v
	case domain.RequestObject:
		if len(v) > 0 {
			return true, nil
		}
	case string:
		if v != "" {
			return true, nil
		}
	default:
		return true, nil
	}
	percentageText := "None"
	var percentage *dto.InstructorDecimal
	if hour.PercentageBilling != nil {
		percentageText = *hour.PercentageBilling
		value, outcome := dto.ParseInstructorDecimal(*hour.PercentageBilling)
		if outcome != dto.DecimalFinite || value.Special != "" {
			return false, domain.ErrInstructorWritesNotPorted
		}
		if !value.IsZero() {
			scaled := value.PercentOf()
			percentage = &scaled
		}
	}
	for index, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return true, nil
		}
		raw, present := object.Lookup("amount")
		if !present {
			return true, nil
		}
		amount, outcome := dto.ParseInstructorDecimal(dto.PythonStr(raw))
		switch outcome {
		case dto.DecimalInvalid:
			return true, nil
		case dto.DecimalNotPorted:
			return false, domain.ErrInstructorWritesNotPorted
		}
		result := amount
		if percentage != nil {
			var outcome dto.TimesResult
			switch result, outcome = amount.Times(*percentage); outcome {
			case dto.TimesInvalid, dto.TimesOverflow:
				return true, nil // the trapped signal raises
			case dto.TimesNotPorted:
				return false, domain.ErrInstructorWritesNotPorted
			}
		}
		athlete, present := object.Lookup("full_athlete_name")
		if !present {
			return true, nil
		}
		course, present := object.Lookup("course")
		if !present {
			return true, nil
		}
		notes.WriteString(strconv.Itoa(index+1) + "° Pagamento\nAtleta: " + dto.PythonStr(athlete) + " \nCorso: " + dto.PythonStr(course) +
			"\nImporto: " + amount.String() + " € * " + percentageText + " % = " + result.String() + " €\n\n")
	}
	return false, nil
}

// compensationGroup is GroupAwareManager.create's raw X-Group-Id: the column keeps its uuid, the instance keeps the text as sent
// (the create entry and the later save write it).
func compensationGroup(ctx context.Context, tx domain.InstructorWritesTransaction, header string) (stored, sent *string, err error) {
	if header == "" {
		return nil, nil, nil
	}
	group := supplierGroup(header)
	if group == nil {
		return nil, nil, errInstructorWritesServer
	}
	exists, err := tx.GroupExists(ctx, *group)
	if err != nil {
		return nil, nil, err
	}
	if !exists {
		return nil, nil, errInstructorWritesServer
	}
	return group, &header, nil
}

// Compensation follows instructor_hours_add_compensation.
func (s *InstructorWrites) Compensation(ctx context.Context, identity domain.ResolvedIdentity, raw string, load InstructorHoursBody,
	group string, audit domain.LoginAuditContext) error {
	audit = paymentCategoryAuditContext(identity, audit)
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	body, form, err := load()
	if err != nil {
		return err
	}
	switch v := body.(type) {
	case domain.RequestObject:
		if _, present := v.Lookup("hours"); !present {
			return ErrInstructorHoursMissing
		}
		if form {
			// data['payment_date'] = ... on an immutable QueryDict.
			return errInstructorWritesServer
		}
	case []any:
		for _, item := range v {
			if item == "hours" {
				return errInstructorWritesServer
			}
		}
		return ErrInstructorHoursMissing
	case string:
		if strings.Contains(v, "hours") {
			return errInstructorWritesServer
		}
		return ErrInstructorHoursMissing
	default:
		return errInstructorWritesServer
	}
	object := body.(domain.RequestObject)
	paymentDate := s.moment()
	if value, present := object.Lookup("payment_date"); present {
		if parsed, ok := dto.CompensationPaymentDate(value); ok {
			paymentDate = parsed
		}
	}
	owner := identity.ScopeUser.ID
	tx, err := s.store.BeginInstructorWrites(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	instructor, err := tx.OwnedInstructor(ctx, id, owner)
	if err != nil {
		return err
	}
	if instructor == nil {
		return ErrInstructorNotFound
	}
	value, _ := object.Lookup("hours")
	ids, err := compensationHourIDs(value)
	if err != nil {
		return err
	}
	hours, err := tx.SelectableHours(ctx, instructor.ID, ids)
	if err != nil {
		return err
	}
	category, err := tx.CompensationCategory(ctx)
	if err != nil {
		return err
	}
	total, memory := new(big.Rat), "0"
	for _, hour := range hours {
		amount, ok := new(big.Rat).SetString(hour.Amount)
		if !ok {
			return errInstructorWritesServer
		}
		total.Add(total, amount)
	}
	stored := total.FloatString(2)
	if len(hours) > 0 {
		memory = stored
	}
	// request.user.sport_association: SportAssociation.objects.get(user=owner).
	associations, err := tx.Associations(ctx, owner)
	if err != nil {
		return err
	}
	if len(associations) != 1 {
		return errInstructorWritesServer
	}
	association := associations[0]
	account, err := tx.DefaultBankAccount(ctx, association)
	if err != nil {
		return err
	}
	groupID, sentGroup, err := compensationGroup(ctx, tx, group)
	if err != nil {
		return err
	}
	notes, failedAt, err := compensationNotes(hours)
	if err != nil {
		return err
	}
	payment := domain.InstructorCompensationPayment{ID: s.newID(), Amount: stored, CreationDate: s.moment(), PaymentDate: paymentDate,
		Description: "Compensi e Rimborsi Spese per " + instructor.FirstName + " " + instructor.LastName, UserID: owner,
		InstructorID: instructor.ID, AssociationID: association, CategoryID: category, AccountID: account, GroupID: groupID}
	if err := tx.InsertPayment(ctx, payment); err != nil {
		return err
	}
	changes, err := tx.CreatedChanges(ctx, "Payment", payment.ID)
	if err != nil {
		return err
	}
	if memory != stored {
		changes["amount"] = [2]string{"None", memory}
	}
	if sentGroup != nil && *sentGroup != *groupID {
		changes["group"] = [2]string{"None", *sentGroup}
	}
	if err := s.append(ctx, tx, domain.InstructorWritesAudit{Model: "Payment", Key: payment.ID, Action: 0, Changes: changes}, &association,
		"direct", audit); err != nil {
		return err
	}
	ownerIndex, ownerPath, err := s.instructorIndex(ctx, tx, instructor.UserID, audit)
	if err != nil {
		return err
	}
	for index, hour := range hours {
		if err := tx.LinkHour(ctx, hour.ID, payment.ID); err != nil {
			return err
		}
		if err := s.append(ctx, tx, domain.InstructorWritesAudit{Model: "InstructorHours", Key: hour.ID, Action: 1,
			Changes: map[string][2]string{"payment": {"None", payment.ID}}}, ownerIndex, ownerPath, audit); err != nil {
			return err
		}
		if index == failedAt {
			return instructorWritesLate(tx)
		}
	}
	if utf8.RuneCountInString(notes) > 5120 {
		// The notes column rejects the save (DataError) after the links.
		return instructorWritesLate(tx)
	}
	update := map[string][2]string{"notes": {"None", notes}}
	if memory != stored {
		update["amount"] = [2]string{stored, memory}
	}
	if sentGroup != nil && *sentGroup != *groupID {
		update["group"] = [2]string{*groupID, *sentGroup}
	}
	if err := tx.SetPaymentNotes(ctx, payment.ID, notes); err != nil {
		return err
	}
	if err := s.append(ctx, tx, domain.InstructorWritesAudit{Model: "Payment", Key: payment.ID, Action: 1, Changes: update}, &association,
		"direct", audit); err != nil {
		return err
	}
	return tx.Commit()
}
