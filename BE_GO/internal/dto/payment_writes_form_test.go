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
	converted := PaymentEntryFormObject(paymentEntryFormBody(pairs...), partial)
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
	converted := PaymentEntryFormObject(paymentEntryFormBody("user.role", "9"), true)
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

// A JSON form field's text is parsed by the serializer's JSONField, which
// reports its nesting for the update view's measured bands (owner decision X15)
// and the lone surrogate escape a value can keep (the jsonb write then refuses
// it at save time). The conversion only marks the text as JSON.
func TestPaymentEntryFormJSONDepth(t *testing.T) {
	parsed := func(pairs ...any) (PaymentEntryInput, *PaymentEntryValidationError) {
		return ParsePaymentEntry(PaymentEntryFormObject(paymentEntryFormBody(pairs...), true), true)
	}
	for _, depth := range []int{1, 501, 600, 9934, 9935, 9937, 9938, 10000, formJSONNesting + 2} {
		input, invalid := parsed("course", deepArrays(depth))
		if invalid != nil || input.FormJSONDepth != depth || input.FormJSONRejected {
			t.Fatalf("course depth %d: invalid=%v depth=%d rejected=%v", depth, invalid, input.FormJSONDepth, input.FormJSONRejected)
		}
		course, isList := input.Course.Value.([]any)
		if !input.Course.Present || !isList {
			t.Fatalf("course depth %d: %#v", depth, input.Course.Value)
		}
		if depth > formJSONNesting && len(course) != 0 {
			t.Fatalf("course depth %d: %d items", depth, len(course))
		}
	}
	// The depth is the deepest of the JSON fields present, and a JSON body is
	// never measured (its text is already parsed).
	input, invalid := parsed("course", deepArrays(9937), "meta_payment_categories", deepArrays(3))
	if invalid != nil || input.FormJSONDepth != 9937 {
		t.Fatalf("two fields: invalid=%v depth=%d", invalid, input.FormJSONDepth)
	}
	if input, invalid = ParsePaymentEntry(domain.RequestObject{{Name: "course", Value: []any{}}}, true); invalid != nil ||
		input.FormJSONDepth != 0 {
		t.Fatalf("json body: invalid=%v depth=%d", invalid, input.FormJSONDepth)
	}
	// A lone surrogate escape parses and validates; the value is reported so the
	// service takes the save's pre_save-audit path.
	if input, invalid = parsed("course", `["\ud800"]`); invalid != nil || !input.FormJSONRejected || input.FormJSONDepth != 1 {
		t.Fatalf("surrogate: invalid=%v rejected=%v depth=%d", invalid, input.FormJSONRejected, input.FormJSONDepth)
	}
	// A text Python cannot parse is the field's "Value must be valid JSON.".
	if _, invalid = parsed("course", "nope"); invalid == nil || invalid.Fields[0].Name != "course" {
		t.Fatalf("invalid text: %v", invalid)
	}
	if _, invalid = parsed("course", `{"a": 1}`); invalid != nil {
		t.Fatalf("shallow JSON text: %v", invalid)
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
