package service

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/channels"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// paidFlowRows is validate_broadcast: the first notification of a user creates
// the broadcast and their subscription.
type paidFlowRows struct{ subscriptions int }

func (r *paidFlowRows) ValidateBroadcast(ctx context.Context, user, name string, audit domain.LoginAuditContext,
	at time.Time) (bool, error) {
	r.subscriptions++
	return r.subscriptions == 1, nil
}

// paidFlowRedis is the notification store: SaveNotifications writes the JSON
// text Notifications reads back, the way Redis DB 0 holds it.
type paidFlowRedis struct {
	lists  map[string]string
	resets []string
}

func newPaidFlowRedis() *paidFlowRedis { return &paidFlowRedis{lists: map[string]string{}} }

func (r *paidFlowRedis) Broadcasts(ctx context.Context, userID string) ([]*string, error) {
	name := "broadcast_" + userID
	return []*string{&name}, nil
}

func (r *paidFlowRedis) Notifications(ctx context.Context, broadcast string) ([]any, error) {
	raw, present := r.lists[broadcast]
	if !present {
		return []any{}, nil
	}
	value, err := dto.ParsePythonJSON(raw)
	if err != nil {
		return nil, err
	}
	object, ok := value.(domain.RequestObject)
	if !ok {
		return nil, ErrNotificationData
	}
	messages, _ := field(object, "messages")
	items, ok := messages.([]any)
	if !ok {
		return nil, ErrNotificationData
	}
	return items, nil
}

func (r *paidFlowRedis) SaveNotifications(ctx context.Context, broadcast string, items []any) error {
	r.lists[broadcast] = string(dto.PythonJSONDumps(domain.RequestObject{{Name: "messages", Value: items}}))
	return nil
}

func (r *paidFlowRedis) ResetNotifications(ctx context.Context, broadcast string) error {
	r.resets = append(r.resets, broadcast)
	delete(r.lists, broadcast)
	return nil
}

func (r *paidFlowRedis) ReadableAssociation(ctx context.Context, userID string) (string, error) {
	return "", nil
}

// paidFlowLayer is the channel layer's group_send.
type paidFlowLayer struct {
	groups   []string
	messages []channels.Dict
}

func (l *paidFlowLayer) GroupSend(ctx context.Context, group string, message channels.Dict) error {
	l.groups = append(l.groups, group)
	l.messages = append(l.messages, message)
	return nil
}

// paidFlowDict reads a channel-layer mapping by key.
func paidFlowDict(value any, key string) any {
	dict, ok := value.(channels.Dict)
	if !ok {
		return nil
	}
	for _, item := range dict {
		if item.Key == key {
			return item.Value
		}
	}
	return nil
}

// TestSubscriptionNotifierAssociationNone checks send_notification's
// association_id for a request user that is neither association nor
// collaborator: the stored item keeps JSON null and _push_to_websocket matches
// it; an association id is still written and matched for the other roles.
func TestSubscriptionNotifierAssociationNone(t *testing.T) {
	now := func() time.Time { return time.Date(2026, 11, 17, 10, 0, 0, 0, time.UTC) }
	for name, association := range map[string]*string{"athlete owner": nil, "association owner": paymentPaidFlowText("assoc-1")} {
		store, layer := newPaidFlowRedis(), &paidFlowLayer{}
		notifier := NewSubscriptionNotifier(&paidFlowRows{}, store, layer, now)
		if err := notifier.DeliverValidatedAssociation(context.Background(), "user-1", "payment", "Pagamento di € 31,00.",
			association, true); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if len(store.resets) != 1 || store.resets[0] != "broadcast_user-1" {
			t.Errorf("%s: resets %v", name, store.resets)
		}
		stored := store.lists["broadcast_user-1"]
		want := `"association_id": null`
		if association != nil {
			want = `"association_id": "` + *association + `"`
		}
		if !strings.Contains(stored, want) {
			t.Errorf("%s: stored %s, want %s", name, stored, want)
		}
		if len(layer.messages) != 1 || layer.groups[0] != "notifications_user_user-1" {
			t.Fatalf("%s: groups %v", name, layer.groups)
		}
		pushed := paidFlowDict(paidFlowDict(layer.messages[0], "notification"), "association_id")
		if association == nil {
			if pushed != nil {
				t.Errorf("%s: pushed association_id %#v, want null", name, pushed)
			}
		} else if pushed != *association {
			t.Errorf("%s: pushed association_id %#v, want %q", name, pushed, *association)
		}
	}
}

