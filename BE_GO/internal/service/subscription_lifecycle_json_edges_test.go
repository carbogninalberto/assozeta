package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// lifecycleEdgesTx is subscription_archive's transaction: a subscription
// without its own payment and two course subscriptions, whose payments carry
// the stored columns. Every save is recorded in order.
type lifecycleEdgesTx struct {
	domain.SubscriptionLifecycleTransaction
	payments map[string]domain.SubscriptionLifecyclePayment
	order    []string
	ranged   []string
	events   *[]string
	audits   *[]domain.PaymentArchiveEvent
}

func (f *lifecycleEdgesTx) log(event string) { *f.events = append(*f.events, event) }

func (f *lifecycleEdgesTx) LifecycleAssociation(context.Context, string, *string) (string, error) {
	return "assoc", nil
}

func (f *lifecycleEdgesTx) ActorAssociation(context.Context, string) (*string, error) {
	return lifecycleEdgesText("assoc"), nil
}

func lifecycleEdgesText(value string) *string { return &value }

func (f *lifecycleEdgesTx) Subscription(context.Context, string, *string, *string) (*domain.SubscriptionLifecycleRow, error) {
	start, end := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC), time.Date(2027, 8, 31, 0, 0, 0, 0, time.UTC)
	return &domain.SubscriptionLifecycleRow{ID: "subscription", AssociationID: "assoc", Start: &start, End: &end,
		Created: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), StartDay: 1, StartMonth: 1, EndDay: 31, EndMonth: 8}, nil
}

func (f *lifecycleEdgesTx) LiveCourseSubscriptions(context.Context, string, *string) ([]domain.CourseSubscriptionRow, error) {
	rows := make([]domain.CourseSubscriptionRow, 0, len(f.order))
	for _, payment := range f.order {
		rows = append(rows, domain.CourseSubscriptionRow{ID: "course-" + payment, PaymentID: lifecycleEdgesText(payment),
			CourseAssociationID: lifecycleEdgesText("assoc")})
	}
	return rows, nil
}

func (f *lifecycleEdgesTx) LiveInstallments(context.Context, string, *string) ([]domain.SubscriptionLifecycleInstallment, error) {
	return nil, nil
}

func (f *lifecycleEdgesTx) Payment(_ context.Context, id string) (*domain.SubscriptionLifecyclePayment, error) {
	payment, ok := f.payments[id]
	if !ok {
		return nil, nil
	}
	return &payment, nil
}

func (f *lifecycleEdgesTx) PaymentSaves() domain.PaymentArchiveTransaction {
	return &lifecycleEdgesSaves{tx: f}
}

func (f *lifecycleEdgesTx) SaveSubscription(context.Context, domain.SubscriptionLifecycleSave, domain.CourseSubscriptionAudit) error {
	f.log("subscription-save")
	return nil
}

func (f *lifecycleEdgesTx) SaveCourseSubscription(context.Context, domain.CourseSubscriptionSave, domain.CourseSubscriptionAudit) error {
	f.log("course-save")
	return nil
}

func (f *lifecycleEdgesTx) RangePayments(context.Context, string, *string, time.Time, time.Time, *string) ([]string, error) {
	return f.ranged, nil
}

func (f *lifecycleEdgesTx) Commit() error {
	f.log("commit")
	return nil
}

func (f *lifecycleEdgesTx) Rollback() error {
	f.log("rollback")
	return nil
}

// lifecycleEdgesSaves is the payment save side of the same transaction.
type lifecycleEdgesSaves struct {
	domain.PaymentArchiveTransaction
	tx *lifecycleEdgesTx
}

func (s *lifecycleEdgesSaves) DefaultPaymentCategory(context.Context, *string) (*string, *string, error) {
	return lifecycleEdgesText("category"), lifecycleEdgesText("Categoria"), nil
}

func (s *lifecycleEdgesSaves) PaymentLinkedName(context.Context, string) (domain.PaymentLinkedName, error) {
	return domain.PaymentLinkedName{}, nil
}

func (s *lifecycleEdgesSaves) SavePaymentArchive(context.Context, string, bool, *string, *string) error {
	s.tx.log("payment-save")
	return nil
}

func (s *lifecycleEdgesSaves) AppendPaymentAudit(_ context.Context, event domain.PaymentArchiveEvent) error {
	*s.tx.audits = append(*s.tx.audits, event)
	s.tx.log("payment-audit")
	return nil
}

type lifecycleEdgesStore struct {
	domain.SubscriptionLifecycleStore
	tx *lifecycleEdgesTx
}

func (s lifecycleEdgesStore) BeginSubscriptionLifecycle(context.Context) (domain.SubscriptionLifecycleTransaction, error) {
	return s.tx, nil
}

func lifecycleEdgesArchivePayment(id string, course *string) domain.SubscriptionLifecyclePayment {
	return domain.SubscriptionLifecyclePayment{PaymentSaveRow: domain.PaymentSaveRow{ID: id, AssociationID: "assoc", Subject: 1,
		CategoryID: lifecycleEdgesText("category"), CategoryName: lifecycleEdgesText("Quote"), Description: lifecycleEdgesText("Iscrizione"),
		Course: course}}
}

