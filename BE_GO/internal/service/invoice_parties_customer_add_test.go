package service

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// customerAddStore answers the lookups a multipart customer add reaches before
// its save: the owner's association and the uniqueness queries.
type customerAddStore struct {
	domain.InvoicePartyStore
	association string
}

func (s customerAddStore) OwnerAssociation(context.Context, string) (string, error) {
	return s.association, nil
}

func (s customerAddStore) CustomerInvoiceTaken(context.Context, string, int64, int64, string) (bool, error) {
	return false, nil
}

func (s customerAddStore) CustomerNumberTaken(context.Context, *int64, *int64, string) (bool, error) {
	return false, nil
}

func customerAddForm(values [][2]string, files ...domain.RequestField) domain.RequestObject {
	object := domain.RequestObject{}
	positions := map[string]int{}
	add := func(name string, value any) {
		if at, ok := positions[name]; ok {
			if object[at].All == nil {
				object[at].All = []any{object[at].Value}
			}
			object[at].Value = value
			object[at].All = append(object[at].All, value)
			return
		}
		positions[name] = len(object)
		object = append(object, domain.RequestField{Name: name, Value: value})
	}
	for _, value := range values {
		add(value[0], value[1])
	}
	for _, file := range files {
		add(file.Name, file.Value)
	}
	return object
}

func customerAddBase() [][2]string {
	return [][2]string{
		{"fiscal_year", "2031"},
		{"assignor_vat_number", "01234567890"},
		{"assignor_tax_code", "01234567890"},
		{"assignor_denomination", "ASD Alfa & Figli"},
		{"assignor_address", "Via Roma 1"},
		{"assignor_postal_code", "10100"},
		{"assignor_city", "Torino"},
		{"assignor_province", "TO"},
		{"assignor_contact_email", "alfa@example.test"},
		{"assignor_contact_phone", "0111234567"},
		{"transferor_tax_code", "RSSMRA80A01L219X"},
		{"transferor_denomination", "Mario Rossi <Ditta>"},
		{"transferor_address", "Via Po 2"},
		{"transferor_postal_code", "10123"},
		{"transferor_city", "Torino"},
		{"transferor_province", "TO"},
		{"transferor_contact_email", "mario.pec@example.test"},
		{"causal", "Quota & servizi"},
		{"lines", `[{"description": "Quota annuale", "quantity": 1, "unit_of_measure": "unita", "unit_price": 100.0, "vat": 22.0}]`},
		{"payment_condition", "TP02"},
		{"payment_modality", "MP05"},
	}
}

func customerAddPairs(overrides ...[2]string) [][2]string {
	pairs := customerAddBase()
	for _, override := range overrides {
		replaced := false
		for index := range pairs {
			if pairs[index][0] == override[0] {
				pairs[index][1], replaced = override[1], true
			}
		}
		if !replaced {
			pairs = append(pairs, override)
		}
	}
	return pairs
}

func customerAddRun(t *testing.T, object domain.RequestObject) error {
	t.Helper()
	store := customerAddStore{association: "00000000-0000-0000-0000-0000000000aa"}
	parties := NewInvoiceParties(store, time.Now, func() string { return "" })
	return parties.CustomerAdd(context.Background(), InvoicePartyRequest{
		Identity: domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: "owner", Role: domain.RoleAssociation}},
		Body:     object,
		Form:     true,
	})
}

func TestCustomerAddMultipartFieldErrors(t *testing.T) {
	file := domain.RequestField{Name: "allegato", Value: domain.RequestUpload{Name: "fattura.txt", Whole: true}}
	object := customerAddForm([][2]string{
		{"number", "x"}, {"lines", ""}, {"json", "{bad"}, {"e_invoice", "maybe"}, {"paid", ""},
		{"fiscal_year", "99999999999"}, {"payment_total_amount", "abc"}, {"payment_modality", "MP99"},
		{"creation_date", "ieri"}, {"customer_invoice_id", "zz"},
	}, file)
	err := customerAddRun(t, object)
	var invalid *dto.InvoicePartyValidationError
	if !errors.As(err, &invalid) {
		t.Fatalf("got %v, want a validation error", err)
	}
	want := dto.InvoicePartyErrors{
		"customer_invoice_id":  []string{dto.PaymentCategoryValidationUUID},
		"number":               []string{dto.DRFValidationInteger},
		"fiscal_year":          []string{dto.InvoicePartyMaxValue + "2147483647"},
		"e_invoice":            []string{dto.PaymentCategoryValidationBoolean},
		"lines":                []string{dto.CourseSubscriptionJSONInvalid},
		"payment_modality":     []string{"choice:MP99"},
		"payment_total_amount": []string{dto.DRFValidationNumber},
		"json":                 []string{dto.CourseSubscriptionJSONInvalid},
		"creation_date":        []string{dto.PaymentCategoryValidationDateTime},
	}
	if !reflect.DeepEqual(invalid.Errors, want) {
		t.Fatalf("errors %#v, want %#v", invalid.Errors, want)
	}
}