// TestPaymentPaidFlowAssociationRole is send_notification's role test: an
// association account keeps the association the view found, a collaborator
// account reads their connected user's live association (and fails like
// Django's DoesNotExist when there is none), and any other role keeps null.
func TestPaymentPaidFlowAssociationRole(t *testing.T) {
	connected := paymentPaidFlowText("connected-1")
	cases := []struct {
		name      string
		user      *domain.Account
		connected *string
		want      *string
		wantErr   bool
	}{
		{name: "association", user: &domain.Account{ID: "user-1", Role: domain.RoleAssociation},
			want: paymentPaidFlowText("assoc-1")},
		{name: "athlete", user: &domain.Account{ID: "user-1", Role: domain.RoleAthlete}},
		{name: "collaborator", user: &domain.Account{ID: "user-1", Role: domain.RoleCollaborator, ConnectedUserID: connected},
			connected: paymentPaidFlowText("assoc-connected"), want: paymentPaidFlowText("assoc-connected")},
		{name: "collaborator without a connected user",
			user: &domain.Account{ID: "user-1", Role: domain.RoleCollaborator}, wantErr: true},
		{name: "collaborator without a live association",
			user:    &domain.Account{ID: "user-1", Role: domain.RoleCollaborator, ConnectedUserID: paymentPaidFlowText("connected-2")},
			wantErr: true},
		{name: "no scope user"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			tx := &paidFlowTx{userAssociation: tc.connected}
			got, err := paymentPaidFlowAssociation(context.Background(), tx, tc.user, "assoc-1")
			if tc.wantErr {
				if !errors.Is(err, domain.ErrPaymentStateServer) {
					t.Fatalf("got %v, want %v", err, domain.ErrPaymentStateServer)
				}
				if got != nil {
					t.Errorf("association %q with the error", *got)
				}
			} else {
				if err != nil {
					t.Fatalf("association: %v", err)
				}
				if (got == nil) != (tc.want == nil) || (got != nil && *got != *tc.want) {
					t.Errorf("got %v, want %v", got, tc.want)
				}
			}
			// Only a collaborator asks for the connected user's association.
			if tc.user != nil && tc.user.Role == domain.RoleCollaborator {
				if len(tx.askedConnection) != 1 || tx.askedConnection[0] != tc.user.ConnectedUserID {
					t.Errorf("connected user asked %v", tx.askedConnection)
				}
			} else if len(tx.askedConnection) != 0 {
				t.Errorf("connected user asked %v", tx.askedConnection)
			}
		})
	}
}

// paidFlowStore hands the flow one transaction.
type paidFlowStore struct{ tx *paidFlowTx }

func (s *paidFlowStore) BeginPaymentPaidFlow(ctx context.Context) (domain.PaymentPaidFlowTransaction, error) {
	return s.tx, nil
}

// paidFlowTx records the paid flow's calls. Only the methods the flow
// reaches are implemented; the embedded interface refuses the rest.
type paidFlowTx struct {
	domain.PaymentPaidFlowTransaction

	row         *domain.PaymentStateRow
	owner       domain.InvoiceGenerateOwner
	last        *int64
	association string

	scopeOwner  string
	ownerUser   string
	lockedFor   string
	from        time.Time
	before      bool
	description string
	created     []domain.InvoiceGenerateCreate
	creation    time.Time
	creationSet bool
	changes     map[string][2]string
	saved       *domain.PaymentPaidFlowSave
	audits      int
	broadcastTo string
	committed   bool
	rolledBack  bool
	// userAssociation is what PaidFlowUserAssociation resolves for the
	// connected user asked (nil: no live association); askedConnection records
	// the lookups the flow ran.
	userAssociation *string
	askedConnection []*string
}

func (tx *paidFlowTx) ScopedAssociation(ctx context.Context, owner string, scope *string) (string, error) {
	tx.scopeOwner = owner
	return tx.association, nil
}

func (tx *paidFlowTx) PaymentState(ctx context.Context, query domain.PaymentStateQuery) (*domain.PaymentStateRow, error) {
	return tx.row, nil
}

