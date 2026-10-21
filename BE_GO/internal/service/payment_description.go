package service

import (
	"context"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ResolvePaymentDescription computes the value persisted by Payment.save's
// description hook. It performs no reads/writes and owns no authorization or
// transaction. Preserve the two distinct metadata reads: a relationship reads
// stored payment metadata, then the placeholder override reads pending metadata.
func ResolvePaymentDescription(ctx context.Context, input domain.PaymentDescriptionInput) (*string, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	var value any
	if input.Description != nil {
		value = *input.Description
	} else {
		switch input.Subject {
		case 0:
			if input.CategoryName != nil {
				value = *input.CategoryName
			}
		case 1:
			value = "Iscrizione"
		case 2:
			var err error
			value, err = paymentCourseDescription(ctx, input)
			if err != nil {
				return nil, err
			}
			if value == nil {
				value = "Quota Corso"
			}
		case 3:
			// The Django branch dereferences destination_account_name, which is not
			// a CustomAccounts field. Do not invent a destination or silently save.
			return nil, domain.ErrPaymentDescription
		}
	}
	if truthyReference(value) {
		text, ok := value.(string)
		if !ok {
			return nil, domain.ErrPaymentDescription
		}
		if strings.ToLower(text) == "quota corso" {
			replacement, exists, err := paymentJSONMember(ctx, input.Meta, "description")
			if err != nil {
				return nil, err
			}
			if exists {
				value = replacement
			}
		}
	}
	if value == nil {
		return nil, nil
	}
	// Django CharField prepares non-string values using str(). Reuse the tested
	// Python representation machinery, including ordered JSON containers.
	text, err := loginValueText(ctx, value)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, domain.ErrPaymentDescription
	}
	return &text, nil
}

func paymentCourseDescription(ctx context.Context, input domain.PaymentDescriptionInput) (any, error) {
	for _, relation := range input.Related {
		if relation == nil {
			continue
		}
		value, exists, err := paymentJSONMember(ctx, relation.Meta, "description")
		if err != nil {
			return nil, err
		}
		if exists {
			return value, nil
		}
		if relation.MissingCarnet {
			return nil, domain.ErrPaymentDescription
		}
		if relation.Title == nil {
			return nil, nil
		}
		return *relation.Title, nil
	}
	value, exists, err := paymentJSONMember(ctx, input.Course, "label")
	if err != nil {
		return nil, err
	}
	if exists {
		label, ok := value.(string)
		if !ok {
			return nil, domain.ErrPaymentDescription
		}
		// This path only runs when the existing description is NULL. The trailing
		// separator is observable legacy behavior, including for an empty label.
		return label + ", ", nil
	}
	return nil, nil
}

// Python's `if value and key in value: value[key]` is not dict.get(): nonempty
// scalars fail membership, while strings/lists fail indexing only on a key hit.
func paymentJSONMember(ctx context.Context, value any, key string) (any, bool, error) {
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	if !truthyReference(value) {
		return nil, false, nil
	}
	switch v := value.(type) {
	case domain.RequestObject:
		for _, field := range v {
			if err := ctx.Err(); err != nil {
				return nil, false, err
			}
			if field.Name == key {
				return field.Value, true, nil
			}
		}
	case []any:
		for _, item := range v {
			if err := ctx.Err(); err != nil {
				return nil, false, err
			}
			if s, ok := item.(string); ok && s == key {
				return nil, false, domain.ErrPaymentDescription
			}
		}
	case string:
		if strings.Contains(v, key) {
			return nil, false, domain.ErrPaymentDescription
		}
	default:
		return nil, false, domain.ErrPaymentDescription
	}
	return nil, false, nil
}
