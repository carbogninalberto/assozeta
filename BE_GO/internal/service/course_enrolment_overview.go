package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// enrolmentLate commits the work Django has already saved (no atomic block)
// before answering a late view error (500 or the event serializer's 400).
func enrolmentLate(tx domain.CourseEnrolmentTransaction, err error) error {
	var invalid *CourseWritesEventInvalid
	if !errors.Is(err, courseEnrolmentServer) && !errors.As(err, &invalid) {
		return err
	}
	if commit := tx.Commit(); commit != nil {
		return commit
	}
	return err
}

// overviewQuote is add_course_to_subscription's multiple_quote extraction:
// the stored quote whose quote_id equals the selection's value, else the
// raw selection; exceptions inside the try leave None.
func overviewQuote(stored *string, selected any) (any, error) {
	object, ok := selected.(domain.RequestObject)
	if !ok {
		return nil, ErrCourseEnrolmentNotPorted
	}
	value, present := object.Lookup("value")
	text, isText := value.(string)
	if !present || !isText {
		return nil, ErrCourseEnrolmentNotPorted
	}
	quotes, err := dto.CourseWritesStoredJSON(stored)
	if err != nil {
		return nil, err
	}
	items, iterable := dto.PythonIterate(quotes)
	if quotes == nil || !iterable {
		return nil, nil
	}
	for _, item := range items {
		quote, ok := item.(domain.RequestObject)
		if !ok {
			return nil, nil
		}
		id, present := quote.Lookup("quote_id")
		if !present {
			return nil, nil
		}
		idText, ok := id.(string)
		if !ok {
			return nil, ErrCourseEnrolmentNotPorted
		}
		if idText == text {
			return quote, nil
		}
	}
	return selected, nil
}

// enrolmentAthleteOwns is the athlete check of course_overview_add and
// course_installment_make_payment: the subscription of the caller's user.
func enrolmentAthleteOwns(identity domain.ResolvedIdentity, user *string) bool {
	return user != nil && *user == identity.ScopeUser.ID
}

// OverviewAdd follows course_overview_add and add_course_to_subscription.
func (s *CourseEnrolment) OverviewAdd(ctx context.Context, identity domain.ResolvedIdentity, rawCourse, rawSubscription string,
	request CourseEnrolmentRequest) (map[string]any, error) {
	courseID, err := paymentInfoParseUUID(rawCourse)
	if err != nil {
		return nil, err
	}
	subscriptionID, err := paymentInfoParseUUID(rawSubscription)
	if err != nil {
		return nil, err
	}
	data := domain.RequestObject{}
	if request.Body != nil {
		object, ok := request.Body.(domain.RequestObject)
		if !ok || request.Form {
			// Lists, scalars and HTML form input are not ported.
			return nil, ErrCourseEnrolmentNotPorted
		}
		data = object
	}
	if err = canonicalGroup(request.GroupHeader); err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	group := supplierGroup(request.GroupHeader)
	own, err := tx.OwnerAssociationFirst(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	if own != nil && !courseEnrolmentScoped(identity, own) {
		own = nil
	}
	course, err := tx.Course(ctx, courseID.String(), group)
	if err != nil {
		return nil, err
	}
	if course != nil && !courseEnrolmentScoped(identity, course.AssociationID) {
		course = nil
	}
	subscription, err := tx.Subscription(ctx, subscriptionID.String(), group)
	if err != nil {
		return nil, err
	}
	if subscription != nil && !courseEnrolmentScoped(identity, subscription.AssociationID) {
		subscription = nil
	}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		if subscription == nil {
			return nil, courseEnrolmentServer
		}
		if !enrolmentAthleteOwns(identity, subscription.UserID) {
			return nil, domain.ErrCourseWritesForeign
		}
	} else {
		if own == nil {
			return nil, domain.ErrCourseWritesForeign
		}
		if course == nil || course.AssociationID == nil {
			return nil, courseEnrolmentServer
		}
		if *course.AssociationID != *own {
			return nil, domain.ErrCourseWritesForeign
		}
	}
	if subscription == nil || subscription.AssociationID == nil || course == nil {
		return nil, courseEnrolmentServer
	}
	audit, err := s.auditContext(ctx, tx, identity, request)
	if err != nil {
		return nil, err
	}
	enrolled, err := s.addCourseToSubscription(ctx, tx, *course, *subscription, *subscription.AssociationID, data,
		identity.ScopeUser.Role == domain.RoleAthlete, request, audit, false)
	if err != nil {
		return nil, err
	}
	if enrolled == nil {
		// The view returns {'data': <Response>}, which the renderer cannot encode.
		return nil, courseEnrolmentServer
	}
	created, payment, dated := enrolled.created, enrolled.payment, enrolled.dated
	render, err := tx.CourseRender(ctx, course.ID)
	if err != nil {
		return nil, err
	}
	if render == nil {
		return nil, courseEnrolmentServer
	}
	row := created.row
	var paymentID any
	if row.PaymentID != nil {
		paymentID = *row.PaymentID
	}
	quoteJSON := courseOverviewJSON(row.MultipleQuote)
	item := map[string]any{"course_subscription_id": row.ID, "course": courseSerializerData(*render), "paid": false,
		"creation_date": dto.DRFUTCDateTime(row.CreationDate), "subscription": subscription.ID, "payment": paymentID,
		"multi_payments": row.MultiPayments, "one_fee_payment": false, "archived": false, "type": 1, "multiple_quote": quoteJSON,
		"billed_frequency": 1, "billed_from": nil, "billed_until": nil, "billed_from_day_of_month": 1, "membership_active": true,
		"membership_fee": "0.00", "auto_renewal": false, "deleted": false, "membership_payments": []any{}}
	var paymentJSON any
	if payment != nil {
		created := dto.DRFUTCDateTime(payment.CreationDate)
		if dated {
			created = payment.CreationDate.Format("2006-01-02")
		}
		paymentJSON = map[string]any{"payment_id": payment.ID, "amount": amountJSON(payment.MemoryAmount), "paid": false, "creation_date": created}
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"data": map[string]any{"msg": "associate added.", "payment": paymentJSON, "course_subscription": item}}, nil
}

