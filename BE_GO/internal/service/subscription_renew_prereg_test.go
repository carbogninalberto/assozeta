package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// A preregistration that names the subscription it renews (preregistration true
// with a subscription_id) replaces the person, certificate and data from that
// subscription: subscription/renew and the associates-draft approve run the
// input refusal after the fill, as subscription/add does. These tests pin that
// the pre-fill refusal no longer answers it, and that the fill's own 400 does.

const preregRenewAssociation = "a1000000-0000-4000-8000-000000958001"
const preregRenewSource = "a1000000-0000-4000-8000-000000958002"

// nonASCIIExpiration is a certificate date strptime accepts beyond ASCII: the
// input refusal refuses it before anything is read.
const nonASCIIExpiration = "٢٠٢٧-٠٣-٣١"

// preregRenewTransaction is the transaction of a renewal preregistration: only
// the reads before the fill's missing source are exercised, so the remaining
// methods stay unimplemented (a call on them panics, failing the test).
type preregRenewTransaction struct {
	domain.SubscriptionRenewTransaction
	associationCount int
	rollbacks        int
	settings         int
	sources          int
}

func (t *preregRenewTransaction) AssociationCount(context.Context, string) (int, error) {
	return t.associationCount, nil
}

func (t *preregRenewTransaction) SetGroup(*string) {}

func (t *preregRenewTransaction) Rollback() error {
	t.rollbacks++
	return nil
}

func (t *preregRenewTransaction) Settings(context.Context, string) (*domain.SubscriptionCreateSettings, int, error) {
	t.settings++
	return &domain.SubscriptionCreateSettings{AssociationID: preregRenewAssociation, OwnerID: preregRenewAssociation}, 1, nil
}

func (t *preregRenewTransaction) RenewalSource(context.Context, string, string) (*domain.SubscriptionRenewalSource, error) {
	t.sources++
	return nil, nil
}

type preregRenewStore struct{ tx *preregRenewTransaction }

func (s preregRenewStore) BeginSubscriptionRenew(context.Context) (domain.SubscriptionRenewTransaction, error) {
	return s.tx, nil
}

func preregRenewIdentity() domain.ResolvedIdentity {
	actor := &domain.Account{ID: preregRenewAssociation, Email: "prereg@example.test", Role: domain.RoleAssociation}
	return domain.ResolvedIdentity{Actor: actor, Effective: actor, ScopeUser: actor}
}

func newPreregRenewals(tx *preregRenewTransaction) *SubscriptionRenewals {
	now := func() time.Time { return time.Date(2026, time.November, 12, 9, 0, 0, 0, time.UTC) }
	return NewSubscriptionRenewals(preregRenewStore{tx: tx}, NewSubscriptionCreates(nil, nil, nil, now, nil))
}

func preregRenewBody(subscriptionID any) domain.RequestObject {
	body := domain.RequestObject{
		{Name: "preregistration", Value: true},
		{Name: "associate_data", Value: domain.RequestObject{{Name: "type", Value: 1}, {Name: "born_date", Value: "01/01/1990"}}},
		{Name: "new_user_account", Value: domain.RequestObject{{Name: "new_member", Value: false}}},
		{Name: "signature", Value: domain.RequestObject{{Name: "there_is_signature", Value: false}, {Name: "data", Value: ""}}},
		{Name: "medical_certificate", Value: domain.RequestObject{{Name: "medical_id", Value: nil},
			{Name: "certificate_expring_date", Value: nonASCIIExpiration}}},
	}
	if subscriptionID != nil {
		body = append(body, domain.RequestField{Name: "subscription_id", Value: subscriptionID})
	}
	return body
}

// subscriptionInvalidText is the APIException's {"msg": ..., "code": "400"} body.
func subscriptionInvalidText(t *testing.T, err error) map[string]string {
	t.Helper()
	var invalid *domain.SubscriptionCreateInvalid
	if !errors.As(err, &invalid) || invalid.Detail == nil {
		t.Fatalf("want the renewal source's APIException, got %v", err)
	}
	return invalid.Detail
}

func TestRenewPreregistrationRenewalReachesTheFill(t *testing.T) {
	// The subscription named by the body is not in the association: the fill
	// answers 'Iscrizione da rinnovare non trovata.' (an APIException, not the
	// view's {"status": "error"} body) instead of the pre-fill refusal.
	tx := &preregRenewTransaction{associationCount: 1}
	_, err := newPreregRenewals(tx).Renew(context.Background(), preregRenewIdentity(),
		SubscriptionCreateRequest{Body: preregRenewBody(preregRenewSource)})
	detail := subscriptionInvalidText(t, err)
	if detail["msg"] != "Iscrizione da rinnovare non trovata." || detail["code"] != "400" {
		t.Fatalf("unexpected body: %v", detail)
	}
	if tx.settings != 1 || tx.sources != 1 {
		t.Fatalf("the fill read settings %d times and sources %d times", tx.settings, tx.sources)
	}
	if tx.rollbacks != 1 {
		t.Fatalf("transaction rolled back %d times", tx.rollbacks)
	}
}

