package service

import (
	"context"
	"errors"
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

func TestCampsWalkMultiRowFailures(t *testing.T) {
	service := domain.CampsService{ID: "service", Fee: "10.00", Title: campsWalkText("Servizio"), CategoryID: campsWalkText("category")}
	// A meta list with a matching item plus another item without a title: the
	// matching save is planned before the notes loop raises.
	missingTitle := campsWalkText(`[{"camps_and_retreats_period_service_id":"service","amount":5},` +
		`{"camps_and_retreats_period_service_id":"other"}]`)
	tests := []struct {
		name    string
		rows    []domain.CampsWalkRow
		want    string
		refused bool
	}{
		{"every row fails before its first save",
			[]domain.CampsWalkRow{campsWalkRow("a", 1, campsWalkText("Iscrizione"), nil), campsWalkRow("b", 1, campsWalkText("Iscrizione"), nil)},
			"'NoneType' object is not iterable", false},
		{"one row saves before its failure",
			[]domain.CampsWalkRow{campsWalkRow("a", 1, campsWalkText("Iscrizione"), missingTitle),
				campsWalkRow("b", 1, campsWalkText("Iscrizione"), missingTitle)}, "", true},
		{"different messages",
			[]domain.CampsWalkRow{campsWalkRow("a", 1, campsWalkText("Iscrizione"), nil),
				campsWalkRow("b", 1, campsWalkText("Iscrizione"), campsWalkText(`"x"`))}, "", true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			request := &campsRequest{tx: campsWalkFake{rows: tc.rows}}
			saves, err := request.campsWalk(context.Background(), service, nil)
			if tc.refused {
				if saves != nil || !errors.Is(err, ErrCampsPaymentNotPorted) {
					t.Fatalf("expected refusal %v/%v", saves, err)
				}
				return
			}
			if err != nil || len(saves) != 1 || saves[0].failure == nil || saves[0].failure.Message != tc.want || saves[0].row.ID != "" {
				t.Fatalf("expected one failure %+v/%v", saves, err)
			}
		})
	}
}
