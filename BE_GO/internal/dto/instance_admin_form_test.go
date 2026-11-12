package dto

import (
	"errors"
	"math/big"
	"net/url"
	"reflect"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// instanceAdminFormBody is a parsed form body: a repeated key keeps its last
// value and every value in All, as the request parser does.
func instanceAdminFormBody(t *testing.T, raw string, files ...domain.RequestField) domain.RequestObject {
	t.Helper()
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
	for _, pair := range strings.Split(raw, "&") {
		if pair == "" {
			continue
		}
		key, value, _ := strings.Cut(pair, "=")
		key, err := url.QueryUnescape(key)
		if err != nil {
			t.Fatalf("key %q: %v", key, err)
		}
		value, err = url.QueryUnescape(value)
		if err != nil {
			t.Fatalf("value %q: %v", value, err)
		}
		add(key, value)
	}
	for _, file := range files {
		add(file.Name, file.Value)
	}
	return object
}

func instanceAdminUpload(name, filename string) domain.RequestField {
	return domain.RequestField{Name: name, Value: domain.RequestUpload{Name: filename, Whole: true}}
}

func instanceAdminFailure(t *testing.T, err error) any {
	t.Helper()
	var invalid *InstanceAdminInvalid
	if !errors.As(err, &invalid) {
		t.Fatalf("got %v, want a validation error", err)
	}
	return invalid.Body
}

func instanceAdminText(value string) *string { return &value }

func TestInstanceAdminFormEmail(t *testing.T) {
	type testCase struct {
		name   string
		raw    string
		files  []domain.RequestField
		valid  *InstanceAdminEmailInput
		errors any
	}
	cases := []testCase{
		{name: "empty", raw: "", errors: map[string]any{
			"revision": []string{PaymentCategoryValidationRequired}, "host": []string{PaymentCategoryValidationRequired},
			"port": []string{PaymentCategoryValidationRequired}, "security": []string{PaymentCategoryValidationRequired},
			"username": []string{PaymentCategoryValidationRequired}, "from_email": []string{PaymentCategoryValidationRequired},
			"sender_name": []string{PaymentCategoryValidationRequired}}},
		{name: "blank fields", raw: "revision=&host=&port=&security=&username=&password=&clear_password=&from_email=&sender_name=",
			errors: map[string]any{
				"revision": []string{DRFValidationInteger}, "host": []string{PaymentCategoryValidationBlank},
				"port": []string{DRFValidationInteger}, "security": []string{"choice:"},
				"from_email": []string{PaymentCategoryValidationBlank}}},
		{name: "all fields", raw: "revision=0&host=smtp.example.com&port=587&security=tls&username=&from_email=a%40example.com&sender_name=",
			valid: &InstanceAdminEmailInput{Revision: big.NewInt(0), Host: "smtp.example.com", Port: 587, Security: "tls",
				FromEmail: "a@example.com"}},
		{name: "blank password skipped and clear flag", raw: "revision=0&host=smtp.example.com&port=587&security=tls&username=u&password=&clear_password=on&from_email=a%40example.com&sender_name=Club",
			valid: &InstanceAdminEmailInput{Revision: big.NewInt(0), Host: "smtp.example.com", Port: 587, Security: "tls",
				Username: "u", ClearPassword: true, FromEmail: "a@example.com", SenderName: "Club"}},
		{name: "last revision stripped host spaced password", raw: "revision=+7+&host=+smtp.example.com+&port=587&security=tls&username=u&password=+p+&clear_password=0&from_email=a%40example.com&sender_name=x&revision=1",
			valid: &InstanceAdminEmailInput{Revision: big.NewInt(1), Host: "smtp.example.com", Port: 587, Security: "tls",
				Username: "u", Password: instanceAdminText(" p "), FromEmail: "a@example.com", SenderName: "x"}},
		{name: "password file", raw: "revision=0&host=smtp.example.com&port=587&security=tls&username=&from_email=a%40example.com&sender_name=",
			files:  []domain.RequestField{instanceAdminUpload("password", "p.txt")},
			errors: map[string]any{"password": []string{PaymentCategoryValidationString}}},
		{name: "revision from file and boolean file", raw: "host=smtp.example.com&port=587&security=tls&username=&from_email=a%40example.com&sender_name=",
			files:  []domain.RequestField{instanceAdminUpload("revision", "5"), instanceAdminUpload("clear_password", "true")},
			errors: map[string]any{"clear_password": []string{PaymentCategoryValidationBoolean}}},
		{name: "host file and security file", raw: "revision=0&port=25&username=&from_email=a%40example.com&sender_name=",
			files:  []domain.RequestField{instanceAdminUpload("host", "h.txt"), instanceAdminUpload("security", "ssl")},
			errors: map[string]any{"host": []string{PaymentCategoryValidationString}}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			body, err := InstanceAdminEmailForm(instanceAdminFormBody(t, tc.raw, tc.files...))
			if err != nil {
				t.Fatalf("normalise: %v", err)
			}
			input, err := InstanceAdminEmail(body)
			if tc.errors != nil {
				if got := instanceAdminFailure(t, err); !reflect.DeepEqual(got, tc.errors) {
					t.Fatalf("got %#v, want %#v", got, tc.errors)
				}
				return
			}
			if err != nil || !reflect.DeepEqual(input, tc.valid) {
				t.Fatalf("got (%#v, %v), want %#v", input, err, tc.valid)
			}
		})
	}
}

