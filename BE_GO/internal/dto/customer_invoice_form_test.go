package dto

import (
	"net/url"
	"reflect"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// customerInvoiceFormBase is the probed BASE body's text fields.
func customerInvoiceFormBase() [][2]string {
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

// customerInvoiceFormRequest builds the multipart form object the request parser
// hands the view: text parts first, then file parts (a repeated key keeps its
// last value, so a file with the same name wins).
func customerInvoiceFormRequest(t *testing.T, pairs [][2]string, files ...domain.RequestField) domain.RequestObject {
	t.Helper()
	values := url.Values{}
	for _, pair := range pairs {
		values.Add(pair[0], pair[1])
	}
	return instanceAdminFormBody(t, values.Encode(), files...)
}

func customerInvoiceFormPairs(overrides ...[2]string) [][2]string {
	pairs := customerInvoiceFormBase()
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

func customerInvoiceFormWithout(drop string, overrides ...[2]string) [][2]string {
	pairs := customerInvoiceFormPairs(overrides...)
	result := pairs[:0]
	for _, pair := range pairs {
		if pair[0] != drop {
			result = append(result, pair)
		}
	}
	return result
}

func customerInvoiceFormParsed(t *testing.T, raw string) any {
	t.Helper()
	value, err := ParseReferenceJSON([]byte(raw))
	if err != nil {
		t.Fatalf("parse %s: %v", raw, err)
	}
	return value
}

func customerInvoiceFormExpected(t *testing.T) map[string]any {
	t.Helper()
	expected := map[string]any{}
	for _, pair := range customerInvoiceFormBase() {
		expected[pair[0]] = pair[1]
	}
	expected["lines"] = customerInvoiceFormParsed(t, `[{"description": "Quota annuale", "quantity": 1, "unit_of_measure": "unita", "unit_price": 100.0, "vat": 22.0}]`)
	return expected
}

func customerInvoiceFormResult(object domain.RequestObject) map[string]any {
	result := map[string]any{}
	for _, field := range object {
		result[field.Name] = field.Value
	}
	return result
}

func customerInvoiceFormUpload(name, filename string) domain.RequestField {
	return domain.RequestField{Name: name, Value: domain.RequestUpload{Name: filename, Whole: true}}
}

func TestCustomerInvoiceFormDataProbes(t *testing.T) {
	file := customerInvoiceFormUpload("allegato", "fattura.txt")
	base := func() map[string]any { return customerInvoiceFormExpected(t) }
	for _, test := range []struct {
		name      string
		pairs     [][2]string
		files     []domain.RequestField
		expected  map[string]any
		errs      InvoicePartyErrors
		notPorted bool
	}{
		{
			name:  "number with file",
			pairs: customerInvoiceFormPairs([2]string{"number", "601"}),
			files: []domain.RequestField{file},
			expected: func() map[string]any {
				expected := base()
				expected["allegato"] = domain.RequestUpload{Name: "fattura.txt", Whole: true}
				expected["number"] = "601"
				expected["e_invoice"], expected["paid"], expected["transmitted"] = false, false, false
				return expected
			}(),
			errs: InvoicePartyErrors{},
		},
		{
			name:  "number without file",
			pairs: customerInvoiceFormPairs([2]string{"number", "602"}),
			expected: func() map[string]any {
				expected := base()
				expected["number"] = "602"
				expected["e_invoice"], expected["paid"], expected["transmitted"] = false, false, false
				return expected
			}(),
			errs: InvoicePartyErrors{},
		},
		{
			name: "blank values and model defaults",
			pairs: customerInvoiceFormPairs(
				[2]string{"number", "603"}, [2]string{"assignor_vat_number", ""}, [2]string{"assignor_address", ""},
				[2]string{"prefix", ""}, [2]string{"e_invoice", ""}, [2]string{"transmitted", "true"},
				[2]string{"payment_total_amount", ""}, [2]string{"creation_date", ""}, [2]string{"xml", ""},
				[2]string{"pdf", ""}, [2]string{"payment", ""}, [2]string{"customer_invoice_id", ""},
				[2]string{"payment_condition", ""}, [2]string{"json", `{"k": [1, 2.5]}`},
				[2]string{"assignor_country", ""}, [2]string{"id_transmitter", ""}, [2]string{"causal", ""}),
			files: []domain.RequestField{file},
			expected: func() map[string]any {
				expected := base()
				expected["allegato"] = domain.RequestUpload{Name: "fattura.txt", Whole: true}
				expected["number"] = "603"
				delete(expected, "assignor_address")
				expected["assignor_vat_number"] = nil
				expected["prefix"] = nil
				expected["transmitted"] = "true"
				expected["xml"] = nil
				expected["pdf"] = nil
				expected["payment"] = nil
				expected["payment_condition"] = nil
				expected["id_transmitter"] = nil
				expected["causal"] = nil
				expected["json"] = customerInvoiceFormParsed(t, `{"k": [1, 2.5]}`)
				expected["paid"] = false
				return expected
			}(),
			errs: InvoicePartyErrors{},
		},
		{
			name: "field errors",
			pairs: [][2]string{
				{"number", "x"}, {"lines", ""}, {"json", "{bad"}, {"e_invoice", "maybe"}, {"paid", ""},
				{"fiscal_year", "99999999999"}, {"payment_total_amount", "abc"}, {"payment_modality", "MP99"},
				{"creation_date", "ieri"}, {"customer_invoice_id", "zz"},
			},
			files: []domain.RequestField{file},
			expected: map[string]any{
				"allegato": domain.RequestUpload{Name: "fattura.txt", Whole: true},
				"number":   "x", "e_invoice": "maybe", "fiscal_year": "99999999999", "payment_total_amount": "abc",
				"payment_modality": "MP99", "creation_date": "ieri", "customer_invoice_id": "zz", "transmitted": false,
			},
			errs: InvoicePartyErrors{"lines": []string{CourseSubscriptionJSONInvalid}, "json": []string{CourseSubscriptionJSONInvalid}},
		},
		{
			name:  "blank transmitting date is copied for the view",
			pairs: customerInvoiceFormPairs([2]string{"number", "604"}, [2]string{"transmitting_date", ""}),
			files: []domain.RequestField{file},
			expected: func() map[string]any {
				expected := base()
				expected["allegato"] = domain.RequestUpload{Name: "fattura.txt", Whole: true}
				expected["number"] = "604"
				expected["transmitting_date"] = ""
				expected["e_invoice"], expected["paid"], expected["transmitted"] = false, false, false
				return expected
			}(),
			errs: InvoicePartyErrors{},
		},
		{
			name: "dates and booleans",
			pairs: customerInvoiceFormPairs(
				[2]string{"number", "605"}, [2]string{"transmitting_date", "2026-03-04"},
				[2]string{"payment_expiry_date", "2026-04-30"}, [2]string{"paid", "on"}, [2]string{"e_invoice", "true"}),
			files: []domain.RequestField{file},
			expected: func() map[string]any {
				expected := base()
				expected["allegato"] = domain.RequestUpload{Name: "fattura.txt", Whole: true}
				expected["number"] = "605"
				expected["transmitting_date"] = "2026-03-04"
				expected["payment_expiry_date"] = "2026-04-30"
				expected["paid"] = "on"
				expected["e_invoice"] = "true"
				expected["transmitted"] = false
				return expected
			}(),
			errs: InvoicePartyErrors{},
		},
		{
			name:  "file parts win the key",
			pairs: customerInvoiceFormPairs([2]string{"number", "606"}),
			files: []domain.RequestField{
				customerInvoiceFormUpload("causal", "c.txt"), customerInvoiceFormUpload("number", "n.txt"),
				customerInvoiceFormUpload("lines", "l.txt"), file,
			},
			expected: func() map[string]any {
				expected := base()
				expected["allegato"] = domain.RequestUpload{Name: "fattura.txt", Whole: true}
				expected["number"] = domain.RequestUpload{Name: "n.txt", Whole: true}
				expected["causal"] = domain.RequestUpload{Name: "c.txt", Whole: true}
				delete(expected, "lines")
				expected["e_invoice"], expected["paid"], expected["transmitted"] = false, false, false
				return expected
			}(),
			errs: InvoicePartyErrors{"lines": []string{CourseSubscriptionJSONInvalid}},
		},
		{
			name:  "repeated number keeps the last value",
			pairs: append(customerInvoiceFormPairs(), [2]string{"number", "1"}, [2]string{"number", "607"}),
			files: []domain.RequestField{file},
			expected: func() map[string]any {
				expected := base()
				expected["allegato"] = domain.RequestUpload{Name: "fattura.txt", Whole: true}
				expected["number"] = "607"
				expected["e_invoice"], expected["paid"], expected["transmitted"] = false, false, false
				return expected
			}(),
			errs: InvoicePartyErrors{},
		},
		{
			name:  "blank transferor email stays empty",
			pairs: customerInvoiceFormPairs([2]string{"number", "608"}, [2]string{"transferor_contact_email", ""}),
			files: []domain.RequestField{file},
			expected: func() map[string]any {
				expected := base()
				expected["allegato"] = domain.RequestUpload{Name: "fattura.txt", Whole: true}
				expected["number"] = "608"
				expected["transferor_contact_email"] = ""
				expected["e_invoice"], expected["paid"], expected["transmitted"] = false, false, false
				return expected
			}(),
			errs: InvoicePartyErrors{},
		},
		{
			name:  "without transferor email",
			pairs: customerInvoiceFormWithout("transferor_contact_email", [2]string{"number", "609"}),
			files: []domain.RequestField{file},
			expected: func() map[string]any {
				expected := base()
				expected["allegato"] = domain.RequestUpload{Name: "fattura.txt", Whole: true}
				delete(expected, "transferor_contact_email")
				expected["number"] = "609"
				expected["e_invoice"], expected["paid"], expected["transmitted"] = false, false, false
				return expected
			}(),
			errs: InvoicePartyErrors{},
		},
		{
			name:  "file only",
			pairs: nil,
			files: []domain.RequestField{file},
			expected: map[string]any{
				"allegato":  domain.RequestUpload{Name: "fattura.txt", Whole: true},
				"e_invoice": false, "paid": false, "transmitted": false,
			},
			errs: InvoicePartyErrors{},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			object := customerInvoiceFormRequest(t, test.pairs, test.files...)
			data, errs, notPorted := CustomerInvoiceFormData(object)
			if notPorted != test.notPorted {
				t.Fatalf("notPorted %v, want %v", notPorted, test.notPorted)
			}
			if !reflect.DeepEqual(errs, test.errs) {
				t.Fatalf("errors %#v, want %#v", errs, test.errs)
			}
			if got := customerInvoiceFormResult(data); !reflect.DeepEqual(got, test.expected) {
				t.Fatalf("converted %#v, want %#v", got, test.expected)
			}
		})
	}
}

func TestCustomerInvoiceFormDataDeepJSONNotPorted(t *testing.T) {
	deep := strings.Repeat("[", 501) + "0" + strings.Repeat("]", 501)
	object := customerInvoiceFormRequest(t, [][2]string{{"number", "1"}, {"lines", deep}}, customerInvoiceFormUpload("allegato", "a.txt"))
	if _, _, notPorted := CustomerInvoiceFormData(object); !notPorted {
		t.Fatal("deep JSON text is not ported")
	}
}

func TestInvoicePartyHelpersRejectUploads(t *testing.T) {
	upload := domain.RequestUpload{Name: "a.txt", Whole: true}
	if _, codes := modelTextValue(SupplierField{Name: "x", Max: 10}, upload, false); !reflect.DeepEqual(codes,
		[]string{PaymentCategoryValidationString}) {
		t.Fatalf("modelTextValue upload codes %v", codes)
	}
	if _, codes := drfInteger(upload, true, 0, 0); !reflect.DeepEqual(codes, []string{DRFValidationInteger}) {
		t.Fatalf("drfInteger upload codes %v", codes)
	}
	if _, code := drfBoolean(upload, false); code != PaymentCategoryValidationBoolean {
		t.Fatalf("drfBoolean upload code %q", code)
	}
	if _, code := drfDecimal(upload, 9, 2); code != DRFValidationNumber {
		t.Fatalf("drfDecimal upload code %q", code)
	}
	if _, ok := drfUUID(upload); ok {
		t.Fatal("drfUUID accepted an upload")
	}
	if _, code := drfDateTime(upload, true); code != PaymentCategoryValidationDateTime {
		t.Fatalf("drfDateTime upload code %q", code)
	}
	if _, code := drfRelated(upload, true); code != "related-invalid-uuid:a.txt" {
		t.Fatalf("drfRelated upload code %q", code)
	}
	_, errs := ParseCustomerInvoiceAdd(domain.RequestObject{{Name: "payment_modality", Value: upload}})
	if !reflect.DeepEqual(errs["payment_modality"], []string{"choice:a.txt"}) {
		t.Fatalf("choice upload errors %#v", errs["payment_modality"])
	}
}