func (tx *paidFlowTx) InvoiceGeneratePayment(ctx context.Context, id string) (domain.InvoiceGeneratePayment, error) {
	return domain.InvoiceGeneratePayment{Amount: "31.00"}, nil
}

func (tx *paidFlowTx) InvoiceGenerateOwner(ctx context.Context, owner string) (domain.InvoiceGenerateOwner, error) {
	tx.ownerUser = owner
	return tx.owner, nil
}

func (tx *paidFlowTx) LockInvoiceNumbering(ctx context.Context, association string) error {
	tx.lockedFor = association
	return nil
}

func (tx *paidFlowTx) LastInvoiceNumberByPaymentDate(ctx context.Context, association string, group *string, before bool,
	from time.Time) (*int64, error) {
	tx.before, tx.from = before, from
	return tx.last, nil
}

func (tx *paidFlowTx) AssociationDenomination(ctx context.Context, association string) (*string, error) {
	return paymentPaidFlowText("Importata atleta"), nil
}

func (tx *paidFlowTx) AssociateNames(ctx context.Context, associate string) (first, last *string, err error) {
	return paymentPaidFlowText("Mario"), paymentPaidFlowText("Rossi"), nil
}

func (tx *paidFlowTx) CreateInvoice(ctx context.Context, create domain.InvoiceGenerateCreate, overrides map[string]string,
	audit domain.LoginAuditContext, at time.Time) error {
	tx.created = append(tx.created, create)
	if create.Description != nil {
		tx.description = *create.Description
	}
	return nil
}

func (tx *paidFlowTx) SetInvoiceCreationDate(ctx context.Context, invoice, association string, date time.Time,
	changes map[string][2]string, audit domain.LoginAuditContext, at time.Time) error {
	tx.creation, tx.creationSet, tx.changes = date, true, changes
	return nil
}

func (tx *paidFlowTx) SavePaidFlowPayment(ctx context.Context, save domain.PaymentPaidFlowSave) error {
	tx.saved = &save
	return nil
}

func (tx *paidFlowTx) AppendPaymentAudit(ctx context.Context, event domain.PaymentArchiveEvent) error {
	tx.audits++
	return nil
}

func (tx *paidFlowTx) SupplierInvoice(ctx context.Context, payment string) (*domain.PaymentStateSupplierInvoice, error) {
	return nil, nil
}

func (tx *paidFlowTx) PaidFlowPeople(ctx context.Context, payment string) (domain.PaymentPaidFlowPeople, error) {
	return domain.PaymentPaidFlowPeople{AssociateFound: true, AssociateFirst: paymentPaidFlowText("Mario"),
		AssociateLast: paymentPaidFlowText("Rossi")}, nil
}

// PaidFlowUserAssociation is SportAssociation.objects.get(user=user): no user
// is no row without a query, a connected user resolves what the test set.
func (tx *paidFlowTx) PaidFlowUserAssociation(ctx context.Context, user *string) (*string, error) {
	tx.askedConnection = append(tx.askedConnection, user)
	if user == nil {
		return nil, nil
	}
	return tx.userAssociation, nil
}

func (tx *paidFlowTx) ValidateBroadcast(ctx context.Context, user, name string, audit domain.LoginAuditContext,
	at time.Time) (bool, error) {
	tx.broadcastTo = user + " " + name
	return true, nil
}

func (tx *paidFlowTx) RenderPayment(ctx context.Context, id string, group *string) (domain.PaymentStateRender, error) {
	return domain.PaymentStateRender{Payment: domain.CourseSubscriptionPayment{InvoicePartyPayment: domain.InvoicePartyPayment{
		ID: id, AssociationID: &tx.association, Subject: 0, Amount: "31.00"}}}, nil
}

func (tx *paidFlowTx) Commit() error   { tx.committed = true; return nil }
func (tx *paidFlowTx) Rollback() error { tx.rolledBack = true; return nil }