// courseEnrolled is one add_course_to_subscription call: the created course
// subscription and its last payment (dated: created on an event date).
type courseEnrolled struct {
	created *enrolmentRow
	payment *domain.CourseEnrolmentPayment
	dated   bool
}

// addCourseToSubscription is add_course_to_subscription after the callers'
// lookups, on the caller's transaction: the quote of a quote course, the
// course subscription, its payments and installments. nil without an error
// means the subscription is already in the course (nothing is written). An
// error after the first write is wrapped by enrolmentLate (the work so far is
// committed) unless the caller is atomic and rolls back.
func (s *CourseEnrolment) addCourseToSubscription(ctx context.Context, tx domain.CourseEnrolmentTransaction, course domain.CourseWriteRow,
	subscription domain.CourseWriteSubscription, association string, data domain.RequestObject, athlete bool, request CourseEnrolmentRequest,
	audit domain.CourseSubscriptionAudit, atomic bool) (*courseEnrolled, error) {
	var err error
	var quote any
	if course.CourseType == 2 {
		if selected, present := data.Lookup("multiple_quote"); present {
			if quote, err = overviewQuote(course.MultipleQuotes, selected); err != nil {
				return nil, err
			}
		}
	}
	exists, err := tx.LiveRow(ctx, course.ID, subscription.ID)
	if err != nil {
		return nil, err
	}
	if exists {
		return nil, nil
	}
	if athlete && course.CourseType == courseMembershipType {
		// The athlete membership prefill (naive strptime dates) is not ported.
		return nil, ErrCourseEnrolmentNotPorted
	}
	oneFeeFlag, _ := data.Lookup("one_fee_payment")
	oneFeePositive, _ := decimalPositive(&course.OneFee)
	oneFee := dto.PythonTruthy(oneFeeFlag) && oneFeePositive
	quoteBranch := !oneFee && course.CourseType == 2 && quote != nil
	var quoteObject domain.RequestObject
	var quoteStored, quoteMemory string
	var quoteErr error
	if quoteBranch {
		var ok bool
		if quoteObject, ok = quote.(domain.RequestObject); ok {
			if selected, present := quoteObject.Lookup("payment_category"); present {
				if _, ok := selected.(domain.RequestObject); !ok {
					return nil, ErrCourseEnrolmentNotPorted
				}
			}
			if raw, present := quoteObject.Lookup("amount"); present {
				quoteStored, quoteMemory, quoteErr = quoteAmount(raw)
				if errors.Is(quoteErr, ErrCourseEnrolmentNotPorted) {
					return nil, quoteErr
				}
			} else {
				quoteErr = courseEnrolmentServer
			}
		} else {
			quoteErr = courseEnrolmentServer
		}
	}
	zero, subscriptionID := "0.00", subscription.ID
	created := &enrolmentRow{row: domain.CourseSubscriptionRow{ID: s.newID(), CourseID: course.ID, CreationDate: s.moment(),
		SubscriptionID: &subscriptionID, Type: 1, MultipleQuote: enrolmentJSON(quote), BilledFrequency: 1, BilledFromDayOfMonth: 1,
		MembershipActive: true, MembershipFee: &zero, CourseAssociationID: course.AssociationID, CourseTitle: course.Title,
		CourseType: course.CourseType}, memory: dto.CourseSubscriptionMemory{Times: map[string]bool{}, QuoteSet: true, Quote: quote},
		floatFee: true, subscription: subscription}
	if err = tx.CreateRow(ctx, created.row, map[string][2]string{"membership_fee": {"None", "0.0"}},
		enrolmentRepr(created.row, created.memory, true), audit); err != nil {
		return nil, err
	}
	payment, dated, err := s.overviewPayments(ctx, tx, created, course, data, association, quoteObject, quoteBranch, oneFee, quoteStored,
		quoteMemory, quoteErr, request, audit)
	if err != nil {
		if atomic {
			return nil, err
		}
		return nil, enrolmentLate(tx, err)
	}
	if payment != nil {
		// payment.amount > 0 compares the in-memory value (a quote's float).
		if positive, _ := decimalPositive(&payment.MemoryAmount); positive {
			created.row.PaymentID = &payment.ID
			if err = tx.SaveRow(ctx, created.save(), audit); err != nil {
				return nil, err
			}
		}
	}
	return &courseEnrolled{created: created, payment: payment, dated: dated}, nil
}

