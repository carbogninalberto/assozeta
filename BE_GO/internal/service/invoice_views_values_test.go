package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/djtemplate"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// invoiceViewsValueStore serves the course name and category lookups of the
// values below.
type invoiceViewsValueStore struct {
	domain.InvoiceViewStore
	course   domain.InvoiceViewCourseName
	category domain.InvoiceViewCategory
	missing  bool
	lookedUp []string
}

func (s *invoiceViewsValueStore) CourseName(context.Context, string, *string) (domain.InvoiceViewCourseName, error) {
	return s.course, nil
}

func (s *invoiceViewsValueStore) Category(_ context.Context, id string) (*domain.InvoiceViewCategory, error) {
	s.lookedUp = append(s.lookedUp, id)
	if s.missing {
		return nil, domain.ErrViewNotFound
	}
	row := s.category
	return &row, nil
}

func invoiceViewsPtr(text string) *string { return &text }

func invoiceViewsJSONList(t *testing.T, text string) []any {
	t.Helper()
	value, err := invoiceViewsJSON(&text)
	if err != nil {
		t.Fatalf("parse %s: %v", text, err)
	}
	list, ok := value.([]any)
	if !ok {
		t.Fatalf("parse %s: %T", text, value)
	}
	return list
}

func invoiceViewsServer(t *testing.T, err error) {
	t.Helper()
	if !errors.Is(err, ErrDocumentViewServer) {
		t.Fatalf("error = %v, want the 500 page", err)
	}
}

// Python 3.13:
//
//	>>> from decimal import Decimal
//	>>> str(Decimal('10.00') + Decimal('0.25'))
//	'10.25'
//
// A value with a different scale cannot come from a
// DecimalField(max_digits=9, decimal_places=2).
func TestInvoiceViewsDecimalSum(t *testing.T) {
	total, err := invoiceViewsDecimalSum("10.00", "0.25")
	if err != nil || total != "10.25" {
		t.Fatalf("total = %q, %v; want 10.25", total, err)
	}
	for _, pair := range [][2]string{{"10", "0.25"}, {"10.00", "1"}, {"x", "0.00"}} {
		_, err := invoiceViewsDecimalSum(pair[0], pair[1])
		if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
			t.Fatalf("%v: error = %v, want an application-state error", pair, err)
		}
	}
}

// Python 3.13:
//
//	>>> 'description' in [1, 'description'], 'description' in [1, 2]
//	(True, False)
//	>>> 'label' in 'xxlabelxx', 'label' in 'nope'
//	(True, False)
//	>>> 'description' in 5
//	TypeError: argument of type 'int' is not iterable
//	>>> 'description' in True
//	TypeError: argument of type 'bool' is not iterable
func TestInvoiceViewsMembership(t *testing.T) {
	cases := []struct {
		value           any
		present, usable bool
	}{
		{[]any{json.Number("1"), "description"}, true, true},
		{[]any{json.Number("1"), json.Number("2")}, false, true},
		{"xxdescriptionxx", true, true},
		{"nope", false, true},
		{json.Number("5"), false, false},
		{true, false, false},
		{nil, false, false},
	}
	for _, c := range cases {
		present, usable := invoiceViewsMembership(c.value, "description")
		if present != c.present || usable != c.usable {
			t.Errorf("%#v: (%v, %v), want (%v, %v)", c.value, present, usable, c.present, c.usable)
		}
	}
}