// newAthleteApprove builds payment_approve's paid flow for an athlete who owns
// an imported association: the athlete's own invoice settings and a
// notification without an association id.
func newAthleteApprove() (*PaymentStates, *paidFlowTx, *paidFlowRedis, *paidFlowLayer, *celery.Recorder) {
	now := time.Date(2026, 11, 17, 10, 0, 0, 0, time.UTC)
	row := &domain.PaymentStateRow{PaymentSaveRow: domain.PaymentSaveRow{ID: "b1000000-0000-4000-8000-000000000001",
		AssociationID: "imported-association", Subject: 0, CategoryID: paymentPaidFlowText("category-1"),
		CategoryName: paymentPaidFlowText("Pagamenti fixture"), Description: paymentPaidFlowText("Quota importata")},
		AssociateID: paymentPaidFlowText("associate-1")}
	last := int64(12)
	transaction := &paidFlowTx{row: row, last: &last, association: "imported-association",
		owner: domain.InvoiceGenerateOwner{StartDay: 15, StartMonth: 10, StartingNumber: 7, PaymentDateEqual: true}}
	store, layer, recorder := newPaidFlowRedis(), &paidFlowLayer{}, &celery.Recorder{}
	notifier := NewSubscriptionNotifier(&paidFlowRows{}, store, layer, func() time.Time { return now })
	states := NewPaymentStates(nil, func() time.Time { return now }).WithTasks(recorder).
		WithPaidFlow(&paidFlowStore{tx: transaction}, notifier)
	return states, transaction, store, layer, recorder
}

func athleteApproveBody(generate bool) domain.RequestObject {
	return domain.RequestObject{{Name: "generate_invoice", Value: generate}}
}

// TestPaymentPaidFlowAthleteOwnerApproves runs the paid flow for an athlete
// owner: the numbering reads that user's settings, the invoice belongs to the
// association, and the notification stores and pushes association_id null.
func TestPaymentPaidFlowAthleteOwnerApproves(t *testing.T) {
	identity := domain.ResolvedIdentity{Actor: &domain.Account{ID: "athlete-1", Email: "athlete@example.test"},
		ScopeUser: &domain.Account{ID: "athlete-1", Role: domain.RoleAthlete}}
	for name, generate := range map[string]bool{"invoice task": true, "generate_invoice false": false} {
		states, tx, store, layer, recorder := newAthleteApprove()
		request := PaymentStateRequest{ID: tx.row.ID, Authorization: paymentPaidFlowText("Bearer token")}
		result, err := states.Approve(context.Background(), identity, request, athleteApproveBody(generate))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		// get(user=request.user): the association lookup and the invoice settings read the athlete.
		if tx.scopeOwner != "athlete-1" || tx.ownerUser != "athlete-1" || tx.lockedFor != "imported-association" {
			t.Errorf("%s: scoped %q owner %q locked %q", name, tx.scopeOwner, tx.ownerUser, tx.lockedFor)
		}
		// 15 October 2026, the athlete's balance year: the payment date is
		// 17 November 2026, so the current year's invoices are read.
		if want := time.Date(2026, 10, 15, 0, 0, 0, 0, time.UTC); !tx.from.Equal(want) || tx.before {
			t.Errorf("%s: from %v before %v, want %v false", name, tx.from, tx.before, want)
		}
		if len(tx.created) != 1 {
			t.Fatalf("%s: created %d invoices", name, len(tx.created))
		}
		invoice := tx.created[0]
		if invoice.Number != 13 || invoice.Association != "imported-association" || invoice.ActivityFee != "31.00" {
			t.Errorf("%s: invoice %+v", name, invoice)
		}
		// The payment has a category, so the description uses its name (the
		// description text is the notification's "per ..." instead).
		if tx.description != "Ricevuta di pagamento con causale <b>Pagamenti fixture</b> per l'associazione <b>Importata atleta</b>." {
			t.Errorf("%s: description %q", name, tx.description)
		}
		if !tx.creationSet || !tx.creation.Equal(time.Date(2026, 11, 17, 10, 0, 0, 0, time.UTC)) {
			t.Errorf("%s: creation date %v (set %v)", name, tx.creation, tx.creationSet)
		}
		if changes := tx.changes["membership_fee"]; changes != [2]string{"0.00", "0.0"} {
			t.Errorf("%s: membership fee changes %v", name, changes)
		}
		if tx.saved == nil || tx.saved.Invoice == nil || *tx.saved.Invoice != invoice.ID {
			t.Errorf("%s: saved %+v", name, tx.saved)
		}
		if !tx.committed || tx.audits != 1 {
			t.Errorf("%s: committed %v audits %d", name, tx.committed, tx.audits)
		}
		if tx.broadcastTo != "athlete-1 broadcast_athlete-1" {
			t.Errorf("%s: broadcast %q", name, tx.broadcastTo)
		}
		// The response's message and the athlete's notification.
		data, _ := result["data"].(map[string]any)
		if data["msg"] != "payment mark as payed." {
			t.Errorf("%s: msg %v", name, data["msg"])
		}
		stored := store.lists["broadcast_athlete-1"]
		if !strings.Contains(stored, `"association_id": null`) || !strings.Contains(stored,
			`"msg": "Pagamento di \u20ac 31,00per Quota importata. Pagamento dell'associato Mario Rossi."`) {
			t.Errorf("%s: stored notification %s", name, stored)
		}
		if len(layer.messages) != 1 {
			t.Fatalf("%s: pushes %d", name, len(layer.messages))
		}
		pushed := paidFlowDict(layer.messages[0], "notification")
		if paidFlowDict(pushed, "association_id") != nil || paidFlowDict(pushed, "msg") !=
			"Pagamento di € 31,00per Quota importata. Pagamento dell'associato Mario Rossi." {
			t.Errorf("%s: pushed notification %#v", name, pushed)
		}
		payment, _ := data["payment"].(map[string]any)
		if payment["payment_id"] != tx.row.ID {
			t.Errorf("%s: payment item %v", name, payment)
		}
		if generates, present := payment["invoice_generating"]; present != generate || (present && generates != true) {
			t.Errorf("%s: invoice_generating %v (present %v)", name, generates, present)
		}
		if generate {
			if len(recorder.Tasks) != 1 || recorder.Tasks[0].Name != "print_document_invoice" ||
				recorder.Tasks[0].Args[0] != invoice.ID || recorder.Tasks[0].Args[1] != "Bearer token" {
				t.Errorf("%s: tasks %+v", name, recorder.Tasks)
			}
		} else if len(recorder.Tasks) != 0 {
			t.Errorf("%s: unexpected tasks %+v", name, recorder.Tasks)
		}
	}
}

