package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

var (
	ErrInstructorHoursNotFound = errors.New("instructor hours not found")
	// ErrInstructorHoursForbidden is the tenant scope's PermissionDenied on a related payment.
	ErrInstructorHoursForbidden = errors.New("instructor hours related payment denied")
	// errInstructorHoursServer is an exception Django answers with the 500 page.
	errInstructorHoursServer = errors.New("instructor hours server error")
)

type InstructorHoursValidationError struct{ Fields map[string]any }

func (e *InstructorHoursValidationError) Error() string { return "invalid instructor hours input" }

// InstructorHoursAnswer is one of instructor_hours_calculate's own responses.
type InstructorHoursAnswer struct {
	Status int
	Body   map[string]string
}

func (e *InstructorHoursAnswer) Error() string { return "instructor hours calculate answer" }

// InstructorHoursBody reads request.data on first use: (data, form, parse error).
type InstructorHoursBody func() (any, bool, error)

type InstructorHoursService struct {
	store domain.InstructorHoursStore
	now   func() time.Time
	newID func() string
	// printer runs print_document_compensation eagerly (instructor_hours_list).
	printer *InvoiceSyncPrint
}

// WithCompensationPrint enables the list's eager compensation prints.
func (s *InstructorHoursService) WithCompensationPrint(printer *InvoiceSyncPrint) *InstructorHoursService {
	s.printer = printer
	return s
}

func NewInstructorHoursService(store domain.InstructorHoursStore, now func() time.Time, newID func() string) *InstructorHoursService {
	return &InstructorHoursService{store: store, now: now, newID: newID}
}

func (s *InstructorHoursService) moment() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

func instructorHoursJSONValue(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	parsed, err := dto.ParseProfileTablesJSON([]byte(*text))
	if err != nil {
		return nil, err
	}
	return json.RawMessage(dto.PythonJSONDumps(parsed)), nil
}

// instructorHourItem is InstructorHoursSerializer(hour).data.
func instructorHourItem(row domain.InstructorHourListed) (map[string]any, error) {
	courses, err := instructorHoursJSONValue(row.Courses)
	if err != nil {
		return nil, err
	}
	calculation, err := instructorHoursJSONValue(row.CalculationData)
	if err != nil {
		return nil, err
	}
	return map[string]any{"instructor_hours_id": row.ID, "document_token": row.DocumentToken,
		"creation_date": dto.DRFUTCDateTime(row.CreationDate), "date": row.Date, "compensation_type": row.CompensationType,
		"hours": row.Hours, "hourly_billing": row.HourlyBilling, "percentage_billing": row.PercentageBilling, "courses": courses,
		"period": row.Period, "amount": row.Amount, "paid": row.Paid, "notes": row.Notes, "calculation_data": calculation,
		"instructor": row.InstructorID, "payment": row.PaymentID, "document": row.DocumentID}, nil
}

func (s *InstructorHoursService) actorAssociation(ctx context.Context, tx domain.InstructorHoursTransaction, audit domain.LoginAuditContext) (*string, error) {
	if audit.ActorID == nil {
		return nil, nil
	}
	return tx.FirstAssociation(ctx, *audit.ActorID)
}