func TestInstanceAdminFormEmailTest(t *testing.T) {
	type testCase struct {
		name   string
		raw    string
		files  []domain.RequestField
		valid  *InstanceEmailTestInput
		errors any
	}
	cases := []testCase{
		{name: "connect", raw: "revision=0&action=connect",
			valid: &InstanceEmailTestInput{Revision: big.NewInt(0), Action: "connect"}},
		{name: "send without recipient", raw: "revision=0&action=send&recipient=",
			errors: map[string]any{"recipient": []string{"Inserisci il destinatario del messaggio di prova."}}},
		{name: "recipient and revision files", raw: "action=send",
			files:  []domain.RequestField{instanceAdminUpload("recipient", "a@example.com"), instanceAdminUpload("revision", "3")},
			errors: map[string]any{"recipient": []string{SupplierValidationEmail}}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			body, err := InstanceAdminEmailTestForm(instanceAdminFormBody(t, tc.raw, tc.files...))
			if err != nil {
				t.Fatalf("normalise: %v", err)
			}
			input, err := InstanceAdminEmailTest(body)
			if tc.errors != nil {
				if got := instanceAdminFailure(t, err); !reflect.DeepEqual(got, tc.errors) {
					t.Fatalf("got %#v, want %#v", got, tc.errors)
				}
				return
			}
			if err != nil || !reflect.DeepEqual(input, tc.valid) {
				t.Fatalf("got (%#v, %v), want %#v", input, err, tc.valid)
			}
		})
	}
}

const instanceAdminAIBase = "revision=0&model=m&cheap_model=c&base_url=&max_iterations=5&max_results=10&query_timeout=5&ws_rate_limit=5&ws_timeout=5&history_cap=5"

func instanceAdminAIValues() map[string]any {
	return map[string]any{"enabled": false, "model": "m", "cheap_model": "c", "base_url": "",
		"max_iterations": int64(5), "max_results": int64(10), "query_timeout": int64(5),
		"ws_rate_limit": int64(5), "ws_timeout": int64(5), "history_cap": int64(5)}
}