func TestRenewPreregistrationWithoutSubscriptionStillRefusesItsInputs(t *testing.T) {
	// Without a subscription_id the body is a plain preregistration: the
	// certificate date is refused before anything is read.
	tx := &preregRenewTransaction{associationCount: 1}
	_, err := newPreregRenewals(tx).Renew(context.Background(), preregRenewIdentity(),
		SubscriptionCreateRequest{Body: preregRenewBody(nil)})
	if !errors.Is(err, domain.ErrSubscriptionCreateNotPorted) {
		t.Fatalf("want the pre-fill refusal, got %v", err)
	}
	if tx.settings != 0 || tx.sources != 0 {
		t.Fatalf("the fill ran: settings %d, sources %d", tx.settings, tx.sources)
	}
}

// preregApproveTransaction is the associates-draft approve transaction of one
// renewal draft: the draft's create_subscription reaches the fill, whose missing
// source answers the APIException. The remaining methods are unimplemented.
type preregApproveTransaction struct {
	domain.SubscriptionDraftApproveTransaction
	association string
	draft       domain.SubscriptionDraftApproveRow
	approved    int
	statuses    int
	rollbacks   int
	settings    int
	sources     int
}

func (t *preregApproveTransaction) SetGroup(*string) {}

func (t *preregApproveTransaction) Rollback() error {
	t.rollbacks++
	return nil
}

func (t *preregApproveTransaction) AssociationCount(context.Context, string) (int, error) {
	return 1, nil
}

func (t *preregApproveTransaction) ActiveAssociationOf(context.Context, string) (*string, error) {
	return &t.association, nil
}

func (t *preregApproveTransaction) DraftCount(context.Context, string, bool, []string) (int, error) {
	return 1, nil
}

func (t *preregApproveTransaction) ValidDrafts(context.Context, string, bool, []string) ([]domain.SubscriptionDraftApproveRow, error) {
	return []domain.SubscriptionDraftApproveRow{t.draft}, nil
}

func (t *preregApproveTransaction) DeleteApprovingStatuses(context.Context, string, domain.LoginAuditContext, time.Time) error {
	t.statuses++
	return nil
}

func (t *preregApproveTransaction) CreateApprovingStatus(context.Context, string, domain.LoginAuditContext, time.Time) error {
	t.statuses++
	return nil
}

func (t *preregApproveTransaction) Savepoint(context.Context) error { return nil }

func (t *preregApproveTransaction) RollbackSavepoint(context.Context) error { return nil }

func (t *preregApproveTransaction) Commit() error { return nil }

func (t *preregApproveTransaction) Settings(context.Context, string) (*domain.SubscriptionCreateSettings, int, error) {
	t.settings++
	return &domain.SubscriptionCreateSettings{AssociationID: preregRenewAssociation, OwnerID: preregRenewAssociation}, 1, nil
}

func (t *preregApproveTransaction) RenewalSource(context.Context, string, string) (*domain.SubscriptionRenewalSource, error) {
	t.sources++
	return nil, nil
}

type preregApproveStore struct{ tx *preregApproveTransaction }

func (s preregApproveStore) BeginSubscriptionDraftApprove(context.Context) (domain.SubscriptionDraftApproveTransaction, error) {
	return s.tx, nil
}

func TestApproveDraftPreregistrationRenewalReachesTheFill(t *testing.T) {
	// The draft's data renews a subscription (preregistration true with a
	// subscription_id): the renaming keeps both keys, the pre-fill refusal
	// skips them and create_subscription fills. Its missing source is an
	// APIException inside the draft's own transaction, which the view swallows:
	// the draft is kept and the answer counts it as not approved ("0 su 1").
	data := `{"associate": {"born_date": "01/01/1990", "first_name": "Pietro", "last_name": "Prenotato", "sex": "F"},`
	data += `"associate_tutor": null, "preregistration": true, "subscription_id": "` + preregRenewSource + `",`
	data += `"medical_certificate": {"certificate_expring_date": "` + nonASCIIExpiration + `"}}`
	tx := &preregApproveTransaction{association: preregRenewAssociation,
		draft: domain.SubscriptionDraftApproveRow{ID: preregRenewSource, Data: &data}}
	now := func() time.Time { return time.Date(2026, time.November, 12, 9, 0, 0, 0, time.UTC) }
	approvals := NewSubscriptionDraftApprovals(preregApproveStore{tx: tx}, NewSubscriptionCreates(nil, nil, nil, now, nil))
	body := domain.RequestObject{
		{Name: "associate_import_draft_ids", Value: []any{preregRenewSource}},
		{Name: "subscription_details", Value: domain.RequestObject{{Name: "type", Value: 1}}},
	}
	result, err := approvals.Approve(context.Background(), preregRenewIdentity(), SubscriptionCreateRequest{Body: body})
	if err != nil {
		t.Fatalf("want the draft kept, got %v", err)
	}
	if result.Approved != 0 || result.Total != 1 {
		t.Fatalf("unexpected result: %d of %d approved", result.Approved, result.Total)
	}
	if tx.settings != 1 || tx.sources != 1 {
		t.Fatalf("the fill read settings %d times and sources %d times", tx.settings, tx.sources)
	}
	if tx.statuses != 3 {
		t.Fatalf("the APPROVING status rows were written %d times", tx.statuses)
	}
}
