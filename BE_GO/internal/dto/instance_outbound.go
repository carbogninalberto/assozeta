package dto

import (
	"math/big"
)

// InstanceEmailTestInput is EmailTestSerializer's validated_data.
type InstanceEmailTestInput struct {
	Revision  *big.Int
	Action    string
	Recipient *string
}

// InstanceAdminEmailTest is EmailTestSerializer(data=body).is_valid(): revision
// IntegerField(min_value=0), action ChoiceField(connect, send), recipient
// EmailField(required=False, max_length=254); then validate() requires a
// recipient to send ({'recipient': [...]}).
func InstanceAdminEmailTest(body any) (*InstanceEmailTestInput, error) {
	object, err := instanceAdminObject(body)
	if err != nil {
		return nil, err
	}
	errs := map[string]any{}
	input := &InstanceEmailTestInput{}
	zero := int64(0)
	if value, ok := object.Lookup("revision"); !ok {
		errs["revision"] = []string{PaymentCategoryValidationRequired}
	} else if number, codes := drfIntegerField(value, &zero, nil); codes != nil {
		errs["revision"] = codes
	} else {
		input.Revision = number
	}
	if value, ok := object.Lookup("action"); !ok {
		errs["action"] = []string{PaymentCategoryValidationRequired}
	} else if choice, codes := drfChoiceField(value, "connect", "send"); codes != nil {
		errs["action"] = codes
	} else {
		input.Action = choice
	}
	if value, ok := object.Lookup("recipient"); ok {
		email := func(text string) []string {
			if !SupplierEmail(text) {
				return []string{SupplierValidationEmail}
			}
			return nil
		}
		recipient, codes := drfText{max: 254, invalid: SupplierValidationEmail, validators: []func(string) []string{email}}.run(value)
		if codes != nil {
			errs["recipient"] = codes
		} else {
			input.Recipient = &recipient
		}
	}
	if len(errs) > 0 {
		return nil, &InstanceAdminInvalid{Body: errs}
	}
	if input.Action == "send" && (input.Recipient == nil || *input.Recipient == "") {
		return nil, &InstanceAdminInvalid{Body: map[string]any{"recipient": []string{"Inserisci il destinatario del messaggio di prova."}}}
	}
	return input, nil
}
