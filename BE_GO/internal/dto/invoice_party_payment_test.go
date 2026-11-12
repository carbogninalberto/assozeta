package dto

import (
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// The verdicts in this file were produced by probing the Django views
// POST /invoice-suppliers/add and PATCH /invoice-suppliers/<uid>/update with
// PaymentSerializer's nested field errors. "server error page" / "payment row
// kept" means the nested value was valid (the request then failed later).

const (
	nestedPaymentMissingID = "a1000000-0000-4000-8000-0000009fffff"
	nestedPaymentCategory  = "a1000000-0000-4000-8000-000000846117"
	nestedPaymentAccounts  = "a1000000-0000-4000-8000-000000846114"
	nestedPaymentDocument  = "a1000000-0000-4000-8000-000000846100"
	nestedPaymentGroup     = "a1000000-0000-4000-8000-000000714001"
	nestedPaymentAssoc     = "a1000000-0000-4000-8000-000000701001"
	nestedPaymentUser      = "a1000000-0000-4000-8000-000000700001"
)

// nestedPaymentSentinel is the Related error of a row owned by another association (DRF's 403 from validation).
var nestedPaymentSentinel = errors.New("foreign association row")

var nestedPaymentExistingIDs = map[string]bool{
	nestedPaymentCategory: true, nestedPaymentAccounts: true, nestedPaymentDocument: true,
	nestedPaymentGroup: true, nestedPaymentAssoc: true, nestedPaymentUser: true,
	"a1000000-0000-4000-8000-000000700004": true,
	"a1000000-0000-4000-8000-000000700005": true,
	"a1000000-0000-4000-8000-000000700006": true,
}

var nestedPaymentForeignIDs = map[string]bool{
	"a1000000-0000-4000-8000-000000846119": true, // payment_category
	"a1000000-0000-4000-8000-000000846116": true, // custom_accounts
	"a1000000-0000-4000-8000-000000701002": true, // sport_association
	"a1000000-0000-4000-8000-000000701003": true, // sport_association
	"a1000000-0000-4000-8000-000000846700": true, // selected_tutor
	"a1000000-0000-4000-8000-000000714002": true, // group
	"a1000000-0000-4000-8000-000000700007": true, // user
}

var nestedPaymentTakenIDs = map[string]bool{
	"a1000000-0000-4000-8000-000000846120": true, // payment
	"a1000000-0000-4000-8000-000000846122": true, // payment
	"a1000000-0000-4000-8000-000000846701": true, // instructor
	"a1000000-0000-4000-8000-000000846702": true, // invoice
}

func nestedPaymentLookups() NestedPaymentLookups {
	return NestedPaymentLookups{
		Related: func(model, id string) (bool, error) {
			if nestedPaymentForeignIDs[id] {
				return false, nestedPaymentSentinel
			}
			return nestedPaymentExistingIDs[id], nil
		},
		Taken: func(model, id string) (bool, error) { return nestedPaymentTakenIDs[id], nil },
	}
}

// nestedPaymentBody decodes a JSON body the way the request layer does (json.Number values, dicts as
// domain.RequestObject).
func nestedPaymentBody(t *testing.T, text string) any {
	t.Helper()
	decoder := json.NewDecoder(strings.NewReader(text))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("decode %s: %v", text, err)
	}
	return nestedPaymentJSONValue(value)
}

