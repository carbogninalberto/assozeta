package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// paymentSaveDefaultsFake supplies only the reads Payment.save()'s category
// and description hooks make.
type paymentSaveDefaultsFake struct {
	domain.PaymentWritesTransaction
}

func (paymentSaveDefaultsFake) DefaultPaymentCategory(ctx context.Context, association *string) (*string, *string, error) {
	id, name := "category", "Categoria"
	return &id, &name, nil
}

func (paymentSaveDefaultsFake) CategoryName(ctx context.Context, id string) (*string, error) {
	name := "Categoria"
	return &name, nil
}

func (paymentSaveDefaultsFake) PaymentLinkedName(ctx context.Context, payment string) (domain.PaymentLinkedName, error) {
	return domain.PaymentLinkedName{}, nil
}

func descriptionValueText(value string) *string { return &value }

// paymentDescriptionValueCases are the stored meta replacement of a 'Quota
// Corso' description, the text the first save writes, the text the next save
// of the same instance writes (nil: NULL) and the AttributeError that stops it
// (empty: it completes).
var paymentDescriptionValueCases = []struct {
	name    string
	meta    string
	first   *string
	text    *string
	memory  bool
	failure string
}{
	{"list", `{"description": ["a"]}`, descriptionValueText("['a']"), descriptionValueText("['a']"), true,
		"'list' object has no attribute 'lower'"},
	{"int", `{"description": 5}`, descriptionValueText("5"), descriptionValueText("5"), true, "'int' object has no attribute 'lower'"},
	{"int zero", `{"description": 0}`, descriptionValueText("0"), descriptionValueText("0"), true, ""},
	{"null", `{"description": null}`, nil, descriptionValueText("Categoria"), false, ""},
	{"string", `{"description": "Danza"}`, descriptionValueText("Danza"), descriptionValueText("Danza"), false, ""},
}

func TestPaymentWritesDescriptionValueSecondSave(t *testing.T) {
	// payment_add saves the created payment twice (serializer.save's
	// Payment.objects.create, then the view's save after the user, association
	// and party): both saves are of the same instance, so the second one starts
	// from the value the first hook left on its description attribute.
	for _, tc := range paymentDescriptionValueCases {
		t.Run(tc.name, func(t *testing.T) {
			writes, tx := &PaymentWrites{}, paymentSaveDefaultsFake{}
			m := &paymentMemory{row: domain.PaymentWriteRow{ID: "payment", Subject: 0,
				Description: descriptionValueText("Quota Corso"), Meta: descriptionValueText(tc.meta)}, jsonAudit: map[string]string{}}
			if _, err := writes.defaults(context.Background(), tx, m); err != nil {
				t.Fatalf("first save: %v", err)
			}
			if !samePointer(m.row.Description, tc.first) {
				t.Fatalf("the first save stored %v, want %v", m.row.Description, tc.first)
			}
			if got := m.descriptionValue != nil; got != tc.memory {
				t.Fatalf("the instance kept %#v", m.descriptionValue)
			}
			_, err := writes.defaults(context.Background(), tx, m)
			if tc.failure != "" {
				// A truthy non-string fails on .lower() before the write: the
				// column keeps the first save's text and the view answers 500.
				if !errors.Is(err, domain.ErrPaymentWritesServer) {
					t.Fatalf("the second save returned %v, want the 500 page", err)
				}
				if !samePointer(m.row.Description, tc.first) {
					t.Fatalf("the failed save wrote %v", m.row.Description)
				}
				return
			}
			if err != nil {
				t.Fatalf("second save: %v", err)
			}
			if !samePointer(m.row.Description, tc.text) {
				t.Fatalf("the second save stored %v, want %v", m.row.Description, tc.text)
			}
		})
	}
}