// Python 3.13:
//
//	>>> [bool(json.loads(t)) for t in ('1e-400', '1e400', '-0.0', '0', '0.0', '""', '[]', '[0]', '{}', '123456789012345678901234567890')]
//	[False, True, False, False, False, False, False, True, False, True]
//	>>> bool(float('inf'))
//	True
func TestInvoiceViewsTruthy(t *testing.T) {
	cases := []struct {
		value any
		want  bool
	}{
		{nil, false}, {false, false}, {true, true}, {"", false}, {"0", true},
		{json.Number("0"), false}, {json.Number("0.0"), false}, {json.Number("-0.0"), false},
		{json.Number("1e-400"), false}, {json.Number("1e400"), true},
		{json.Number("123456789012345678901234567890"), true},
		{[]any{}, false}, {[]any{json.Number("0")}, true}, {domain.RequestObject{}, false},
		{int64(0), false}, {int64(1), true}, {0.0, false}, {math.Inf(1), true},
		{djtemplate.List{}, false}, {djtemplate.NewDict(), false},
	}
	for _, c := range cases {
		if got := invoiceViewsTruthy(c.value); got != c.want {
			t.Errorf("bool(%#v) = %v, want %v", c.value, got, c.want)
		}
	}
}

// Python 3.13:
//
//	>>> 'description' in ['x'], 'description' in ['description']
//	(False, True)
//	>>> ['description']['description']
//	TypeError: list indices must be integers or slices, not str
//	>>> 'description' in 'x', 'description' in 'a description'
//	(False, True)
//	>>> 'a description'['description']
//	TypeError: string indices must be integers, not 'str'
//	>>> 'description' in 5
//	TypeError: argument of type 'int' is not iterable
//	>>> 'label' in {'label': 'Pilates'}, {'label': 'Pilates'}['label']
//	(True, 'Pilates')
//	>>> ", ".join([5, 'Lunedì'])
//	TypeError: sequence item 0: expected str instance, int found
func TestInvoiceViewsCourseCarnetName(t *testing.T) {
	course := domain.InvoiceViewCourseName{Found: true, Title: invoiceViewsPtr("Nuoto & Acqua")}
	store := &invoiceViewsValueStore{course: course}
	views := NewInvoiceViews(store, nil)
	payment := func(meta, course, description *string) domain.InvoiceViewPayment {
		return domain.InvoiceViewPayment{ID: "p", Meta: meta, Course: course, Description: description}
	}
	ctx := context.Background()

	found := []struct {
		name   string
		meta   *string
		want   any
		server bool
	}{
		{"list without description", invoiceViewsPtr(`["x"]`), "Nuoto & Acqua", false},
		{"list with description", invoiceViewsPtr(`["description"]`), nil, true},
		{"str without description", invoiceViewsPtr(`"x"`), "Nuoto & Acqua", false},
		{"str with description", invoiceViewsPtr(`"a description"`), nil, true},
		{"number", invoiceViewsPtr(`5`), nil, true},
		{"bool", invoiceViewsPtr(`true`), nil, true},
		{"description int", invoiceViewsPtr(`{"description": 5}`), int64(5), false},
		{"description float", invoiceViewsPtr(`{"description": 1.5}`), 1.5, false},
		{"description none", invoiceViewsPtr(`{"description": null}`), nil, false},
		{"description big int", invoiceViewsPtr(`{"description": 123456789012345678901234567890}`),
			mustBigInt(t, "123456789012345678901234567890"), false},
	}
	for _, c := range found {
		got, err := views.courseCarnetName(ctx, payment(c.meta, nil, invoiceViewsPtr("Lunedì")), nil)
		if c.server {
			invoiceViewsServer(t, err)
			continue
		}
		if err != nil || !invoiceViewsSameValue(got, c.want) {
			t.Errorf("%s: got %#v, %v; want %#v", c.name, got, err, c.want)
		}
	}

	notFound := &invoiceViewsValueStore{}
	views = NewInvoiceViews(notFound, nil)
	courses := []struct {
		name   string
		course *string
		want   any
		server bool
	}{
		{"list without label", invoiceViewsPtr(`[1, 2]`), "Lunedì", false},
		{"list with label", invoiceViewsPtr(`["label"]`), nil, true},
		{"str without label", invoiceViewsPtr(`"x"`), "Lunedì", false},
		{"str with label", invoiceViewsPtr(`"a label"`), nil, true},
		{"number", invoiceViewsPtr(`5`), nil, true},
		{"dict without label", invoiceViewsPtr(`{"x": 1}`), "Lunedì", false},
		{"dict label str", invoiceViewsPtr(`{"label": "Pilates"}`), "Pilates, Lunedì", false},
		{"dict label int", invoiceViewsPtr(`{"label": 5}`), nil, true},
	}
	for _, c := range courses {
		got, err := views.courseCarnetName(ctx, payment(nil, c.course, invoiceViewsPtr("Lunedì")), nil)
		if c.server {
			invoiceViewsServer(t, err)
			continue
		}
		if err != nil || got != c.want {
			t.Errorf("%s: got %#v, %v; want %#v", c.name, got, err, c.want)
		}
	}
}