func TestCustomerAddFormWithoutFileIsServerError(t *testing.T) {
	object := customerAddForm(customerAddPairs([2]string{"number", "602"}))
	if err := customerAddRun(t, object); !errors.Is(err, domain.ErrInvoicePartyServer) {
		t.Fatalf("got %v, want the immutable QueryDict's server error", err)
	}
}

func TestCustomerAddMultipartFilePartsFieldErrors(t *testing.T) {
	upload := func(name, filename string) domain.RequestField {
		return domain.RequestField{Name: name, Value: domain.RequestUpload{Name: filename, Whole: true}}
	}
	object := customerAddForm(customerAddPairs([2]string{"number", "606"}),
		upload("causal", "c.txt"), upload("number", "n.txt"), upload("lines", "l.txt"),
		upload("allegato", "fattura.txt"))
	err := customerAddRun(t, object)
	var invalid *dto.InvoicePartyValidationError
	if !errors.As(err, &invalid) {
		t.Fatalf("got %v, want a validation error", err)
	}
	want := dto.InvoicePartyErrors{
		"number": []string{dto.DRFValidationInteger},
		"causal": []string{dto.PaymentCategoryValidationString},
		"lines":  []string{dto.CourseSubscriptionJSONInvalid},
	}
	if !reflect.DeepEqual(invalid.Errors, want) {
		t.Fatalf("errors %#v, want %#v", invalid.Errors, want)
	}
}

func TestCustomerAddMultipartReachesTheSave(t *testing.T) {
	file := domain.RequestField{Name: "allegato", Value: domain.RequestUpload{Name: "fattura.txt", Whole: true}}
	object := customerAddForm(customerAddPairs([2]string{"number", "601"}), file)
	if err := customerAddRun(t, object); !errors.Is(err, domain.ErrInvoicePartyNotPorted) {
		// The fake has no customer sync: a valid multipart add is refused there.
		t.Fatalf("got %v, want the save to be reached", err)
	}
}

func TestCustomerAddMultipartBlankTransmittingDateIsServerError(t *testing.T) {
	file := domain.RequestField{Name: "allegato", Value: domain.RequestUpload{Name: "fattura.txt", Whole: true}}
	object := customerAddForm(customerAddPairs([2]string{"number", "604"}, [2]string{"transmitting_date", ""}), file)
	if err := customerAddRun(t, object); !errors.Is(err, domain.ErrInvoicePartyServer) {
		t.Fatalf("got %v, want a server error", err)
	}
}

func TestCustomerAddMultipartContactRequired(t *testing.T) {
	file := domain.RequestField{Name: "allegato", Value: domain.RequestUpload{Name: "fattura.txt", Whole: true}}
	for name, pairs := range map[string][][2]string{
		"without transferor email": func() [][2]string {
			var result [][2]string
			for _, pair := range customerAddPairs([2]string{"number", "609"}) {
				if pair[0] != "transferor_contact_email" {
					result = append(result, pair)
				}
			}
			return result
		}(),
		"file only": nil,
	} {
		t.Run(name, func(t *testing.T) {
			err := customerAddRun(t, customerAddForm(pairs, file))
			var invalid *dto.InvoicePartyValidationError
			if !errors.As(err, &invalid) {
				t.Fatalf("got %v, want a validation error", err)
			}
			want := dto.InvoicePartyErrors{"non_field_errors": []string{
				dto.InvoicePartyText + "Either transferor_contact_email or id_transmitter must be provided!"}}
			if !reflect.DeepEqual(invalid.Errors, want) {
				t.Fatalf("errors %#v, want %#v", invalid.Errors, want)
			}
		})
	}
}
