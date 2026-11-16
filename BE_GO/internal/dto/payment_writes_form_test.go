package dto

import (
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// paymentEntryFormBody is a form RequestObject: repeated keys keep the last
// value and their list, as the transport parses them.
func paymentEntryFormBody(pairs ...any) domain.RequestObject {
	object := domain.RequestObject{}
	positions := map[string]int{}
	for index := 0; index+1 < len(pairs); index += 2 {
		name, value := pairs[index].(string), pairs[index+1]
		if at, known := positions[name]; known {
			if object[at].All == nil {
				object[at].All = []any{object[at].Value}
			}
			object[at].Value = value
			object[at].All = append(object[at].All, value)
			continue
		}
		positions[name] = len(object)
		object = append(object, domain.RequestField{Name: name, Value: value})
	}
	return object
}

func paymentEntryFormCodes(invalid *PaymentEntryValidationError) map[string][]string {
	if invalid == nil {
		return nil
	}
	codes := map[string][]string{}
	for _, field := range invalid.Fields {
		codes[field.Name] = field.Codes
		for _, item := range field.Items {
			codes[field.Name+"/item"] = item.Codes
		}
	}
	return codes
}

func paymentEntryFormParse(t *testing.T, partial bool, pairs ...any) (PaymentEntryInput, map[string][]string) {
	t.Helper()
	converted, refused := PaymentEntryFormObject(paymentEntryFormBody(pairs...), partial)
	if refused {
		t.Fatalf("unexpected refusal for %v", pairs)
	}
	input, invalid := ParsePaymentEntry(converted, partial)
	return input, paymentEntryFormCodes(invalid)
}

// An empty HTML input follows Field.get_value: null+blank keeps the empty
// string, null alone is None, an optional field without either is skipped.
func TestPaymentEntryFormEmptyValues(t *testing.T) {
	input, codes := paymentEntryFormParse(t, true, "description", "", "notes", "", "amount", "", "type", "", "paid", "")
	if len(codes) > 0 {
		t.Fatalf("codes=%v", codes)
	}
	if !input.Description.Present || input.Description.Value != nil {
		t.Fatalf("description=%+v", input.Description)
	}
	if !input.Notes.Present || input.Notes.Value == nil || *input.Notes.Value != "" {
		t.Fatalf("notes=%+v", input.Notes)
	}
	if input.Amount != nil || input.Type != nil || input.Paid != nil {
		t.Fatalf("skipped fields kept: amount=%v type=%v paid=%v", input.Amount, input.Type, input.Paid)
	}
}

// A repeated HTML key is read as its last value (QueryDict.__getitem__).
func TestPaymentEntryFormRepeatedKeyLastValue(t *testing.T) {
	input, codes := paymentEntryFormParse(t, true, "type", "cash", "type", "transfer")
	if len(codes) > 0 {
		t.Fatalf("codes=%v", codes)
	}
	if input.Type == nil || *input.Type != "transfer" {
		t.Fatalf("type=%v", input.Type)
	}
}

// A JSONField marks a present HTML value as JSON text: json.loads parses it
// and an empty input is invalid JSON (DRF's JSONField.get_value override).
func TestPaymentEntryFormJSONText(t *testing.T) {
	input, codes := paymentEntryFormParse(t, true, "course", `{"a": 1}`, "meta_payment_categories", `[{"amount": 2}]`)
	if len(codes) > 0 {
		t.Fatalf("codes=%v", codes)
	}
	course, ok := input.Course.Value.(domain.RequestObject)
	if !input.Course.Present || !ok || len(course) != 1 {
		t.Fatalf("course=%+v", input.Course)
	}
	meta, ok := input.Meta.Value.([]any)
	if !input.Meta.Present || !ok || len(meta) != 1 {
		t.Fatalf("meta=%+v", input.Meta)
	}
	if _, codes = paymentEntryFormParse(t, true, "course", ""); len(codes) == 0 || codes["course"][0] != CourseSubscriptionJSONInvalid {
		t.Fatalf("empty course codes=%v", codes)
	}
	if _, codes = paymentEntryFormParse(t, true, "meta_payment_categories", "{a"); len(codes) == 0 ||
		codes["meta_payment_categories"][0] != CourseSubscriptionJSONInvalid {
		t.Fatalf("invalid json codes=%v", codes)
	}
}

// A nested serializer reads its "<name>.<field>" keys; a plain <name> key is
// ignored (Serializer.get_value parses an HTML dictionary).
func TestPaymentEntryFormNestedUser(t *testing.T) {
	input, codes := paymentEntryFormParse(t, true, "user", "abc")
	if len(codes) > 0 || input.UserPresent {
		t.Fatalf("codes=%v user=%v", codes, input.UserPresent)
	}
	input, codes = paymentEntryFormParse(t, true, "user.first_name", "Mario", "user.username", "")
	if len(codes) > 0 || !input.UserPresent {
		t.Fatalf("codes=%v user=%v", codes, input.UserPresent)
	}
	// A top-level field error is reported beside the nested object's.
	converted, _ := PaymentEntryFormObject(paymentEntryFormBody("user.role", "9"), true)
	_, invalid := ParsePaymentEntry(converted, true)
	if invalid == nil || len(invalid.Fields) != 1 || invalid.Fields[0].Name != "user" || len(invalid.Fields[0].Nested) == 0 ||
		invalid.Fields[0].Nested[0].Codes[0] != "choice:9" {
		t.Fatalf("invalid=%v", invalid)
	}
}

// A ListField reads QueryDict.getlist, else html.parse_html_list: the indexed
// keys of one item keep their key names (DictField.to_internal_value's dict()).
func TestPaymentEntryFormAttachmentsData(t *testing.T) {
	input, codes := paymentEntryFormParse(t, true, "attachments_data[0][document]", "UmVn",
		"attachments_data[0][filename]", "x.pdf")
	if len(codes) > 0 {
		t.Fatalf("codes=%v", codes)
	}
	if !input.AttachmentsPresent || len(input.Attachments) != 1 {
		t.Fatalf("attachments=%v", input.Attachments)
	}
	if _, ok := input.Attachments[0].Lookup("document"); ok {
		t.Fatalf("item keys=%v", input.Attachments[0])
	}
	if filename, _ := input.Attachments[0].Lookup("[filename]"); filename != "x.pdf" {
		t.Fatalf("item keys=%v", input.Attachments[0])
	}
	// attachments_data='' is QueryDict.getlist's [''], a string item.
	if _, codes = paymentEntryFormParse(t, true, "attachments_data", ""); codes["attachments_data/item"] == nil {
		t.Fatalf("codes=%v", codes)
	}
	// A plain indexed value is a string item too.
	if _, codes = paymentEntryFormParse(t, true, "attachments_data[0]", "abc"); codes["attachments_data/item"] == nil {
		t.Fatalf("codes=%v", codes)
	}
	// An absent list is skipped in partial mode.
	if input, codes = paymentEntryFormParse(t, true, "amount", "5"); len(codes) > 0 || input.AttachmentsPresent {
		t.Fatalf("codes=%v attachments=%v", codes, input.Attachments)
	}
}

// A related field's empty input is None (allow_null), a choice's/boolean's is
// skipped, and a non-empty choice/boolean is validated as sent.
func TestPaymentEntryFormRelatedAndChoices(t *testing.T) {
	input, codes := paymentEntryFormParse(t, true, "payment_category", "", "custom_accounts", "",
		"paid", "", "type", "")
	if len(codes) > 0 {
		t.Fatalf("codes=%v", codes)
	}
	if input.Paid != nil || input.Type != nil || !input.Category.Present || input.Category.ID != nil ||
		!input.Account.Present || input.Account.ID != nil {
		t.Fatalf("paid=%v type=%v category=%+v account=%+v", input.Paid, input.Type, input.Category, input.Account)
	}
	if _, codes = paymentEntryFormParse(t, true, "paid", "maybe", "type", "card"); codes["paid"] == nil || codes["type"] == nil {
		t.Fatalf("codes=%v", codes)
	}
}

// A JSON field nested past the ported depth or holding a lone surrogate escape
// is a handoff refusal, not a field error: the conversion reports it and
// ParsePaymentEntry still sees the text.
func TestPaymentEntryFormJSONTooDeep(t *testing.T) {
	for _, text := range []string{strings.Repeat("[", 502) + strings.Repeat("]", 502), `"\ud800"`} {
		converted, refused := PaymentEntryFormObject(paymentEntryFormBody("course", text), true)
		if !refused {
			t.Fatalf("%q not reported as refused", text)
		}
		if _, present := converted.Lookup("course"); !present {
			t.Fatalf("%q: course dropped", text)
		}
	}
	// A depth the ported parser accepts is not refused (Django's band depends
	// on the live recursion depth and stays the handoff's concern).
	if _, refused := PaymentEntryFormObject(paymentEntryFormBody("course", `{"a": 1}`), true); refused {
		t.Fatal("shallow JSON text refused")
	}
}

// payment_id is the serializer's UUIDField: uuid.UUID(hex=...) forms, an int
// (a bool included) through UUID(int=...), null and anything else.
func TestPaymentEntryPaymentID(t *testing.T) {
	canonical := "a1000000-0000-4000-8000-000000988450"
	for _, value := range []any{canonical, strings.ReplaceAll(canonical, "-", ""), "{" + canonical + "}",
		"urn:uuid:" + canonical, strings.ToUpper(canonical)} {
		input, invalid := ParsePaymentEntry(domain.RequestObject{{Name: "payment_id", Value: value}}, true)
		if invalid != nil || input.PaymentID == nil || *input.PaymentID != canonical {
			t.Fatalf("value=%v input=%+v invalid=%v", value, input.PaymentID, invalid)
		}
	}
	input, invalid := ParsePaymentEntry(domain.RequestObject{{Name: "payment_id", Value: JSONInteger(1)}}, true)
	if invalid != nil || input.PaymentID == nil || *input.PaymentID != "00000000-0000-0000-0000-000000000001" {
		t.Fatalf("int input=%+v invalid=%v", input.PaymentID, invalid)
	}
	if input, invalid = ParsePaymentEntry(domain.RequestObject{{Name: "payment_id", Value: true}}, true); invalid != nil ||
		input.PaymentID == nil || *input.PaymentID != "00000000-0000-0000-0000-000000000001" {
		t.Fatalf("bool input=%+v invalid=%v", input.PaymentID, invalid)
	}
	for _, value := range []any{nil, "abc", "", []any{"x"}, map[string]any{}} {
		if _, invalid = ParsePaymentEntry(domain.RequestObject{{Name: "payment_id", Value: value}}, true); invalid == nil {
			t.Fatalf("value=%v accepted", value)
		}
		want := PaymentCategoryValidationUUID
		if value == nil {
			want = PaymentCategoryValidationNull
		}
		if invalid.Fields[0].Name != "payment_id" || invalid.Fields[0].Codes[0] != want {
			t.Fatalf("value=%v invalid=%v", value, invalid.Fields)
		}
	}
	// payment_id leads the serializer's error order.
	_, invalid = ParsePaymentEntry(domain.RequestObject{{Name: "amount", Value: "x"}, {Name: "payment_id", Value: "abc"}}, false)
	if invalid == nil || invalid.Fields[0].Name != "payment_id" {
		t.Fatalf("invalid=%v", invalid)
	}
}