func mustBigInt(t *testing.T, text string) *big.Int {
	t.Helper()
	number, ok := new(big.Int).SetString(text, 10)
	if !ok {
		t.Fatalf("big int %s", text)
	}
	return number
}

func invoiceViewsSameValue(left, right any) bool {
	switch right := right.(type) {
	case *big.Int:
		left, ok := left.(*big.Int)
		return ok && left.Cmp(right) == 0
	case nil:
		return left == nil
	}
	return left == right
}

// Python 3.13:
//
//	>>> json.loads('[5, 123456789012345678901234567890, 1e400, -1e400, 1e-400, 1.5, true, null, "x", [1], {"a": 2}]')
//	[5, 123456789012345678901234567890, inf, -inf, 0.0, 1.5, True, None, 'x', [1], {'a': 2}]
func TestInvoiceViewsTemplateValue(t *testing.T) {
	cases := []struct {
		text string
		want any
	}{
		{`5`, int64(5)},
		{`123456789012345678901234567890`, mustBigInt(t, "123456789012345678901234567890")},
		{`1e400`, math.Inf(1)},
		{`-1e400`, math.Inf(-1)},
		{`1e-400`, 0.0},
		{`1.5`, 1.5},
		{`true`, true},
		{`null`, nil},
		{`"x"`, "x"},
	}
	for _, c := range cases {
		raw := c.text
		parsed, err := invoiceViewsJSON(&raw)
		if err != nil {
			t.Fatalf("parse %s: %v", c.text, err)
		}
		got, err := invoiceViewsTemplateValue(parsed)
		if err != nil {
			t.Fatalf("%s: %v", c.text, err)
		}
		if !invoiceViewsSameValue(got, c.want) {
			t.Errorf("%s: got %#v, want %#v", c.text, got, c.want)
		}
	}
	raw := `[1, {"a": 2}]`
	parsed, err := invoiceViewsJSON(&raw)
	if err != nil {
		t.Fatal(err)
	}
	got, err := invoiceViewsTemplateValue(parsed)
	if err != nil {
		t.Fatal(err)
	}
	list, ok := got.(djtemplate.List)
	if !ok || len(list) != 2 {
		t.Fatalf("list = %#v", got)
	}
	dict, ok := list[1].(*djtemplate.Dict)
	if !ok {
		t.Fatalf("item = %#v", list[1])
	}
	if value, _ := dict.Get("a"); value != int64(2) {
		t.Fatalf("a = %#v", value)
	}
	// jsonb reads 1.9e308 + 0.5 back with its fraction, so json.loads reads inf.
	overflow := "1" + "9" + strings.Repeat("0", 307) + ".5"
	value, err := invoiceViewsTemplateValue(json.Number(overflow))
	if err != nil || !math.IsInf(value.(float64), 1) {
		t.Fatalf("overflow = %#v, %v", value, err)
	}
}

