package service

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// campsPaymentsMeta names the walk's service in one item.
const campsPaymentsMeta = `[{"camps_and_retreats_period_service_id":"service","title":"Servizio","amount":5}]`

func campsPaymentsUpdate(amount float64) dto.CampsServiceMeta {
	return dto.CampsServiceMeta{ServiceID: "service", Title: campsWalkText("Servizio"), Category: "category", Amount: amount}
}

func campsPaymentsTestRow(id string, category *string, metas *string) domain.CampsWalkRow {
	row := campsWalkRow(id, 1, campsWalkText("Iscrizione"), metas)
	row.Payment.CategoryID = category
	return row
}

// campsPaymentsPlan runs one row of the walk with a service fee that replaces
// the meta item's amount.
func campsPaymentsPlan(t *testing.T, row domain.CampsWalkRow, fee float64) ([]campsPaymentSave, bool) {
	t.Helper()
	request := &campsRequest{tx: campsWalkFake{rows: []domain.CampsWalkRow{row}}}
	saves, failure, rejected, err := request.campsWalkRow(context.Background(), row, campsPaymentsUpdate(fee),
		map[string]*domain.CampsPayment{}, nil)
	if err != nil {
		t.Fatalf("walk failed: %v", err)
	}
	if failure != nil {
		t.Fatalf("walk stopped with %v", failure)
	}
	return saves, rejected
}

// A payment the soft-delete, group-aware manager hides and that has no
// association is saved like any other: Payment.save() takes the global default
// category, each save is audited as a full update against None and the index
// falls back to the actor.
func TestCampsWalkHiddenPaymentWithoutAssociation(t *testing.T) {
	row := campsPaymentsTestRow("hidden", nil, campsWalkText(campsPaymentsMeta))
	row.Payment.Deleted, row.Payment.AssociationNull = true, true
	saves, rejected := campsPaymentsPlan(t, row, 9.5)
	if rejected {
		t.Fatal("the hidden payment stopped the walk")
	}
	if len(saves) != 2 {
		t.Fatalf("expected the meta save and the final save, got %+v", saves)
	}
	for index, save := range saves {
		if save.refused || save.rejected != nil || save.failure != nil {
			t.Fatalf("save %d stopped the walk: %+v", index, save)
		}
		if save.event == nil || !save.event.Full || save.event.AssociationID != nil {
			t.Fatalf("save %d is not a full entry without an association: %+v", index, save.event)
		}
	}
	if saves[1].row.Amount != "29.50" {
		t.Fatalf("final amount %q, want 29.50", saves[1].row.Amount)
	}
	if saves[1].event.Changes["amount"] != [2]string{"None", "29.5"} {
		t.Fatalf("final entry amount %v, want None -> 29.5", saves[1].event.Changes["amount"])
	}
	if saves[0].row.CategoryID == nil || *saves[0].row.CategoryID != "category" {
		t.Fatalf("the default category was not taken from the null association: %+v", saves[0].row.PaymentSaveRow)
	}
}

// A final amount the amount check constraint rejects is planned as a save the
// database refuses: the UPDATE runs later, in a savepoint, for its own text.
func TestCampsWalkNegativeAmountIsRefused(t *testing.T) {
	row := campsPaymentsTestRow("visible", campsWalkText("category"), campsWalkText(campsPaymentsMeta))
	saves, rejected := campsPaymentsPlan(t, row, -30)
	if !rejected || len(saves) != 2 {
		t.Fatalf("expected the walk to stop at two saves, got %+v (rejected %v)", saves, rejected)
	}
	if saves[0].refused || saves[0].rejected != nil {
		t.Fatalf("the meta save was refused: %+v", saves[0])
	}
	last := saves[1]
	if !last.refused || last.rejected != nil || last.failure != nil {
		t.Fatalf("the negative save is not a refused row: %+v", last)
	}
	if last.row.Amount != "-10.00" || last.event == nil {
		t.Fatalf("refused save row %+v", last)
	}
	if last.event.Changes["amount"] != [2]string{"0.00", "-10.0"} {
		t.Fatalf("refused entry amount %v", last.event.Changes["amount"])
	}
}

// A negative amount that numeric(9,2) rounds to zero satisfies the check
// constraint and is saved normally.
func TestCampsWalkNegativeRoundedToZeroIsSaved(t *testing.T) {
	row := campsPaymentsTestRow("visible", campsWalkText("category"), campsWalkText(campsPaymentsMeta))
	// The period fee is 20.00, so -20.001 rounds to a zero amount.
	saves, rejected := campsPaymentsPlan(t, row, -20.001)
	if rejected || len(saves) != 2 || saves[1].refused || saves[1].rejected != nil {
		t.Fatalf("expected the rounded save to be stored, got %+v", saves)
	}
	if saves[1].row.Amount != "0.00" {
		t.Fatalf("stored amount %q, want 0.00", saves[1].row.Amount)
	}
}

