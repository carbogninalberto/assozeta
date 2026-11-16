package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// errEventDropsNoPayment is Payment's DoesNotExist: installment.payment reads a
// row through the base manager, so a deleted payment raises.
var errEventDropsNoPayment = errors.New("Payment.DoesNotExist")

// eventDropsFake is the write side of the dropped installments: the payments
// and the installments' payment references it holds, the calls in order and the
// saves it received. DeleteInstallment applies the installment's pre_delete (an
// unpaid payment goes with it, its installments with the payment).
type eventDropsFake struct {
	paid     map[string]bool
	states   map[string]domain.CourseSubscriptionPaymentState
	payments map[string]string // installment id -> payment id
	deleted  map[string]bool
	calls    []string
	saves    []domain.CourseSubscriptionPaymentSave
}

func (f *eventDropsFake) PaymentPaid(_ context.Context, payment string) (bool, error) {
	f.calls = append(f.calls, "payment:"+payment)
	if _, present := f.states[payment]; !present {
		return false, errEventDropsNoPayment
	}
	return f.paid[payment], nil
}

func (f *eventDropsFake) PaymentState(_ context.Context, payment string) (domain.CourseSubscriptionPaymentState, error) {
	state, present := f.states[payment]
	if !present {
		return domain.CourseSubscriptionPaymentState{}, errEventDropsNoPayment
	}
	return state, nil
}

func (f *eventDropsFake) DefaultPaymentCategory(_ context.Context, _ *string) (*string, error) {
	f.calls = append(f.calls, "category")
	return nil, nil
}

func (f *eventDropsFake) SavePayment(_ context.Context, save domain.CourseSubscriptionPaymentSave, _ domain.CourseSubscriptionAudit) error {
	f.calls = append(f.calls, "save:"+save.ID)
	f.saves = append(f.saves, save)
	state := f.states[save.ID]
	description, meta := save.Description, save.Meta
	state.Description, state.Meta = &description, &meta
	f.states[save.ID] = state
	return nil
}

func (f *eventDropsFake) DeleteInstallment(_ context.Context, id string, _ domain.CourseSubscriptionAudit) error {
	f.calls = append(f.calls, "delete:"+id)
	f.deleted[id] = true
	if payment, present := f.payments[id]; present && !f.paid[payment] {
		// Payment.delete(): the payment goes with its live installments.
		delete(f.states, payment)
		for installment, reference := range f.payments {
			if reference == payment {
				f.deleted[installment] = true
			}
		}
	}
	return nil
}

func eventText(value string) *string { return &value }

// sharedPaymentDrops is one course subscription whose two installments share a
// payment (paid or not), dropped in the queryset's order.
func sharedPaymentDrops(paid bool) (*eventDropsFake, domain.CourseSubscriptionRow, []domain.CourseSubscriptionEventInstallment) {
	payment, association, category := "pay", "association", "category"
	fake := &eventDropsFake{paid: map[string]bool{payment: paid}, deleted: map[string]bool{},
		states: map[string]domain.CourseSubscriptionPaymentState{payment: {Amount: "40.00", Description: eventText("Rata condivisa"),
			AssociationID: &association, CategoryID: &category}},
		payments: map[string]string{"ev0": payment, "ev1": payment}}
	row := domain.CourseSubscriptionRow{ID: "cs", CourseID: "course", CourseTitle: eventText("Corsa")}
	drop := []domain.CourseSubscriptionEventInstallment{
		{ID: "ev0", Number: 0, PaymentID: &payment, PaymentDate: time.Date(2026, 10, 1, 22, 30, 0, 0, time.UTC)},
		{ID: "ev1", Number: 1, PaymentID: &payment, PaymentDate: time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)},
	}
	return fake, row, drop
}

func eventDropsMeta(description, date string) string {
	row := domain.CourseSubscriptionRow{ID: "cs", CourseID: "course"}
	return string(dto.PythonJSONDumpsSorted(courseSubscriptionEventMeta(row, "Corsa", description, date)))
}

// TestCourseSubscriptionEventDropsSharedPaidPayment is Django's loop for two
// installments sharing a paid payment: each reads the payment fresh, relabels
// and saves it (the second sees the first save), then deletes its installment;
// the paid payment stays, with the second label.
func TestCourseSubscriptionEventDropsSharedPaidPayment(t *testing.T) {
	fake, row, drop := sharedPaymentDrops(true)
	if err := (&CourseSubscriptions{}).dropInstallments(context.Background(), fake, row, drop, domain.CourseSubscriptionAudit{}); err != nil {
		t.Fatalf("dropInstallments: %v", err)
	}
	want := []string{"payment:pay", "save:pay", "delete:ev0", "payment:pay", "save:pay", "delete:ev1"}
	if len(fake.calls) != len(want) {
		t.Fatalf("calls %v, want %v", fake.calls, want)
	}
	for index := range want {
		if fake.calls[index] != want[index] {
			t.Fatalf("calls %v, want %v", fake.calls, want)
		}
	}
	if len(fake.saves) != 2 {
		t.Fatalf("%d saves, want 2", len(fake.saves))
	}
	first, second := "Rata n.1 del 01/10/26 (Corsa)", "Rata n.2 del 01/11/26 (Corsa)"
	if fake.saves[0].Description != first || fake.saves[0].Changes["description"] != [2]string{"Rata condivisa", first} {
		t.Errorf("first save %q %v", fake.saves[0].Description, fake.saves[0].Changes)
	}
	if got := fake.saves[0].Changes["meta"]; got[0] != "null" || got[1] != eventDropsMeta(first, "2026-10-01 22:30:00+00:00") {
		t.Errorf("first meta change %v", got)
	}
	if fake.saves[1].Description != second || fake.saves[1].Changes["description"] != [2]string{first, second} {
		t.Errorf("second save %q %v", fake.saves[1].Description, fake.saves[1].Changes)
	}
	if got := fake.saves[1].Changes["meta"]; got[0] != eventDropsMeta(first, "2026-10-01 22:30:00+00:00") ||
		got[1] != eventDropsMeta(second, "2026-11-01 00:00:00+00:00") {
		t.Errorf("second meta change %v", got)
	}
	if description := fake.states["pay"].Description; description == nil || *description != second {
		t.Errorf("stored description %v, want %q", description, second)
	}
	if !fake.deleted["ev0"] || !fake.deleted["ev1"] {
		t.Errorf("deleted %v, want both installments", fake.deleted)
	}
}

// TestCourseSubscriptionEventDropsSharedUnpaidPaymentStops is the same pair
// with an unpaid payment: the first delete takes the payment with it, so the
// second installment's payment read raises (Django's DoesNotExist, the 500 page
// with the atomic update rolled back).
func TestCourseSubscriptionEventDropsSharedUnpaidPaymentStops(t *testing.T) {
	fake, row, drop := sharedPaymentDrops(false)
	err := (&CourseSubscriptions{}).dropInstallments(context.Background(), fake, row, drop, domain.CourseSubscriptionAudit{})
	if !errors.Is(err, errEventDropsNoPayment) {
		t.Fatalf("dropInstallments: %v, want the missing payment", err)
	}
	want := []string{"payment:pay", "delete:ev0", "payment:pay"}
	if len(fake.calls) != len(want) {
		t.Fatalf("calls %v, want %v", fake.calls, want)
	}
	for index := range want {
		if fake.calls[index] != want[index] {
			t.Fatalf("calls %v, want %v", fake.calls, want)
		}
	}
	if len(fake.saves) != 0 {
		t.Errorf("%d saves, want none for an unpaid payment", len(fake.saves))
	}
}