// CPython 3.13.16, on the JSON texts jsonb hands back: a numeric is written in
// full, so no exponent survives and only a fraction keeps a float. <10**N>
// below stands for its N+1 digits, <2**N> for that integer in full and
// <1.9e308 + 0.5> for its 309 digits followed by ".5" (json.loads reads inf).
//
//	>>> import json
//	>>> def s(text):
//	...     return repr(float(sum(json.loads(text))))
//	>>> s('[0.1, 0.2, 0.3]')                 # plain floats: 0.6000000000000001
//	'0.6'
//	>>> s('[1, 0.1, 0.2, 0.3]')
//	'1.6'
//	>>> s('[0.1, <2**70>, 0.2, 0.3]')
//	'1.1805916207174113e+21'
//	>>> s('[<2**63>, 0.5]')
//	'9.223372036854776e+18'
//	>>> s('[0.5, <2**63>, 0.1, 0.2]')
//	'9.223372036854776e+18'
//	>>> s('[true, 0.1, 0.7]')
//	'1.8'
//	>>> s('[<10**308>, <10**308>, -<10**308>]')
//	'1e+308'
//	>>> s('[0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]')
//	'1.0'
//	>>> s('[<10**16>, 1.0, -<10**16>]')
//	'0.0'
//	>>> s('[<10**400>, 0.5]')
//	OverflowError: int too large to convert to float
//	>>> s('[0.5, <10**400>]')
//	OverflowError: int too large to convert to float
//	>>> s('[<1.9e308 + 0.5>, -<1.9e308 + 0.5>]')
//	'nan'
func TestInvoiceViewsSum(t *testing.T) {
	ten := func(exponent int) string { return "1" + strings.Repeat("0", exponent) }
	infinity := "1" + "9" + strings.Repeat("0", 307) + ".5"
	amounts := func(elements ...string) string {
		items := make([]string, len(elements))
		for i, element := range elements {
			items[i] = `{"amount": ` + element + `}`
		}
		return "[" + strings.Join(items, ", ") + "]"
	}
	fill := func(element string, count int) []string {
		elements := make([]string, count)
		for i := range elements {
			elements[i] = element
		}
		return elements
	}
	// [2**53, 1.0 * 5000, 2**63]: Neumaier's compensation reaches 5000 before
	// the int beyond a C long is added, so it is added to the total first.
	flushed := append([]string{"9007199254740992"}, fill("1.0", 5000)...)
	flushed = append(flushed, "9223372036854775808")
	// [0.5, 2**63, 1000.0 * 1000]: the tail after an int beyond a C long is a
	// plain addition, so it never reaches the total.
	naiveTail := append([]string{"0.5", "9223372036854775808"}, fill("1000.0", 1000)...)

	cases := []struct {
		name string
		text string
		want string // repr(float(sum(...)))
	}{
		{"plain floats would say 0.6000000000000001", amounts("0.1", "0.2", "0.3"), "0.6"},
		{"an int total hands over to the float loop", amounts("1", "0.1", "0.2", "0.3"), "1.6"},
		{"ten tenths", amounts(fill("0.1", 10)...), "1.0"},
		{"ints that fit stay exact", amounts("1", "2"), "3.0"},
		{"a bool is an int", amounts("true", "true"), "2.0"},
		{"a bool before floats is the int total", amounts("true", "0.1", "0.7"), "1.8"},
		{"an int and a float", amounts("1", "2.5"), "3.5"},
		{"a negative zero total", amounts("-0.0"), "0.0"},
		{"a float total and a float", amounts("0.1", "0.2"), "0.30000000000000004"},
		{"a big int total", amounts("1606938044258990275541962092341162602522202993782792835301376"), "1.6069380442589903e+60"},
		{"jsonb's 1e308 is an exact integer", amounts(ten(308), ten(308), "-"+ten(308)), "1e+308"},
		{"an int that fits a C long skips the compensation", amounts(ten(16), "1.0", "-"+ten(16)), "0.0"},
		{"an int beyond a C long ends the compensation", amounts("0.1", "1180591620717411303424", "0.2", "0.3"), "1.1805916207174113e+21"},
		{"an int beyond int64 in the int loop", amounts("9223372036854775808", "0.5"), "9.223372036854776e+18"},
		{"an int beyond int64 after a float", amounts("0.5", "9223372036854775808", "0.1", "0.2"), "9.223372036854776e+18"},
		{"the compensation is added before that int", amounts(flushed...), "9.23237923610952e+18"},
		{"the tail after that int is naive", amounts(naiveTail...), "9.223372036854776e+18"},
		{"an infinite float", amounts(infinity), "inf"},
		{"inf and -inf", amounts(infinity, "-"+infinity), "nan"},
		{"an infinite total ignores the compensation", amounts(infinity, "1.0"), "inf"},
		{"inf, inf and -inf", amounts(infinity, infinity, "-"+infinity), "nan"},
	}
	for _, c := range cases {
		got, err := invoiceViewsSum(invoiceViewsJSONList(t, c.text))
		if err != nil {
			t.Errorf("%s: %v", c.name, err)
			continue
		}
		if text := dto.PythonFloatStr(got); text != c.want {
			t.Errorf("%s: got %s, want %s", c.name, text, c.want)
		}
	}
	for _, c := range []struct {
		name string
		text string
	}{
		{"an int beyond a double alone", amounts(ten(400))},
		{"an int beyond a double in the int loop", amounts(ten(400), "0.5")},
		{"an int beyond a double after a float", amounts("0.5", ten(400))},
		{"an int beyond a double after the compensation", amounts("0.1", "0.2", ten(400))},
		{"a str amount", `[{"amount": "3"}]`},
		{"a null amount", `[{"amount": null}]`},
		{"a list amount", `[{"amount": [1]}]`},
		{"an object amount", `[{"amount": {}}]`},
		{"a missing amount", `[{}]`},
		{"an item that is not an object", `[5]`},
	} {
		if _, err := invoiceViewsSum(invoiceViewsJSONList(t, c.text)); !errors.Is(err, ErrDocumentViewServer) {
			t.Errorf("%s: error = %v, want the 500 page", c.name, err)
		}
	}
}

