package service

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestPaymentDescriptionContextAndInputOwnership(t *testing.T) {
	text := "Quota Corso"
	input := domain.PaymentDescriptionInput{Subject: 2, Description: &text, Meta: domain.RequestObject{{Name: "description", Value: "Danza"}}}
	before := input
	got, err := ResolvePaymentDescription(context.Background(), input)
	if err != nil || got == nil || *got != "Danza" || !reflect.DeepEqual(input, before) || text != "Quota Corso" {
		t.Fatalf("output/input %v/%v/%+v", got, err, input)
	}
	*got = "Mutated output"
	if text != "Quota Corso" || input.Meta.(domain.RequestObject)[0].Value != "Danza" {
		t.Fatal("returned pointer aliases input")
	}
	input.Description = &text
	input.Meta = nil
	got, err = ResolvePaymentDescription(context.Background(), input)
	if err != nil || got == nil || *got != text || got == &text {
		t.Fatal("unchanged value must also be owned by caller")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	got, err = ResolvePaymentDescription(ctx, input)
	if got != nil || !errors.Is(err, context.Canceled) {
		t.Fatalf("cancel %v/%v", got, err)
	}
}

func TestPaymentDescriptionExplicitContract(t *testing.T) {
	empty, category, title, placeholder := "", "Quota associativa", "Danza", "Quota Corso"
	tests := []struct {
		name    string
		input   domain.PaymentDescriptionInput
		want    *string
		invalid bool
	}{
		{"membership", domain.PaymentDescriptionInput{Subject: 1}, paymentDescriptionString("Iscrizione"), false},
		{"other category", domain.PaymentDescriptionInput{CategoryName: &category}, &category, false},
		{"no category", domain.PaymentDescriptionInput{}, nil, false},
		{"empty is explicit", domain.PaymentDescriptionInput{Subject: 1, Description: &empty}, &empty, false},
		{"course fallback", domain.PaymentDescriptionInput{Subject: 2}, &placeholder, false},
		{"related title", domain.PaymentDescriptionInput{Subject: 2, Related: [3]*domain.PaymentDescriptionRelation{{Title: &title}}}, &title, false},
		{"invalid transfer", domain.PaymentDescriptionInput{Subject: 3}, nil, true},
		{"truthy number before lower", domain.PaymentDescriptionInput{Subject: 2, Related: [3]*domain.PaymentDescriptionRelation{{Meta: domain.RequestObject{{Name: "description", Value: true}}}}}, nil, true},
		{"truthy number after lower", domain.PaymentDescriptionInput{Subject: 2, Description: &placeholder, Meta: domain.RequestObject{{Name: "description", Value: true}}}, paymentDescriptionString("True"), false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := ResolvePaymentDescription(context.Background(), tc.input)
			if tc.invalid {
				if got != nil || !errors.Is(err, domain.ErrPaymentDescription) {
					t.Fatalf("expected rejection %v/%v", got, err)
				}
				return
			}
			if err != nil || !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("got %v/%v want %v", got, err, tc.want)
			}
		})
	}
}

func paymentDescriptionString(s string) *string { return &s }
