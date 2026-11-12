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

// invoicePartyNestedStore answers the lookups of a nested supplier's
// validation: its supplier_id uniqueness and its references.
type invoicePartyNestedStore struct {
	domain.InvoicePartyStore
	taken        map[string]bool
	associations map[string]bool
	groups       map[string]*string
	// related is the nested payment reference lookup: id -> owner (nil when
	// the model has no association); an absent id is not found.
	related      map[string]*string
	paymentTaken map[string]bool
	failRelated  error
}

func (s invoicePartyNestedStore) SupplierIDTaken(_ context.Context, id string, _ *string) (bool, error) {
	return s.taken[id], nil
}

func (s invoicePartyNestedStore) AssociationExists(_ context.Context, id string) (bool, error) {
	return s.associations[id], nil
}

func (s invoicePartyNestedStore) GroupAssociation(_ context.Context, id string) (*string, bool, error) {
	owner, found := s.groups[id]
	return owner, found, nil
}

func invoicePartyNestedErrors(t *testing.T, store invoicePartyNestedStore, body any, partial, form bool) (dto.InvoicePartyErrors, error) {
	t.Helper()
	association := "00000000-0000-0000-0000-0000000000aa"
	parties := NewInvoiceParties(store, time.Now, func() string { return "" })
	input, fieldErrors := dto.ParseSupplierInvoice(body, partial, form)
	err := parties.nestedSupplier(context.Background(), input, fieldErrors, association, partial, nil)
	return fieldErrors, err
}

