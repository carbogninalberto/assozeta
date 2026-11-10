package service

import (
	"context"
	"encoding/json"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

func (s *CourseSubscriptions) newID() string { return uuid.NewString() }

// courseSubscriptionEventIDEquals is event['id'] == installment.id for a JSON
// value: numbers by value, booleans as 0/1, anything else unequal.
func courseSubscriptionEventIDEquals(value any, number int) bool {
	switch v := value.(type) {
	case bool:
		return v && number == 1 || !v && number == 0
	case json.Number:
		parsed, ok := new(big.Rat).SetString(string(v))
		return ok && parsed.Cmp(big.NewRat(int64(number), 1)) == 0
	}
	return false
}

// courseSubscriptionEventHasKey is 'course_subscription_installment_id' in
// event; ok is false when the operand raises TypeError.
func courseSubscriptionEventHasKey(event any) (has, ok bool) {
	const key = "course_subscription_installment_id"
	switch v := event.(type) {
	case domain.RequestObject:
		_, present := v.Lookup(key)
		return present, true
	case []any:
		for _, item := range v {
			if text, isText := item.(string); isText && text == key {
				return true, true
			}
		}
		return false, true
	case string:
		return strings.Contains(v, key), true
	}
	return false, false
}

// courseSubscriptionPythonDateTime is str() of an aware UTC datetime.
func courseSubscriptionPythonDateTime(value time.Time) string {
	moment := value.UTC()
	if moment.Nanosecond()/1000 == 0 {
		return moment.Format("2006-01-02 15:04:05") + "+00:00"
	}
	return moment.Format("2006-01-02 15:04:05.000000") + "+00:00"
}

type courseSubscriptionEventPlan struct {
	drop    []domain.CourseSubscriptionEventInstallment
	created []dto.CourseEvent
}

// planEvents runs the control flow of CourseSubscriptionOverviewSerializer.
// update's events edit without writing: which installments go (no event with
// their id), which events create an installment (no
// course_subscription_installment_id key), and the first error the loops
// raise (the update is atomic, so any error leaves nothing behind).
func (s *CourseSubscriptions) planEvents(ctx context.Context, tx domain.CourseSubscriptionTransaction, instance domain.CourseSubscriptionRow,
	events any, group *string, header string) (courseSubscriptionEventPlan, error) {
	var plan courseSubscriptionEventPlan
	var items []any
	switch v := events.(type) {
	case []any:
		items = v
	case domain.RequestObject, string:
		// A dict yields its keys and a string its characters: each is a
		// string event (event['id'] raises, the key test is a substring test).
		items, _ = dto.PythonIterate(v)
	default:
		// for ... in <number|bool>: TypeError.
		return plan, domain.ErrCourseSubscriptionServer
	}
	installments, err := tx.EventInstallments(ctx, instance.ID, group)
	if err != nil {
		return plan, err
	}
	shared := map[string]bool{}
	for _, installment := range installments {
		matched := false
		for _, event := range items {
			object, ok := event.(domain.RequestObject)
			if !ok {
				// event['id'] of a non-dict: TypeError.
				return plan, domain.ErrCourseSubscriptionServer
			}
			value, present := object.Lookup("id")
			if !present {
				// KeyError.
				return plan, domain.ErrCourseSubscriptionServer
			}
			if courseSubscriptionEventIDEquals(value, installment.Number) {
				matched = true
				break
			}
		}
		if matched {
			continue
		}
		if installment.PaymentID != nil {
			if shared[*installment.PaymentID] {
				// A payment deleted with one installment and read again
				// through another (its cached instance) is not ported.
				return plan, ErrCourseSubscriptionNotPorted
			}
			shared[*installment.PaymentID] = true
		}
		plan.drop = append(plan.drop, installment)
	}
	for _, event := range items {
		has, ok := courseSubscriptionEventHasKey(event)
		if !ok {
			return plan, domain.ErrCourseSubscriptionServer
		}
		if has {
			continue
		}
		parsed, invalid, err := dto.ValidateCourseEvent(event, nil)
		if err != nil {
			if err == domain.ErrCourseWritesNotPorted {
				return plan, ErrCourseSubscriptionNotPorted
			}
			return plan, err
		}
		if invalid != nil {
			return plan, &CourseWritesEventInvalid{Fields: invalid}
		}
		if len(plan.created) == 0 {
			// The first create: instance.course.sport_association.user,
			// instance.subscription.associate, then the group-aware create.
			if instance.CourseAssociationID == nil || instance.SubscriptionID == nil {
				return plan, domain.ErrCourseSubscriptionServer
			}
			exists, _, err := tx.SubscriptionAssociate(ctx, *instance.SubscriptionID)
			if err != nil {
				return plan, err
			}
			if !exists {
				return plan, domain.ErrCourseSubscriptionServer
			}
			if header != "" {
				parsedGroup := supplierGroup(header)
				if parsedGroup == nil {
					return plan, domain.ErrCourseSubscriptionServer
				}
				if *parsedGroup != header {
					// auditlog would record the header as sent.
					return plan, ErrCourseSubscriptionNotPorted
				}
				found, err := tx.GroupExists(ctx, *parsedGroup)
				if err != nil {
					return plan, err
				}
				if !found {
					return plan, domain.ErrCourseSubscriptionServer
				}
			}
		}
		plan.created = append(plan.created, parsed)
	}
	return plan, nil
}

// applyEvents writes the planned events edit: each dropped installment's paid
// payment is relabelled and saved, the installment is deleted (its pre_delete
// deletes an unpaid payment), then each new event becomes a payment of the
// course's association and an installment.
func (s *CourseSubscriptions) applyEvents(ctx context.Context, tx domain.CourseSubscriptionTransaction, instance domain.CourseSubscriptionRow,
	plan courseSubscriptionEventPlan, group *string, audit domain.CourseSubscriptionAudit) error {
	title := "None"
	if instance.CourseTitle != nil {
		title = *instance.CourseTitle
	}
	label := func(number int, day time.Time) string {
		return "Rata n." + strconv.Itoa(number+1) + " del " + day.UTC().Format("02/01/06") + " (" + title + ")"
	}
	meta := func(description, date string) domain.RequestObject {
		return domain.RequestObject{{Name: "description", Value: description}, {Name: "course_id", Value: instance.CourseID},
			{Name: "course_title", Value: title}, {Name: "course_subscription_id", Value: instance.ID}, {Name: "payment_date", Value: date}}
	}
	for _, installment := range plan.drop {
		if installment.PaymentID != nil {
			paid, err := tx.PaymentPaid(ctx, *installment.PaymentID)
			if err != nil {
				return err
			}
			if paid {
				if err = s.relabelPaidPayment(ctx, tx, *installment.PaymentID, label(installment.Number, installment.PaymentDate),
					meta, courseSubscriptionPythonDateTime(installment.PaymentDate), audit); err != nil {
					return err
				}
			}
		}
		if err := tx.DeleteInstallment(ctx, installment.ID, audit); err != nil {
			return err
		}
	}
	if len(plan.created) == 0 {
		return nil
	}
	association := *instance.CourseAssociationID
	context, err := tx.EventPaymentContext(ctx, association)
	if err != nil {
		return err
	}
	_, associate, err := tx.SubscriptionAssociate(ctx, *instance.SubscriptionID)
	if err != nil {
		return err
	}
	for _, event := range plan.created {
		description := label(event.ID, event.Date)
		metaText := string(dto.PythonJSONDumps(meta(description, event.Date.Format("2006-01-02"))))
		payment := domain.CourseWritePayment{ID: s.newID(), Amount: event.Amount, MemoryAmount: event.Amount, CreationDate: event.Date,
			Description: description, CategoryID: context.CategoryID, UserID: context.UserID, AssociateID: associate, AssociationID: &association,
			Meta: &metaText, GroupID: group}
		if context.UserID != nil {
			payment.AccountID = context.AccountID
		}
		if err = tx.CreateEventPayment(ctx, payment, audit); err != nil {
			return err
		}
		if err = tx.CreateEventInstallment(ctx, domain.CourseWriteInstallmentCreate{ID: s.newID(), CourseSubscription: instance.ID,
			Number: event.ID, Amount: event.Amount, PaymentDate: event.Date, PaymentID: payment.ID, GroupID: group,
			AssociationID: &association}, audit); err != nil {
			return err
		}
	}
	return nil
}

// relabelPaidPayment is installment.payment.save() with the new description
// and meta (Payment.save() fills a missing category with the default one).
func (s *CourseSubscriptions) relabelPaidPayment(ctx context.Context, tx domain.CourseSubscriptionTransaction, id, description string,
	meta func(string, string) domain.RequestObject, date string, audit domain.CourseSubscriptionAudit) error {
	state, err := tx.PaymentState(ctx, id)
	if err != nil {
		return err
	}
	value := meta(description, date)
	save := domain.CourseSubscriptionPaymentSave{ID: id, Amount: state.Amount, Description: description, Meta: string(dto.PythonJSONDumps(value)),
		CategoryID: state.CategoryID, AssociationID: state.AssociationID, Changes: map[string][2]string{}}
	if old := courseSubscriptionAuditText(state.Description); old != description {
		save.Changes["description"] = [2]string{old, description}
	}
	before, err := courseSubscriptionAuditJSON(state.Meta, nil, false)
	if err != nil {
		return err
	}
	if after := string(dto.PythonJSONDumpsSorted(value)); before != after {
		save.Changes["meta"] = [2]string{before, after}
	}
	if state.CategoryID == nil {
		if save.CategoryID, err = tx.DefaultPaymentCategory(ctx, state.AssociationID); err != nil {
			return err
		}
		if save.CategoryID != nil {
			save.Changes["payment_category"] = [2]string{"None", *save.CategoryID}
		}
	}
	return tx.SavePayment(ctx, save, audit)
}