// TestPaymentPaidFlowAssociationOwnerNotification keeps the association and
// collaborator paths writing and pushing their association id.
func TestPaymentPaidFlowAssociationOwnerNotification(t *testing.T) {
	identity := domain.ResolvedIdentity{Actor: &domain.Account{ID: "owner-1", Email: "owner@example.test"},
		ScopeUser: &domain.Account{ID: "owner-1", Role: domain.RoleAssociation}}
	states, tx, store, layer, _ := newAthleteApprove()
	request := PaymentStateRequest{ID: tx.row.ID}
	if _, err := states.Approve(context.Background(), identity, request, athleteApproveBody(false)); err != nil {
		t.Fatal(err)
	}
	if stored := store.lists["broadcast_owner-1"]; !strings.Contains(stored, `"association_id": "imported-association"`) {
		t.Errorf("stored notification %s", stored)
	}
	if len(layer.messages) != 1 {
		t.Fatalf("pushes %d", len(layer.messages))
	}
	pushed := paidFlowDict(paidFlowDict(layer.messages[0], "notification"), "association_id")
	if pushed != "imported-association" {
		t.Errorf("pushed association_id %#v", pushed)
	}
	if tx.ownerUser != "owner-1" {
		t.Errorf("owner %q", tx.ownerUser)
	}
}

// TestPaymentPaidFlowCollaboratorNotification is send_notification for a
// collaborator account: the association import makes the scope user a
// collaborator who owns the imported association the view found, so the view
// runs as for an owner, while the notification carries the connected user's
// live association (stored and pushed).
func TestPaymentPaidFlowCollaboratorNotification(t *testing.T) {
	connected := paymentPaidFlowText("connected-1")
	identity := domain.ResolvedIdentity{Actor: &domain.Account{ID: "collaborator-1", Email: "collaborator@example.test"},
		ScopeUser: &domain.Account{ID: "collaborator-1", Role: domain.RoleCollaborator, ConnectedUserID: connected}}
	states, tx, store, layer, _ := newAthleteApprove()
	tx.userAssociation = paymentPaidFlowText("assoc-connected")
	request := PaymentStateRequest{ID: tx.row.ID}
	if _, err := states.Approve(context.Background(), identity, request, athleteApproveBody(false)); err != nil {
		t.Fatal(err)
	}
	if tx.ownerUser != "collaborator-1" || tx.lockedFor != "imported-association" {
		t.Errorf("owner %q locked %q", tx.ownerUser, tx.lockedFor)
	}
	if len(tx.askedConnection) != 1 || tx.askedConnection[0] != connected {
		t.Errorf("connected user asked %v", tx.askedConnection)
	}
	if !tx.committed {
		t.Error("not committed")
	}
	stored := store.lists["broadcast_collaborator-1"]
	if !strings.Contains(stored, `"association_id": "assoc-connected"`) {
		t.Errorf("stored notification %s", stored)
	}
	if len(layer.messages) != 1 {
		t.Fatalf("pushes %d", len(layer.messages))
	}
	if pushed := paidFlowDict(paidFlowDict(layer.messages[0], "notification"), "association_id"); pushed != "assoc-connected" {
		t.Errorf("pushed association_id %#v", pushed)
	}
}

