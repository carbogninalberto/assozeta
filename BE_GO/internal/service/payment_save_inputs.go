package service

import (
	"context"
	"encoding/json"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type PaymentSavePreparation struct{ reader domain.PaymentSaveInputReader }

func NewPaymentSavePreparation(reader domain.PaymentSaveInputReader) *PaymentSavePreparation {
	return &PaymentSavePreparation{reader: reader}
}

// Prepare computes existing-payment defaults for trusted background work. The
// caller must bind it to its mutation transaction/locks before applying a plan;
// these read-only snapshots are not a durable authorization or write receipt.
func (s *PaymentSavePreparation) Prepare(ctx context.Context, refs []domain.PaymentSaveReference) ([]domain.PaymentSavePlan, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	unique, err := domain.UniquePaymentSaveReferences(refs)
	if err != nil {
		return nil, err
	}
	if len(unique) == 0 {
		return nil, nil
	}
	rows, err := s.reader.ReadPaymentSaveInputs(ctx, unique, "entrate e proventi da attività tipiche")
	if err != nil {
		return nil, err
	}
	if len(rows) != len(unique) {
		return nil, domain.ErrPaymentSaveScope
	}
	plans := make([]domain.PaymentSavePlan, 0, len(rows))
	parse := func(raw json.RawMessage) (any, error) {
		if len(raw) == 0 {
			return nil, nil
		}
		v, err := dto.ParseReferenceJSON(raw)
		if err != nil {
			return nil, domain.ErrPaymentDescription
		}
		return v, nil
	}
	for i, row := range rows {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if row.ID != unique[i].PaymentID || row.AssociationID != unique[i].AssociationID {
			return nil, domain.ErrPaymentSaveScope
		}
		meta, err := parse(row.Meta)
		if err != nil {
			return nil, err
		}
		course, err := parse(row.Course)
		if err != nil {
			return nil, err
		}
		input := domain.PaymentDescriptionInput{Subject: row.Subject, Description: row.Description, CategoryName: row.CategoryName, Meta: meta, Course: course}
		for slot, relation := range row.Related {
			if relation != nil {
				input.Related[slot] = &domain.PaymentDescriptionRelation{Title: relation.Title, Meta: meta, MissingCarnet: relation.MissingCarnet}
			}
		}
		description, err := ResolvePaymentDescription(ctx, input)
		if err != nil {
			return nil, err
		}
		plans = append(plans, domain.PaymentSavePlan{Payment: row, Description: description})
	}
	return plans, nil
}
