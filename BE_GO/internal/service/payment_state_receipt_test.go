package service

import (
	"encoding/json"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// send_receipt_email is a print_document_invoice argument: a file part (an
// UploadedFile in request.data) or a nested one is what kombu cannot encode,
// so the publish is skipped and the approve still answers 200.
func TestPaymentStateReceiptUploadIsUnencodable(t *testing.T) {
	upload := domain.RequestUpload{Name: "ricevuta.txt", Size: 2, Content: "si", Whole: true}
	for name, value := range map[string]any{
		"file part":      upload,
		"inside a list":  []any{true, upload},
		"inside an dict": domain.RequestObject{{Name: "file", Value: upload}},
	} {
		if _, err := paymentStateReceiptArg(value); !errors.Is(err, errPaymentStateReceiptUnencodable) {
			t.Errorf("%s: want the unencodable receipt, got %v", name, err)
		}
	}
	if _, err := paymentStateReceiptArg(json.Number("1")); err != nil {
		t.Fatalf("an integer receipt: %v", err)
	}
}