func TestPaymentPaidFlowDescriptionValueSecondSave(t *testing.T) {
	// mark_payment_as_paid selects the payment once: the update_fields save
	// runs the hook but writes paid, payment_date and type only, and the later
	// full save (after the invoice) starts from the instance's description.
	for _, tc := range paymentDescriptionValueCases {
		t.Run(tc.name, func(t *testing.T) {
			tx := paymentSaveDefaultsFake{}
			stored := domain.PaymentSaveRow{ID: "payment", AssociationID: "association", Subject: 0,
				CategoryID: descriptionValueText("category"), CategoryName: descriptionValueText("Categoria"),
				Description: descriptionValueText("Quota Corso"), Meta: descriptionValueText(tc.meta)}
			// payment.save(update_fields=['paid', 'payment_date', 'type']).
			instance := stored
			if err := paymentSaveDefaults(context.Background(), tx, &instance, map[string][2]string{}); err != nil {
				t.Fatalf("update_fields save: %v", err)
			}
			if !samePointer(instance.Description, tc.first) {
				t.Fatalf("the instance holds %v, want %v", instance.Description, tc.first)
			}
			if got := instance.DescriptionValue != nil; got != tc.memory {
				t.Fatalf("the instance kept %#v", instance.DescriptionValue)
			}
			// The full save of the same instance: the row read for it is the
			// stored one, and the instance's values come first.
			row := domain.PaymentStateRow{PaymentSaveRow: stored}
			paymentPaidFlowInstance(&row, &instance)
			err := paymentSaveDefaults(context.Background(), tx, &row.PaymentSaveRow, map[string][2]string{})
			if tc.failure != "" {
				// The AttributeError rolls the whole mark_payment_as_paid block
				// back: no invoice, no paid flag, no audit entry.
				if !errors.Is(err, ErrPaymentSaveFailed) {
					t.Fatalf("the full save returned %v, want the save failure", err)
				}
				var description *domain.PaymentDescriptionError
				if !errors.As(err, &description) || description.Message != tc.failure {
					t.Fatalf("the full save failed with %v, want %q", err, tc.failure)
				}
				if !samePointer(row.Description, tc.first) {
					t.Fatalf("the failed save wrote %v", row.Description)
				}
				return
			}
			if err != nil {
				t.Fatalf("full save: %v", err)
			}
			if !samePointer(row.Description, tc.text) {
				t.Fatalf("the full save stored %v, want %v", row.Description, tc.text)
			}
		})
	}
}

// paymentPaidFlowDescriptionFake is mark_payment_as_paid's transaction for the
// full save: the stored row PaymentState reads, the hook's reads and the rest
// of the invoice flow (the audit entry's changes are kept).
type paymentPaidFlowDescriptionFake struct {
	domain.PaymentPaidFlowTransaction
	stored      domain.PaymentStateRow
	invoice     bool
	saved       bool
	description *string
	changes     map[string][2]string
}

func (f *paymentPaidFlowDescriptionFake) PaymentState(ctx context.Context, query domain.PaymentStateQuery) (*domain.PaymentStateRow, error) {
	row := f.stored
	return &row, nil
}

func (f *paymentPaidFlowDescriptionFake) DefaultPaymentCategory(ctx context.Context, association *string) (*string, *string, error) {
	return descriptionValueText("category"), descriptionValueText("Categoria"), nil
}

func (f *paymentPaidFlowDescriptionFake) CategoryName(ctx context.Context, id string) (*string, error) {
	return descriptionValueText("Categoria"), nil
}

func (f *paymentPaidFlowDescriptionFake) PaymentLinkedName(ctx context.Context, payment string) (domain.PaymentLinkedName, error) {
	return domain.PaymentLinkedName{}, nil
}

func (f *paymentPaidFlowDescriptionFake) InvoiceGeneratePayment(ctx context.Context, id string) (domain.InvoiceGeneratePayment, error) {
	f.invoice = true
	return domain.InvoiceGeneratePayment{Amount: "23.00"}, nil
}

func (f *paymentPaidFlowDescriptionFake) PaidFlowOwner(ctx context.Context, association string) (string, domain.InvoiceGenerateOwner, error) {
	return "owner", domain.InvoiceGenerateOwner{StartDay: 1, StartMonth: 1}, nil
}

func (f *paymentPaidFlowDescriptionFake) MaxInvoiceNumberCreated(ctx context.Context, association string, group *string, from, to time.Time) (*int64, error) {
	return nil, nil
}

