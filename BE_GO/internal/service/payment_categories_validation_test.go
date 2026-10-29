package service

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// Expected maps were specified from real Django JWT/PG combined-error probes.
// All field validation must complete before any category, VAT or audit write.
func TestPaymentCategoryCombinedValidation(t *testing.T) {
	foreign := "00000000-0000-4000-8000-000000000999"
	for _, partial := range []bool{false, true} {
		t.Run(map[bool]string{false: "create", true: "update"}[partial], func(t *testing.T) {
			body := domain.RequestObject{
				{Name: "payment_category_id", Value: foreign},
				{Name: "sport_association", Value: foreign},
				{Name: "name", Value: ""},
				{Name: "vat_management", Value: domain.RequestObject{
					{Name: "vat_management_id", Value: paymentCategoryTestVAT},
					{Name: "vat", Value: json.Number("99")},
					{Name: "deduction", Value: json.Number("99")},
				}},
			}
			input, parseErr := dto.ParsePaymentCategoryWrite(body, partial)
			if parseErr == nil {
				t.Fatal("invalid scalar fields passed transport validation")
			}
			store := &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{
				association: paymentCategoryTestAssociation, associationExists: true, categoryExists: true,
				category: domain.PaymentCategoryState{ID: paymentCategoryTestCategory,
					AssociationID: stringPointer(paymentCategoryTestAssociation)},
				vat: &domain.PaymentCategoryVAT{ID: paymentCategoryTestVAT},
			}}
			flow := NewPaymentCategories(store, time.Now)
			var err error
			if partial {
				_, err = flow.Update(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, input, domain.LoginAuditContext{})
			} else {
				_, err = flow.Add(context.Background(), paymentCategoryIdentity(), input, domain.LoginAuditContext{})
			}
			categoryError := dto.PaymentCategoryValidationUniqueID
			if partial {
				categoryError = "immutable-category-id"
			}
			want := map[string]any{
				"payment_category_id": []string{categoryError},
				"name":                []string{dto.PaymentCategoryValidationBlank},
				"vat_management": map[string]any{
					"vat_management_id": []string{dto.PaymentCategoryValidationUniqueVAT},
					"vat":               []string{"choice:99"}, "deduction": []string{"choice:99"},
				},
			}
			var validation *dto.PaymentCategoryValidationError
			if partial {
				// The foreign association's PermissionDenied escapes before the
				// serializer raises the collected field errors.
				if !errors.Is(err, domain.ErrPaymentCategoryForeignReference) {
					t.Fatalf("error=%v want foreign reference denial", err)
				}
			} else if !errors.As(err, &validation) || !reflect.DeepEqual(validation.Errors, want) {
				t.Fatalf("error=%v validation=%#v want=%#v", err, validation, want)
			}
			if store.tx.inserted || store.tx.updated || store.tx.committed || len(store.tx.audits) != 0 {
				t.Fatal("invalid input caused durable effects", store.tx)
			}
		})
	}
}

func TestPaymentCategoryMissingTargetPrecedesImmutableID(t *testing.T) {
	store := &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: paymentCategoryTestAssociation}}
	input := dto.PaymentCategoryWrite{CategoryID: stringPointer("00000000-0000-4000-8000-000000000999")}
	_, err := NewPaymentCategories(store, time.Now).Update(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, input, domain.LoginAuditContext{})
	if !errors.Is(err, domain.ErrPaymentCategoryNotFound) {
		t.Fatalf("missing target was validated as an owned category: %v", err)
	}
}

func TestPaymentCategoryRepeatedDeleteRetainsSaveFailures(t *testing.T) {
	store := &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{
		association: paymentCategoryTestAssociation, fail: "write",
		category: domain.PaymentCategoryState{ID: paymentCategoryTestCategory, Deleted: true,
			AssociationID: stringPointer(paymentCategoryTestAssociation)},
	}}
	err := NewPaymentCategories(store, time.Now).Delete(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, domain.LoginAuditContext{})
	if !errors.Is(err, errPaymentCategoryTest) || store.tx.committed || len(store.tx.audits) != 0 {
		t.Fatal("repeated DELETE bypassed Django save failure", err, store.tx)
	}
}