func TestInstanceAdminFormIntegration(t *testing.T) {
	type testCase struct {
		name     string
		provider string
		raw      string
		files    []domain.RequestField
		valid    *InstanceAdminIntegrationInput
		errors   any
	}
	cases := []testCase{
		{name: "ai base", provider: "ai", raw: instanceAdminAIBase,
			valid: &InstanceAdminIntegrationInput{Revision: big.NewInt(0), Values: instanceAdminAIValues(), Clear: []string{}}},
		{name: "ai replace and clear", provider: "ai", raw: instanceAdminAIBase + "&enabled=true&api_key=k&clear_secrets=api_key",
			errors: map[string]any{"non_field_errors": []string{"Scegli se sostituire o rimuovere la chiave API."}}},
		{name: "ai repeated clear secrets", provider: "ai",
			raw: instanceAdminAIBase + "&enabled=on&clear_secrets=api_key&clear_secrets=api_key",
			valid: &InstanceAdminIntegrationInput{Revision: big.NewInt(0), Values: func() map[string]any {
				values := instanceAdminAIValues()
				values["enabled"] = true
				return values
			}(), Clear: []string{"api_key", "api_key"}}},
		{name: "ai indexed clear secrets", provider: "ai", raw: instanceAdminAIBase + "&clear_secrets%5B0%5D=api_key&clear_secrets%5B2%5D=x",
			errors: map[string]any{"clear_secrets": map[string]any{"1": []string{"choice:x"}}}},
		{name: "ai blank clear secrets", provider: "ai", raw: instanceAdminAIBase + "&clear_secrets=",
			errors: map[string]any{"clear_secrets": map[string]any{"0": []string{"choice:"}}}},
		{name: "ai empty index ignored", provider: "ai", raw: instanceAdminAIBase + "&clear_secrets%5B%5D=api_key",
			valid: &InstanceAdminIntegrationInput{Revision: big.NewInt(0), Values: instanceAdminAIValues(), Clear: []string{}}},
		{name: "ai enabled file and clear file", provider: "ai", raw: instanceAdminAIBase + "&enabled=",
			files:  []domain.RequestField{instanceAdminUpload("clear_secrets", "api_key")},
			errors: map[string]any{"enabled": []string{PaymentCategoryValidationBoolean}}},
		{name: "ai invalid enabled and blank key", provider: "ai", raw: instanceAdminAIBase + "&enabled=maybe&api_key=",
			errors: map[string]any{"enabled": []string{PaymentCategoryValidationBoolean}}},
		{name: "stripe blank public key", provider: "stripe", raw: "revision=0&public_key=",
			valid: &InstanceAdminIntegrationInput{Revision: big.NewInt(0),
				Values: map[string]any{"enabled": false, "public_key": ""}, Clear: []string{}}},
		{name: "stripe replace and clear", provider: "stripe",
			raw:    "revision=0&enabled=1&public_key=pk_test_a&secret_key=sk_test_b&webhook_secret=&clear_secrets=webhook_secret&clear_secrets=secret_key",
			errors: map[string]any{"non_field_errors": []string{"Scegli se sostituire o rimuovere ciascuna credenziale."}}},
		{name: "stripe indexes sorted", provider: "stripe",
			raw: "revision=0&public_key=pk_test_a&clear_secrets%5B1%5D=secret_key&clear_secrets%5B0%5D=webhook_secret",
			valid: &InstanceAdminIntegrationInput{Revision: big.NewInt(0),
				Values: map[string]any{"enabled": false, "public_key": "pk_test_a"}, Clear: []string{"webhook_secret", "secret_key"}}},
		{name: "login blank client", provider: "google", raw: "revision=0&client_id=",
			valid: &InstanceAdminIntegrationInput{Revision: big.NewInt(0), Values: map[string]any{"enabled": false, "client_id": ""},
				Clear: []string{}}},
		{name: "login true client", provider: "apple", raw: "revision=0&client_id=abc&enabled=TRUE",
			valid: &InstanceAdminIntegrationInput{Revision: big.NewInt(0), Values: map[string]any{"enabled": true, "client_id": "abc"},
				Clear: []string{}}},
		{name: "login empty", provider: "google", raw: "",
			errors: map[string]any{"revision": []string{PaymentCategoryValidationRequired},
				"client_id": []string{PaymentCategoryValidationRequired}}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			body, err := InstanceAdminIntegrationForm(tc.provider, instanceAdminFormBody(t, tc.raw, tc.files...))
			if err != nil {
				t.Fatalf("normalise: %v", err)
			}
			input, err := InstanceAdminIntegration(tc.provider, body)
			if tc.errors != nil {
				if got := instanceAdminFailure(t, err); !reflect.DeepEqual(got, tc.errors) {
					t.Fatalf("got %#v, want %#v", got, tc.errors)
				}
				return
			}
			if err != nil || !reflect.DeepEqual(input, tc.valid) {
				t.Fatalf("got (%#v, %v), want %#v", input, err, tc.valid)
			}
		})
	}
}

