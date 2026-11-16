package service

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// paymentReadsStore is the PaymentReadsStore of the unit tests: each method
// reports what the view/builders hand it.
type paymentReadsStore struct {
	userAssociation   *string
	scopedAssociation *string
	compressed        []domain.PaymentReadsCompressed
	compressedFilter  *domain.PaymentReadsFilter
	count             int64
	paymentIDs        []string
	paymentsFilter    *domain.PaymentReadsFilter
	renders           map[string]domain.PaymentReadsRender
	subscription      *domain.PaymentReadsSubscription
}

var _ domain.PaymentReadsStore = (*paymentReadsStore)(nil)

func (s *paymentReadsStore) UserAssociation(context.Context, string) (*string, error) {
	return s.userAssociation, nil
}

func (s *paymentReadsStore) ScopedAssociation(context.Context, string, *string) (*string, error) {
	return s.scopedAssociation, nil
}

func (s *paymentReadsStore) StripeInstanceSettings(context.Context) (bool, error) { return false, nil }

func (s *paymentReadsStore) Compressed(_ context.Context, filter domain.PaymentReadsFilter) ([]domain.PaymentReadsCompressed, error) {
	s.compressedFilter = &filter
	return s.compressed, nil
}

func (s *paymentReadsStore) CountPayments(_ context.Context, filter domain.PaymentReadsFilter) (int64, error) {
	s.paymentsFilter = &filter
	return s.count, nil
}

func (s *paymentReadsStore) Payments(_ context.Context, filter domain.PaymentReadsFilter, _, _ int64) ([]string, error) {
	s.paymentsFilter = &filter
	return s.paymentIDs, nil
}

func (s *paymentReadsStore) Render(context.Context, []string, *string) (map[string]domain.PaymentReadsRender, error) {
	return s.renders, nil
}

func (s *paymentReadsStore) Hours(context.Context, []string) ([]domain.PaymentReadsHours, error) {
	return nil, nil
}

func (s *paymentReadsStore) Totals(context.Context, domain.PaymentReadsFilter) (domain.PaymentReadsTotals, error) {
	return domain.PaymentReadsTotals{}, nil
}

func (s *paymentReadsStore) AccountChanges(context.Context, domain.PaymentReadsFilter) ([]domain.PaymentReadsAccountChange, error) {
	return nil, nil
}

func (s *paymentReadsStore) SubscriptionOf(context.Context, string, *string, *string) (*domain.PaymentReadsSubscription, error) {
	return s.subscription, nil
}

func (s *paymentReadsStore) Link(context.Context, string, *string, *string) (domain.PaymentReadsLink, error) {
	return domain.PaymentReadsLink{}, nil
}

func paymentReadsString(value string) *string { return &value }

func paymentReadsTestNow() time.Time { return time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC) }

func paymentReadsQuery(values ...string) domain.RequestObject {
	query := domain.RequestObject{}
	for i := 0; i+1 < len(values); i += 2 {
		query = append(query, domain.RequestField{Name: values[i], Value: values[i+1]})
	}
	return query
}

func paymentReadsAthleteIdentity(scope string) domain.ResolvedIdentity {
	return domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: "athlete", Role: domain.RoleAthlete}, ImpersonationAssociationID: scope}
}

// TestPaymentReadsCompressedAthleteOwner is site 1: an athlete who owns a
// SportAssociation (an association import can leave one) lists its payments
// through the same lookup as an association user.
func TestPaymentReadsCompressedAthleteOwner(t *testing.T) {
	owned := "10000000-0000-4000-8000-000000000001"
	store := &paymentReadsStore{userAssociation: &owned,
		compressed: []domain.PaymentReadsCompressed{{ID: "20000000-0000-4000-8000-000000000002", Amount: "1.00"}}}
	flow := NewPaymentReads(store, paymentReadsTestNow, false)
	result, err := flow.List(context.Background(), PaymentReadsRequest{Identity: paymentReadsAthleteIdentity(""),
		Query: paymentReadsQuery("mode", "compressed")})
	if err != nil {
		t.Fatal(err)
	}
	if store.compressedFilter == nil || store.compressedFilter.AssociationID != owned || store.compressedFilter.Scope != nil {
		t.Fatalf("filter = %#v", store.compressedFilter)
	}
	data, _ := result.(domain.RequestObject).Lookup("data")
	if rows, ok := data.([]any); !ok || len(rows) != 1 {
		t.Fatalf("data = %#v", data)
	}

	// An association-scoped session keeps its association filter (scoped_queryset) even though the athlete owns
	// this association; the two conditions cannot both hold for a session of another association.
	store = &paymentReadsStore{userAssociation: &owned, compressed: []domain.PaymentReadsCompressed{}}
	flow = NewPaymentReads(store, paymentReadsTestNow, false)
	if _, err := flow.List(context.Background(), PaymentReadsRequest{Identity: paymentReadsAthleteIdentity("30000000-0000-4000-8000-000000000003"),
		Query: paymentReadsQuery("mode", "compressed")}); err != nil {
		t.Fatal(err)
	}
	if store.compressedFilter == nil || store.compressedFilter.Scope == nil ||
		*store.compressedFilter.Scope != "30000000-0000-4000-8000-000000000003" {
		t.Fatalf("scoped filter = %#v", store.compressedFilter)
	}
}

