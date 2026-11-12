package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type paymentSaveReaderStub struct {
	rows     []domain.PaymentSaveState
	err      error
	calls    int
	refs     []domain.PaymentSaveReference
	category string
}

func (s *paymentSaveReaderStub) ReadPaymentSaveInputs(_ context.Context, refs []domain.PaymentSaveReference, category string) ([]domain.PaymentSaveState, error) {
	s.calls++
	s.refs = refs
	s.category = category
	return s.rows, s.err
}
func TestPaymentSavePreparationRejectsUntrustedReaderResults(t *testing.T) {
	id, association := uuid.New(), uuid.New()
	ref := domain.PaymentSaveReference{PaymentID: id, AssociationID: association}
	failure := errors.New("private reader failure")
	for _, tc := range []struct {
		name      string
		rows      []domain.PaymentSaveState
		err, want error
	}{
		{"missing row", nil, nil, domain.ErrPaymentSaveScope},
		{"different identity", []domain.PaymentSaveState{{ID: uuid.New(), AssociationID: association}}, nil, domain.ErrPaymentSaveScope},
		{"different association", []domain.PaymentSaveState{{ID: id, AssociationID: uuid.New()}}, nil, domain.ErrPaymentSaveScope},
		{"malformed JSON", []domain.PaymentSaveState{{ID: id, AssociationID: association, Meta: []byte("{")}}, nil, domain.ErrPaymentDescription},
		{"database failure", nil, failure, failure},
	} {
		t.Run(tc.name, func(t *testing.T) {
			reader := &paymentSaveReaderStub{rows: tc.rows, err: tc.err}
			plans, err := NewPaymentSavePreparation(reader).Prepare(context.Background(), []domain.PaymentSaveReference{ref})
			if plans != nil || !errors.Is(err, tc.want) || reader.calls != 1 {
				t.Fatalf("invalid reader output %v/%v", plans, err)
			}
		})
	}
}
func TestPaymentSavePreparationCancellationAndDefaultPolicy(t *testing.T) {
	reader := &paymentSaveReaderStub{}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	plans, err := NewPaymentSavePreparation(reader).Prepare(ctx, []domain.PaymentSaveReference{{}})
	if plans != nil || !errors.Is(err, context.Canceled) || reader.calls != 0 {
		t.Fatal("canceled preparation reached reader")
	}
	reader.rows = []domain.PaymentSaveState{{Subject: 1}}
	plans, err = NewPaymentSavePreparation(reader).Prepare(context.Background(), []domain.PaymentSaveReference{{}, {}})
	if err != nil || len(plans) != 1 || plans[0].Description == nil || *plans[0].Description != "Iscrizione" || len(reader.refs) != 1 || reader.category != "entrate e proventi da attività tipiche" {
		t.Fatal("wrong owned/default preparation", err)
	}
}
