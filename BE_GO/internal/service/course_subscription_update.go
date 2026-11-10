package service

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// Membership validation and payment period messages (validate() and update()).
const (
	courseSubscriptionForeignCourse        = "Il corso non appartiene alla tua associazione."
	courseSubscriptionCourseChange         = "Non puoi cambiare il corso di un abbonamento esistente."
	courseSubscriptionPersonMissing        = "Seleziona un tesserato della stessa associazione del corso."
	courseSubscriptionPersonChange         = "Non puoi cambiare il tesserato di un abbonamento esistente."
	courseSubscriptionStartMissing         = "Inserisci una data di inizio valida."
	courseSubscriptionEndMissing           = "Inserisci una data di fine valida."
	courseSubscriptionEndBeforeStart       = "La data di fine non può precedere la data di inizio."
	courseSubscriptionFrequency            = "La durata deve essere compresa tra 1 e 12 mesi."
	courseSubscriptionFee                  = "La quota deve essere un importo maggiore o uguale a zero."
	courseSubscriptionManyPayments         = "Più pagamenti corrispondono al periodo corrente. Verifica i pagamenti prima di modificare l’abbonamento."
	courseSubscriptionPeriodMismatch       = "Il periodo dell’abbonamento non corrisponde ai pagamenti. Verifica i periodi prima di salvare."
	courseSubscriptionPaymentStart         = "Il pagamento corrente non contiene una data di inizio valida."
	courseSubscriptionPaymentEndBefore     = "La fine non può precedere l’inizio del periodo di pagamento corrente."
	courseSubscriptionPaymentOverlap       = "Il periodo si sovrappone a un pagamento precedente. I periodi già fatturati devono rimanere separati."
	courseSubscriptionMembership       int = 3
)

// applyCourseSubscriptionWrite is ModelSerializer.update()'s setattr loop.
func applyCourseSubscriptionWrite(row *domain.CourseSubscriptionRow, write dto.CourseSubscriptionWrite) {
	if write.ID != nil {
		row.ID = *write.ID
	}
	if write.SubscriptionID != nil {
		row.SubscriptionID = write.SubscriptionID
	}
	if write.CreationDate != nil {
		row.CreationDate = *write.CreationDate
	}
	for name, value := range write.Bools {
		switch name {
		case "paid":
			row.Paid = value
		case "multi_payments":
			row.MultiPayments = value
		case "one_fee_payment":
			row.OneFeePayment = value
		case "membership_active":
			row.MembershipActive = value
		case "auto_renewal":
			row.AutoRenewal = value
		}
	}
	if write.QuoteSet {
		if write.Quote == nil {
			row.MultipleQuote = nil
		} else {
			text := string(dto.PythonJSONDumps(write.Quote))
			row.MultipleQuote = &text
		}
	}
	if write.CourseSet {
		row.CourseID = write.Course
	}
	if value, ok := write.Ints["billed_frequency"]; ok {
		row.BilledFrequency = value
	}
	if value, ok := write.Ints["billed_from_day_of_month"]; ok {
		row.BilledFromDayOfMonth = value
	}
	if value, ok := write.Times["billed_from"]; ok {
		row.BilledFrom = value
	}
	if value, ok := write.Times["billed_until"]; ok {
		row.BilledUntil = value
	}
	if write.FeeSet {
		row.MembershipFee = write.Fee
	}
}

// validateMembership is the serializer's validate() for a membership course
// or instance (the actor is never an athlete here).
func (s *CourseSubscriptions) validateMembership(ctx context.Context, tx domain.CourseSubscriptionTransaction, association string,
	instance domain.CourseSubscriptionRow, course domain.CourseSubscriptionCourse, write dto.CourseSubscriptionWrite, group *string) error {
	if course.Type != courseSubscriptionMembership && instance.Type != courseSubscriptionMembership {
		return nil
	}
	errs := map[string]any{}
	if course.AssociationID == nil || *course.AssociationID != association {
		errs["course"] = []string{courseSubscriptionForeignCourse}
	}
	if course.ID != instance.CourseID {
		errs["course"] = []string{courseSubscriptionCourseChange}
	}
	person := instance.SubscriptionID
	if write.SubscriptionID != nil {
		person = write.SubscriptionID
	}
	visible := false
	if person != nil {
		var err error
		if visible, err = tx.SubscriptionVisible(ctx, *person, derefText(course.AssociationID), group); err != nil {
			return err
		}
	}
	if !visible {
		errs["subscription_id"] = []string{courseSubscriptionPersonMissing}
	}
	if !sameText(person, instance.SubscriptionID) {
		errs["subscription_id"] = []string{courseSubscriptionPersonChange}
	}
	start, end := instance.BilledFrom, instance.BilledUntil
	if value, ok := write.Times["billed_from"]; ok {
		start = value
	}
	if value, ok := write.Times["billed_until"]; ok {
		end = value
	}
	if start == nil {
		errs["billed_from"] = []string{courseSubscriptionStartMissing}
	}
	if end == nil {
		errs["billed_until"] = []string{courseSubscriptionEndMissing}
	}
	if start != nil && end != nil && end.Before(*start) {
		errs["billed_until"] = []string{courseSubscriptionEndBeforeStart}
	}
	frequency := instance.BilledFrequency
	if value, ok := write.Ints["billed_frequency"]; ok {
		frequency = value
	}
	if frequency < 1 || frequency > 12 {
		errs["billed_frequency"] = []string{courseSubscriptionFrequency}
	}
	fee := instance.MembershipFee
	if write.FeeSet {
		fee = write.Fee
	}
	if fee == nil || dto.CourseSubscriptionDecimalNegative(*fee) {
		errs["membership_fee"] = []string{courseSubscriptionFee}
	}
	if len(errs) > 0 {
		return courseSubscriptionInvalid(errs)
	}
	return nil
}