// PostgreSQL rejects an over-long varchar while it reads the parameters, before
// the amount check constraint runs, so the note wins over the negative amount.
func TestCampsWalkLongNotesBeforeNegativeAmount(t *testing.T) {
	row := campsPaymentsTestRow("visible", campsWalkText("category"), campsWalkText(campsPaymentsMeta))
	row.CampDescription = campsWalkText(strings.Repeat("x", 6000))
	saves, rejected := campsPaymentsPlan(t, row, -30)
	if !rejected {
		t.Fatalf("expected the walk to stop at the varchar refusal: %+v", saves)
	}
	last := saves[len(saves)-1]
	if last.rejected == nil || last.rejected.Message != campsNotesTooLong || last.refused {
		t.Fatalf("expected the varchar refusal, got %+v", last)
	}
}

// campsGenerateFake is generate_payment_for_periods' transaction: one period
// whose only service has a negative fee, so the generated amount is negative.
type campsGenerateFake struct {
	domain.CampsSubscriptionsTransaction
	rejected string
	accepted bool
	inserts  int
}

func (f *campsGenerateFake) GenerateRows(ctx context.Context, subscription string) ([]domain.CampGenerateRow, error) {
	return []domain.CampGenerateRow{{ID: "period", PeriodTitle: campsWalkText("Luglio"), PeriodFee: "0.00"}}, nil
}

func (f *campsGenerateFake) SelectedServices(ctx context.Context, subscriptionPeriod string) ([]domain.CampSelectedService, error) {
	return []domain.CampSelectedService{{ID: "service", Title: campsWalkText("Extra"), Fee: "-50.00",
		CategoryID: campsWalkText("category")}}, nil
}

func (f *campsGenerateFake) DefaultPaymentCategory(ctx context.Context, association *string) (*string, *string, error) {
	id, name := "category", "Categoria"
	return &id, &name, nil
}

func (f *campsGenerateFake) CampOwnerUser(ctx context.Context, camp string) (string, error) {
	return "owner", nil
}

func (f *campsGenerateFake) DefaultBankAccount(ctx context.Context, association string) (*string, error) {
	return nil, nil
}

func (f *campsGenerateFake) CreatePayment(ctx context.Context, row domain.CampPaymentCreate) (map[string][2]string, error) {
	f.inserts++
	return nil, errors.New("a negative amount reached the plain INSERT")
}

func (f *campsGenerateFake) CreatePaymentRejected(ctx context.Context, row domain.CampPaymentCreate) (string, bool, error) {
	f.inserts++
	return f.rejected, !f.accepted, nil
}

func campsGenerateRequest(fake *campsGenerateFake) *campsSubscriptionRequest {
	return &campsSubscriptionRequest{
		campsRequest: &campsRequest{s: &CampsPeriods{now: time.Now, ids: func() string { return "payment" }}, tx: fake},
		subs:         fake}
}

// A generated payment with a negative amount runs its INSERT in a savepoint and
// answers the database's text.
func TestCampsGeneratePaymentNegativeAmount(t *testing.T) {
	text := "new row for relation \"application_payment\" violates check constraint \"payment_amount_positive\"\n" +
		"DETAIL:  Failing row contains (payment, default, 0, -50.00, null).\n"
	fake := &campsGenerateFake{rejected: text}
	err := campsGenerateRequest(fake).generatePayment(context.Background(),
		domain.CampSubscription{ID: "subscription", CampID: "camp", CampAssociationID: "association"}, campsWalkText("Campo"),
		&domain.CampRegistration{ID: "registration"})
	var python *dto.CampsPythonError
	if !errors.As(err, &python) || python.Message != text {
		t.Fatalf("expected the database text, got %v", err)
	}
	if fake.inserts != 1 {
		t.Fatalf("expected one rejected INSERT, got %d", fake.inserts)
	}
}

// A statement planned as refused that the database accepts is application state.
func TestCampsGeneratePaymentNegativeAccepted(t *testing.T) {
	fake := &campsGenerateFake{accepted: true}
	err := campsGenerateRequest(fake).generatePayment(context.Background(),
		domain.CampSubscription{ID: "subscription", CampID: "camp", CampAssociationID: "association"}, campsWalkText("Campo"),
		&domain.CampRegistration{ID: "registration"})
	if !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("expected an application-state error, got %v", err)
	}
}
