package handler

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/labstack/echo/v4"
)

// default_storage.save's name validation is Django's plain 400 page.
func TestPaymentWritesSuspiciousFileIsBadRequestPage(t *testing.T) {
	handler := &PaymentWritesHandler{}
	request := httptest.NewRequest("POST", "/payment/add", nil)
	request.Header.Set("Allow", "POST")
	recorder := httptest.NewRecorder()
	context := echo.New().NewContext(request, recorder)
	if err := handler.failure(context, &documentkey.SuspiciousFileError{Message: "Detected path traversal attempt"}, true); err != nil {
		t.Fatalf("failure=%v", err)
	}
	if recorder.Code != 400 || recorder.Body.String() != string(djangoBadRequestPage) {
		t.Fatalf("status=%d body=%q", recorder.Code, recorder.Body.String())
	}
	if kind := recorder.Header().Get("Content-Type"); kind != "text/html; charset=utf-8" {
		t.Fatalf("content-type=%q", kind)
	}
	if allow := recorder.Header().Get("Allow"); allow != "" {
		t.Fatalf("allow=%q", allow)
	}
}

// The validator codes render Django's and DRF's messages.
func TestPaymentWritesPaymentIDMessages(t *testing.T) {
	cases := []struct{ code, english, italian string }{
		{"unique:payment:payment id", "payment with this payment id already exists.", "payment con questo payment id esiste già."},
		{"unique-group-invalid:abc", "“abc” is not a valid UUID.", `"abc" non è uno UUID valido.`},
		{"json-invalid", "Value must be valid JSON.", "Il valore deve essere un JSON valido."},
	}
	for _, item := range cases {
		if got := paymentCategoryValidationMessage(item.code, true); got != item.english {
			t.Fatalf("%s english=%q", item.code, got)
		}
		if got := paymentCategoryValidationMessage(item.code, false); got != item.italian {
			t.Fatalf("%s italian=%q", item.code, got)
		}
	}
}

// The add view answers serializer.errors in its repr form; a payment_id error
// leads the map.
func TestPaymentWritesPaymentIDErrorRepr(t *testing.T) {
	request := httptest.NewRequest("POST", "/payment/add", nil)
	invalid := &dto.PaymentEntryValidationError{}
	invalid.Insert("payment_id", "unique:payment:payment id")
	invalid.Insert("payment_id", "unique-group-invalid:abc")
	want := `validation errors: {'payment_id': [ErrorDetail(string='payment con questo payment id esiste già.', code='unique'), ` +
		`ErrorDetail(string='"abc" non è uno UUID valido.', code='invalid')]}`
	if got := paymentEntryErrorsRepr(request, invalid); got != want {
		t.Fatalf("got=%q", got)
	}
}

// An application-state error (the jsonb column refusing a value after the audit
// entry, or a form JSON text past its measured band) is never handed off and
// stays Go's 500 page, as does any other unhandled failure.
func TestPaymentWritesFailuresAreNotHandedOff(t *testing.T) {
	var upstreamCalls atomic.Int32
	django := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		upstreamCalls.Add(1)
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte("django"))
	}))
	defer django.Close()
	upstream, err := handoff.New(django.URL, 0, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	for name, test := range map[string]struct {
		failure error
		status  int
		calls   int32
	}{
		"other failure":     {domain.ErrPaymentWritesServer, 500, 0},
		"application state": {refusal.AppState("payment write: jsonb rejects a lone surrogate"), 500, 0},
	} {
		upstreamCalls.Store(0)
		e := echo.New()
		e.Pre(upstream.Middleware())
		e.PATCH("/payment/:uid/update", func(c echo.Context) error {
			return (&PaymentWritesHandler{}).failure(c, test.failure, false)
		})
		server := httptest.NewServer(e)
		request, err := http.NewRequest(http.MethodPatch, server.URL+"/payment/a1000000-0000-4000-8000-000000988450/update", nil)
		if err != nil {
			t.Fatal(err)
		}
		response, err := http.DefaultClient.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		_, err = io.ReadAll(response.Body)
		response.Body.Close()
		server.Close()
		if err != nil {
			t.Fatal(err)
		}
		if response.StatusCode != test.status || upstreamCalls.Load() != test.calls {
			t.Errorf("%s: status %d with %d upstream calls, want %d with %d", name, response.StatusCode, upstreamCalls.Load(),
				test.status, test.calls)
		}
	}
}
