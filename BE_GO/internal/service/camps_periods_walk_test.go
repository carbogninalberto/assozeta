package service

import (
	"context"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// campsWalkFake supplies only the reads campsWalk's payment save makes.
type campsWalkFake struct {
	domain.CampsTransaction
	rows []domain.CampsWalkRow
}

func (f campsWalkFake) WalkRows(ctx context.Context, period string) ([]domain.CampsWalkRow, error) {
	return f.rows, nil
}

func (f campsWalkFake) DefaultPaymentCategory(ctx context.Context, association *string) (*string, *string, error) {
	id, name := "category", "Categoria"
	return &id, &name, nil
}

func (f campsWalkFake) PaymentLinkedName(ctx context.Context, payment string) (domain.PaymentLinkedName, error) {
	return domain.PaymentLinkedName{}, nil
}

func campsWalkText(value string) *string { return &value }

func campsWalkRow(id string, subject int, description, metas *string) domain.CampsWalkRow {
	return domain.CampsWalkRow{PeriodTitle: campsWalkText("Novembre"), PeriodFee: "20.00", CampTitle: campsWalkText("Campo"),
		CampDescription: campsWalkText("Mare"), Payment: domain.CampsPayment{
			PaymentSaveRow: domain.PaymentSaveRow{ID: id, AssociationID: "association", Subject: subject, CategoryID: campsWalkText("category"),
				Description: description, Meta: nil},
			Amount: "0.00", Metas: metas}}
}

// campsWalkService is the row the walk's update metadata comes from.
func campsWalkService() domain.CampsService {
	return domain.CampsService{ID: "service", Fee: "10.00", Title: campsWalkText("Servizio"), CategoryID: campsWalkText("category")}
}

// campsWalkCheck asserts the planned saves: the payments saved in order, then
// (when failure is not empty) the exception that stops the walk.
func campsWalkCheck(t *testing.T, saves []campsPaymentSave, err error, saved []string, failure string) {
	t.Helper()
	if err != nil {
		t.Fatalf("walk refused: %v", err)
	}
	count := len(saved)
	if failure != "" {
		count++
	}
	if len(saves) != count {
		t.Fatalf("expected %d saves, got %+v", count, saves)
	}
	for index, id := range saved {
		if saves[index].failure != nil || saves[index].row.ID != id {
			t.Fatalf("save %d: expected payment %q, got %+v", index, id, saves[index])
		}
	}
	if failure == "" {
		return
	}
	last := saves[len(saves)-1]
	if last.failure == nil || last.failure.Message != failure || last.row.ID != "" {
		t.Fatalf("expected the trailing failure %q, got %+v", failure, last)
	}
}

func TestCampsWalkMultiRowFailures(t *testing.T) {
	service := campsWalkService()
	// A meta list with a matching item plus another item without a title: the
	// matching save is planned before the notes loop raises.
	missingTitle := campsWalkText(`[{"camps_and_retreats_period_service_id":"service","amount":5},` +
		`{"camps_and_retreats_period_service_id":"other"}]`)
	tests := []struct {
		name    string
		rows    []domain.CampsWalkRow
		saved   []string
		failure string
	}{
		{"every row fails before its first save",
			[]domain.CampsWalkRow{campsWalkRow("a", 1, campsWalkText("Iscrizione"), nil), campsWalkRow("b", 1, campsWalkText("Iscrizione"), nil)},
			nil, "'NoneType' object is not iterable"},
		{"one row saves before its failure",
			[]domain.CampsWalkRow{campsWalkRow("a", 1, campsWalkText("Iscrizione"), missingTitle),
				campsWalkRow("b", 1, campsWalkText("Iscrizione"), missingTitle)},
			[]string{"a"}, "'title'"},
		{"different messages",
			[]domain.CampsWalkRow{campsWalkRow("a", 1, campsWalkText("Iscrizione"), nil),
				campsWalkRow("b", 1, campsWalkText("Iscrizione"), campsWalkText(`"x"`))},
			nil, "'NoneType' object is not iterable"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			request := &campsRequest{tx: campsWalkFake{rows: tc.rows}}
			saves, err := request.campsWalk(context.Background(), service, nil)
			campsWalkCheck(t, saves, err, tc.saved, tc.failure)
		})
	}
}

// A row that fails stops the walk: the rows after it are never saved.
func TestCampsWalkStopsAtFirstFailure(t *testing.T) {
	request := &campsRequest{tx: campsWalkFake{rows: []domain.CampsWalkRow{
		campsWalkRow("a", 1, campsWalkText("Iscrizione"), nil),
		campsWalkRow("b", 1, campsWalkText("Iscrizione"), campsWalkText("[]")),
	}}}
	saves, err := request.campsWalk(context.Background(), campsWalkService(), nil)
	campsWalkCheck(t, saves, err, nil, "'NoneType' object is not iterable")
	for _, save := range saves {
		if save.row.ID == "b" {
			t.Fatalf("the row after the failure was saved: %+v", save)
		}
	}
}

// The rows before a failing one keep their saves; the failing row is the last.
func TestCampsWalkEarlierSavesThenFailure(t *testing.T) {
	request := &campsRequest{tx: campsWalkFake{rows: []domain.CampsWalkRow{
		campsWalkRow("a", 1, campsWalkText("Iscrizione"), campsWalkText("[]")),
		campsWalkRow("b", 1, campsWalkText("Iscrizione"), nil),
	}}}
	saves, err := request.campsWalk(context.Background(), campsWalkService(), nil)
	campsWalkCheck(t, saves, err, []string{"a"}, "'NoneType' object is not iterable")
}