// List follows instructor_hours_list.
func (s *InstructorHoursService) List(ctx context.Context, identity domain.ResolvedIdentity, raw string, parameters domain.RequestObject,
	audit domain.LoginAuditContext, authorization *string) (map[string]any, error) {
	audit = paymentCategoryAuditContext(identity, audit)
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	query := domain.InstructorHoursQuery{Owner: identity.ScopeUser.ID, Instructor: id}
	if value, present := parameters.Lookup("query[date_range]"); present && value != "" {
		text, _ := value.(string)
		from, to, err := InstructorWindow(s.now().UTC(), text, true)
		if err != nil {
			return nil, err
		}
		start, end := from.Format("2006-01-02"), to.Format("2006-01-02")
		query.From, query.To = &start, &end
	}
	if value, present := parameters.Lookup("query[generalSearch]"); present {
		text, _ := value.(string)
		query.Search = &text
	}
	tx, err := s.store.BeginInstructorHours(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	count, err := tx.CountHours(ctx, query)
	if err != nil {
		return nil, err
	}
	size, sizePresent := parameters.Lookup("pagination[perpage]")
	number, numberPresent := parameters.Lookup("pagination[page]")
	page, err := dto.DRFPageNumber(number, numberPresent, count, dto.DRFPageSize(size, sizePresent, 10))
	if err != nil {
		return nil, err
	}
	rows, err := tx.Hours(ctx, query, page.Size, page.Offset())
	if err != nil {
		return nil, err
	}
	// Refuse, before any write, the rows whose loop would delete a document
	// Django's collector would cascade from (and compensation prints when no
	// printer is configured).
	orphans := map[string]bool{}
	for _, row := range rows {
		switch {
		case row.PaymentID != nil && row.DocumentID == nil:
			if s.printer == nil {
				return nil, domain.ErrInstructorHoursNotPorted
			}
		case row.PaymentID == nil && row.DocumentID != nil:
			if orphans[*row.DocumentID] {
				return nil, domain.ErrInstructorHoursNotPorted
			}
			orphans[*row.DocumentID] = true
			deletable, err := tx.DocumentDeletable(ctx, *row.DocumentID)
			if err != nil {
				return nil, err
			}
			if !deletable {
				return nil, domain.ErrDocumentCascadeUnsupported
			}
		}
	}
	association, err := s.actorAssociation(ctx, tx, audit)
	if err != nil {
		return nil, err
	}
	data := make([]map[string]any, 0, len(rows))
	ids := make([]string, 0, len(rows))
	prints := []string{}
	for index := range rows {
		row := &rows[index]
		switch {
		case row.PaymentID != nil && row.PaymentPaid != nil && *row.PaymentPaid:
			// Model.save() writes every field; auditlog records the changed flag.
			if !row.Paid {
				if err := tx.MarkPaid(ctx, row.ID); err != nil {
					return nil, err
				}
				event := domain.InstructorHoursAudit{Model: "InstructorHours", Key: row.ID, Action: 1,
					Changes: map[string][2]string{"paid": {"False", "True"}}, Instructor: &row.InstructorID}
				if err := tx.AppendAudit(ctx, event, association, audit, s.moment()); err != nil {
					return nil, err
				}
			}
			row.Paid = true
		case row.PaymentID == nil && row.DocumentID != nil:
			// The response keeps the in-memory document id and token.
			changes, err := tx.DeleteDocument(ctx, *row.DocumentID)
			if err != nil {
				return nil, err
			}
			event := domain.InstructorHoursAudit{Model: "Document", Key: *row.DocumentID, Action: 2, Changes: changes}
			if err := tx.AppendAudit(ctx, event, association, audit, s.moment()); err != nil {
				return nil, err
			}
		}
		if row.PaymentID != nil && row.DocumentID == nil {
			prints = append(prints, *row.PaymentID)
		}
		item, err := instructorHourItem(*row)
		if err != nil {
			return nil, err
		}
		data = append(data, item)
		ids = append(ids, row.ID)
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	// print_document_compensation.apply(args=[payment_id, authorization]) for
	// each page row with a payment and no document, in loop order, after the
	// loop's saves are committed. The response keeps the in-memory rows (no
	// document), as Django's serializer does.
	for _, payment := range prints {
		if err := s.printer.Compensation(ctx, payment, authorization); err != nil {
			return nil, err
		}
	}
	return map[string]any{"data": data, "meta": map[string]any{"total": count, "page": page.Number, "pages": page.Pages,
		"perpage": page.Size, "rowIds": ids}}, nil
}

// pythonNaiveUTC is auditlog's str() of a DateTimeField value.
func pythonNaiveUTC(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05")
	}
	return value.Format("2006-01-02 15:04:05.000000")
}

// pythonAwareUTC is str() of an aware UTC datetime.
func pythonAwareUTC(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05+00:00")
	}
	return value.Format("2006-01-02 15:04:05.000000+00:00")
}