func nestedPaymentJSONValue(value any) any {
	switch v := value.(type) {
	case map[string]any:
		keys := make([]string, 0, len(v))
		for key := range v {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		object := make(domain.RequestObject, 0, len(keys))
		for _, key := range keys {
			object = append(object, domain.RequestField{Name: key, Value: nestedPaymentJSONValue(v[key])})
		}
		return object
	case []any:
		items := make([]any, len(v))
		for index, item := range v {
			items[index] = nestedPaymentJSONValue(item)
		}
		return items
	}
	return value
}

// nestedPaymentFormField is one HTML input; repeated values are QueryDict.getlist order.
func nestedPaymentFormField(name string, values ...any) domain.RequestField {
	field := domain.RequestField{Name: name, Value: values[len(values)-1]}
	if len(values) > 1 {
		field.All = values
	}
	return field
}

func TestValidateNestedPaymentJSONUpdate(t *testing.T) {
	bigPayment := fmt.Sprintf(`{"amount":"x","type":"zzz","subject":9,"paid":"maybe","description":"",`+
		`"payment_date":"y","meta":{"a":1},"payment_category":"abc",`+
		`"custom_accounts":%q,"payment_id":"abc","user":null,"associate":[],"instructor":5,`+
		`"supplier":{"name":""},"invoice":{"membership_fee":"x"},"notes":5,"creation_date":null,`+
		`"course":"x","group":%q,"signature":"abc","sport_association":%q,"archived":null,`+
		`"expense":"si","payment_intent_id":%q}`,
		nestedPaymentMissingID, nestedPaymentMissingID, nestedPaymentMissingID, strings.Repeat("p", 126))
	cases := []struct {
		name    string
		payment string
		want    any
	}{
		{"empty object is a no-op", `{}`, nil},
		{"every failing field", bigPayment, map[string]any{
			"payment_id":        []string{PaymentCategoryValidationUUID},
			"user":              []string{PaymentCategoryValidationNull},
			"associate":         map[string]any{"non_field_errors": []string{"dictionary:list"}},
			"instructor":        map[string]any{"non_field_errors": []string{"dictionary:int"}},
			"supplier":          map[string]any{"name": []string{PaymentCategoryValidationBlank}},
			"invoice":           map[string]any{"membership_fee": []string{DRFValidationNumber}},
			"type":              []string{"choice:zzz"},
			"subject":           []string{"choice:9"},
			"amount":            []string{DRFValidationNumber},
			"creation_date":     []string{PaymentCategoryValidationNull},
			"payment_date":      []string{PaymentCategoryValidationDateTime},
			"paid":              []string{PaymentCategoryValidationBoolean},
			"expense":           []string{PaymentCategoryValidationBoolean},
			"description":       []string{PaymentCategoryValidationBlank},
			"payment_intent_id": []string{"max:125"},
			"archived":          []string{PaymentCategoryValidationNull},
			"payment_category":  []string{"related-invalid-uuid:abc"},
			"sport_association": []string{"related-does-not-exist:" + nestedPaymentMissingID},
			"custom_accounts":   []string{"related-does-not-exist:" + nestedPaymentMissingID},
			"signature":         []string{"related-invalid-uuid:abc"},
			"group":             []string{"related-does-not-exist:" + nestedPaymentMissingID},
		}},
		{"taken payment id", `{"payment_id":"a1000000-0000-4000-8000-000000846120"}`,
			map[string]any{"payment_id": []string{"unique:payment:payment id"}}},
		{"taken payment id of the update", `{"payment_id":"a1000000-0000-4000-8000-000000846122"}`,
			map[string]any{"payment_id": []string{"unique:payment:payment id"}}},
		{"taken instructor id", `{"instructor":{"instructor_id":"a1000000-0000-4000-8000-000000846701"}}`,
			map[string]any{"instructor": map[string]any{"instructor_id": []string{"unique:instructor:instructor id"}}}},
		{"taken invoice id", `{"invoice":{"invoice_id":"a1000000-0000-4000-8000-000000846702"}}`,
			map[string]any{"invoice": map[string]any{"invoice_id": []string{"unique:invoice:invoice id"}}}},
		{"user field errors", fmt.Sprintf(`{"user":{"email":"x","role":9,"first_name":%q,"phone":null,`+
			`"username":null,"last_name":null}}`, strings.Repeat("a", 151)), map[string]any{
			"user": map[string]any{
				"first_name": []string{"max:150"},
				"last_name":  []string{PaymentCategoryValidationNull},
				"email":      []string{SupplierValidationEmail},
				"username":   []string{PaymentCategoryValidationNull},
				"role":       []string{"choice:9"},
			}}},
		{"associate field errors", `{"associate":{"address_cap":"abc","born_date":"x","sex":"Z","is_minor":"maybe",` +
			`"sport_association":"abc","family":"` + nestedPaymentMissingID + `","email":"bad","associate_id":null,` +
			`"first_name":null,"tax_code":"` + strings.Repeat("t", 26) + `"}}`, map[string]any{
			"associate": map[string]any{
				"associate_id":      []string{PaymentCategoryValidationNull},
				"address_cap":       []string{AssociateCapMessage},
				"born_date":         []string{PersonaDateFormat},
				"email":             []string{SupplierValidationEmail},
				"sex":               []string{"choice:Z"},
				"is_minor":          []string{PaymentCategoryValidationBoolean},
				"sport_association": []string{"related-invalid-uuid:abc"},
				"tax_code":          []string{"max:25"},
				"family":            []string{"related-does-not-exist:" + nestedPaymentMissingID},
			}}},
		{"instructor field errors", `{"instructor":{"first_name":"","user":"` + nestedPaymentMissingID + `",` +
			`"default_hourly_billing":"x","documents":"abc","instructor_id":"abc","draft":"maybe",` +
			`"stipulated_contract_in":"x","default_percentage_billing":"123.4","associated_user_id":"z"}}`,
			map[string]any{"instructor": map[string]any{
				"instructor_id":              []string{PaymentCategoryValidationUUID},
				"first_name":                 []string{PaymentCategoryValidationBlank},
				"draft":                      []string{PaymentCategoryValidationBoolean},
				"associated_user_id":         []string{PaymentCategoryValidationUUID},
				"stipulated_contract_in":     []string{PersonaDateFormat},
				"default_hourly_billing":     []string{DRFValidationNumber},
				"default_percentage_billing": []string{"max-whole-digits:2"},
				"user":                       []string{"related-does-not-exist:" + nestedPaymentMissingID},
				"documents":                  []string{"not-a-list:str"},
			}}},
		{"invoice field errors", `{"invoice":{"membership_fee":"x","number":"y","sport_association":null,` +
			`"invoice_id":"abc","attached_documents":["` + nestedPaymentMissingID + `"],"selected_tutor":"abc",` +
			`"imported_from_associami":null,"meta":"x"}}`, map[string]any{"invoice": map[string]any{
			"invoice_id":         []string{PaymentCategoryValidationUUID},
			"membership_fee":     []string{DRFValidationNumber},
			"number":             []string{DRFValidationInteger},
			"sport_association":  []string{PaymentCategoryValidationNull},
			"selected_tutor":     []string{"related-invalid-uuid:abc"},
			"attached_documents": []string{"related-does-not-exist:" + nestedPaymentMissingID},
		}}},
		{"user is not a dictionary", `{"user":[]}`,
			map[string]any{"user": map[string]any{"non_field_errors": []string{"dictionary:list"}}}},
		{"choice uses str()", `{"user":{"role":2.0}}`,
			map[string]any{"user": map[string]any{"role": []string{"choice:2.0"}}}},
		{"associate null sex and date format", `{"associate":{"sex":null,"born_date":"1990-05-10",` +
			`"address_cap":"1234","email":"","tax_code":null}}`, map[string]any{"associate": map[string]any{
			"address_cap": []string{AssociateCapMessage},
			"born_date":   []string{PersonaDateFormat},
			"sex":         []string{PaymentCategoryValidationNull},
		}}},
		{"first failing many item", `{"instructor":{"documents":["abc","` + nestedPaymentMissingID + `"]},` +
			`"invoice":{"attached_documents":["` + nestedPaymentDocument + `","x"]}}`,
			map[string]any{
				"instructor": map[string]any{"documents": []string{"related-invalid-uuid:abc"}},
				"invoice":    map[string]any{"attached_documents": []string{"related-invalid-uuid:x"}},
			}},
		{"instructor nulls and lengths", `{"instructor":{"first_name":null,"last_name":"` +
			strings.Repeat("x", 151) + `","email":"a@b","role":"","phone":"` + strings.Repeat("1", 21) +
			`","born_date":"31/02/2020","default_hourly_billing":null,"is_volunteer":1}}`,
			map[string]any{"instructor": map[string]any{
				"first_name":             []string{PaymentCategoryValidationNull},
				"last_name":              []string{"max:150"},
				"email":                  []string{SupplierValidationEmail},
				"born_date":              []string{PersonaDateFormat},
				"role":                   []string{PaymentCategoryValidationBlank},
				"phone":                  []string{"max:20"},
				"default_hourly_billing": []string{PaymentCategoryValidationNull},
			}}},
		{"invoice decimals and integer bound", `{"invoice":{"membership_fee":"1.234","activity_fee":null,` +
			`"creation_date":"x","description":"","number":"2147483648","archived":"t","meta":null,"deleted":"no"}}`,
			map[string]any{"invoice": map[string]any{
				"creation_date":  []string{PaymentCategoryValidationDateTime},
				"membership_fee": []string{"max-decimal-places:2"},
				"activity_fee":   []string{PaymentCategoryValidationNull},
				"description":    []string{PaymentCategoryValidationBlank},
				"number":         []string{InvoicePartyMaxValue + "2147483647"},
			}}},
		{"valid payment category, accounts and association", `{"payment_category":"` + nestedPaymentCategory +
			`","custom_accounts":"` + nestedPaymentAccounts + `","sport_association":"` + nestedPaymentAssoc + `"}`,
			nil},
		{"valid null invoice", `{"invoice":null,"amount":"1"}`, nil},
		{"valid empty document lists", `{"instructor":{"documents":[]},"invoice":{"attached_documents":[]}}`, nil},
		{"valid document ids", `{"instructor":{"documents":["` + nestedPaymentDocument + `"]},` +
			`"invoice":{"attached_documents":["` + nestedPaymentDocument + `"],"document_pdf":"` +
			nestedPaymentDocument + `"}}`, nil},
		{"valid instructor users", `{"instructor":{"user":"a1000000-0000-4000-8000-000000700004"}}`, nil},
		{"valid instructor user 5", `{"instructor":{"user":"a1000000-0000-4000-8000-000000700005"}}`, nil},
		{"valid instructor user 1", `{"instructor":{"user":"` + nestedPaymentUser + `"}}`, nil},
		{"valid instructor user 6", `{"instructor":{"user":"a1000000-0000-4000-8000-000000700006"}}`, nil},
		{"valid group", `{"group":"` + nestedPaymentGroup + `"}`, nil},
		{"valid free payment id", `{"payment_id":"a1000000-0000-4000-8000-000000846705"}`, nil},
		{"valid add sample payment id", `{"payment_id":"a1000000-0000-4000-8000-0000009aaaa1"}`, nil},
		{"valid user blanks", `{"user":{"role":"1","email":"","first_name":""}}`, nil},
		{"valid associate single digit values", `{"associate":{"sex":"","born_date":"10/05/1990",` +
			`"address_cap":"12345","is_minor":"true","associate_id":5}}`, nil},
		{"valid empty invoice on update", `{"invoice":{}}`, nil},
		{"valid type subject amount and null description", `{"subject":"2","type":"cash","amount":5,` +
			`"description":null}`, nil},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			got, notPorted, err := ValidateNestedPayment(nestedPaymentBody(t, test.payment), true, false,
				nestedPaymentLookups())
			if err != nil || notPorted {
				t.Fatalf("unexpected abort: %v notPorted=%v", err, notPorted)
			}
			if !reflect.DeepEqual(got, test.want) {
				t.Fatalf("got %#v, want %#v", got, test.want)
			}
		})
	}
}