// TestPaymentReadsCompressedAthleteWithoutAssociation is site 1's 500 page:
// without an owned association both lookups miss.
func TestPaymentReadsCompressedAthleteWithoutAssociation(t *testing.T) {
	store := &paymentReadsStore{}
	flow := NewPaymentReads(store, paymentReadsTestNow, false)
	_, err := flow.List(context.Background(), PaymentReadsRequest{Identity: paymentReadsAthleteIdentity(""),
		Query: paymentReadsQuery("mode", "compressed")})
	if !errors.Is(err, domain.ErrPaymentReadsServer) {
		t.Fatalf("err = %v", err)
	}
}

// TestPaymentReadsRandomSearchRepeatsJoinRows is site 2 at the service level:
// the store's page can repeat an id (Django's DISTINCT ..., RANDOM() keeps the
// join rows), so its row is rendered again while the meta keeps the count of
// distinct payments.
func TestPaymentReadsRandomSearchRepeatsJoinRows(t *testing.T) {
	id := "20000000-0000-4000-8000-000000000002"
	store := &paymentReadsStore{userAssociation: paymentReadsString("10000000-0000-4000-8000-000000000001"), count: 2,
		paymentIDs: []string{id, id}, renders: map[string]domain.PaymentReadsRender{id: {}}}
	flow := NewPaymentReads(store, paymentReadsTestNow, false)
	result, err := flow.List(context.Background(), PaymentReadsRequest{Identity: domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: "owner", Role: domain.RoleAssociation}},
		Query: paymentReadsQuery("query[generalSearch]", "duevolte", "sort[field]", "?")})
	if err != nil {
		t.Fatal(err)
	}
	if store.paymentsFilter == nil || !store.paymentsFilter.Random || store.paymentsFilter.Smart == nil {
		t.Fatalf("filter = %#v", store.paymentsFilter)
	}
	object := result.(domain.RequestObject)
	meta, _ := object.Lookup("meta")
	total, _ := meta.(domain.RequestObject).Lookup("total")
	if total != json.Number("2") {
		t.Fatalf("total = %#v", total)
	}
	rows, _ := meta.(domain.RequestObject).Lookup("rowIds")
	if ids, ok := rows.([]any); !ok || len(ids) != 2 || ids[0] != id || ids[1] != id {
		t.Fatalf("rowIds = %#v", rows)
	}
	data, _ := object.Lookup("data")
	if items, ok := data.([]any); !ok || len(items) != 2 {
		t.Fatalf("data = %#v", data)
	}
}

// TestPaymentReadsFullNameOrder is site 3: full_name is an annotation of
// subscription_payments only with a text search; without it the ordering is
// unknown (FieldError).
func TestPaymentReadsFullNameOrder(t *testing.T) {
	text := "rossi"
	filter := domain.PaymentReadsFilter{MemberText: &text}
	if err := paymentReadsOrder(&filter, "full_name", "", map[string]bool{"full_name": true}); err != nil {
		t.Fatal(err)
	}
	if len(filter.Order) != 1 || filter.Order[0].Column != "full_name" || filter.Order[0].Descending {
		t.Fatalf("order = %#v", filter.Order)
	}
	filter = domain.PaymentReadsFilter{MemberText: &text}
	if err := paymentReadsOrder(&filter, "full_name", "desc", map[string]bool{"full_name": true}); err != nil {
		t.Fatal(err)
	}
	if len(filter.Order) != 1 || !filter.Order[0].Descending {
		t.Fatalf("desc order = %#v", filter.Order)
	}
	filter = domain.PaymentReadsFilter{}
	if err := paymentReadsOrder(&filter, "full_name", "", map[string]bool{"full_name": false}); !errors.Is(err, domain.ErrPaymentReadsServer) {
		t.Fatalf("err = %v", err)
	}
}

// TestPaymentExportPythonStrNonFinite is site 4: json.loads reads a number
// with a fraction as float; csv writes str(float).
func TestPaymentExportPythonStrNonFinite(t *testing.T) {
	huge := "1" + strings.Repeat("0", 400) + ".5"
	tiny := "0." + strings.Repeat("0", 400) + "1"
	for _, test := range []struct {
		name string
		text string
		want string
	}{
		{"overflow", huge, "inf"},
		{"negative overflow", "-" + huge, "-inf"},
		{"underflow", tiny, "0.0"},
		{"negative underflow", "-" + tiny, "-0.0"},
	} {
		got, err := paymentExportPythonStr(json.Number(test.text))
		if err != nil || got != test.want {
			t.Errorf("%s: %q, %v; want %q", test.name, got, err, test.want)
		}
	}
	if got, err := paymentExportPythonStr(json.Number("1.5")); err != nil || got != "1.5" {
		t.Errorf("finite: %q, %v", got, err)
	}
	if got, err := paymentExportPythonStr(json.Number("7")); err != nil || got != "7" {
		t.Errorf("integer: %q, %v", got, err)
	}
	// The CSV's course column decodes the stored jsonb text with json's decoder.
	for _, test := range []struct {
		name string
		raw  string
		want string
	}{
		{"course overflow", huge, "inf"},
		{"course underflow", tiny, "0.0"},
	} {
		text := `{"label": ` + test.raw + `}`
		if got, err := paymentExportCourseLabel(&text); err != nil || got != test.want {
			t.Errorf("%s: %q, %v; want %q", test.name, got, err, test.want)
		}
	}
}
