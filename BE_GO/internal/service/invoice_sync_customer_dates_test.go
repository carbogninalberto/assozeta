package service

import (
	"errors"
	"reflect"
	"runtime"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// customerInvoiceDateRun posts the base multipart body (with the file the
// QueryDict copy needs) after overriding the two request dates.
func customerInvoiceDateRun(t *testing.T, dates ...[2]string) error {
	t.Helper()
	file := domain.RequestField{Name: "allegato", Value: domain.RequestUpload{Name: "fattura.txt", Whole: true}}
	return customerAddRun(t, customerAddForm(customerAddPairs(dates...), file))
}

// The view's own strptime('%Y-%m-%d') needs four year digits, so a three-digit
// year raises before the serializer on every platform
// (invoice_views.py:878-880): Django's 500 page, before any write.
func TestCustomerAddThreeDigitYearIsServerError(t *testing.T) {
	for _, dates := range [][][2]string{
		{{"number", "605"}, {"transmitting_date", "999-03-05"}},
		{{"number", "606"}, {"payment_expiry_date", "999-06-15"}},
	} {
		if err := customerInvoiceDateRun(t, dates...); !errors.Is(err, domain.ErrInvoicePartyServer) {
			t.Fatalf("%v: got %v, want the view's strptime server error", dates, err)
		}
	}
}

// A four-digit date before 1000 follows the platform (X16): the view rewrites
// it with strftime('%d/%m/%Y') and the serializer's DateField re-reads it with
// strptime, so glibc's unpadded '05/03/999' is a field error while the BSD
// library's '05/03/0999' passes and the add reaches its save.
func TestCustomerAddDateBefore1000FollowsPlatform(t *testing.T) {
	err := customerInvoiceDateRun(t, [2]string{"number", "607"}, [2]string{"transmitting_date", "0999-03-05"})
	if runtime.GOOS == "darwin" {
		if !errors.Is(err, domain.ErrServerComposition) {
			t.Fatalf("got %v, want the save to be reached", err)
		}
		return
	}
	var invalid *dto.InvoicePartyValidationError
	if !errors.As(err, &invalid) {
		t.Fatalf("got %v, want a validation error", err)
	}
	want := dto.InvoicePartyErrors{"transmitting_date": []string{dto.PersonaDateFormat}}
	if !reflect.DeepEqual(invalid.Errors, want) {
		t.Fatalf("errors %#v, want %#v", invalid.Errors, want)
	}
}