func TestValidateNestedPaymentJSONAbortsOnForeignReference(t *testing.T) {
	cases := map[string]string{
		"payment_category":             `{"payment_category":"a1000000-0000-4000-8000-000000846119"}`,
		"custom_accounts":              `{"custom_accounts":"a1000000-0000-4000-8000-000000846116","amount":"x"}`,
		"instructor user":              `{"instructor":{"user":"a1000000-0000-4000-8000-000000700007"}}`,
		"sport_association":            `{"sport_association":"a1000000-0000-4000-8000-000000701002"}`,
		"sport_association other":      `{"sport_association":"a1000000-0000-4000-8000-000000701003"}`,
		"associate association":        `{"associate":{"sport_association":"a1000000-0000-4000-8000-000000701002"}}`,
		"invoice association":          `{"invoice":{"sport_association":"a1000000-0000-4000-8000-000000701002"}}`,
		"invoice selected tutor":       `{"invoice":{"selected_tutor":"a1000000-0000-4000-8000-000000846700"}}`,
		"group":                        `{"group":"a1000000-0000-4000-8000-000000714002"}`,
		"group after an earlier error": `{"amount":"x","group":"a1000000-0000-4000-8000-000000714002"}`,
	}
	for name, payment := range cases {
		t.Run(name, func(t *testing.T) {
			got, notPorted, err := ValidateNestedPayment(nestedPaymentBody(t, payment), true, false,
				nestedPaymentLookups())
			if !errors.Is(err, nestedPaymentSentinel) || notPorted || got != nil {
				t.Fatalf("got %#v notPorted=%v err=%v, want the Related error", got, notPorted, err)
			}
		})
	}
}

