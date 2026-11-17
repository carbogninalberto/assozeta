package service

import (
	"context"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ResolvePaymentDescription computes the value persisted by Payment.save's
// description hook. It performs no reads/writes and owns no authorization or
// transaction. Preserve the two distinct metadata reads: a relationship reads
// stored payment metadata, then the placeholder override reads pending metadata.
func ResolvePaymentDescription(ctx context.Context, input domain.PaymentDescriptionInput) (*string, error) {
	text, _, err := resolvePaymentDescriptionValue(ctx, input, nil)
	return text, err
}

// resolvePaymentDescriptionValue is ResolvePaymentDescription for one save of
// a Payment instance: inMemory (when non-nil) is the Python value the previous
// save left on the description attribute instead of Description's text. The
// second result is the value the save leaves on the instance for its next save
// (nil when the attribute holds the text).
func resolvePaymentDescriptionValue(ctx context.Context, input domain.PaymentDescriptionInput, inMemory any) (*string, any, error) {
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	var value any
	switch {
	case inMemory != nil:
		// A later save of the same instance starts from its attribute, not the
		// stored text: a truthy non-string fails on .lower() before any write.
		value = inMemory
	case input.Description != nil:
		value = *input.Description
	default:
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
				return nil, nil, err
			}
			if value == nil {
				value = "Quota Corso"
			}
		case 3:
			// The Django branch dereferences destination_account_name, which is not
			// a CustomAccounts field. Do not invent a destination or silently save.
			message := ""
			if input.CustomAccounts != nil {
				if *input.CustomAccounts {
					message = "'CustomAccounts' object has no attribute 'destination_account_name'"
				} else {
					message = "'NoneType' object has no attribute 'name'"
				}
			}
			return nil, nil, &domain.PaymentDescriptionError{Message: message}
		}
	}
	if truthyReference(value) {
		text, ok := value.(string)
		if !ok {
			return nil, nil, &domain.PaymentDescriptionError{Message: "'" + dto.PaymentMiscTypeName(value) + "' object has no attribute 'lower'"}
		}
		if dto.PythonLower(text) == "quota corso" {
			replacement, exists, err := paymentJSONMember(ctx, input.Meta, "description")
			if err != nil {
				return nil, nil, err
			}
			if exists {
				value = replacement
			}
		}
	}
	if value == nil {
		return nil, nil, nil
	}
	// Django CharField prepares non-string values using str(). Reuse the tested
	// Python representation machinery, including ordered JSON containers.
	text, err := loginValueText(ctx, value)
	if err != nil {
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		return nil, nil, &domain.PaymentDescriptionError{}
	}
	var memory any
	if _, ok := value.(string); !ok {
		// The attribute keeps the Python object; the column got str() of it.
		memory = value
	}
	return &text, memory, nil
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
			return nil, &domain.PaymentDescriptionError{Message: "'NoneType' object has no attribute 'title'"}
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
			return nil, &domain.PaymentDescriptionError{Message: "sequence item 0: expected str instance, " + dto.PaymentMiscTypeName(value) + " found"}
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
				return nil, false, &domain.PaymentDescriptionError{Message: "list indices must be integers or slices, not str"}
			}
		}
	case string:
		if strings.Contains(v, key) {
			return nil, false, &domain.PaymentDescriptionError{Message: "string indices must be integers, not 'str'"}
		}
	default:
		return nil, false, &domain.PaymentDescriptionError{Message: "argument of type '" + dto.PaymentMiscTypeName(value) + "' is not iterable"}
	}
	return nil, false, nil
}
