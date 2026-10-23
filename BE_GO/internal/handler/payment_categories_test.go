package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/labstack/echo/v4"
)

func TestPaymentCategoryValidationResponse(t *testing.T) {
	for language, expected := range map[string]string{
		"it": "Campo obbligatorio.",
		"en": "This field is required.",
	} {
		e := echo.New()
		request := httptest.NewRequest(http.MethodPost, "/payment/category/add", nil)
		request.Header.Set("Accept-Language", language)
		recorder := httptest.NewRecorder()
		c := e.NewContext(request, recorder)
		validation := &dto.PaymentCategoryValidationError{Errors: map[string]any{
			"name": []string{dto.PaymentCategoryValidationRequired},
		}}
		if err := paymentCategoryError(c, validation); err != nil {
			t.Fatal(err)
		}
		var body map[string][]string
		if unmarshalErr := json.Unmarshal(recorder.Body.Bytes(), &body); unmarshalErr != nil || recorder.Code != 400 ||
			len(body["name"]) != 1 || body["name"][0] != expected {
			t.Fatal(recorder.Code, recorder.Body.String(), unmarshalErr)
		}
	}
}

func TestPaymentCategoryFormBodySemantics(t *testing.T) {
	for _, partial := range []bool{false, true} {
		r := httptest.NewRequest(http.MethodPatch, "/payment/category/id/update", strings.NewReader("name=Updated&vat_management.vat=16&vat_management.deduction=2"))
		r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		c := echo.New().NewContext(r, httptest.NewRecorder())
		body, err := paymentCategoryWriteBody(c, partial)
		if !partial {
			if err == nil {
				t.Fatal("CREATE lost immutable QueryDict failure")
			}
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		value, err := dto.ParsePaymentCategoryWrite(body, true)
		if err != nil || value.Name == nil || *value.Name != "Updated" || value.VAT.Value.VAT == nil || *value.VAT.Value.VAT != 16 || *value.VAT.Value.Deduction != 2 {
			t.Fatal("PATCH HTML nested serializer fields lost", value, err)
		}
	}
	r := httptest.NewRequest(http.MethodPatch, "/payment/category/id/update", strings.NewReader("sport_association="))
	r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	body, err := paymentCategoryWriteBody(echo.New().NewContext(r, httptest.NewRecorder()), true)
	if err != nil {
		t.Fatal(err)
	}
	input, err := dto.ParsePaymentCategoryWrite(body, true)
	if err != nil || !input.AssociationPresent || input.AssociationID != nil {
		t.Fatal("nullable empty HTML association must reach immutable-owner validation", input, err)
	}
}

func TestPaymentCategoryApplicationErrors(t *testing.T) {
	tests := []struct {
		err    error
		status int
		body   string
	}{
		{domain.ErrPaymentCategoryForbidden, 403, `{"error":"user not authorized"}`},
		{domain.ErrPaymentCategoryListForbidden, 403, `{"error":"user not allowed."}`},
		{domain.ErrPaymentCategoryNotFound, 404, `{"error":"payment category not found"}`},
	}
	for _, tc := range tests {
		e := echo.New()
		request := httptest.NewRequest(http.MethodGet, "/payment/category/list", nil)
		recorder := httptest.NewRecorder()
		if err := paymentCategoryError(e.NewContext(request, recorder), tc.err); err != nil {
			t.Fatal(err)
		}
		if recorder.Code != tc.status || recorder.Body.String() != tc.body+"\n" {
			t.Fatalf("%v: %d %s", tc.err, recorder.Code, recorder.Body.String())
		}
	}
}

func TestPaymentCategoryCorrectedFieldMessages(t *testing.T) {
	validation := &dto.PaymentCategoryValidationError{Errors: map[string]any{
		"payment_category_id": []string{"immutable-category-id"},
		"sport_association":   []string{"immutable-association"},
		"vat_management": map[string]any{
			"vat_management_id": []string{dto.PaymentCategoryValidationUniqueVAT},
			"vat":               []string{"choice:99"},
		},
	}}
	e := echo.New()
	request := httptest.NewRequest(http.MethodPost, "/payment/category/add", nil)
	request.Header.Set("Accept-Language", "en")
	recorder := httptest.NewRecorder()
	if err := paymentCategoryError(e.NewContext(request, recorder), validation); err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body["payment_category_id"].([]any)[0] != "L'identificativo della categoria non può essere modificato." ||
		body["sport_association"].([]any)[0] != "L'associazione della categoria non può essere modificata." {
		t.Fatal(body)
	}
	vat := body["vat_management"].(map[string]any)
	if vat["vat_management_id"].([]any)[0] != "vat management with this vat management id already exists." ||
		vat["vat"].([]any)[0] != `"99" is not a valid choice.` {
		t.Fatal(vat)
	}
	validation.Errors["sport_association"] = []string{"related-does-not-exist:00000000-0000-4000-8000-000000000997"}
	recorder = httptest.NewRecorder()
	c := e.NewContext(request, recorder)
	if err := paymentCategoryError(c, validation); err != nil {
		t.Fatal(err)
	}
	body = nil
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil ||
		body["sport_association"].([]any)[0] != `Invalid pk "00000000-0000-4000-8000-000000000997" - object does not exist.` {
		t.Fatal(recorder.Body.String(), err)
	}
}

func TestPaymentCategoryMalformedLegacyUUIDIsServerError(t *testing.T) {
	e := echo.New()
	request := httptest.NewRequest(http.MethodPatch, "/payment/category/not-a-uuid/update", nil)
	recorder := httptest.NewRecorder()
	if err := paymentCategoryError(e.NewContext(request, recorder), domain.ErrPaymentCategoryInvalidID); err != nil {
		t.Fatal(err)
	}
	if recorder.Code != 500 || recorder.Header().Get(echo.HeaderContentType) != "text/html; charset=utf-8" {
		t.Fatal(recorder.Code, recorder.Header(), recorder.Body.String())
	}
}

func TestPaymentCategoryItalianCatalogFallbacks(t *testing.T) {
	// The pinned DRF Italian catalogue deliberately falls back to English for
	// these three messages; inventing translated text changes API compatibility.
	for code, expected := range map[string]string{
		dto.PaymentCategoryValidationUUID:     "Must be a valid UUID.",
		dto.PaymentCategoryValidationString:   "Not a valid string.",
		dto.PaymentCategoryValidationBoolean:  "Must be a valid boolean.",
		dto.PaymentCategoryValidationDateTime: "L'oggetto di tipo datetime è in un formato errato. Usa uno dei seguenti formati: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].",
		"dictionary:list":                     "Dati non validi. Era atteso un dizionario, ma si è ricevuto list.",
	} {
		if got := paymentCategoryValidationMessage(code, false); got != expected {
			t.Errorf("%s: %q, want %q", code, got, expected)
		}
	}
}

func TestPaymentCategoryDateTimeEnglishWireMessage(t *testing.T) {
	const expected = "Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z]."
	if got := paymentCategoryValidationMessage(dto.PaymentCategoryValidationDateTime, true); got != expected {
		t.Errorf("%q, want %q", got, expected)
	}
	for _, english := range []bool{false, true} {
		if got := paymentCategoryValidationMessage(dto.PaymentCategoryValidationDateTimeOverflow, english); got != "Datetime value out of range." {
			t.Errorf("overflow: %q", got)
		}
	}
}