func derefText(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

// courseSubscriptionPeriod is the matched membership payment and its new period.
type courseSubscriptionPeriod struct {
	payment      domain.CourseSubscriptionMembershipPayment
	start, end   time.Time
	startChanged bool
}

// membershipPayment is update()'s current-payment matching and period checks
// (each failure is the view's ValidationError: a plain text).
func (s *CourseSubscriptions) membershipPayment(ctx context.Context, tx domain.CourseSubscriptionTransaction, instance domain.CourseSubscriptionRow,
	write dto.CourseSubscriptionWrite, group *string) (*courseSubscriptionPeriod, error) {
	if instance.Type != courseSubscriptionMembership || instance.BilledUntil == nil {
		return nil, nil
	}
	until := instance.BilledUntil.UTC().Format("2006-01-02")
	matched, err := tx.MembershipPaymentsWhere(ctx, instance.ID, group, true, &until, 2)
	if err != nil {
		return nil, err
	}
	if len(matched) > 1 {
		return nil, courseSubscriptionInvalid(map[string]any{"billed_until": courseSubscriptionManyPayments})
	}
	recovering := false
	var payment *domain.CourseSubscriptionMembershipPayment
	if len(matched) == 1 {
		payment = &matched[0]
	} else {
		history, err := tx.MembershipPaymentsWhere(ctx, instance.ID, group, false, &until, 0)
		if err != nil {
			return nil, err
		}
		if len(history) == 0 {
			existing, err := tx.MembershipPaymentsWhere(ctx, instance.ID, group, false, nil, 2)
			if err != nil {
				return nil, err
			}
			_, from := write.Times["billed_from"]
			_, to := write.Times["billed_until"]
			if len(existing) == 1 && !existing[0].Paid && from && to {
				payment, recovering = &existing[0], true
			} else {
				unpaid, err := tx.MembershipPaymentsWhere(ctx, instance.ID, group, true, nil, 0)
				if err != nil {
					return nil, err
				}
				if len(unpaid) > 0 {
					return nil, courseSubscriptionInvalid(map[string]any{"billed_until": courseSubscriptionPeriodMismatch})
				}
			}
		}
	}
	if payment == nil {
		return nil, nil
	}
	start, ok := courseSubscriptionMetaStart(payment.Meta)
	if !ok {
		return nil, courseSubscriptionInvalid(map[string]any{"billed_from": courseSubscriptionPaymentStart})
	}
	period := &courseSubscriptionPeriod{payment: *payment, start: start}
	newFrom := instance.BilledFrom
	if value, ok := write.Times["billed_from"]; ok {
		newFrom = value
	}
	// The form also resends unchanged membership dates on a fee-only edit.
	period.startChanged = recovering || !sameTime(newFrom, instance.BilledFrom)
	if period.startChanged {
		if newFrom == nil {
			return nil, domain.ErrCourseSubscriptionServer
		}
		period.start = newFrom.UTC()
	}
	end := instance.BilledUntil
	if value, ok := write.Times["billed_until"]; ok {
		end = value
	}
	if end == nil {
		return nil, domain.ErrCourseSubscriptionServer
	}
	period.end = end.UTC()
	if dayOf(period.end).Before(dayOf(period.start)) {
		return nil, courseSubscriptionInvalid(map[string]any{"billed_until": courseSubscriptionPaymentEndBefore})
	}
	overlap, err := tx.MembershipPaymentOverlaps(ctx, instance.ID, payment.ID, group, period.end.Format("2006-01-02"), period.start.Format("2006-01-02"))
	if err != nil {
		return nil, err
	}
	if overlap {
		return nil, courseSubscriptionInvalid(map[string]any{"billed_from": courseSubscriptionPaymentOverlap})
	}
	return period, nil
}

func dayOf(value time.Time) time.Time {
	return time.Date(value.Year(), value.Month(), value.Day(), 0, 0, 0, 0, time.UTC)
}

// courseSubscriptionMetaStart is make_aware(strptime(payment.meta['billed_from'],
// '%Y-%m-%d')); KeyError, TypeError and ValueError are the view's 400.
func courseSubscriptionMetaStart(meta *string) (time.Time, bool) {
	if meta == nil {
		return time.Time{}, false
	}
	parsed, err := dto.ParseReferenceJSON([]byte(*meta))
	if err != nil {
		return time.Time{}, false
	}
	object, ok := parsed.(domain.RequestObject)
	if !ok {
		return time.Time{}, false
	}
	value, present := object.Lookup("billed_from")
	if !present {
		return time.Time{}, false
	}
	return dto.CourseSubscriptionStrptime(value)
}

func setMeta(object domain.RequestObject, name string, value any) domain.RequestObject {
	for index, field := range object {
		if field.Name == name {
			object[index].Value = value
			return object
		}
	}
	return append(object, domain.RequestField{Name: name, Value: value})
}

// savePayment is the payment part of update(): amount, period dates (when the
// start changed), description and meta, then Payment.save() fills a null
// category with the default one.
func (s *CourseSubscriptions) savePayment(ctx context.Context, tx domain.CourseSubscriptionTransaction, row domain.CourseSubscriptionRow,
	course domain.CourseSubscriptionCourse, period courseSubscriptionPeriod, audit domain.CourseSubscriptionAudit) error {
	state, err := tx.PaymentState(ctx, period.payment.ID)
	if err != nil {
		return err
	}
	if row.MembershipFee == nil {
		return domain.ErrCourseSubscriptionServer
	}
	title := "None"
	var courseTitle any
	if course.Title != nil {
		title, courseTitle = *course.Title, *course.Title
	}
	description := "Abbonamento: " + title + " dal " + period.start.Format("02/01/2006") + " al " + period.end.Format("02/01/2006")
	parsed, err := dto.ParseReferenceJSON([]byte(*state.Meta))
	if err != nil {
		return err
	}
	old, ok := parsed.(domain.RequestObject)
	if !ok {
		return domain.ErrCourseSubscriptionServer
	}
	meta := append(domain.RequestObject{}, old...)
	for _, field := range []domain.RequestField{{Name: "description", Value: description}, {Name: "course_id", Value: row.CourseID},
		{Name: "course_title", Value: courseTitle}, {Name: "course_subscription_id", Value: row.ID},
		{Name: "billed_from", Value: period.start.Format("2006-01-02")}, {Name: "billed_until", Value: period.end.Format("2006-01-02")},
		{Name: "amount", Value: *row.MembershipFee}} {
		meta = setMeta(meta, field.Name, field.Value)
	}
	save := domain.CourseSubscriptionPaymentSave{ID: period.payment.ID, Amount: *row.MembershipFee, Description: description,
		Meta: string(dto.PythonJSONDumps(meta)), CategoryID: state.CategoryID, AssociationID: state.AssociationID, Changes: map[string][2]string{}}
	diff := func(name, before, after string) {
		if before != after {
			save.Changes[name] = [2]string{before, after}
		}
	}
	diff("amount", state.Amount, *row.MembershipFee)
	if period.startChanged {
		start := period.start
		save.CreationDate, save.PaymentDate = &start, &start
		diff("creation_date", courseSubscriptionAuditTime(&state.CreationDate), courseSubscriptionAuditTime(&start))
		diff("payment_date", courseSubscriptionAuditTime(state.PaymentDate), courseSubscriptionAuditTime(&start))
	}
	diff("description", courseSubscriptionAuditText(state.Description), description)
	diff("meta", string(dto.PythonJSONDumpsSorted(old)), string(dto.PythonJSONDumpsSorted(meta)))
	if state.CategoryID == nil {
		if save.CategoryID, err = tx.DefaultPaymentCategory(ctx); err != nil {
			return err
		}
		if save.CategoryID != nil {
			diff("payment_category", "None", *save.CategoryID)
		}
	}
	return tx.SavePayment(ctx, save, audit)
}

// Update follows CourseSubscriptionViewSet.update (atomic, partial).
func (s *CourseSubscriptions) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, request CourseSubscriptionRequest, body any,
	form bool, audit domain.LoginAuditContext) (map[string]any, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	instance, association, err := s.object(ctx, tx, identity, raw, false)
	if err != nil {
		return nil, err
	}
	if identity.ImpersonationAssociationID != "" {
		// scoped_queryset(...).select_for_update().get(pk=...) raises
		// DoesNotExist (500) outside the session's association.
		scope := identity.ImpersonationAssociationID
		rows, err := tx.CourseSubscriptions(ctx, domain.CourseSubscriptionQuery{AssociationID: scope, ID: &instance.ID, Scope: &scope, Lock: true})
		if err != nil {
			return nil, err
		}
		if len(rows) == 0 {
			return nil, domain.ErrCourseSubscriptionServer
		}
	}
	group := supplierGroup(request.GroupHeader)
	write, invalid := dto.ParseCourseSubscriptionWrite(body, form)
	if write.FormJSON {
		return nil, ErrCourseSubscriptionNotPorted
	}
	if invalid == nil {
		invalid = map[string]any{}
	}
	course := domain.CourseSubscriptionCourse{ID: instance.CourseID, AssociationID: instance.CourseAssociationID, Type: instance.CourseType,
		Title: instance.CourseTitle}
	if write.CourseSet {
		found, err := tx.Course(ctx, write.Course)
		if err != nil {
			return nil, err
		}
		if found == nil {
			invalid["course"] = []string{"related-does-not-exist:" + write.CourseRaw}
		} else {
			course = *found
		}
	}
	if write.ID != nil {
		taken, err := tx.CourseSubscriptionIDTaken(ctx, *write.ID, instance.ID)
		if err != nil {
			return nil, err
		}
		if taken {
			invalid["course_subscription_id"] = []string{dto.CourseSubscriptionUnique}
		}
	}
	if len(invalid) > 0 {
		return nil, courseSubscriptionInvalid(invalid)
	}
	if err = s.validateMembership(ctx, tx, association, instance, course, write, group); err != nil {
		return nil, err
	}
	period, err := s.membershipPayment(ctx, tx, instance, write, group)
	if err != nil {
		return nil, err
	}
	if write.ID != nil && *write.ID != instance.ID {
		// A primary-key change (Model.save() would insert a copy) is not ported.
		return nil, ErrCourseSubscriptionNotPorted
	}
	var events courseSubscriptionEventPlan
	if write.Events != nil {
		if events, err = s.planEvents(ctx, tx, instance, write.Events, group, request.GroupHeader); err != nil {
			return nil, err
		}
	}
	context, err := s.auditContext(ctx, tx, identity, audit, group)
	if err != nil {
		return nil, err
	}
	if write.Events != nil {
		// serializer.save(): the events edit runs before the instance save.
		if err = s.applyEvents(ctx, tx, instance, events, group, context); err != nil {
			return nil, err
		}
	}
	row := instance
	applyCourseSubscriptionWrite(&row, write)
	changes, err := courseSubscriptionChanges(instance, row, write)
	if err != nil {
		return nil, err
	}
	memory := dto.CourseSubscriptionMemory{Times: map[string]bool{}, QuoteSet: write.QuoteSet, Quote: write.Quote}
	if write.CreationDate != nil {
		memory.Times["creation_date"] = true
	}
	for name, value := range write.Times {
		memory.Times[name] = value != nil
	}
	save := domain.CourseSubscriptionSave{Row: row, Changes: changes, Representation: dto.CourseSubscriptionRepr(row, memory),
		AssociationID: instance.CourseAssociationID, ResolutionPath: "course.sport_association"}
	if err = tx.SaveCourseSubscription(ctx, save, context); err != nil {
		return nil, err
	}
	if period != nil {
		if err = s.savePayment(ctx, tx, row, course, *period, context); err != nil {
			return nil, err
		}
	}
	rows, err := tx.CourseSubscriptions(ctx, domain.CourseSubscriptionQuery{AssociationID: association, ID: &row.ID, IncludeDeleted: true})
	if err != nil {
		return nil, err
	}
	saved := row
	if len(rows) == 1 {
		saved = rows[0]
	} else {
		// A course the scoped manager does not load: render the in-memory row.
		saved.CourseAssociationID, saved.CourseTitle, saved.CourseType = course.AssociationID, course.Title, course.Type
	}
	renders, err := s.render(ctx, tx, []domain.CourseSubscriptionRow{saved}, group)
	if err != nil {
		return nil, err
	}
	renders[0].QuoteSet, renders[0].Quote = write.QuoteSet, write.Quote
	items, err := s.items(renders)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, domain.ErrCourseSubscriptionServer
	}
	return items[0], nil
}
