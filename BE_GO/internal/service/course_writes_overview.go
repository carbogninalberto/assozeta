package service

import (
	"context"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

func courseWritesOverviewIDs(rawCourse, rawSubscription string) (string, string, error) {
	course, err := paymentInfoParseUUID(rawCourse)
	if err != nil {
		return "", "", err
	}
	subscription, err := paymentInfoParseUUID(rawSubscription)
	if err != nil {
		return "", "", err
	}
	return course.String(), subscription.String(), nil
}

// courseWritesBody is request.data as the overview views read it, `key in
// data` then data[key], for the keys they test. An object answers for
// itself. A list (membership) or a string (substring) that holds none of the
// keys reads as an empty object; one that holds a key raises on data[key], and
// a number or boolean raises on the test itself: raises reports both.
func courseWritesBody(body any, keys ...string) (object domain.RequestObject, raises bool) {
	switch v := body.(type) {
	case nil:
		return domain.RequestObject{}, false
	case domain.RequestObject:
		return v, false
	case []any:
		for _, item := range v {
			text, ok := item.(string)
			for _, key := range keys {
				if ok && text == key {
					return domain.RequestObject{}, true
				}
			}
		}
		return domain.RequestObject{}, false
	case string:
		for _, key := range keys {
			if strings.Contains(v, key) {
				return domain.RequestObject{}, true
			}
		}
		return domain.RequestObject{}, false
	}
	return domain.RequestObject{}, true
}

// deleteListed is the installment loop of course_overview_delete: the
// payment (base manager) is fetched for each loaded installment (a payment
// deleted meanwhile raises); an unpaid one is deleted with its signals; the
// installment's own pre_delete then does nothing (its cached payment is the
// deleted or paid instance), so the row is deleted and audited as loaded.
func courseWritesDeleteInstallment(ctx context.Context, tx domain.CourseWritesTransaction, installment domain.CourseWriteInstallment,
	audit domain.CourseSubscriptionAudit) (bool, error) {
	if installment.PaymentID != nil {
		exists, paid, err := tx.PaymentPaid(ctx, *installment.PaymentID)
		if err != nil {
			return false, err
		}
		if !exists {
			return true, nil
		}
		if !paid {
			if err = tx.DeletePayment(ctx, *installment.PaymentID, audit); err != nil {
				return false, err
			}
		}
	}
	return false, tx.DeleteInstallment(ctx, installment, nil, audit)
}

// OverviewDelete follows course_overview_delete.
func (s *CourseWrites) OverviewDelete(ctx context.Context, identity domain.ResolvedIdentity, rawCourse, rawSubscription string,
	request CourseWritesRequest) error {
	courseID, subscriptionID, err := courseWritesOverviewIDs(rawCourse, rawSubscription)
	if err != nil {
		return err
	}
	data, raises := courseWritesBody(request.Body, "installments")
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := s.courseWritesOwner(ctx, tx, identity)
	if err != nil {
		return err
	}
	group := supplierGroup(request.GroupHeader)
	course, err := courseWritesLiveCourse(ctx, tx, identity, courseID, group)
	if err != nil {
		return err
	}
	if course == nil || course.AssociationID == nil {
		return domain.ErrCourseWritesServer
	}
	if *course.AssociationID != association {
		return domain.ErrCourseWritesForeign
	}
	subscription, err := courseWritesLiveSubscription(ctx, tx, identity, subscriptionID, group)
	if err != nil {
		return err
	}
	var subscriptionKey *string
	if subscription != nil {
		subscriptionKey = &subscription.ID
	}
	row, err := tx.LiveCourseSubscription(ctx, course.ID, subscriptionKey)
	if err != nil {
		return err
	}
	shared := false
	if row != nil {
		if shared, err = tx.SharedPayment(ctx, *row, group); err != nil {
			return err
		}
	}
	audit, err := s.auditContext(ctx, tx, identity, request)
	if err != nil {
		return err
	}
	if raises {
		// 'installments' in data, or data['installments'], of a body that is not an object.
		return domain.ErrCourseWritesServer
	}
	if listed, present := data.Lookup("installments"); present {
		items, ok := dto.PythonIterate(listed)
		if !ok {
			return domain.ErrCourseWritesServer
		}
		for _, item := range items {
			if item == nil {
				continue
			}
			id, ok := dto.DjangoUUIDLookup(item)
			if !ok {
				return courseWritesLate(tx, domain.ErrCourseWritesServer)
			}
			if row == nil {
				continue
			}
			installment, err := tx.LiveInstallment(ctx, row.ID, id, group)
			if err != nil {
				return err
			}
			if installment == nil {
				continue
			}
			failed, err := courseWritesDeleteInstallment(ctx, tx, *installment, audit)
			if err != nil {
				return err
			}
			if failed {
				return courseWritesLate(tx, domain.ErrCourseWritesServer)
			}
		}
	} else if row != nil {
		installments, err := tx.Installments(ctx, row.ID, group, false)
		if err != nil {
			return err
		}
		for _, installment := range installments {
			failed, err := courseWritesDeleteInstallment(ctx, tx, installment, audit)
			if err != nil {
				return err
			}
			if failed {
				return courseWritesLate(tx, domain.ErrCourseWritesServer)
			}
		}
	}
	if row == nil {
		return courseWritesLate(tx, domain.ErrCourseWritesServer)
	}
	// CourseSubscription.delete(): the live group-aware installments, then the save.
	if err = tx.DeleteInstallmentsOf(ctx, row.ID, audit); err != nil {
		return err
	}
	save := *row
	save.Deleted = true
	if shared {
		// An installment's signal deleted the row's own payment and cleared the
		// stored reference; the model reads it back before the save.
		save.PaymentID = nil
	}
	if err = tx.SaveCourseSubscription(ctx, domain.CourseWriteSubscriptionSave{Row: save}, audit); err != nil {
		if err == domain.ErrCourseWritesStalePayment {
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
		return err
	}
	return tx.Commit()
}

// dropInstallments deletes the row's live group-aware installments whose
// payment is unpaid or missing: each payment is fetched (a deleted one
// raises), deleted with its signals and cleared in memory, so the
// installment's entry records payment None.
func courseWritesDropInstallments(ctx context.Context, tx domain.CourseWritesTransaction, row string, audit domain.CourseSubscriptionAudit) (bool, error) {
	installments, err := tx.Installments(ctx, row, audit.Group, true)
	if err != nil {
		return false, err
	}
	for _, installment := range installments {
		var overrides map[string][2]string
		if installment.PaymentID != nil {
			exists, _, err := tx.PaymentPaid(ctx, *installment.PaymentID)
			if err != nil {
				return false, err
			}
			if !exists {
				return true, nil
			}
			if err = tx.DeletePayment(ctx, *installment.PaymentID, audit); err != nil {
				return false, err
			}
			overrides = map[string][2]string{"payment": {"None", "None"}}
		}
		if err = tx.DeleteInstallment(ctx, installment, overrides, audit); err != nil {
			return false, err
		}
	}
	return false, nil
}

// OverviewUpdate follows course_overview_update.
func (s *CourseWrites) OverviewUpdate(ctx context.Context, identity domain.ResolvedIdentity, rawCourse, rawSubscription string,
	request CourseWritesRequest) error {
	courseID, subscriptionID, err := courseWritesOverviewIDs(rawCourse, rawSubscription)
	if err != nil {
		return err
	}
	data, raises := courseWritesBody(request.Body, "multi_payments", "one_fee_payment")
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := courseWritesOwnerFirst(ctx, tx, identity)
	if err != nil {
		return err
	}
	group := supplierGroup(request.GroupHeader)
	course, err := courseWritesLiveCourse(ctx, tx, identity, courseID, group)
	if err != nil {
		return err
	}
	row, err := tx.LiveCourseSubscription(ctx, courseID, &subscriptionID)
	if err != nil {
		return err
	}
	if row != nil && !courseEnrolmentScoped(identity, row.CourseAssociationID) {
		row = nil
	}
	subscription, err := courseWritesLiveSubscription(ctx, tx, identity, subscriptionID, group)
	if err != nil {
		return err
	}
	if row == nil {
		return domain.ErrCourseWritesServer
	}
	athlete := identity.ScopeUser.Role == domain.RoleAthlete
	if !athlete {
		if association == nil {
			return domain.ErrCourseWritesForeign
		}
		if course == nil {
			// course.sport_association_id of None.
			return domain.ErrCourseWritesServer
		}
		if course.AssociationID == nil || *course.AssociationID != *association {
			return domain.ErrCourseWritesForeign
		}
	}
	if athlete {
		if subscription == nil || subscription.UserID == nil {
			return domain.ErrCourseWritesServer
		}
		if *subscription.UserID != identity.ScopeUser.ID {
			return domain.ErrCourseWritesForeign
		}
		association = subscription.AssociationID
	}
	if association == nil {
		// Unreachable with stored data: a subscription's association column is
		// NOT NULL and other callers were denied above.
		return domain.ErrCourseWritesNotPorted
	}
	audit, err := s.auditContext(ctx, tx, identity, request)
	if err != nil {
		return err
	}
	if raises {
		// The key tests, or data[key], of a body that is not an object.
		return domain.ErrCourseWritesServer
	}
	multi, hasMulti := data.Lookup("multi_payments")
	oneFee, hasOneFee := data.Lookup("one_fee_payment")
	switch {
	case hasMulti && dto.PythonTruthy(multi):
		err = s.toInstallments(ctx, tx, data, course, *row, subscription, *association, request.GroupHeader, audit)
	case hasOneFee && dto.PythonTruthy(oneFee):
		err = s.toOneFee(ctx, tx, course, *row, subscription, *association, request.GroupHeader, audit)
	}
	if err != nil {
		return err
	}
	return tx.Commit()
}

func (s *CourseWrites) overviewPayment(ctx context.Context, tx domain.CourseWritesTransaction, subscription *domain.CourseWriteSubscription,
	association, header string, amount string, created time.Time, description *string, meta any) (domain.CourseWritePayment, error) {
	group, err := s.createGroup(ctx, tx, header)
	if err != nil {
		return domain.CourseWritePayment{}, err
	}
	category, err := tx.DefaultPaymentCategory(ctx, &association)
	if err != nil {
		return domain.CourseWritePayment{}, err
	}
	var account *string
	if subscription.UserID != nil {
		if account, err = tx.DefaultBankAccount(ctx, &association); err != nil {
			return domain.CourseWritePayment{}, err
		}
	}
	text := "Quota Corso"
	if description != nil {
		text = *description
	} else if object, ok := meta.(domain.RequestObject); ok {
		if value, present := object.Lookup("description"); present {
			if value, ok := value.(string); ok {
				text = value
			}
		}
	}
	return domain.CourseWritePayment{ID: s.newID(), Amount: amount, MemoryAmount: amount, CreationDate: created, Description: text,
		CategoryID: category, UserID: subscription.UserID, AssociateID: subscription.AssociateID, AssociationID: &association,
		Meta: dto.CourseWritesJSONText(meta), AccountID: account, GroupID: group}, nil
}

func courseWritesLateOn(tx domain.CourseWritesTransaction, err error) error {
	if err == domain.ErrCourseWritesServer {
		return courseWritesLate(tx, err)
	}
	return err
}

// toInstallments is the multi_payments branch.
func (s *CourseWrites) toInstallments(ctx context.Context, tx domain.CourseWritesTransaction, data domain.RequestObject, course *domain.CourseWriteRow,
	row domain.CourseSubscriptionRow, subscription *domain.CourseWriteSubscription, association, header string,
	audit domain.CourseSubscriptionAudit) error {
	if row.MultiPayments {
		failed, err := courseWritesDropInstallments(ctx, tx, row.ID, audit)
		if err != nil {
			return err
		}
		if failed {
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
	} else {
		if row.PaymentID != nil {
			exists, paid, err := tx.PaymentPaid(ctx, *row.PaymentID)
			if err != nil {
				return err
			}
			if !exists {
				return courseWritesLate(tx, domain.ErrCourseWritesServer)
			}
			if !paid {
				if err = tx.DeletePayment(ctx, *row.PaymentID, audit); err != nil {
					return err
				}
			}
		}
		row.PaymentID, row.MultiPayments, row.OneFeePayment = nil, true, false
		if err := tx.SaveCourseSubscription(ctx, domain.CourseWriteSubscriptionSave{Row: row}, audit); err != nil {
			return err
		}
	}
	if course == nil {
		return courseWritesLate(tx, domain.ErrCourseWritesServer)
	}
	events, err := dto.CourseWritesStoredJSON(course.Events)
	if err != nil {
		return err
	}
	items := []any{}
	if dto.PythonTruthy(events) {
		var ok bool
		if items, ok = dto.PythonIterate(events); !ok {
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
	}
	created := row.CreationDate.UTC()
	created = time.Date(created.Year(), created.Month(), created.Day(), 0, 0, 0, 0, time.UTC)
	title := courseWritesText(course.Title)
	for index, item := range items {
		event, invalid, err := dto.ValidateCourseEvent(item, courseEventTaken(ctx, tx, header))
		if err != nil {
			return err
		}
		if invalid != nil {
			// Logged and skipped.
			continue
		}
		if event.Date.Before(created) {
			all, present := data.Lookup("all")
			if !present {
				// data['all']: KeyError.
				return courseWritesLate(tx, domain.ErrCourseWritesServer)
			}
			if all != true {
				continue
			}
		}
		if subscription == nil {
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
		text := "Rata n." + strconv.Itoa(index+1) + " del " + event.Date.Format("02/01/06") + " (" + title + ")"
		meta := domain.RequestObject{{Name: "description", Value: text}, {Name: "course_id", Value: course.ID},
			{Name: "course_title", Value: title}, {Name: "course_subscription_id", Value: row.ID},
			{Name: "payment_date", Value: event.Date.Format("2006-01-02")}}
		payment, err := s.overviewPayment(ctx, tx, subscription, association, header, event.Amount, event.Date, &text, meta)
		if err != nil {
			return courseWritesLateOn(tx, err)
		}
		if err = tx.CreatePayment(ctx, payment, audit); err != nil {
			return err
		}
		if err = tx.CreateInstallment(ctx, domain.CourseWriteInstallmentCreate{ID: s.newID(), CourseSubscription: row.ID, Number: event.ID,
			Amount: event.Amount, PaymentDate: event.Date, PaymentID: payment.ID, GroupID: payment.GroupID,
			AssociationID: course.AssociationID}, audit); err != nil {
			return err
		}
	}
	return nil
}

// toOneFee is the one_fee_payment branch (instalment rows only).
func (s *CourseWrites) toOneFee(ctx context.Context, tx domain.CourseWritesTransaction, course *domain.CourseWriteRow,
	row domain.CourseSubscriptionRow, subscription *domain.CourseWriteSubscription, association, header string,
	audit domain.CourseSubscriptionAudit) error {
	if !row.MultiPayments {
		return nil
	}
	failed, err := courseWritesDropInstallments(ctx, tx, row.ID, audit)
	if err != nil {
		return err
	}
	if failed {
		return courseWritesLate(tx, domain.ErrCourseWritesServer)
	}
	row.MultiPayments, row.OneFeePayment = false, true
	if course == nil || subscription == nil {
		return courseWritesLate(tx, domain.ErrCourseWritesServer)
	}
	title := courseWritesText(course.Title)
	meta := domain.RequestObject{{Name: "one_fee", Value: "true"}, {Name: "description", Value: "Pagamento in un'unica soluzione per " + title},
		{Name: "course_id", Value: course.ID}, {Name: "course_title", Value: title},
		{Name: "course_type", Value: strconv.Itoa(course.CourseType)},
		{Name: "payment_date", Value: row.CreationDate.UTC().Format("2006-01-02")}}
	payment, err := s.overviewPayment(ctx, tx, subscription, association, header, course.OneFee, s.now().UTC().Truncate(time.Microsecond), nil, meta)
	if err != nil {
		return courseWritesLateOn(tx, err)
	}
	if err = tx.CreatePayment(ctx, payment, audit); err != nil {
		return err
	}
	row.PaymentID = &payment.ID
	return tx.SaveCourseSubscription(ctx, domain.CourseWriteSubscriptionSave{Row: row}, audit)
}