func TestInstanceAdminFormIntegrationNestedListRefused(t *testing.T) {
	body := instanceAdminFormBody(t, instanceAdminAIBase+"&clear_secrets%5B0%5Dx=api_key")
	if _, err := InstanceAdminIntegrationForm("ai", body); !errors.Is(err, domain.ErrInstanceAdminRefused) {
		t.Fatalf("got %v, want the refusal", err)
	}
	// A plain value with the same index replaces the nested shape.
	body = instanceAdminFormBody(t, instanceAdminAIBase+"&clear_secrets%5B0%5Dx=api_key&clear_secrets%5B0%5D=api_key")
	normalised, err := InstanceAdminIntegrationForm("ai", body)
	if err != nil {
		t.Fatalf("normalise: %v", err)
	}
	if _, err := InstanceAdminIntegration("ai", normalised); err != nil {
		t.Fatalf("validate: %v", err)
	}
}

func TestInstanceAdminFormUpdate(t *testing.T) {
	const request = "6d2d1e0e-1c1c-4c1c-8c1c-1c1c1c1c1c1c"
	type testCase struct {
		name   string
		raw    string
		files  []domain.RequestField
		valid  *InstanceAdminUpdate
		errors any
	}
	cases := []testCase{
		{name: "valid", raw: "release_id=5&tag=v1.2.3&request_id=" + request,
			valid: &InstanceAdminUpdate{ReleaseID: big.NewInt(5), Tag: "v1.2.3", RequestID: request}},
		{name: "blank", raw: "release_id=&tag=&request_id=", errors: map[string]any{
			"release_id": []string{DRFValidationInteger}, "tag": []string{PaymentCategoryValidationBlank},
			"request_id": []string{PaymentCategoryValidationUUID}}},
		{name: "files", raw: "tag=t",
			files: []domain.RequestField{instanceAdminUpload("release_id", "7"), instanceAdminUpload("request_id", request)},
			errors: map[string]any{
				"request_id": []string{PaymentCategoryValidationUUID}}},
		{name: "tag file", raw: "", files: []domain.RequestField{instanceAdminUpload("tag", "t.txt")},
			errors: map[string]any{"release_id": []string{PaymentCategoryValidationRequired},
				"tag": []string{PaymentCategoryValidationString}, "request_id": []string{PaymentCategoryValidationRequired}}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			body, err := InstanceAdminUpdateForm(instanceAdminFormBody(t, tc.raw, tc.files...))
			if err != nil {
				t.Fatalf("normalise: %v", err)
			}
			update, err := InstanceAdminUpdateValues(body)
			if tc.errors != nil {
				if got := instanceAdminFailure(t, err); !reflect.DeepEqual(got, tc.errors) {
					t.Fatalf("got %#v, want %#v", got, tc.errors)
				}
				return
			}
			if err != nil || !reflect.DeepEqual(update, tc.valid) {
				t.Fatalf("got (%#v, %v), want %#v", update, err, tc.valid)
			}
		})
	}
}

func TestInstanceAdminFormRestart(t *testing.T) {
	const request = "6d2d1e0e1c1c4c1c8c1c1c1c1c1c1c1c"
	const canonical = "6d2d1e0e-1c1c-4c1c-8c1c-1c1c1c1c1c1c"
	valid := instanceAdminFormBody(t, "request_id="+request)
	body, err := InstanceAdminRestartForm(valid)
	if err != nil {
		t.Fatalf("normalise: %v", err)
	}
	if id, err := InstanceAdminRestartValue(body); err != nil || id != canonical {
		t.Fatalf("got (%q, %v), want %q", id, err, canonical)
	}
	for _, raw := range []string{"request_id="} {
		for _, files := range [][]domain.RequestField{nil, {instanceAdminUpload("request_id", request)}} {
			body, err := InstanceAdminRestartForm(instanceAdminFormBody(t, raw, files...))
			if err != nil {
				t.Fatalf("normalise: %v", err)
			}
			_, err = InstanceAdminRestartValue(body)
			got := instanceAdminFailure(t, err)
			if !reflect.DeepEqual(got, map[string]any{"request_id": []string{PaymentCategoryValidationUUID}}) {
				t.Fatalf("%q %v: got %#v", raw, files, got)
			}
		}
	}
}

