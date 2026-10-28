package service

import (
	"context"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// Django runs every field validator before saving, including validators which
// read existing identifiers. Preserve that complete error map without writes.
func validatePaymentCategoryWrite(ctx context.Context, tx domain.PaymentCategoryTransaction, input dto.PaymentCategoryWrite, before *domain.PaymentCategoryState) error {
	// TenantScopedPrimaryKeyRelatedField raises PermissionDenied from
	// to_internal_value, before the serializer collects the other field errors.
	if _, invalid := input.ValidationErrors["sport_association"]; before != nil && input.AssociationID != nil && !invalid &&
		(before.AssociationID == nil || *input.AssociationID != *before.AssociationID) {
		exists, err := tx.AssociationExists(ctx, *input.AssociationID)
		if err != nil {
			return err
		}
		if exists {
			return domain.ErrPaymentCategoryForeignReference
		}
	}
	validation := make(map[string]any, len(input.ValidationErrors))
	for field, value := range input.ValidationErrors {
		validation[field] = value
	}
	if before == nil {
		if _, invalid := validation["name"]; !invalid {
			if input.Name == nil {
				validation["name"] = []string{dto.PaymentCategoryValidationRequired}
			} else if strings.TrimSpace(*input.Name) == "" {
				validation["name"] = []string{dto.PaymentCategoryValidationBlank}
			}
		}
		if !input.VAT.Present {
			validation["vat_management"] = []string{dto.PaymentCategoryValidationRequired}
		}
	}
	if input.VAT.Present && input.VAT.Null {
		validation["vat_management"] = []string{dto.PaymentCategoryValidationNull}
	}
	if input.CategoryID != nil {
		if before != nil {
			if *input.CategoryID != before.ID {
				validation["payment_category_id"] = []string{"immutable-category-id"}
			}
		} else {
			exists, err := tx.PaymentCategoryIDExists(ctx, *input.CategoryID)
			if err != nil {
				return err
			}
			if exists {
				validation["payment_category_id"] = []string{dto.PaymentCategoryValidationUniqueID}
			}
		}
	}
	if _, invalid := validation["sport_association"]; before != nil && input.AssociationPresent && !invalid {
		if input.AssociationID == nil {
			validation["sport_association"] = []string{"immutable-association"}
		} else if before.AssociationID == nil || *input.AssociationID != *before.AssociationID {
			exists, err := tx.AssociationExists(ctx, *input.AssociationID)
			if err != nil {
				return err
			}
			if !exists {
				validation["sport_association"] = []string{"related-does-not-exist:" + *input.AssociationID}
			}
		}
	}
	if input.VAT.Present && input.VAT.Value.ID != nil {
		vat, err := tx.VATByID(ctx, *input.VAT.Value.ID)
		if err != nil {
			return err
		}
		if vat != nil {
			nested := map[string]any{}
			if previous, ok := validation["vat_management"].(map[string]any); ok {
				for field, value := range previous {
					nested[field] = value
				}
			}
			nested["vat_management_id"] = []string{dto.PaymentCategoryValidationUniqueVAT}
			validation["vat_management"] = nested
		}
	}
	if len(validation) > 0 {
		return &dto.PaymentCategoryValidationError{Errors: validation}
	}
	return nil
}
