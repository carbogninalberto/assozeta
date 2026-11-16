package dto

import (
	"encoding/json"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// customerInvoiceDateObject is a valid invoice-customers/add body with the two
// dates under test.
func customerInvoiceDateObject(dates ...[2]string) domain.RequestObject {
	object := domain.RequestObject{
		{Name: "number", Value: json.Number("700")},
		{Name: "fiscal_year", Value: json.Number("2031")},
		{Name: "transferor_contact_email", Value: "mario.pec@example.test"},
	}
	for _, date := range dates {
		object = append(object, domain.RequestField{Name: date[0], Value: date[1]})
	}
	return object
}

// The view rewrites both dates with strftime('%d/%m/%Y') and the serializer's
// DateField re-reads them with strptime, which needs four year digits: glibc
// leaves a year below 1000 unpadded, so the field rejects it; the BSD library on
// macOS pads it and the dates pass.
func TestCustomerInvoiceAddDateBefore1000FollowsPlatform(t *testing.T) {
	saved := strftimeGOOS
	defer func() { strftimeGOOS = saved }()
	for _, tc := range []struct {
		platform string
		want     InvoicePartyErrors
	}{
		{"linux", InvoicePartyErrors{
			"transmitting_date":   []string{PersonaDateFormat},
			"payment_expiry_date": []string{PersonaDateFormat},
		}},
		{"darwin", InvoicePartyErrors{}},
	} {
		strftimeGOOS = tc.platform
		_, errors := ParseCustomerInvoiceAdd(customerInvoiceDateObject(
			[2]string{"transmitting_date", "0999-03-05"}, [2]string{"payment_expiry_date", "0999-06-15"}))
		if !reflect.DeepEqual(errors, tc.want) {
			t.Errorf("%s: %#v, want %#v", tc.platform, errors, tc.want)
		}
	}
	// Only the date that does not survive the rewrite is reported.
	strftimeGOOS = "linux"
	want := InvoicePartyErrors{"transmitting_date": []string{PersonaDateFormat}}
	if _, errors := ParseCustomerInvoiceAdd(customerInvoiceDateObject(
		[2]string{"transmitting_date", "0999-03-05"}, [2]string{"payment_expiry_date", "2031-06-15"})); !reflect.DeepEqual(errors, want) {
		t.Errorf("single date: %#v, want %#v", errors, want)
	}
}