// overviewPayments creates add_course_to_subscription's payments; the last
// one is returned (dated: its creation_date is an event date).
func (s *CourseEnrolment) overviewPayments(ctx context.Context, tx domain.CourseEnrolmentTransaction, created *enrolmentRow, course domain.CourseWriteRow,
	data domain.RequestObject, association string, quote domain.RequestObject, quoteBranch, oneFee bool, quoteStored, quoteMemory string,
	quoteErr error, request CourseEnrolmentRequest, audit domain.CourseSubscriptionAudit) (*domain.CourseEnrolmentPayment, bool, error) {
	category, err := tx.CoursesCategory(ctx, association)
	if err != nil {
		return nil, false, err
	}
	subscription := created.subscription
	create := func(amount, memory string, when time.Time, meta any, category *string) (*domain.CourseEnrolmentPayment, error) {
		courseJSON, description, err := courseLabel(titleValue(course.Title), course.ID)
		if err != nil {
			return nil, err
		}
		payment, err := s.payment(ctx, tx, request.GroupHeader, subscription.UserID, &association, amount, memory, when, description, category, meta,
			courseJSON)
		if err != nil {
			return nil, err
		}
		payment.AssociateID, payment.SecondSave = subscription.AssociateID, true
		return &payment, tx.CreatePayment(ctx, payment, audit)
	}
	switch {
	case oneFee:
		payment, err := create(course.OneFee, course.OneFee, s.moment(), domain.RequestObject{{Name: "one_fee", Value: "true"}}, category)
		return payment, false, err
	case quoteBranch:
		if quoteErr != nil {
			return nil, false, quoteErr
		}
		if selected, present := quote.Lookup("payment_category"); present {
			if value, present := selected.(domain.RequestObject).Lookup("value"); present {
				if text, ok := value.(string); !ok {
					return nil, false, ErrCourseEnrolmentNotPorted
				} else if id, err := paymentInfoParseUUID(text); err == nil {
					if category, err = tx.CategoryFirst(ctx, id.String()); err != nil {
						return nil, false, err
					}
				}
			}
		}
		payment, err := create(quoteStored, quoteMemory, s.moment(), domain.RequestObject{{Name: "multiple_quote", Value: quote}}, category)
		return payment, false, err
	case course.MultiPayments:
		created.row.MultiPayments = true
		if err := tx.SaveRow(ctx, created.save(), audit); err != nil {
			return nil, false, err
		}
		stored, err := dto.CourseWritesStoredJSON(course.Events)
		if err != nil {
			return nil, false, err
		}
		events, ok := dto.PythonIterate(stored)
		if stored == nil || !ok {
			return nil, false, courseEnrolmentServer
		}
		day := created.row.CreationDate.UTC()
		day = time.Date(day.Year(), day.Month(), day.Day(), 0, 0, 0, 0, time.UTC)
		var last *domain.CourseEnrolmentPayment
		for _, item := range events {
			event, invalid, err := dto.ValidateCourseEvent(item, courseEventTaken(ctx, tx, request.GroupHeader))
			if err != nil {
				return nil, false, err
			}
			if invalid != nil {
				return nil, false, &CourseWritesEventInvalid{Fields: invalid}
			}
			if event.Date.Before(day) {
				user, err := tx.AssociationUser(ctx, association)
				if err != nil || user == nil {
					return nil, false, errors.Join(err, courseEnrolmentServer)
				}
				plan, err := tx.UserFullInstallmentsPlan(ctx, *user)
				if err != nil {
					return nil, false, err
				}
				if !plan {
					continue
				}
			}
			payment, err := create(event.Amount, event.Amount, event.Date, nil, category)
			if err != nil {
				return nil, false, err
			}
			if err = tx.CreateInstallment(ctx, domain.CourseWriteInstallmentCreate{ID: s.newID(), CourseSubscription: created.row.ID,
				Number: event.ID, Amount: event.Amount, PaymentDate: event.Date, PaymentID: payment.ID, GroupID: payment.GroupID,
				AssociationID: course.AssociationID}, audit); err != nil {
				return nil, false, err
			}
			last = payment
		}
		return last, last != nil, nil
	}
	positive, valid := decimalPositive(course.Fee)
	if !valid {
		return nil, false, courseEnrolmentServer
	}
	if !positive {
		return nil, false, nil
	}
	payment, err := create(*course.Fee, *course.Fee, s.moment(), nil, category)
	return payment, false, err
}

