//go:build paymentdescriptionintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

func TestPaymentDescriptionDjangoOracle(t *testing.T) {
	var fixture struct {
		Cases []struct {
			Name                                string
			Subject                             int
			Description, CategoryName, Expected *string
			Meta, Course                        json.RawMessage
			Related                             []struct {
				Title         *string
				Meta          json.RawMessage
				MissingCarnet bool
			}
			Error bool
		}
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_PAYMENT_DESCRIPTION_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || len(fixture.Cases) != 81 {
		t.Fatal("run accounts.py --only payment-description; complete actual-save fixture required")
	}
	for _, row := range fixture.Cases {
		t.Run(row.Name, func(t *testing.T) {
			parse := func(raw json.RawMessage) any {
				value, err := dto.ParseReferenceJSON(raw)
				if err != nil {
					t.Fatal(err)
				}
				return value
			}
			in := domain.PaymentDescriptionInput{Subject: row.Subject, Description: row.Description, CategoryName: row.CategoryName, Meta: parse(row.Meta), Course: parse(row.Course)}
			if len(row.Related) != 3 {
				t.Fatal("all three relationship slots required")
			}
			for i, r := range row.Related {
				if len(r.Meta) != 0 {
					in.Related[i] = &domain.PaymentDescriptionRelation{Title: r.Title, Meta: parse(r.Meta), MissingCarnet: r.MissingCarnet}
				}
			}
			got, err := ResolvePaymentDescription(context.Background(), in)
			if row.Error {
				if !errors.Is(err, domain.ErrPaymentDescription) || got != nil {
					t.Fatalf("got %v/%v; expected invalid description", got, err)
				}
				return
			}
			if err != nil || !reflect.DeepEqual(got, row.Expected) {
				t.Fatalf("got %v/%v want %v", got, err, row.Expected)
			}
		})
	}
}