func instructorHoursStr(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func instructorHoursStoredJSON(text *string) (string, error) {
	if text == nil {
		return "null", nil
	}
	parsed, err := dto.ParseProfileTablesJSON([]byte(*text))
	if err != nil {
		return "", err
	}
	return string(dto.PythonJSONDumpsSorted(parsed)), nil
}

// related validates the related fields and the primary key's uniqueness the
// way the serializer's fields do, adding their errors; a foreign payment is
// the tenant scope's PermissionDenied.
func (s *InstructorHoursService) related(ctx context.Context, tx domain.InstructorHoursTransaction, identity domain.ResolvedIdentity,
	object domain.RequestObject, write dto.InstructorHoursWrite, errs map[string]any, exclude *string, group *string) error {
	if write.ID != nil {
		taken, err := tx.HourIDTaken(ctx, *write.ID, exclude)
		if err != nil {
			return err
		}
		if taken {
			errs["instructor_hours_id"] = []string{dto.InstructorHoursUnique}
		}
	}
	missing := func(name string) {
		value, _ := object.Lookup(name)
		errs[name] = []string{"related-does-not-exist:" + dto.PythonStr(value)}
	}
	if write.Instructor.ID != nil {
		found, owner, err := tx.InstructorAssociation(ctx, *write.Instructor.ID)
		if err != nil {
			return err
		}
		if !found {
			missing("instructor")
		} else if owner != nil {
			current, err := tx.FirstAssociation(ctx, identity.ScopeUser.ID)
			if err != nil {
				return err
			}
			if current != nil && *current != *owner {
				return ErrInstructorHoursForbidden
			}
		}
	}
	if write.Payment.ID != nil {
		payment, err := tx.VisiblePayment(ctx, *write.Payment.ID, group)
		if err != nil {
			return err
		}
		if payment == nil {
			missing("payment")
		} else if payment.AssociationID != nil {
			current, err := tx.FirstAssociation(ctx, identity.ScopeUser.ID)
			if err != nil {
				return err
			}
			if current != nil && *current != *payment.AssociationID {
				return ErrInstructorHoursForbidden
			}
		}
	}
	if write.Document.ID != nil {
		found, err := tx.DocumentExists(ctx, *write.Document.ID)
		if err != nil {
			return err
		}
		if !found {
			missing("document")
		}
	}
	return nil
}

// apply sets the validated fields on row (serializer.update's setattr, or
// create's keyword arguments over the model defaults).
func instructorHoursApply(row *domain.InstructorHour, write dto.InstructorHoursWrite) {
	if write.ID != nil {
		row.ID = *write.ID
	}
	if write.Instructor.Present {
		row.InstructorID = *write.Instructor.ID
	}
	if write.Payment.Present {
		row.PaymentID = write.Payment.ID
	}
	if write.Document.Present {
		row.DocumentID = write.Document.ID
	}
	if write.CreationDate != nil {
		row.CreationDate = write.CreationDate.UTC()
	}
	if write.Date != nil {
		row.Date = *write.Date
	}
	if write.Paid != nil {
		row.Paid = *write.Paid
	}
	for name, value := range write.Text {
		switch name {
		case "compensation_type":
			row.CompensationType = *value
		case "period":
			row.Period = value
		case "notes":
			row.Notes = value
		}
	}
	for name, value := range write.Decimals {
		switch name {
		case "hours":
			row.Hours = *value
		case "hourly_billing":
			row.HourlyBilling = *value
		case "percentage_billing":
			row.PercentageBilling = value
		case "amount":
			row.Amount = *value
		}
	}
	for name, value := range write.JSON {
		var text *string
		if value != nil {
			stored := string(dto.PythonJSONDumps(value))
			text = &stored
		}
		if name == "courses" {
			row.Courses = text
		} else {
			row.CalculationData = text
		}
	}
}

// Add follows instructor_hours_add.
func (s *InstructorHoursService) Add(ctx context.Context, identity domain.ResolvedIdentity, raw string, load InstructorHoursBody,
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
	found, err := s.store.OwnedInstructorExists(ctx, id, identity.ScopeUser.ID)
	if err != nil {
		return err
	}
	if !found {
		return ErrInstructorNotFound
	}
	object, ok := body.(domain.RequestObject)
	if !ok || form {
		// data['instructor'] = uid on a QueryDict, list, string or None raises.
		return errInstructorHoursServer
	}
	data := domain.RequestObject{}
	for _, field := range object {
		if field.Name != "instructor" {
			data = append(data, field)
		}
	}
	data = append(data, domain.RequestField{Name: "instructor", Value: raw})
	write, errs, err := dto.ParseInstructorHoursWrite(data, false, false)
	if err != nil {
		return err
	}
	if errs == nil {
		errs = map[string]any{}
	}
	tx, err := s.store.BeginInstructorHours(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := s.related(ctx, tx, identity, data, write, errs, nil, supplierGroup(group)); err != nil {
		return err
	}
	if len(errs) > 0 {
		return &InstructorHoursValidationError{Fields: errs}
	}
	if write.NonFinite {
		return errInstructorHoursServer
	}
	// Model defaults are evaluated in field order: creation_date, then date.
	created := s.moment()
	day := s.moment()
	empty := "[]"
	row := domain.InstructorHour{ID: s.newID(), CreationDate: created, Date: day.Format("2006-01-02"), CompensationType: "hourly",
		HourlyBilling: "0.00", Amount: "0.00", CalculationData: &empty}
	zero := "0.00"
	row.PercentageBilling = &zero
	instructorHoursApply(&row, write)
	if err := tx.InsertHour(ctx, row); err != nil {
		return err
	}
	changes, err := tx.CreatedChanges(ctx, row.ID)
	if err != nil {
		return err
	}
	// auditlog reads the in-memory instance: absent decimal defaults are the
	// float 0.0 and an absent date is timezone.now().
	for _, name := range []string{"hourly_billing", "percentage_billing", "amount"} {
		if _, given := write.Decimals[name]; !given {
			changes[name] = [2]string{"None", "0.0"}
		}
	}
	if write.Date == nil {
		changes["date"] = [2]string{"None", pythonAwareUTC(day)}
	}
	association, err := s.actorAssociation(ctx, tx, audit)
	if err != nil {
		return err
	}
	event := domain.InstructorHoursAudit{Model: "InstructorHours", Key: row.ID, Action: 0, Changes: changes, Instructor: &row.InstructorID}
	if err := tx.AppendAudit(ctx, event, association, audit, s.moment()); err != nil {
		return err
	}
	return tx.Commit()
}

// Update follows instructor_hours_update (a partial serializer save).
func (s *InstructorHoursService) Update(ctx context.Context, identity domain.ResolvedIdentity, rawInstructor, rawHours string,
	load InstructorHoursBody, group string, audit domain.LoginAuditContext) error {
	audit = paymentCategoryAuditContext(identity, audit)
	instructor, err := messageReadsParseUUID(rawInstructor)
	if err != nil {
		return err
	}
	hours, err := messageReadsParseUUID(rawHours)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginInstructorHours(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	old, err := tx.OwnedHour(ctx, identity.ScopeUser.ID, instructor, hours)
	if err != nil {
		return err
	}
	if old == nil {
		return ErrInstructorHoursNotFound
	}
	body, form, err := load()
	if err != nil {
		return err
	}
	write, errs, err := dto.ParseInstructorHoursWrite(body, form, true)
	if err != nil {
		return err
	}
	if _, invalid := errs["non_field_errors"]; invalid {
		return &InstructorHoursValidationError{Fields: errs}
	}
	if errs == nil {
		errs = map[string]any{}
	}
	object, _ := body.(domain.RequestObject)
	if err := s.related(ctx, tx, identity, object, write, errs, &old.ID, supplierGroup(group)); err != nil {
		return err
	}
	if len(errs) > 0 {
		return &InstructorHoursValidationError{Fields: errs}
	}
	if write.NonFinite {
		return errInstructorHoursServer
	}
	row := *old
	instructorHoursApply(&row, write)
	association, err := s.actorAssociation(ctx, tx, audit)
	if err != nil {
		return err
	}
	if row.ID != old.ID {
		// save() under a new primary key inserts a copy; auditlog's pre_save
		// finds no row and logs the full diff as an update, then the create.
		if err := tx.InsertHour(ctx, row); err != nil {
			return err
		}
		changes, err := tx.CreatedChanges(ctx, row.ID)
		if err != nil {
			return err
		}
		for _, action := range []int{1, 0} {
			event := domain.InstructorHoursAudit{Model: "InstructorHours", Key: row.ID, Action: action, Changes: changes}
			if action == 0 {
				// The update entry is written before the copy exists (the actor's association).
				event.Instructor = &row.InstructorID
			}
			if err := tx.AppendAudit(ctx, event, association, audit, s.moment()); err != nil {
				return err
			}
		}
		return tx.Commit()
	}
	changes, err := instructorHoursChanges(*old, row, write)
	if err != nil {
		return err
	}
	if err := tx.UpdateHour(ctx, row); err != nil {
		return err
	}
	if len(changes) > 0 {
		// auditlog's pre_save entry resolves the stored row: the previous instructor.
		event := domain.InstructorHoursAudit{Model: "InstructorHours", Key: row.ID, Action: 1, Changes: changes, Instructor: &old.InstructorID}
		if err := tx.AppendAudit(ctx, event, association, audit, s.moment()); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// instructorHoursChanges is auditlog's update diff: str() of each field before
// and after, keeping only the changed ones.
func instructorHoursChanges(old, row domain.InstructorHour, write dto.InstructorHoursWrite) (map[string][2]string, error) {
	paid := func(value bool) string {
		if value {
			return "True"
		}
		return "False"
	}
	pairs := map[string][2]string{
		"instructor_hours_id": {old.ID, row.ID}, "instructor": {old.InstructorID, row.InstructorID},
		"creation_date": {pythonNaiveUTC(old.CreationDate), pythonNaiveUTC(row.CreationDate)}, "date": {old.Date, row.Date},
		"compensation_type": {old.CompensationType, row.CompensationType}, "hours": {old.Hours, row.Hours},
		"hourly_billing":     {old.HourlyBilling, row.HourlyBilling},
		"percentage_billing": {instructorHoursStr(old.PercentageBilling), instructorHoursStr(row.PercentageBilling)},
		"period":             {instructorHoursStr(old.Period), instructorHoursStr(row.Period)}, "amount": {old.Amount, row.Amount},
		"paid": {paid(old.Paid), paid(row.Paid)}, "payment": {instructorHoursStr(old.PaymentID), instructorHoursStr(row.PaymentID)},
		"notes":    {instructorHoursStr(old.Notes), instructorHoursStr(row.Notes)},
		"document": {instructorHoursStr(old.DocumentID), instructorHoursStr(row.DocumentID)},
	}
	for name, stored := range map[string]*string{"courses": old.Courses, "calculation_data": old.CalculationData} {
		before, err := instructorHoursStoredJSON(stored)
		if err != nil {
			return nil, err
		}
		after := before
		if value, given := write.JSON[name]; given {
			after = string(dto.PythonJSONDumpsSorted(value))
		}
		pairs[name] = [2]string{before, after}
	}
	changes := map[string][2]string{}
	for name, pair := range pairs {
		if pair[0] != pair[1] {
			changes[name] = pair
		}
	}
	return changes, nil
}

// Delete follows instructor_hours_delete: the hour's document first.
func (s *InstructorHoursService) Delete(ctx context.Context, identity domain.ResolvedIdentity, rawInstructor, rawHours string,
	audit domain.LoginAuditContext) error {
	audit = paymentCategoryAuditContext(identity, audit)
	instructor, err := messageReadsParseUUID(rawInstructor)
	if err != nil {
		return err
	}
	hours, err := messageReadsParseUUID(rawHours)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginInstructorHours(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row, err := tx.OwnedHour(ctx, identity.ScopeUser.ID, instructor, hours)
	if err != nil {
		return err
	}
	if row == nil {
		return ErrInstructorHoursNotFound
	}
	association, err := s.actorAssociation(ctx, tx, audit)
	if err != nil {
		return err
	}
	if row.DocumentID != nil {
		deletable, err := tx.DocumentDeletable(ctx, *row.DocumentID)
		if err != nil {
			return err
		}
		if !deletable {
			return domain.ErrDocumentCascadeUnsupported
		}
	}
	// The in-memory hour keeps its document id after the document's delete.
	changes, err := tx.DeletedChanges(ctx, row.ID)
	if err != nil {
		return err
	}
	if row.DocumentID != nil {
		documentChanges, err := tx.DeleteDocument(ctx, *row.DocumentID)
		if err != nil {
			return err
		}
		event := domain.InstructorHoursAudit{Model: "Document", Key: *row.DocumentID, Action: 2, Changes: documentChanges}
		if err := tx.AppendAudit(ctx, event, association, audit, s.moment()); err != nil {
			return err
		}
	}
	if err := tx.DeleteHour(ctx, row.ID); err != nil {
		return err
	}
	event := domain.InstructorHoursAudit{Model: "InstructorHours", Key: row.ID, Action: 2, Changes: changes}
	if err := tx.AppendAudit(ctx, event, association, audit, s.moment()); err != nil {
		return err
	}
	return tx.Commit()
}

func instructorHoursAnswer(status int, key, message string) error {
	return &InstructorHoursAnswer{Status: status, Body: map[string]string{key: message}}
}

// instructorHoursPeriod parses calculate's 'A al B': the first format parsed
// the start into a datetime, so a failing end makes the second strptime() a
// TypeError (500).
func instructorHoursPeriod(text string) (time.Time, time.Time, error) {
	parts := strings.Split(text, " al ")
	if len(parts) != 2 {
		return time.Time{}, time.Time{}, instructorHoursAnswer(400, "error", "Invalid period format")
	}
	if from, ok := strptimeDate(parts[0], true); ok {
		to, ok := strptimeDate(parts[1], true)
		if !ok {
			return time.Time{}, time.Time{}, errInstructorHoursServer
		}
		return from, to, nil
	}
	from, ok := strptimeDate(parts[0], false)
	to, ok2 := strptimeDate(parts[1], false)
	if !ok || !ok2 {
		return time.Time{}, time.Time{}, instructorHoursAnswer(400, "error", "Invalid date format")
	}
	return from, to, nil
}

// instructorHoursFloat is float(value): ok false is the ValueError/TypeError
// the view answers 400; an int beyond float range is an OverflowError.
func instructorHoursFloat(value any) (float64, bool, error) {
	switch v := value.(type) {
	case bool:
		if v {
			return 1, true, nil
		}
		return 0, true, nil
	case string:
		number, ok := dto.PythonFloatParse(v)
		return number, ok, nil
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			number, ok := dto.PythonFloatParse(string(v))
			return number, ok, nil
		}
		integer, ok := new(big.Int).SetString(string(v), 10)
		if !ok {
			return 0, false, nil
		}
		number, _ := new(big.Float).SetInt(integer).Float64()
		if math.IsInf(number, 0) {
			return 0, false, errInstructorHoursServer
		}
		return number, true, nil
	}
	return 0, false, nil
}

func decimalAdd(total *big.Rat, text string) error {
	value, ok := new(big.Rat).SetString(text)
	if !ok {
		return errInstructorHoursServer
	}
	total.Add(total, value)
	return nil
}

// Calculate follows instructor_hours_calculate (a read).
func (s *InstructorHoursService) Calculate(ctx context.Context, identity domain.ResolvedIdentity, raw string, load InstructorHoursBody,
	group string) (map[string]any, error) {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	body, _, err := load()
	if err != nil {
		return nil, err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		// request.data.get on a list, string or None.
		return nil, errInstructorHoursServer
	}
	period, _ := object.Lookup("period")
	courses, _ := object.Lookup("courses")
	percentage, _ := object.Lookup("percentage")
	if !dto.PythonTruthy(courses) {
		return nil, instructorHoursAnswer(400, "error", "courses is required")
	}
	found, err := s.store.OwnedInstructorExists(ctx, id, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, instructorHoursAnswer(404, "error", "instructor not found")
	}
	items, ok := courses.([]any)
	if !ok {
		return nil, instructorHoursAnswer(400, "error", "Invalid courses format")
	}
	ids := []string{}
	var rawIDs []any
	for _, item := range items {
		entry, ok := item.(domain.RequestObject)
		if !ok {
			return nil, instructorHoursAnswer(400, "error", "Invalid courses format")
		}
		value, present := entry.Lookup("course_id")
		if !present {
			return nil, instructorHoursAnswer(400, "error", "Invalid courses format")
		}
		rawIDs = append(rawIDs, value)
	}
	// Course.objects.filter(course_id__in=...) prepares every value (None is dropped).
	for _, value := range rawIDs {
		if value == nil {
			continue
		}
		course, ok := dto.DjangoUUIDLookup(value)
		if !ok {
			return nil, errInstructorHoursServer
		}
		ids = append(ids, course)
	}
	var from, to time.Time
	switch text, isText := period.(string); {
	case period == nil || isText && text == "":
		return nil, instructorHoursAnswer(400, "msg", "Periodo di pagamento non valido")
	case !isText:
		return nil, errInstructorHoursServer
	default:
		if from, to, err = instructorHoursPeriod(text); err != nil {
			return nil, err
		}
	}
	var factor *float64
	if percentage != nil {
		number, ok, err := instructorHoursFloat(percentage)
		if err != nil {
			return nil, err
		}
		if !ok {
			return nil, instructorHoursAnswer(400, "error", "Invalid percentage")
		}
		if number < 0 || number > 100 {
			return nil, instructorHoursAnswer(400, "error", "Percentage must be 0-100")
		}
		factor = &number
	}
	groupID := supplierGroup(group)
	rows, err := s.store.Courses(ctx, ids, identity.ScopeUser.ID, groupID)
	if err != nil {
		return nil, err
	}
	total := new(big.Rat)
	summed := false
	entries := []map[string]any{}
	record := func(course domain.InstructorHoursCourse, subscription domain.InstructorHoursSubscription, installment *string,
		amount string, date time.Time) error {
		if !subscription.HasAssociate {
			// course_subscription.subscription.associate is None.
			return errInstructorHoursServer
		}
		if err := decimalAdd(total, amount); err != nil {
			return err
		}
		summed = true
		value, err := dto.PythonDecimalFloat(amount)
		if err != nil {
			return err
		}
		name := strings.TrimFunc(instructorHoursStr(subscription.FirstName)+" "+instructorHoursStr(subscription.LastName), dto.PythonIsSpace)
		item := map[string]any{"course": course.Title, "course_subscription": subscription.ID, "full_athlete_name": name,
			"amount": value, "payment_date": dto.DRFUTCDateTime(date)}
		if installment != nil {
			item["course_subscription_installment"] = *installment
		}
		entries = append(entries, item)
		return nil
	}
	for _, course := range rows {
		subscriptions, err := s.store.CourseSubscriptions(ctx, course.ID)
		if err != nil {
			return nil, err
		}
		for _, subscription := range subscriptions {
			switch {
			case course.Type == 3 || course.Multi:
				var paid []domain.InstructorHoursPaid
				if course.Type == 3 {
					paid, err = s.store.MembershipPayments(ctx, subscription.ID, from, to, groupID)
				} else {
					paid, err = s.store.InstallmentPayments(ctx, subscription.ID, from, to, groupID)
				}
				if err != nil {
					return nil, err
				}
				for _, payment := range paid {
					if err := record(course, subscription, payment.InstallmentID, payment.Amount, payment.PaymentDate); err != nil {
						return nil, err
					}
				}
			default:
				if subscription.PaymentID == nil || subscription.Paid == nil || !*subscription.Paid || subscription.PaymentDate == nil {
					continue
				}
				amount, ok := new(big.Rat).SetString(*subscription.Amount)
				if !ok {
					return nil, errInstructorHoursServer
				}
				date := *subscription.PaymentDate
				if amount.Sign() > 0 && !date.Before(from) && !date.After(to) {
					if err := record(course, subscription, nil, *subscription.Amount, date); err != nil {
						return nil, err
					}
				}
			}
		}
	}
	var amount any = json.Number("0")
	if summed {
		value, err := dto.PythonDecimalFloat(total.FloatString(6))
		if err != nil {
			return nil, err
		}
		amount = value
	}
	if factor != nil {
		base := 0.0
		if summed {
			base, _ = total.Float64()
		}
		result := base * (*factor / 100)
		if math.IsNaN(result) || math.IsInf(result, 0) {
			// The strict JSON renderer refuses out-of-range floats.
			return nil, errInstructorHoursServer
		}
		amount = json.Number(dto.PythonFloatStr(result))
	}
	return map[string]any{"data": map[string]any{"amount": amount, "calculation_data": entries}}, nil
}