// MakePayment follows course_installment_make_payment.
func (s *CourseEnrolment) MakePayment(ctx context.Context, identity domain.ResolvedIdentity, raw string, request CourseEnrolmentRequest) (map[string]any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	if err = canonicalGroup(request.GroupHeader); err != nil {
		return nil, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	group := supplierGroup(request.GroupHeader)
	installment, err := tx.Installment(ctx, id.String(), group)
	if err != nil {
		return nil, err
	}
	if installment != nil && !courseEnrolmentScoped(identity, installment.SubscriptionAssociation, installment.CourseAssociation) {
		installment = nil
	}
	if installment == nil {
		// raise Exception('Installment not found.')
		return nil, courseEnrolmentServer
	}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		if installment.SubscriptionID == nil || !enrolmentAthleteOwns(identity, installment.SubscriptionUserID) {
			return nil, domain.ErrCourseWritesForeign
		}
	} else {
		own, err := tx.OwnerAssociationFirst(ctx, identity.ScopeUser.ID)
		if err != nil {
			return nil, err
		}
		if own == nil || !courseEnrolmentScoped(identity, own) || installment.CourseAssociation == nil || *installment.CourseAssociation != *own {
			return nil, domain.ErrCourseWritesForeign
		}
	}
	if installment.SubscriptionID == nil {
		// subscription.user on None.
		return nil, courseEnrolmentServer
	}
	audit, err := s.auditContext(ctx, tx, identity, request)
	if err != nil {
		return nil, err
	}
	payment, err := s.payment(ctx, tx, request.GroupHeader, installment.SubscriptionUserID, installment.CourseAssociation, installment.Amount,
		installment.Amount, installment.PaymentDate, "Quota Corso", nil, nil, nil)
	if err != nil {
		return nil, err
	}
	payment.AssociateID, payment.SecondSave = installment.AssociateID, true
	if err = tx.CreatePayment(ctx, payment, audit); err != nil {
		return nil, err
	}
	if err = tx.LinkInstallment(ctx, *installment, payment.ID, audit); err != nil {
		return nil, err
	}
	rendered, err := tx.Payment(ctx, payment.ID, group)
	if err != nil {
		return nil, err
	}
	if rendered.InstructorID != nil || rendered.InvoiceID != nil {
		return nil, ErrCourseEnrolmentNotPorted
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"data": dto.CourseEnrolmentPaymentItem(rendered)}, nil
}