func TestValidateNestedPaymentJSONAdd(t *testing.T) {
	cases := []struct {
		name    string
		payment string
		want    any
	}{
		{"empty object requires the nested serializers", `{}`, map[string]any{
			"user":       []string{PaymentCategoryValidationRequired},
			"associate":  []string{PaymentCategoryValidationRequired},
			"instructor": []string{PaymentCategoryValidationRequired},
			"supplier":   []string{PaymentCategoryValidationRequired},
		}},
		{"all nulls", `{"user":null,"associate":null,"instructor":null,"supplier":null,"invoice":null}`,
			map[string]any{
				"user":       []string{PaymentCategoryValidationNull},
				"associate":  []string{PaymentCategoryValidationNull},
				"instructor": []string{PaymentCategoryValidationNull},
				"supplier":   []string{PaymentCategoryValidationNull},
			}},
		{"empty invoice", `{"user":{},"associate":{},"instructor":{"first_name":"a","last_name":"b",` +
			`"user":"` + nestedPaymentUser + `"},"supplier":{"name":"s"},"invoice":{}}`, map[string]any{
			"invoice": map[string]any{
				"membership_fee":    []string{PaymentCategoryValidationRequired},
				"activity_fee":      []string{PaymentCategoryValidationRequired},
				"sport_association": []string{PaymentCategoryValidationRequired},
			}}},
		{"empty instructor and supplier", `{"user":{},"associate":{},"instructor":{},"supplier":{}}`, map[string]any{
			"instructor": map[string]any{
				"first_name": []string{PaymentCategoryValidationRequired},
				"last_name":  []string{PaymentCategoryValidationRequired},
				"user":       []string{PaymentCategoryValidationRequired},
			},
			"supplier": map[string]any{"name": []string{PaymentCategoryValidationRequired}},
		}},
		{"valid minimal payment", `{"user":{},"associate":{},"instructor":{"first_name":"a","last_name":"b",` +
			`"user":"` + nestedPaymentUser + `"},"supplier":{"name":"s"}}`, nil},
		{"valid complete payment", `{"payment_id":"a1000000-0000-4000-8000-0000009aaaa1","user":{"role":1},` +
			`"associate":{"first_name":"x","sport_association":"` + nestedPaymentAssoc + `"},` +
			`"instructor":{"first_name":"a","last_name":"b","user":"` + nestedPaymentUser + `","documents":["` +
			nestedPaymentDocument + `"]},"supplier":{"name":"s","supplier_id":"a1000000-0000-4000-8000-0000009aaaa2"},` +
			`"invoice":{"membership_fee":"1","activity_fee":"2","sport_association":"` + nestedPaymentAssoc + `"},` +
			`"amount":"3","payment_category":"` + nestedPaymentCategory + `"}`, nil},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			got, notPorted, err := ValidateNestedPayment(nestedPaymentBody(t, test.payment), false, false,
				nestedPaymentLookups())
			if err != nil || notPorted {
				t.Fatalf("unexpected abort: %v notPorted=%v", err, notPorted)
			}
			if !reflect.DeepEqual(got, test.want) {
				t.Fatalf("got %#v, want %#v", got, test.want)
			}
		})
	}
}