func (f *paymentPaidFlowDescriptionFake) AssociationDenomination(ctx context.Context, association string) (*string, error) {
	return descriptionValueText("Associazione"), nil
}

func (f *paymentPaidFlowDescriptionFake) AssociateNames(ctx context.Context, associate string) (first, last *string, err error) {
	return descriptionValueText("Mario"), descriptionValueText("Rossi"), nil
}

func (f *paymentPaidFlowDescriptionFake) CreateInvoice(ctx context.Context, create domain.InvoiceGenerateCreate,
	overrides map[string]string, audit domain.LoginAuditContext, at time.Time) error {
	return nil
}

func (f *paymentPaidFlowDescriptionFake) SavePaidFlowPayment(ctx context.Context, save domain.PaymentPaidFlowSave) error {
	f.saved = true
	f.description = save.Description
	return nil
}

func (f *paymentPaidFlowDescriptionFake) AppendPaymentAudit(ctx context.Context, event domain.PaymentArchiveEvent) error {
	f.changes = event.Changes
	return nil
}

func (f *paymentPaidFlowDescriptionFake) PaidFlowPeople(ctx context.Context, payment string) (domain.PaymentPaidFlowPeople, error) {
	return domain.PaymentPaidFlowPeople{UserFound: true, UserID: descriptionValueText("user")}, nil
}

func (f *paymentPaidFlowDescriptionFake) ValidateBroadcast(ctx context.Context, user, name string, audit domain.LoginAuditContext,
	at time.Time) (bool, error) {
	return false, nil
}

// The full save stripePaidFlow runs starts from the instance the update_fields
// save left: a truthy non-string meta replacement stops it with Django's
// AttributeError before the invoice, and a falsy or string one completes the
// save, whose audit entry records the description the update_fields save did
// not write.
func TestPaymentPaidFlowDescriptionValueFullSave(t *testing.T) {
	for _, tc := range paymentDescriptionValueCases {
		t.Run(tc.name, func(t *testing.T) {
			fake := &paymentPaidFlowDescriptionFake{stored: domain.PaymentStateRow{PaymentSaveRow: domain.PaymentSaveRow{ID: "payment",
				AssociationID: "association", Subject: 0, CategoryID: descriptionValueText("category"),
				CategoryName: descriptionValueText("Categoria"), Description: descriptionValueText("Quota Corso"),
				Meta: descriptionValueText(tc.meta)}}}
			instance := fake.stored.PaymentSaveRow
			if err := paymentSaveDefaults(context.Background(), fake, &instance, map[string][2]string{}); err != nil {
				t.Fatalf("update_fields save: %v", err)
			}
			stripe := &StripeRoutes{now: func() time.Time { return time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC) }}
			_, err := stripe.stripePaidFlow(context.Background(), fake, domain.ResolvedIdentity{}, StripeRequest{}, "payment",
				time.Now(), false, &instance)
			if tc.failure != "" {
				var description *domain.PaymentDescriptionError
				if !errors.Is(err, ErrPaymentSaveFailed) || !errors.As(err, &description) || description.Message != tc.failure {
					t.Fatalf("the full save returned %v, want %q", err, tc.failure)
				}
				if fake.invoice || fake.saved || fake.changes != nil {
					t.Fatalf("the block ran after the failed save: invoice %v saved %v changes %v", fake.invoice, fake.saved, fake.changes)
				}
				return
			}
			if err != nil {
				t.Fatalf("full save: %v", err)
			}
			if !fake.saved || fake.changes == nil {
				t.Fatalf("the flow did not save: %+v", fake)
			}
			if !samePointer(fake.description, tc.text) {
				t.Fatalf("the full save wrote %v, want %v", fake.description, tc.text)
			}
			// The update_fields save wrote paid, payment_date and type only: the
			// entry records the description against the stored row.
			if got := fake.changes["description"]; got != [2]string{"Quota Corso", *tc.text} {
				t.Fatalf("the audit entry's description is %v", got)
			}
			if _, ok := fake.changes["invoice"]; !ok {
				t.Fatalf("the audit entry misses the invoice: %v", fake.changes)
			}
		})
	}
}
