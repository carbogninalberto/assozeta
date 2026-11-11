package dto

import (
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// MedicalAppointmentAddInput is MedicalAppointmentsSerializer's validated
// data; a nil pointer field was not sent (the model default applies) unless
// its Null flag says the request sent null.
type MedicalAppointmentAddInput struct {
	Region                string
	Date                  *string // ISO date; nil with DateNull false: the timezone.now() default
	DateNull              bool
	Notes, Sport, Address *string
	ID, Document          *string // validated pks (existence and uniqueness are the service's)
	Meta                  any     // the parsed request value
	MetaPresent           bool
	CreationDate          *time.Time
}

// MedicalAppointmentAddInvalid holds the DRF field error codes.
type MedicalAppointmentAddInvalid struct{ Fields map[string][]string }

func (e *MedicalAppointmentAddInvalid) Error() string { return "invalid medical appointment" }

// ParseMedicalAppointmentAdd validates a JSON object body with
// MedicalAppointmentsSerializer (fields '__all__'); subscription is the
// view's own value and always valid.
func ParseMedicalAppointmentAdd(object domain.RequestObject) (MedicalAppointmentAddInput, error) {
	var input MedicalAppointmentAddInput
	fields := map[string][]string{}
	if value, present := object.Lookup("medical_appointments_id"); present {
		if value == nil {
			fields["medical_appointments_id"] = []string{PaymentCategoryValidationNull}
		} else if id, ok := medicalAppointmentAddUUID(value); ok {
			input.ID = &id
		} else {
			fields["medical_appointments_id"] = []string{"related-invalid-uuid:" + PythonStr(value)}
		}
	}
	if value, present := object.Lookup("document"); present && value != "" && value != nil {
		// PrimaryKeyRelatedField: '' is None; the tenant check passes for a
		// Document (no owning association); existence is the service's.
		if id, code := supplierRelated(value); code != "" {
			fields["document"] = []string{code}
		} else {
			input.Document = &id
		}
	}
	if value, present := object.Lookup("region"); !present {
		fields["region"] = []string{PaymentCategoryValidationRequired}
	} else if text, codes := modelTextValue(SupplierField{Name: "region", Max: 100}, value, false); len(codes) > 0 {
		fields["region"] = codes
	} else {
		input.Region = *text
	}
	if value, present := object.Lookup("date"); present {
		if value == nil {
			input.DateNull = true
		} else if parsed, ok := ParseSubscriptionMembershipDate(value); ok {
			input.Date = &parsed
		} else {
			fields["date"] = []string{SubscriptionCreateDateFormat}
		}
	}
	for _, field := range []struct {
		spec   SupplierField
		target **string
	}{{SupplierField{Name: "notes", Null: true}, &input.Notes}, {SupplierField{Name: "sport", Max: 255, Null: true, Blank: true}, &input.Sport},
		{SupplierField{Name: "address", Max: 255, Null: true, Blank: true}, &input.Address}} {
		value, present := object.Lookup(field.spec.Name)
		if !present {
			continue
		}
		text, codes := modelTextValue(field.spec, value, false)
		if len(codes) > 0 {
			fields[field.spec.Name] = codes
			continue
		}
		*field.target = text
	}
	if value, present := object.Lookup("meta"); present {
		input.Meta, input.MetaPresent = value, true
	}
	if value, present := object.Lookup("creation_date"); present {
		if value == nil {
			fields["creation_date"] = []string{PaymentCategoryValidationNull}
		} else if text, ok := value.(string); !ok {
			fields["creation_date"] = []string{PaymentCategoryValidationDateTime}
		} else if parsed, err := parsePaymentCategoryDate(text); err != nil {
			if errors.Is(err, errPaymentCategoryDateTimeOverflow) {
				fields["creation_date"] = []string{PaymentCategoryValidationDateTimeOverflow}
			} else {
				fields["creation_date"] = []string{PaymentCategoryValidationDateTime}
			}
		} else {
			utc := parsed.UTC()
			input.CreationDate = &utc
		}
	}
	if len(fields) > 0 {
		return input, &MedicalAppointmentAddInvalid{Fields: fields}
	}
	return input, nil
}

// medicalAppointmentAddUUID is UUIDField.to_internal_value: ints (a bool is
// one) and strings; anything else is invalid.
func medicalAppointmentAddUUID(value any) (string, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return "00000000-0000-0000-0000-000000000001", true
		}
		return "00000000-0000-0000-0000-000000000000", true
	case string, json.Number:
		if number, ok := v.(json.Number); ok && strings.ContainsAny(string(number), ".eE") {
			return "", false
		}
		return supplierUUID(v)
	}
	return "", false
}
