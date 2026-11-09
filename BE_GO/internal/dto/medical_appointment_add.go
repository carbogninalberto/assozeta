package dto

import (
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ErrMedicalAppointmentAddNotPorted refuses request keys whose DRF fields are
// not ported: a non-null document (PrimaryKeyRelatedField with the tenant
// scope check) and medical_appointments_id (UUIDField with UniqueValidator).
var ErrMedicalAppointmentAddNotPorted = errors.New("medical appointment field not ported")

// MedicalAppointmentAddInput is MedicalAppointmentsSerializer's validated
// data; a nil pointer field was not sent (the model default applies) unless
// its Null flag says the request sent null.
type MedicalAppointmentAddInput struct {
	Region                string
	Date                  *string // ISO date; nil with DateNull false: the timezone.now() default
	DateNull              bool
	Notes, Sport, Address *string
	Meta                  any // the parsed request value
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
	if value, present := object.Lookup("medical_appointments_id"); present {
		_ = value
		return MedicalAppointmentAddInput{}, ErrMedicalAppointmentAddNotPorted
	}
	if value, present := object.Lookup("document"); present && value != nil {
		return MedicalAppointmentAddInput{}, ErrMedicalAppointmentAddNotPorted
	}
	var input MedicalAppointmentAddInput
	fields := map[string][]string{}
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
		return MedicalAppointmentAddInput{}, &MedicalAppointmentAddInvalid{Fields: fields}
	}
	return input, nil
}