func lifecycleEdgesArchive(t *testing.T, tx *lifecycleEdgesTx) (error, []string, []domain.PaymentArchiveEvent) {
	t.Helper()
	events := []string{}
	var audits []domain.PaymentArchiveEvent
	tx.events, tx.audits = &events, &audits
	service := NewSubscriptionLifecycles(lifecycleEdgesStore{tx: tx},
		func() time.Time { return time.Date(2026, 11, 9, 10, 0, 0, 0, time.UTC) })
	owner := &domain.Account{ID: "owner"}
	err := service.Archive(context.Background(), domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner},
		"a1000000-0000-4000-8000-000000995010", "", domain.LoginAuditContext{})
	return err, events, audits
}

// subscription_archive's measured edges for the payments its course
// subscriptions hold: past Index the entry's audit index resolves through the
// resolver's fallbacks, and past Save (the same level as Load) the load of the
// row raises after the earlier rows' saves, which the non-atomic view keeps.
func TestSubscriptionArchiveStoredJSONBands(t *testing.T) {
	for _, test := range []struct {
		name      string
		depth     int
		recursion bool
		fallback  bool
		audits    int
	}{
		{name: "index at", depth: 9927, audits: 2},
		{name: "index past", depth: 9928, fallback: true, audits: 2},
		{name: "save at", depth: 9935, fallback: true, audits: 2},
		{name: "save and load past", depth: 9936, recursion: true, audits: 1},
	} {
		t.Run(test.name, func(t *testing.T) {
			tx := &lifecycleEdgesTx{order: []string{"shallow", "deep"}, payments: map[string]domain.SubscriptionLifecyclePayment{
				"shallow": lifecycleEdgesArchivePayment("shallow", nil),
				"deep":    lifecycleEdgesArchivePayment("deep", jsonEdgesArray(test.depth))}}
			err, events, audits := lifecycleEdgesArchive(t, tx)
			if test.recursion {
				var recursion *PaymentStoredJSONRecursionError
				if !errors.As(err, &recursion) {
					t.Fatalf("err=%v, want the recursion error", err)
				}
				if recursion.AfterWrites {
					t.Fatal("the row's own load raised, no write of it stays")
				}
				if recursion.Text != recursionArrayText {
					t.Fatalf("Text=%q", recursion.Text)
				}
				// The request is not atomic: the earlier rows' saves stay.
				if !containsEvent(events, "commit") || !containsEvent(events, "payment-save") {
					t.Fatalf("events %v: the earlier save was not kept", events)
				}
				if len(audits) != test.audits || audits[0].IndexFallback {
					t.Fatalf("audits %+v", audits)
				}
				return
			}
			if err != nil {
				t.Fatalf("err=%v", err)
			}
			if !containsEvent(events, "commit") {
				t.Fatalf("events %v: the save was not committed", events)
			}
			if len(audits) != test.audits {
				t.Fatalf("audits %+v", audits)
			}
			if audits[0].IndexFallback {
				t.Fatal("the shallow row's entry fell back")
			}
			if audits[1].IndexFallback != test.fallback {
				t.Fatalf("deep row fallback=%v want %v", audits[1].IndexFallback, test.fallback)
			}
		})
	}
}

// The unpaid payments of the subscription period are loaded by an iterator
// whose stack parses two levels deeper: a value between the two loads completes
// here and raises in the auditlog refetch, which keeps the earlier saves.
func TestSubscriptionArchiveRangePaymentStoredJSONBands(t *testing.T) {
	for _, test := range []struct {
		name      string
		depth     int
		recursion bool
		after     bool
		fallback  bool
	}{
		{name: "save at", depth: 9935, fallback: true},
		{name: "save past", depth: 9936, recursion: true, after: true},
		{name: "load at", depth: 9941, recursion: true, after: true},
		{name: "load past", depth: 9942, recursion: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			tx := &lifecycleEdgesTx{ranged: []string{"range"}, payments: map[string]domain.SubscriptionLifecyclePayment{
				"range": lifecycleEdgesArchivePayment("range", jsonEdgesArray(test.depth))}}
			err, events, audits := lifecycleEdgesArchive(t, tx)
			if !test.recursion {
				if err != nil {
					t.Fatalf("err=%v", err)
				}
				if len(audits) != 1 || audits[0].IndexFallback != test.fallback {
					t.Fatalf("audits %+v: fallback want %v", audits, test.fallback)
				}
				if !containsEvent(events, "commit") {
					t.Fatalf("events %v: the save was not committed", events)
				}
				return
			}
			var recursion *PaymentStoredJSONRecursionError
			if !errors.As(err, &recursion) {
				t.Fatalf("err=%v, want the recursion error", err)
			}
			if recursion.AfterWrites != test.after {
				t.Fatalf("AfterWrites=%v want %v", recursion.AfterWrites, test.after)
			}
			// The subscription is saved before the period's payments are read:
			// the non-atomic view keeps it.
			if !containsEvent(events, "commit") || !containsEvent(events, "subscription-save") {
				t.Fatalf("events %v: the earlier save was not kept", events)
			}
			if len(audits) != 0 {
				t.Fatalf("audits %+v on a failed save", audits)
			}
		})
	}
}