func TestNestedSupplierValidationJoinsSupplierIDErrors(t *testing.T) {
	const taken = "12345678-1234-5678-9abc-def012345678"
	store := invoicePartyNestedStore{taken: map[string]bool{taken: true}}
	body, err := dto.ParseReferenceJSON([]byte(`{"supplier":{"supplier_id":"abc","email":"x"}}`))
	if err != nil {
		t.Fatal(err)
	}
	fieldErrors, err := invoicePartyNestedErrors(t, store, body, false, false)
	if err != nil {
		t.Fatalf("nested supplier: %v", err)
	}
	want := map[string]any{"supplier_id": []string{dto.PaymentCategoryValidationUUID},
		"name": []string{dto.PaymentCategoryValidationRequired}, "email": []string{dto.SupplierValidationEmail}}
	if !reflect.DeepEqual(fieldErrors["supplier"], want) {
		t.Fatalf("supplier errors %#v, want %#v", fieldErrors["supplier"], want)
	}
	body, err = dto.ParseReferenceJSON([]byte(`{"supplier":{"name":"N","supplier_id":"` + taken + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	fieldErrors, err = invoicePartyNestedErrors(t, store, body, false, false)
	if err != nil {
		t.Fatalf("nested supplier: %v", err)
	}
	want = map[string]any{"supplier_id": []string{dto.InvoicePartyUniqueSupplierID}}
	if !reflect.DeepEqual(fieldErrors["supplier"], want) {
		t.Fatalf("taken supplier_id errors %#v, want %#v", fieldErrors["supplier"], want)
	}
	body, err = dto.ParseReferenceJSON([]byte(`{"supplier":{"name":"N","supplier_id":"00000000-0000-0000-0000-000000000005"}}`))
	if err != nil {
		t.Fatal(err)
	}
	fieldErrors, err = invoicePartyNestedErrors(t, store, body, false, false)
	if err != nil || fieldErrors["supplier"] != nil {
		t.Fatalf("a fresh nested supplier_id: %#v %v", fieldErrors["supplier"], err)
	}
}

func TestNestedSupplierValidationFormRules(t *testing.T) {
	store := invoicePartyNestedStore{}
	form := func(raw ...domain.RequestField) domain.RequestObject { return domain.RequestObject(raw) }
	blank := form(domain.RequestField{Name: "supplier.name", Value: ""}, domain.RequestField{Name: "supplier.email", Value: "x"},
		domain.RequestField{Name: "notes", Value: "n"})
	fieldErrors, err := invoicePartyNestedErrors(t, store, blank, true, true)
	if err != nil {
		t.Fatalf("nested supplier form: %v", err)
	}
	want := map[string]any{"name": []string{dto.PaymentCategoryValidationBlank}, "email": []string{dto.SupplierValidationEmail}}
	if !reflect.DeepEqual(fieldErrors["supplier"], want) {
		t.Fatalf("supplier errors %#v, want %#v", fieldErrors["supplier"], want)
	}
	absentBooleans := form(domain.RequestField{Name: "supplier.deleted", Value: ""}, domain.RequestField{Name: "supplier.email", Value: ""})
	if fieldErrors, err = invoicePartyNestedErrors(t, store, absentBooleans, true, true); err != nil || fieldErrors["supplier"] != nil {
		t.Fatalf("blank deleted and email: %#v %v", fieldErrors["supplier"], err)
	}
	fullErrors := form(domain.RequestField{Name: "supplier.name", Value: ""}, domain.RequestField{Name: "supplier.supplier_id", Value: "abc"},
		domain.RequestField{Name: "supplier.deleted", Value: ""})
	fieldErrors, err = invoicePartyNestedErrors(t, store, fullErrors, false, true)
	if err != nil {
		t.Fatalf("nested supplier form: %v", err)
	}
	want = map[string]any{"supplier_id": []string{dto.PaymentCategoryValidationUUID}, "name": []string{dto.PaymentCategoryValidationBlank}}
	if !reflect.DeepEqual(fieldErrors["supplier"], want) {
		t.Fatalf("supplier errors %#v, want %#v", fieldErrors["supplier"], want)
	}
}

func TestNestedSupplierValidationKeepsForeignReference(t *testing.T) {
	const foreign = "00000000-0000-0000-0000-0000000000bb"
	store := invoicePartyNestedStore{associations: map[string]bool{foreign: true}}
	body, err := dto.ParseReferenceJSON([]byte(`{"supplier":{"name":"N","sport_association":"` + foreign + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = invoicePartyNestedErrors(t, store, body, false, false); !errors.Is(err, domain.ErrInvoicePartyForeignReference) {
		t.Fatalf("foreign association error %v", err)
	}
}

// invoicePartyNestedPaymentAssociation is the caller's association.
const invoicePartyNestedPaymentAssociation = "00000000-0000-0000-0000-0000000000aa"

func (s invoicePartyNestedStore) NestedPaymentRelated(_ context.Context, _, id string, _ *string) (bool, *string, error) {
	if s.failRelated != nil {
		return false, nil, s.failRelated
	}
	owner, found := s.related[id]
	return found, owner, nil
}

func (s invoicePartyNestedStore) NestedPaymentTaken(_ context.Context, _, id string, _ *string) (bool, error) {
	return s.paymentTaken[id], nil
}

func invoicePartyNestedPaymentErrors(t *testing.T, store invoicePartyNestedStore, body any, partial, form bool) (dto.InvoicePartyErrors, error) {
	t.Helper()
	parties := NewInvoiceParties(store, time.Now, func() string { return "" })
	input, fieldErrors := dto.ParseSupplierInvoice(body, partial, form)
	err := parties.nestedPayment(context.Background(), input, fieldErrors, invoicePartyNestedPaymentAssociation, partial, nil)
	return fieldErrors, err
}

func TestNestedPaymentValidationJoinsFieldErrors(t *testing.T) {
	body, err := dto.ParseReferenceJSON([]byte(`{"payment":{"amount":"x","user":{"email":"y"}}}`))
	if err != nil {
		t.Fatal(err)
	}
	fieldErrors, err := invoicePartyNestedPaymentErrors(t, invoicePartyNestedStore{}, body, true, false)
	if err != nil {
		t.Fatalf("nested payment: %v", err)
	}
	want := map[string]any{"amount": []string{dto.DRFValidationNumber},
		"user": map[string]any{"email": []string{dto.SupplierValidationEmail}}}
	if !reflect.DeepEqual(fieldErrors["payment"], want) {
		t.Fatalf("payment errors %#v, want %#v", fieldErrors["payment"], want)
	}
}

func TestNestedPaymentValidationAcceptsMissingNestedValue(t *testing.T) {
	body, err := dto.ParseReferenceJSON([]byte(`{"notes":"n"}`))
	if err != nil {
		t.Fatal(err)
	}
	fieldErrors, err := invoicePartyNestedPaymentErrors(t, invoicePartyNestedStore{}, body, true, false)
	if err != nil || fieldErrors["payment"] != nil {
		t.Fatalf("absent nested payment %#v %v", fieldErrors["payment"], err)
	}
}

func TestNestedPaymentValidationKeepsForeignReference(t *testing.T) {
	const foreign = "00000000-0000-0000-0000-0000000000bb"
	owner := foreign
	store := invoicePartyNestedStore{related: map[string]*string{foreign: &owner}}
	body, err := dto.ParseReferenceJSON([]byte(`{"payment":{"sport_association":"` + foreign + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = invoicePartyNestedPaymentErrors(t, store, body, true, false); !errors.Is(err, domain.ErrInvoicePartyForeignReference) {
		t.Fatalf("foreign association error %v", err)
	}
}

func TestNestedPaymentValidationAcceptsOwnAndOwnerlessReferences(t *testing.T) {
	const document = "00000000-0000-0000-0000-0000000000cc"
	const missing = "00000000-0000-0000-0000-0000000000dd"
	own := invoicePartyNestedPaymentAssociation
	store := invoicePartyNestedStore{related: map[string]*string{own: &own, document: nil}}
	body, err := dto.ParseReferenceJSON([]byte(`{"payment":{"sport_association":"` + own + `","document_pdf":"` + document + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	fieldErrors, err := invoicePartyNestedPaymentErrors(t, store, body, true, false)
	if err != nil || fieldErrors["payment"] != nil {
		t.Fatalf("own references %#v %v", fieldErrors["payment"], err)
	}
	body, err = dto.ParseReferenceJSON([]byte(`{"payment":{"payment_category":"` + missing + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	fieldErrors, err = invoicePartyNestedPaymentErrors(t, store, body, true, false)
	if err != nil {
		t.Fatalf("missing reference: %v", err)
	}
	want := map[string]any{"payment_category": []string{"related-does-not-exist:" + missing}}
	if !reflect.DeepEqual(fieldErrors["payment"], want) {
		t.Fatalf("payment errors %#v, want %#v", fieldErrors["payment"], want)
	}
}

func TestNestedPaymentValidationJoinsTakenPaymentID(t *testing.T) {
	const taken = "00000000-0000-0000-0000-0000000000ee"
	store := invoicePartyNestedStore{paymentTaken: map[string]bool{taken: true}}
	body, err := dto.ParseReferenceJSON([]byte(`{"payment":{"payment_id":"` + taken + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	fieldErrors, err := invoicePartyNestedPaymentErrors(t, store, body, true, false)
	if err != nil {
		t.Fatalf("taken payment_id: %v", err)
	}
	want := map[string]any{"payment_id": []string{"unique:payment:payment id"}}
	if !reflect.DeepEqual(fieldErrors["payment"], want) {
		t.Fatalf("payment errors %#v, want %#v", fieldErrors["payment"], want)
	}
	// A valid partial nested payment joins nothing.
	body, err = dto.ParseReferenceJSON([]byte(`{"payment":{}}`))
	if err != nil {
		t.Fatal(err)
	}
	fieldErrors, err = invoicePartyNestedPaymentErrors(t, store, body, true, false)
	if err != nil || fieldErrors["payment"] != nil {
		t.Fatalf("valid nested payment %#v %v", fieldErrors["payment"], err)
	}
}

func TestNestedPaymentValidationPropagatesLookupError(t *testing.T) {
	failure := errors.New("database down")
	const foreign = "00000000-0000-0000-0000-0000000000bb"
	store := invoicePartyNestedStore{failRelated: failure}
	body, err := dto.ParseReferenceJSON([]byte(`{"payment":{"sport_association":"` + foreign + `"}}`))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = invoicePartyNestedPaymentErrors(t, store, body, true, false); !errors.Is(err, failure) {
		t.Fatalf("lookup error %v", err)
	}
}

func TestNestedPaymentValidationFormFields(t *testing.T) {
	body := domain.RequestObject{
		{Name: "payment.amount", Value: "x"},
		{Name: "payment.user.email", Value: "y"},
		{Name: "payment.instructor.documents", Value: "doc", All: []any{"abc", "doc"}},
	}
	fieldErrors, err := invoicePartyNestedPaymentErrors(t, invoicePartyNestedStore{}, body, true, true)
	if err != nil {
		t.Fatalf("nested payment form: %v", err)
	}
	want := map[string]any{"amount": []string{dto.DRFValidationNumber},
		"user":       map[string]any{"email": []string{dto.SupplierValidationEmail}},
		"instructor": map[string]any{"documents": []string{"related-invalid-uuid:abc"}}}
	if !reflect.DeepEqual(fieldErrors["payment"], want) {
		t.Fatalf("payment errors %#v, want %#v", fieldErrors["payment"], want)
	}
}
