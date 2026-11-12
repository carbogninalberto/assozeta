package service

import (
	"context"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// paymentAthleteRenderer is DocumentRender: document_invoice called in-process.
type paymentAthleteRenderer interface {
	Render(ctx context.Context, identity domain.ResolvedIdentity, request DocumentRenderRequest) (*DocumentRenderReply, error)
}

// paymentReadsAthlete is what payment_list's athlete branch needs beyond the shared reads.
type paymentReadsAthlete struct {
	store   domain.PaymentAthleteStore
	stripe  subscriptionReadsStripe
	renders paymentAthleteRenderer
}

// WithAthleteList enables payment_list's athlete branch: its querysets,
// stripe_direct_credentials_configured() and the document_invoice view.
func (s *PaymentReads) WithAthleteList(store domain.PaymentAthleteStore, stripe subscriptionReadsStripe,
	renders paymentAthleteRenderer) *PaymentReads {
	s.athletes = &paymentReadsAthlete{store: store, stripe: stripe, renders: renders}
	return s
}

// paymentAthleteShowZero is ...sport_association.user.show_zero_payments,
// read only when the amount is not above zero (None raises AttributeError).
func paymentAthleteShowZero(positive bool, showZero *bool) (bool, error) {
	if positive {
		return true, nil
	}
	if showZero == nil {
		return false, domain.ErrPaymentReadsServer
	}
	return *showZero, nil
}

// courseSubscriptionPayments is the course_subscription_id branch: the course
// subscription must be the athlete's or an associate's (otherwise the 403 of a
// resource that is not available); the
// payments of the course subscription's installments (multi_payments) or its
// own payment, then those of the carnet holdings linked to it, each when its
// amount is above zero or the association owner shows zero payments.
func (a *paymentReadsAthlete) courseSubscriptionPayments(ctx context.Context, id string, scope domain.PaymentAthleteScope) ([]string, error) {
	subscription, err := a.store.CourseSubscription(ctx, id, scope)
	if err != nil {
		return nil, err
	}
	if subscription == nil {
		return nil, &domain.PermissionDenied{Detail: unavailableResource}
	}
	ids := []string{}
	missing := false
	if subscription.MultiPayments {
		installments, err := a.store.InstallmentCandidates(ctx, subscription.ID, scope)
		if err != nil {
			return nil, err
		}
		for _, installment := range installments {
			shown, err := paymentAthleteShowZero(installment.Positive != nil && *installment.Positive, subscription.ShowZero)
			if err != nil {
				return nil, err
			}
			if !shown {
				continue
			}
			if installment.PaymentID == nil {
				// The None payment fails when the ids are collected, after the holdings.
				missing = true
				continue
			}
			ids = append(ids, *installment.PaymentID)
		}
	} else if subscription.PaymentID != nil {
		shown, err := paymentAthleteShowZero(subscription.PaymentPositive != nil && *subscription.PaymentPositive, subscription.ShowZero)
		if err != nil {
			return nil, err
		}
		if shown {
			ids = append(ids, *subscription.PaymentID)
		}
	}
	holdings, err := a.store.CarnetCandidates(ctx, subscription.ID, scope)
	if err != nil {
		return nil, err
	}
	for _, holding := range holdings {
		if holding.PaymentID == nil {
			continue
		}
		shown, err := paymentAthleteShowZero(holding.Positive != nil && *holding.Positive, holding.ShowZero)
		if err != nil {
			return nil, err
		}
		if shown {
			ids = append(ids, *holding.PaymentID)
		}
	}
	if missing {
		return nil, domain.ErrPaymentReadsServer
	}
	return ids, nil
}

// excluded is the exclusion pass over the athlete's subscriptions: unpaid
// installments due more than 31 days ahead, and zero payments of installments
// and course subscriptions unless the association owner shows them.
func (a *paymentReadsAthlete) excluded(ctx context.Context, scope domain.PaymentAthleteScope, today time.Time) (map[string]bool, error) {
	rows, err := a.store.Exclusions(ctx, scope, today)
	if err != nil {
		return nil, err
	}
	excluded := map[string]bool{}
	for _, row := range rows {
		switch {
		case row.Multi:
			if !row.HasInstallment {
				continue
			}
			if row.Late != nil && *row.Late {
				if row.InstallmentPayment != nil {
					excluded[*row.InstallmentPayment] = true
				}
				continue
			}
			if row.InstallmentPayment == nil || row.InstallmentZero == nil {
				return nil, domain.ErrPaymentReadsServer // None.amount
			}
			if *row.InstallmentZero && !row.ShowZero {
				excluded[*row.InstallmentPayment] = true
			}
		case row.CoursePayment != nil && row.CourseZero != nil && *row.CourseZero && !row.ShowZero:
			excluded[*row.CoursePayment] = true
		}
	}
	return excluded, nil
}

// athleteList follows payment_list for an athlete (no mode=compressed).
func (s *PaymentReads) athleteList(ctx context.Context, request PaymentReadsRequest) (any, error) {
	a := s.athletes
	if a == nil {
		return nil, domain.ErrPaymentReadsNotPorted
	}
	identity := request.Identity
	scope := domain.PaymentAthleteScope{Association: paymentReadsScope(identity), Person: identity.ScopeUser.ID,
		Group: supplierGroup(request.GroupHeader)}
	now := s.now().UTC()
	var rows []domain.PaymentAthleteRow
	excluded := map[string]bool{}
	if raw, present := paymentReadsParam(request.Query, "course_subscription_id"); present {
		// An empty value skips is_valid_uuid and fails the UUID lookup.
		id, err := paymentReadsUUID(raw)
		if err != nil {
			return nil, err
		}
		ids, err := a.courseSubscriptionPayments(ctx, id, scope)
		if err != nil {
			return nil, err
		}
		if rows, err = a.store.ByIDs(ctx, ids, scope); err != nil {
			return nil, err
		}
	} else {
		var err error
		if excluded, err = a.excluded(ctx, scope, now); err != nil {
			return nil, err
		}
		if rows, err = a.store.Listed(ctx, scope, now.Add(60*24*time.Hour)); err != nil {
			return nil, err
		}
	}
	// document_invoice(request, invoice_id) for each paid payment whose invoice
	// has no PDF: the view's response is ignored and the rows keep the invoice
	// as loaded above.
	for _, row := range rows {
		if !row.Paid || row.InvoiceID == nil || row.InvoiceDocument != nil {
			continue
		}
		if _, err := a.renders.Render(ctx, identity, DocumentRenderRequest{Route: RenderInvoice, UID: *row.InvoiceID, Query: request.Query,
			Authorization: request.Authorization, Group: request.GroupHeader,
			Body: func() (any, bool, error) { return nil, false, nil }, Audit: request.Audit}); err != nil {
			var denied *domain.PermissionDenied
			if errors.As(err, &denied) {
				continue // the view's 403 response is ignored like its other answers
			}
			return nil, err
		}
	}
	kept := make([]domain.PaymentAthleteRow, 0, len(rows))
	ids := make([]string, 0, len(rows))
	for _, row := range rows {
		if !excluded[row.ID] {
			kept = append(kept, row)
			ids = append(ids, row.ID)
		}
	}
	installments, err := a.store.Installments(ctx, ids, scope)
	if err != nil {
		return nil, err
	}
	byPayment := map[string]domain.PaymentAthleteInstallment{}
	for _, installment := range installments {
		byPayment[installment.PaymentID] = installment
	}
	var configured *bool
	data := domain.RequestObject{}
	for index, row := range kept {
		meta, err := a.store.Meta(ctx, row.ID, scope.Group)
		if err != nil {
			return nil, err
		}
		item := dto.PaymentAthleteItem(row, meta)
		if row.AssociationID == nil || row.OwnerOnline == nil {
			return nil, domain.ErrPaymentReadsServer // None.user
		}
		// online_payments_available: the owner's flag, then the Stripe credentials.
		online := false
		if *row.OwnerOnline {
			if configured == nil {
				value, err := a.stripe.DirectCredentialsConfigured(ctx)
				if err != nil {
					return nil, err
				}
				configured = &value
			}
			online = *configured
		}
		oneSolution := false
		if installment, found := byPayment[row.ID]; online && found && installment.OneFee &&
			installment.PaymentPaid != nil && !*installment.PaymentPaid {
			solution, ok := dto.PaymentAthleteOneSolution(installment.Events, installment.PaymentDate)
			if !ok {
				return nil, domain.ErrPaymentReadsServer
			}
			oneSolution = solution
		}
		item["payment_method"] = row.Type
		item["online_pay_available"] = online
		item["sport_association_denomination"] = dto.PaymentAthleteOptional(row.Denomination)
		item["one_solution_available"] = oneSolution
		if row.Paid && row.InvoiceID != nil {
			item["document_pdf"] = dto.PaymentAthleteOptional(row.InvoiceDocument)
		}
		data = append(data, domain.RequestField{Name: strconv.Itoa(index), Value: item})
	}
	return domain.RequestObject{{Name: "data", Value: data}, {Name: "meta", Value: domain.RequestObject{}}}, nil
}