// TestPaymentPaidFlowCollaboratorWithoutAssociationRollsBack is the 500 page:
// request.user.sport_association raises for a collaborator without a live
// connected association, after the invoice and the payment save and before
// validate_broadcast, so the block never commits and nothing is delivered.
func TestPaymentPaidFlowCollaboratorWithoutAssociationRollsBack(t *testing.T) {
	for name, connected := range map[string]*string{"no connected user": nil, "no live association": paymentPaidFlowText("connected-2")} {
		identity := domain.ResolvedIdentity{Actor: &domain.Account{ID: "collaborator-1", Email: "collaborator@example.test"},
			ScopeUser: &domain.Account{ID: "collaborator-1", Role: domain.RoleCollaborator, ConnectedUserID: connected}}
		states, tx, store, layer, recorder := newAthleteApprove()
		request := PaymentStateRequest{ID: tx.row.ID}
		if _, err := states.Approve(context.Background(), identity, request, athleteApproveBody(true)); !errors.Is(err,
			domain.ErrPaymentStateServer) {
			t.Fatalf("%s: got %v, want %v", name, err, domain.ErrPaymentStateServer)
		}
		// The transaction never committed, so the caller's deferred Rollback
		// discards the invoice, the payment save and the audit entries.
		if tx.committed || !tx.rolledBack {
			t.Errorf("%s: committed %v rolled back %v", name, tx.committed, tx.rolledBack)
		}
		if tx.broadcastTo != "" {
			t.Errorf("%s: validate_broadcast called: %q", name, tx.broadcastTo)
		}
		if len(store.lists) != 0 || len(store.resets) != 0 {
			t.Errorf("%s: notifications stored %v resets %v", name, store.lists, store.resets)
		}
		if len(layer.groups) != 0 || len(layer.messages) != 0 {
			t.Errorf("%s: pushes %v", name, layer.groups)
		}
		if len(recorder.Tasks) != 0 {
			t.Errorf("%s: tasks %+v", name, recorder.Tasks)
		}
		// The flow had already written inside the block.
		if len(tx.created) != 1 || tx.saved == nil || tx.audits != 1 {
			t.Errorf("%s: invoices %d saved %v audits %d", name, len(tx.created), tx.saved, tx.audits)
		}
	}
}

// TestPaymentPaidFlowNotificationJSON pins the stored item's JSON shape.
func TestPaymentPaidFlowNotificationJSON(t *testing.T) {
	item := domain.RequestObject{{Name: "id", Value: "id-1"}, {Name: "type", Value: "payment"}, {Name: "msg", Value: "m"},
		{Name: "association_id", Value: notificationAssociation(nil)}, {Name: "users", Value: []any{}},
		{Name: "expires", Value: json.Number("1")}}
	stored := string(dto.PythonJSONDumps(domain.RequestObject{{Name: "messages", Value: []any{item}}}))
	if !strings.Contains(stored, `{"id": "id-1", "type": "payment", "msg": "m", "association_id": null, "users": [], "expires": 1}`) {
		t.Fatalf("stored %s", stored)
	}
	if !notificationAssociationMatches(nil, nil) || notificationAssociationMatches("assoc-1", nil) ||
		!notificationAssociationMatches("assoc-1", paymentPaidFlowText("assoc-1")) || notificationAssociationMatches(nil, paymentPaidFlowText("assoc-1")) {
		t.Fatal("push association matching")
	}
}