func TestValidateNestedPaymentForm(t *testing.T) {
	cases := []struct {
		name    string
		partial bool
		form    domain.RequestObject
		want    any
	}{
		{"invalid email and amount", true, domain.RequestObject{
			nestedPaymentFormField("amount", "x"),
			nestedPaymentFormField("user.email", "y"),
		}, map[string]any{
			"user":   map[string]any{"email": []string{SupplierValidationEmail}},
			"amount": []string{DRFValidationNumber},
		}},
		{"invalid amount alone", true, domain.RequestObject{nestedPaymentFormField("amount", "x")},
			map[string]any{"amount": []string{DRFValidationNumber}}},
		{"valid amount", true, domain.RequestObject{nestedPaymentFormField("amount", "3")}, nil},
		{"empty booleans and amount", true, domain.RequestObject{
			nestedPaymentFormField("paid", ""),
			nestedPaymentFormField("archived", ""),
			nestedPaymentFormField("amount", ""),
		}, nil},
		{"invalid meta json", true, domain.RequestObject{
			nestedPaymentFormField("meta", "x"),
			nestedPaymentFormField("course", `{"a":1}`),
		}, map[string]any{"meta": []string{CourseSubscriptionJSONInvalid}}},
		{"empty nullable fields", true, domain.RequestObject{
			nestedPaymentFormField("description", ""),
			nestedPaymentFormField("payment_date", ""),
			nestedPaymentFormField("payment_category", ""),
			nestedPaymentFormField("notes", ""),
		}, nil},
		{"first failing document", true, domain.RequestObject{
			nestedPaymentFormField("instructor.documents", "abc", nestedPaymentMissingID),
		}, map[string]any{"instructor": map[string]any{
			"documents": []string{"related-invalid-uuid:abc"},
		}}},
		{"empty user fields", true, domain.RequestObject{
			nestedPaymentFormField("user.role", ""),
			nestedPaymentFormField("user.phone", ""),
			nestedPaymentFormField("user.username", ""),
		}, nil},
		{"empty invoice fields", true, domain.RequestObject{
			nestedPaymentFormField("invoice.number", ""),
			nestedPaymentFormField("invoice.membership_fee", ""),
			nestedPaymentFormField("invoice.sport_association", ""),
		}, map[string]any{"invoice": map[string]any{
			"membership_fee":    []string{DRFValidationNumber},
			"sport_association": []string{PaymentCategoryValidationNull},
		}}},
		{"empty associate fields", true, domain.RequestObject{
			nestedPaymentFormField("associate.sex", ""),
			nestedPaymentFormField("associate.born_date", ""),
			nestedPaymentFormField("associate.associate_id", ""),
		}, nil},
		{"empty payment id and subject", true, domain.RequestObject{
			nestedPaymentFormField("payment_id", ""),
			nestedPaymentFormField("subject", ""),
		}, nil},
		{"add requires the nested serializers", false, domain.RequestObject{
			nestedPaymentFormField("amount", "x"),
		}, map[string]any{
			"user":       []string{PaymentCategoryValidationRequired},
			"associate":  []string{PaymentCategoryValidationRequired},
			"instructor": []string{PaymentCategoryValidationRequired},
			"supplier":   []string{PaymentCategoryValidationRequired},
			"amount":     []string{DRFValidationNumber},
		}},
		{"add with only the user nested dictionary", false, domain.RequestObject{
			nestedPaymentFormField("user.email", ""),
		}, map[string]any{
			"associate":  []string{PaymentCategoryValidationRequired},
			"instructor": []string{PaymentCategoryValidationRequired},
			"supplier":   []string{PaymentCategoryValidationRequired},
		}},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			got, notPorted, err := ValidateNestedPayment(test.form, test.partial, true, nestedPaymentLookups())
			if err != nil || notPorted {
				t.Fatalf("unexpected abort: %v notPorted=%v", err, notPorted)
			}
			if !reflect.DeepEqual(got, test.want) {
				t.Fatalf("got %#v, want %#v", got, test.want)
			}
		})
	}
}

func TestValidateNestedPaymentNonObject(t *testing.T) {
	got, notPorted, err := ValidateNestedPayment([]any{"x"}, true, false, nestedPaymentLookups())
	want := map[string]any{"non_field_errors": []string{"dictionary:list"}}
	if err != nil || notPorted || !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v notPorted=%v err=%v, want %#v", got, notPorted, err, want)
	}
}

func TestValidateNestedPaymentFormJSONDepthIsNotPorted(t *testing.T) {
	deep := strings.Repeat("[", 501) + strings.Repeat("]", 501)
	got, notPorted, err := ValidateNestedPayment(domain.RequestObject{
		nestedPaymentFormField("course", deep),
	}, true, true, nestedPaymentLookups())
	if err != nil || !notPorted || got != nil {
		t.Fatalf("got %#v notPorted=%v err=%v, want the unported depth", got, notPorted, err)
	}
}