// Python 3.13:
//
//	>>> 'ΟΔΟΣ'.lower(), 'İstanbul'.lower()
//	('οδος', 'i̇stanbul')
//	>>> uuid.UUID(int=214005707321371757778191212399293915153)
//	UUID('a1000000-0000-4000-8000-000000986011')
//	>>> uuid.UUID(int=True)
//	UUID('00000000-0000-0000-0000-000000000001')
//	>>> PaymentCategory.objects.get(payment_category_id=5)
//	DoesNotExist (UUID(int=5))
func TestInvoiceViewsPaymentCategories(t *testing.T) {
	store := &invoiceViewsValueStore{category: domain.InvoiceViewCategory{Name: "ΟΔΟΣ"}}
	views := NewInvoiceViews(store, nil)
	invoice := &domain.InvoiceViewInvoice{ActivityFee: "10.00"}
	category := &invoiceViewsCategory{name: "Kit & Maglia"}
	meta := `[{"payment_category_id": "a1000000-0000-4000-8000-000000986011", "amount": 1}]`
	payment := domain.InvoiceViewPayment{Subject: 0, MetaPaymentCategories: &meta}
	got, stampFree, err := views.paymentCategories(context.Background(), payment, invoice, category, false)
	if err != nil {
		t.Fatal(err)
	}
	dict := got.(*djtemplate.Dict)
	if keys := dict.Keys(); len(keys) != 2 || keys[0] != "kit & maglia" || keys[1] != "οδος" {
		t.Fatalf("keys = %q", keys)
	}
	if value, _ := dict.Get("kit & maglia"); value != 9.0 {
		t.Fatalf("main amount = %#v", value)
	}
	if stampFree {
		t.Fatal("stamp_free true")
	}

	store = &invoiceViewsValueStore{category: domain.InvoiceViewCategory{Name: "İstanbul"}}
	views = NewInvoiceViews(store, nil)
	meta = `[{"payment_category_id": "a1000000-0000-4000-8000-000000986016", "amount": 2}]`
	payment = domain.InvoiceViewPayment{Subject: 0, MetaPaymentCategories: &meta}
	got, _, err = views.paymentCategories(context.Background(), payment, invoice, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	if keys := got.(*djtemplate.Dict).Keys(); len(keys) != 1 || keys[0] != "i̇stanbul" {
		t.Fatalf("U+0130 keys = %q", keys)
	}

	// UUIDField.to_python(int) and the lowercased name of an int id.
	store = &invoiceViewsValueStore{category: domain.InvoiceViewCategory{Name: "KIT"}}
	views = NewInvoiceViews(store, nil)
	meta = `[{"payment_category_id": 214005707321371757778191212399293915153, "amount": 2}]`
	payment = domain.InvoiceViewPayment{Subject: 0, MetaPaymentCategories: &meta}
	got, _, err = views.paymentCategories(context.Background(), payment, invoice, nil, false)
	if err != nil {
		t.Fatal(err)
	}
	dict = got.(*djtemplate.Dict)
	if keys := dict.Keys(); len(keys) != 1 || keys[0] != "kit" {
		t.Fatalf("keys = %q", keys)
	}
	if len(store.lookedUp) != 1 || store.lookedUp[0] != "a1000000-0000-4000-8000-000000986011" {
		t.Fatalf("looked up %q", store.lookedUp)
	}

	// bool -> UUID(int=1); a missing row is the 500 page.
	store = &invoiceViewsValueStore{missing: true}
	views = NewInvoiceViews(store, nil)
	meta = `[{"payment_category_id": true, "amount": 1}]`
	payment = domain.InvoiceViewPayment{Subject: 0, MetaPaymentCategories: &meta}
	if _, _, err := views.paymentCategories(context.Background(), payment, invoice, nil, false); err == nil {
		t.Fatal("missing bool category accepted")
	} else if !errors.Is(err, ErrDocumentViewServer) {
		t.Fatalf("error = %v", err)
	}
	if len(store.lookedUp) != 1 || store.lookedUp[0] != "00000000-0000-0000-0000-000000000001" {
		t.Fatalf("looked up %q", store.lookedUp)
	}

	// A float, a list or None is a ValidationError.
	for _, raw := range []string{`1.5`, `[1]`, `null`, `{"a": 1}`} {
		meta = `[{"payment_category_id": ` + raw + `, "amount": 1}]`
		payment = domain.InvoiceViewPayment{Subject: 0, MetaPaymentCategories: &meta}
		if _, _, err := views.paymentCategories(context.Background(), payment, invoice, nil, false); !errors.Is(err, ErrDocumentViewServer) {
			t.Errorf("%s: error = %v", raw, err)
		}
	}
}

// Python 3.13:
//
//	>>> 'plan_id' in ['plan_id']
//	True
//	>>> ['plan_id'].get('plan_id')
//	AttributeError: 'list' object has no attribute 'get'
//	>>> 'plan_id' in 'x'
//	False
//	>>> # the unbound plan_id line then raises NameError, caught by the view.
func TestInvoiceViewsPlansMembership(t *testing.T) {
	cases := []struct {
		meta string
		want invoiceViewsPlanResult
	}{
		{`["plan_id"]`, invoiceViewsPlanResult{plan: true, planName: "-", membershipName: "-"}},
		{`["membership_plan_id"]`, invoiceViewsPlanResult{membership: true, planName: "-", membershipName: "-"}},
		{`[1, 2]`, invoiceViewsPlanResult{planName: "-", membershipName: "-"}},
		{`"a plan_id here"`, invoiceViewsPlanResult{plan: true, planName: "-", membershipName: "-"}},
		{`"nothing"`, invoiceViewsPlanResult{planName: "-", membershipName: "-"}},
		{`5`, invoiceViewsPlanResult{planName: "-", membershipName: "-"}},
		{`true`, invoiceViewsPlanResult{planName: "-", membershipName: "-"}},
	}
	for _, c := range cases {
		meta := c.meta
		sub := &domain.InvoiceViewSubscription{Meta: &meta}
		got, err := invoiceViewsPlans(sub)
		if err != nil {
			t.Fatalf("%s: %v", c.meta, err)
		}
		if got.plan != c.want.plan || got.membership != c.want.membership || got.planName != c.want.planName ||
			got.membershipName != c.want.membershipName {
			t.Errorf("%s: got %+v, want %+v", c.meta, got, c.want)
		}
	}

	// >>> {'plan_name': 'Scelto'}: 'plan_id' is absent, so plan_id is unbound
	// >>> after the names are read: NameError.
	meta := `{"plan_name": "Scelto"}`
	sub := &domain.InvoiceViewSubscription{Meta: &meta}
	got, err := invoiceViewsPlans(sub)
	if err != nil || got.plan || got.membership || got.planName != "Scelto" || got.membershipName != "-" {
		t.Fatalf("dict names: %+v, %v", got, err)
	}

	// >>> {'plan_id': 2} with [{'id': 2, 'name': 'Due'}]: plan['id'] == plan_id.
	meta = `{"plan_id": 2}`
	plans := `[{"id": 2, "name": "Due"}]`
	sub = &domain.InvoiceViewSubscription{Meta: &meta, SubscriptionPlans: &plans}
	got, err = invoiceViewsPlans(sub)
	if err != nil || !got.plan || got.membership || got.planName != "Due" || got.membershipName != "-" {
		t.Fatalf("dict plan: %+v, %v", got, err)
	}
}

// Python 3.13:
//
//	>>> format_html("Ricevuta di pagamento con causale <b>{}</b> per l'associazione <b>{}</b>.", 7, "ASD <Alfa> & Co")
//	'Ricevuta di pagamento con causale <b>7</b> per l\'associazione <b>ASD &lt;Alfa&gt; &amp; Co</b>.'
//	>>> format_html('... <b>{}</b> ...', [1, 2], 'ASD <Alfa> & Co')
//	'... <b>[1, 2]</b> ...'
//	>>> format_html('... <b>{}</b> ...', {'a': 1}, 'ASD <Alfa> & Co')
//	"... <b>{&#x27;a&#x27;: 1}</b> ..."
//	>>> format_html('... <b>{}</b> ...', float('inf'), 'ASD <Alfa> & Co')
//	'... <b>inf</b> ...'
func TestInvoiceViewsDescriptionValues(t *testing.T) {
	views := NewInvoiceViews(&invoiceViewsValueStore{}, nil)
	payment := domain.InvoiceViewPayment{Subject: 0}
	cases := []struct {
		name     string
		category *invoiceViewsCategory
		want     string
	}{
		{"int", &invoiceViewsCategory{name: int64(7)},
			"Ricevuta di pagamento con causale <b>7</b> per l'associazione <b>ASD &lt;Alfa&gt; &amp; Co</b>."},
		{"list", &invoiceViewsCategory{name: djtemplate.List{int64(1), int64(2)}},
			"Ricevuta di pagamento con causale <b>[1, 2]</b> per l'associazione <b>ASD &lt;Alfa&gt; &amp; Co</b>."},
		{"dict", &invoiceViewsCategory{name: djtemplate.NewDict().Set("a", int64(1))},
			"Ricevuta di pagamento con causale <b>{&#x27;a&#x27;: 1}</b> per l'associazione <b>ASD &lt;Alfa&gt; &amp; Co</b>."},
		{"float", &invoiceViewsCategory{name: math.Inf(1)},
			"Ricevuta di pagamento con causale <b>inf</b> per l'associazione <b>ASD &lt;Alfa&gt; &amp; Co</b>."},
		{"none", &invoiceViewsCategory{name: nil},
			"Ricevuta di pagamento con causale <b>Pagamento</b> per l'associazione <b>ASD &lt;Alfa&gt; &amp; Co</b>."},
	}
	for _, c := range cases {
		got, err := views.description(context.Background(), payment, c.category, "ASD <Alfa> & Co")
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		if got != c.want {
			t.Errorf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
}