func TestInstanceAdminFormRevisionGet(t *testing.T) {
	type testCase struct {
		name  string
		raw   string
		files []domain.RequestField
		want  *big.Int
		code  string
	}
	cases := []testCase{
		{name: "missing", raw: "", code: PaymentCategoryValidationNull},
		{name: "value", raw: "revision=4", want: big.NewInt(4)},
		{name: "last value", raw: "revision=4&revision=x", code: DRFValidationInteger},
		{name: "blank", raw: "revision=", code: DRFValidationInteger},
		{name: "file number", raw: "", files: []domain.RequestField{instanceAdminUpload("revision", "9")}, want: big.NewInt(9)},
		{name: "file text", raw: "", files: []domain.RequestField{instanceAdminUpload("revision", "r.txt")}, code: DRFValidationInteger},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			body, err := InstanceAdminRevisionForm(instanceAdminFormBody(t, tc.raw, tc.files...))
			if err != nil {
				t.Fatalf("normalise: %v", err)
			}
			value, _ := InstanceAdminFormGet(body, "revision")
			number, err := InstanceAdminRevision(value)
			if tc.code != "" {
				failure := instanceAdminFailure(t, err)
				if !reflect.DeepEqual(failure, []string{tc.code}) {
					t.Fatalf("got %#v, want %q", failure, tc.code)
				}
				return
			}
			if err != nil || number.Cmp(tc.want) != 0 {
				t.Fatalf("got (%v, %v), want %v", number, err, tc.want)
			}
		})
	}
}

func TestInstanceAdminFormBranding(t *testing.T) {
	type testCase struct {
		name   string
		raw    string
		files  []domain.RequestField
		within bool
		fields map[string]string
		errors any
	}
	cases := []testCase{
		{name: "empty", raw: "", within: false},
		{name: "dotted oem", raw: "oem.name=x", within: false},
		{name: "plain oem", raw: "oem=x", within: true, fields: map[string]string{}},
		{name: "version", raw: "uiApiVersion=v2", within: true, fields: map[string]string{"ui_api_version": "v2"}},
		{name: "blank version", raw: "uiApiVersion=", within: true, fields: map[string]string{}},
		{name: "repeated version", raw: "uiApiVersion=v1&uiApiVersion=v9", within: true,
			errors: map[string]any{"uiApiVersion": []string{"choice:v9"}}},
		{name: "version file", raw: "", files: []domain.RequestField{instanceAdminUpload("uiApiVersion", "v2")},
			within: true, fields: map[string]string{"ui_api_version": "v2"}},
		{name: "version image file", raw: "", files: []domain.RequestField{instanceAdminUpload("uiApiVersion", "a.png")},
			within: true, errors: map[string]any{"uiApiVersion": []string{"choice:a.png"}}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			body, err := InstanceAdminBrandingForm(instanceAdminFormBody(t, tc.raw, tc.files...))
			if err != nil {
				t.Fatalf("normalise: %v", err)
			}
			within, err := InstanceAdminKeysWithin(body, "oem", "uiApiVersion")
			if err != nil || within != tc.within {
				t.Fatalf("keys within: got (%v, %v), want %v", within, err, tc.within)
			}
			fields, err := InstanceAdminEdit(body)
			if tc.errors != nil {
				if got := instanceAdminFailure(t, err); !reflect.DeepEqual(got, tc.errors) {
					t.Fatalf("got %#v, want %#v", got, tc.errors)
				}
				return
			}
			if !tc.within {
				return
			}
			if err != nil || !reflect.DeepEqual(fields, tc.fields) {
				t.Fatalf("got (%#v, %v), want %#v", fields, err, tc.fields)
			}
		})
	}
}

func TestInstanceAdminFormKeysUseRawObject(t *testing.T) {
	body, err := InstanceAdminBrandingForm(instanceAdminFormBody(t, "uiApiVersion="))
	if err != nil {
		t.Fatalf("normalise: %v", err)
	}
	within, err := InstanceAdminKeysWithin(body, "oem", "uiApiVersion")
	if err != nil || !within {
		t.Fatalf("got (%v, %v), want the raw key", within, err)
	}
	only, err := InstanceAdminKeysEqual(instanceAdminFormBody(t, "request_id=x"), "request_id")
	if err != nil || !only {
		t.Fatalf("got (%v, %v), want the raw key", only, err)
	}
}
