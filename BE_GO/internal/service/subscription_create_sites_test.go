package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// TestSubscriptionCreatePythonEqual pins Python's == between JSON values: a
// plan id of another type (the import stores the archive's jsonb as it is).
func TestSubscriptionCreatePythonEqual(t *testing.T) {
	equal := []struct {
		name        string
		left, right any
	}{
		{name: "int", left: json.Number("1"), right: json.Number("1")},
		{name: "int and float", left: json.Number("1"), right: json.Number("1.0")},
		{name: "float integral", left: json.Number("2.0"), right: json.Number("2")},
		{name: "bool and int", left: true, right: json.Number("1")},
		{name: "int and bool", left: json.Number("0"), right: false},
		{name: "exact large ints", left: json.Number("9007199254740993"), right: json.Number("9007199254740993")},
		{name: "none", left: nil, right: nil},
		{name: "text", left: "a", right: "a"},
		{name: "lists", left: []any{"a", json.Number("1")}, right: []any{"a", json.Number("1.0")}},
		{name: "objects", left: domain.RequestObject{{Name: "a"}, {Name: "b", Value: json.Number("1")}},
			right: domain.RequestObject{{Name: "b", Value: json.Number("1.0")}, {Name: "a"}}},
		{name: "empty containers", left: []any{}, right: []any{}},
	}
	for _, test := range equal {
		t.Run(test.name, func(t *testing.T) {
			if !pythonJSONEqual(test.left, test.right) || !pythonJSONEqual(test.right, test.left) {
				t.Fatalf("%#v != %#v", test.left, test.right)
			}
		})
	}
	different := []struct {
		name        string
		left, right any
	}{
		{name: "text and int", left: "1", right: json.Number("1")},
		{name: "int and text", left: json.Number("1"), right: "1"},
		{name: "none and false", left: nil, right: false},
		{name: "none and zero", left: nil, right: json.Number("0")},
		{name: "float and int", left: json.Number("1.5"), right: json.Number("1")},
		{name: "large int past float precision", left: json.Number("9007199254740993"), right: json.Number("9007199254740992.0")},
		{name: "list and object", left: []any{}, right: domain.RequestObject{}},
		{name: "lists of other length", left: []any{"a"}, right: []any{"a", "b"}},
		{name: "objects of other keys", left: domain.RequestObject{{Name: "a"}}, right: domain.RequestObject{{Name: "b"}}},
		{name: "nan", left: json.Number("NaN"), right: json.Number("NaN")},
		{name: "infinity and int", left: math.Inf(1), right: json.Number("1")},
		{name: "bool and text", left: true, right: "1"},
	}
	for _, test := range different {
		t.Run(test.name, func(t *testing.T) {
			if pythonJSONEqual(test.left, test.right) || pythonJSONEqual(test.right, test.left) {
				t.Fatalf("%#v == %#v", test.left, test.right)
			}
		})
	}
}

// TestSubscriptionQuickAmount pins Payment's DecimalField(9, 2): Decimal() of
// the fee (whitespace, underscores, other-script digits), Django's prec-9
// conversion, PostgreSQL's rounding to the column, and str() of the in-memory
// value auditlog renders.
func TestSubscriptionQuickAmount(t *testing.T) {
	cases := []struct {
		name     string
		value    any
		stored   string
		rendered string
	}{
		{name: "association fee text", value: "25.00", stored: "25.00", rendered: "25.00"},
		{name: "plan int", value: json.Number("40"), stored: "40.00", rendered: "40"},
		{name: "plan float", value: json.Number("25.5"), stored: "25.50", rendered: "25.5"},
		{name: "third decimal rounded", value: json.Number("25.005"), stored: "25.01", rendered: "25.005"},
		{name: "underscores", value: "1_0.5", stored: "10.50", rendered: "1_0.5"},
		{name: "surrounding whitespace", value: " 25 ", stored: "25.00", rendered: " 25 "},
		{name: "exponent", value: " 1e2 ", stored: "100.00", rendered: " 1e2 "},
		{name: "arabic-indic digits", value: "\u0662\u0665", stored: "25.00", rendered: "\u0662\u0665"},
		{name: "negative", value: "-12.345", stored: "-12.35", rendered: "-12.345"},
		{name: "negative zero", value: "-0", stored: "0.00", rendered: "-0"},
		{name: "true", value: true, stored: "1.00", rendered: "True"},
		{name: "trailing dot", value: "5.", stored: "5.00", rendered: "5."},
		{name: "leading dot", value: ".5", stored: "0.50", rendered: ".5"},
		// A text keeps its exact decimal: only the column's two decimals round.
		{name: "text keeps every digit", value: "0.0049999999999", stored: "0.00", rendered: "0.0049999999999"},
		{name: "text rounds at the column", value: "0.005", stored: "0.01", rendered: "0.005"},
		{name: "smaller than half a cent", value: "0.004", stored: "0.00", rendered: "0.004"},
		// A float is Context(prec=9).create_decimal_from_float: nine digits.
		{name: "float nine digits", value: json.Number("25.005"), stored: "25.01", rendered: "25.005"},
		// Decimal() keeps an exponent within its own limits; the column rounds it.
		{name: "far below a cent", value: "1e-999999999", stored: "0.00", rendered: "1e-999999999"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			stored, rendered, err := subscriptionQuickAmount(test.value)
			if err != nil || stored != test.stored || rendered != test.rendered {
				t.Fatalf("got (%q, %q, %v), want (%q, %q)", stored, rendered, err, test.stored, test.rendered)
			}
		})
	}
	for name, value := range map[string]any{
		"text":       "non-un-numero",
		"comma":      "1,5",
		"empty":      "",
		"none":       nil,
		"list":       []any{json.Number("1")},
		"object":     domain.RequestObject{{Name: "amount", Value: json.Number("1")}},
		"overflow":   json.Number("10000000"),
		"too large":  "9999999.995",
		"huge":       "1e999999999",
		"nan text":   "NaN",
		"infinity":   "Infinity",
		"huge int":   "1e999999999999999999999",
		"huge small": "1e-999999999999999999999",
	} {
		t.Run(name, func(t *testing.T) {
			if stored, rendered, err := subscriptionQuickAmount(value); !errors.Is(err, domain.ErrSubscriptionCreateServer) ||
				stored != "" || rendered != "" {
				t.Fatalf("got (%q, %q, %v), want the server error", stored, rendered, err)
			}
		})
	}
}

func quickSettings(plans string, multiple bool) *domain.SubscriptionCreateSettings {
	return &domain.SubscriptionCreateSettings{SubscriptionFee: "25.00", MultipleSubscriptionFee: multiple,
		SubscriptionPlans: []byte(plans)}
}

func quickBody(planID any) domain.RequestObject {
	object := domain.RequestObject{}
	if planID != nil {
		object = append(object, domain.RequestField{Name: "plan_id", Value: planID})
	}
	return object
}

// TestSubscriptionQuickFee pins resolve_subscription_fee_and_meta's outcomes,
// including the exceptions Django lets out (the 500 page) and the ones its own
// except turns into ValidationError(str(e)) (a 400 list).
func TestSubscriptionQuickFee(t *testing.T) {
	t.Run("single fee", func(t *testing.T) {
		settings := quickSettings("[]", false)
		fee, meta, err := subscriptionQuickFee(settings, quickBody(nil))
		if err != nil || fee != "25.00" || meta != nil {
			t.Fatalf("got (%v, %v, %v)", fee, meta, err)
		}
	})
	t.Run("empty plans", func(t *testing.T) {
		fee, meta, err := subscriptionQuickFee(quickSettings("[]", true), quickBody("plan"))
		if err != nil || fee != "25.00" || meta != nil {
			t.Fatalf("got (%v, %v, %v)", fee, meta, err)
		}
	})
	t.Run("matched plan", func(t *testing.T) {
		plans := `[{"id":"a","name":"A","subscription_fee":30},{"id":"b","name":"B","subscription_fee":45}]`
		fee, meta, err := subscriptionQuickFee(quickSettings(plans, true), quickBody("b"))
		if err != nil || dto.PythonStr(fee) != "45" || meta == nil ||
			*meta != `{"subscription_data": {"subscription_fee": 45, "name": "B", "id": "b"}}` {
			t.Fatalf("got (%v, %v, %v)", fee, meta, err)
		}
	})
	t.Run("python equality picks the first plan", func(t *testing.T) {
		// true == 1: the stored bool id matches a numeric plan_id.
		plans := `[{"id":true,"name":"A","subscription_fee":5},{"id":1,"name":"B","subscription_fee":6}]`
		fee, meta, err := subscriptionQuickFee(quickSettings(plans, true), quickBody(json.Number("1")))
		if err != nil || dto.PythonStr(fee) != "5" || meta == nil || *meta != `{"subscription_data": {"subscription_fee": 5, "name": "A", "id": true}}` {
			t.Fatalf("got (%v, %v, %v)", fee, meta, err)
		}
	})
	t.Run("text id and numeric plan", func(t *testing.T) {
		plans := `[{"id":"1","name":"A","subscription_fee":5}]`
		fee, meta, err := subscriptionQuickFee(quickSettings(plans, true), quickBody(json.Number("1")))
		if err != nil || dto.PythonStr(fee) != "5" || meta == nil || *meta != `{"subscription_data": {"subscription_fee": 5, "name": "A", "id": "1"}}` {
			t.Fatalf("got (%v, %v, %v), want the fallback's first fee", fee, meta, err)
		}
	})
	t.Run("fallback takes the first fee and the last data", func(t *testing.T) {
		plans := `[{"id":"a","name":"A","subscription_fee":"30"},{"id":"b","name":"B","subscription_fee":45}]`
		fee, meta, err := subscriptionQuickFee(quickSettings(plans, true), quickBody("nessuno"))
		if err != nil || fee != "30" || meta == nil || *meta != `{"subscription_data": {"subscription_fee": 45, "name": "B", "id": "b"}}` {
			t.Fatalf("got (%v, %v, %v)", fee, meta, err)
		}
	})
	t.Run("no plan id keeps the last plan's data", func(t *testing.T) {
		plans := `[{"id":"a","name":"A","subscription_fee":30}]`
		fee, meta, err := subscriptionQuickFee(quickSettings(plans, true), quickBody(nil))
		if err != nil || dto.PythonStr(fee) != "30" || meta == nil {
			t.Fatalf("got (%v, %v, %v)", fee, meta, err)
		}
	})

	// An uncaught exception: the 500 page.
	for name, test := range map[string]struct {
		plans string
		body  domain.RequestObject
	}{
		"plans are an object":       {plans: `{"a":1}`, body: quickBody(nil)},
		"plans are a number":        {plans: `5`, body: quickBody(nil)},
		"plans are null":            {plans: `null`, body: quickBody(nil)},
		"plans text with a plan":    {plans: `"ab"`, body: quickBody("a")},
		"plan is not an object":     {plans: `[5]`, body: quickBody("a")},
		"plan without id":           {plans: `[{"name":"A"}]`, body: quickBody("a")},
		"matched plan without fee":  {plans: `[{"id":"a","name":"A"}]`, body: quickBody("a")},
		"matched plan without name": {plans: `[{"id":"a","subscription_fee":1}]`, body: quickBody("a")},
	} {
		t.Run(name, func(t *testing.T) {
			fee, meta, err := subscriptionQuickFee(quickSettings(test.plans, true), test.body)
			if err == nil || fee != nil || meta != nil {
				t.Fatalf("got (%v, %v, %v), want an error", fee, meta, err)
			}
			if errors.Is(err, domain.ErrSubscriptionCreateServer) {
				return // the 500 page
			}
			var failure *domain.SubscriptionCreateFailure
			var raised *domain.SubscriptionCreateRaised
			if !errors.As(err, &failure) && !errors.As(err, &raised) {
				t.Fatalf("got %v, want the 500 page or a Python exception", err)
			}
		})
	}

	// ValidationError(str(e)) inside the fallback's try: a 400 list body.
	rejected := map[string]struct {
		plans string
		want  string
	}{
		"plans text without a plan": {plans: `"ab"`, want: "string indices must be integers, not 'str'"},
		"first item not an object":  {plans: `[5]`, want: "'int' object is not subscriptable"},
		"first item without fee":    {plans: `[{"id":"a"},{"id":"b","name":"B","subscription_fee":1}]`, want: "'subscription_fee'"},
		"last item not an object":   {plans: `[{"id":"a","subscription_fee":1},5]`, want: "'int' object is not subscriptable"},
		"last item without fee":     {plans: `[{"id":"a","subscription_fee":1},{"id":"b"}]`, want: "'subscription_fee'"},
	}
	for name, test := range rejected {
		t.Run(name, func(t *testing.T) {
			_, _, err := subscriptionQuickFee(quickSettings(test.plans, true), quickBody(nil))
			var invalid *domain.SubscriptionCreateRejected
			if !errors.As(err, &invalid) || !reflect.DeepEqual(invalid.Messages, []string{test.want}) {
				t.Fatalf("got %v, want the 400 list %q", err, test.want)
			}
		})
	}
}

// mainTutorTransaction is the transaction reduced to get_main_tutor's calls.
type mainTutorTransaction struct {
	domain.SubscriptionCreateTransaction
	relations []domain.SubscriptionEditRelation
	primary   []int64
	audits    []domain.SubscriptionCreateAudit
}

func (t *mainTutorTransaction) Relations(context.Context, string) ([]domain.SubscriptionEditRelation, error) {
	return t.relations, nil
}

func (t *mainTutorTransaction) MarkRelationPrimary(_ context.Context, id int64) error {
	t.primary = append(t.primary, id)
	return nil
}

func (t *mainTutorTransaction) AuditEntry(_ context.Context, entry domain.SubscriptionCreateAudit) error {
	t.audits = append(t.audits, entry)
	return nil
}

// TestSubscriptionCreateMainTutorMakesTheFirstPrimary pins get_main_tutor: the
// primary relation when there is one, else the first relation by key saved as
// primary with its update entry.
func TestSubscriptionCreateMainTutorMakesTheFirstPrimary(t *testing.T) {
	creates := tutorCreates()
	association := tutorAssociation
	tx := &mainTutorTransaction{relations: []domain.SubscriptionEditRelation{
		{ID: 7, TutorID: "tutor-a"}, {ID: 9, TutorID: "tutor-b", IsPrimary: true}}}
	tutor, err := creates.mainTutor(context.Background(), tx, "minor", domain.LoginAuditContext{}, &association)
	if err != nil || tutor != "tutor-b" || len(tx.primary) != 0 || len(tx.audits) != 0 {
		t.Fatalf("got (%q, %v) with writes %v %v", tutor, err, tx.primary, tx.audits)
	}
	tx = &mainTutorTransaction{relations: []domain.SubscriptionEditRelation{
		{ID: 7, TutorID: "tutor-a"}, {ID: 9, TutorID: "tutor-b"}}}
	tutor, err = creates.mainTutor(context.Background(), tx, "minor", domain.LoginAuditContext{}, &association)
	if err != nil || tutor != "tutor-a" {
		t.Fatalf("got (%q, %v)", tutor, err)
	}
	if !reflect.DeepEqual(tx.primary, []int64{7}) {
		t.Fatalf("marked %v, want the first relation 7", tx.primary)
	}
	if len(tx.audits) != 1 {
		t.Fatalf("audits %v, want one update entry", tx.audits)
	}
	entry := tx.audits[0]
	if entry.Model != "associatetutorrelation" || entry.Representation != "AssociateTutorRelation" || entry.Key != "7" ||
		entry.Action != 1 || entry.Created || entry.Path != "associate.sport_association" ||
		entry.Association == nil || *entry.Association != association {
		t.Fatalf("entry %+v", entry)
	}
	if want := map[string][2]string{"is_primary": {"False", "True"}}; !reflect.DeepEqual(entry.Changes, want) {
		t.Fatalf("changes %v, want %v", entry.Changes, want)
	}
	tx = &mainTutorTransaction{}
	if tutor, err = creates.mainTutor(context.Background(), tx, "minor", domain.LoginAuditContext{}, &association); err != nil || tutor != "" ||
		len(tx.primary) != 0 || len(tx.audits) != 0 {
		t.Fatalf("no relations: got (%q, %v) with %v %v", tutor, err, tx.primary, tx.audits)
	}
}

// TestSubscriptionCreateMedicalDateUnicodeDigits pins strptime's \d: the day's
// second digit and the year accept the decimal digits of other scripts.
func TestSubscriptionCreateMedicalDateUnicodeDigits(t *testing.T) {
	cases := []struct {
		text string
		want string
	}{
		{text: "15/03/2027", want: "2027-03-15"},
		{text: "1\u0665/03/2027", want: "2027-03-15"},
		{text: "15/03/\u0662\u0660\u0662\u0667", want: "2027-03-15"},
		{text: "2027-03-15", want: "2027-03-15"},
		{text: "\u0662\u0660\u0662\u0667-03-15", want: "2027-03-15"},
	}
	for _, test := range cases {
		t.Run(test.text, func(t *testing.T) {
			date, ok := subscriptionMedicalDate(test.text)
			if !ok || date.Format("2006-01-02") != test.want {
				t.Fatalf("got (%v, %v), want %s", date, ok, test.want)
			}
		})
	}
	for _, text := range []string{"5/3/2027x", "2027-13-01", "2027-02-30", "\u0663\u0660/01/2027", ""} {
		t.Run("invalid/"+text, func(t *testing.T) {
			if _, ok := subscriptionMedicalDate(text); ok {
				t.Fatalf("got ok=%v", ok)
			}
		})
	}
	if _, ok := subscriptionMedicalDate(20270331); ok {
		t.Fatal("a number is no date")
	}
}

// TestSubscriptionCreateRenewalDateText pins date.strftime('%d/%m/%Y') with the
// platform's year width, and the year the renewal re-parses.
func TestSubscriptionCreateRenewalDateText(t *testing.T) {
	date := time.Date(999, 3, 5, 0, 0, 0, 0, time.UTC)
	want := "05/03/" + dto.PythonStrftimeYear(999)
	if got := renewalDateText(date); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
	parsed, result, message := dto.PersonDateTextAnyYear(want, time.Date(2026, 11, 16, 0, 0, 0, 0, time.UTC))
	if result != dto.DateutilParsed || parsed.Year() != 999 || parsed.Month() != 3 || parsed.Day() != 5 {
		t.Fatalf("got (%v, %v, %q)", parsed, result, message)
	}
	if _, ok := dto.SubscriptionCreateBornDate(want); ok != (len(dto.PythonStrftimeYear(999)) == 4) {
		// A three-digit year is not strptime's %Y: the serializer rejects it.
		t.Fatalf("born date %q parsed=%v", want, ok)
	}
	// The glibc text is an ordinary date for dateutil (the age) and no born date
	// for the serializer, whatever platform this test runs on.
	if parsed, result, _ := dto.PersonDateTextAnyYear("05/03/999", time.Date(2026, 11, 16, 0, 0, 0, 0, time.UTC)); result != dto.DateutilParsed ||
		parsed.Year() != 999 {
		t.Fatalf("glibc text: got (%v, %v)", parsed, result)
	}
	if _, ok := dto.SubscriptionCreateBornDate("05/03/999"); ok {
		t.Fatal("a three-digit year is not a four-digit %Y")
	}
}

// TestSubscriptionCreateTextContainers pins the CharField lookup of a name sent
// as another JSON type: str() of the value.
func TestSubscriptionCreateTextContainers(t *testing.T) {
	cases := map[string]struct {
		value any
		want  *string
	}{
		"text":   {value: "ab", want: ptr("ab")},
		"int":    {value: json.Number("42"), want: ptr("42")},
		"float":  {value: json.Number("42.5"), want: ptr("42.5")},
		"true":   {value: true, want: ptr("True")},
		"list":   {value: []any{"a"}, want: ptr("['a']")},
		"object": {value: domain.RequestObject{{Name: "a", Value: json.Number("1")}}, want: ptr("{'a': 1}")},
		"none":   {value: nil, want: nil},
	}
	for name, test := range cases {
		t.Run(name, func(t *testing.T) {
			got := dto.SubscriptionCreateText(test.value)
			if !reflect.DeepEqual(got, test.want) {
				t.Fatalf("got %v, want %v", got, test.want)
			}
		})
	}
}

// TestSubscriptionCreateRenewalPerson pins AssociateSerializer(person).data for
// the renewal's fill: the account the serializer reads (its own, else the
// association's owner) and the DD/MM/YYYY born date with the platform's year.
func TestSubscriptionCreateRenewalPerson(t *testing.T) {
	association := "a1000000-0000-4000-8000-000000970001"
	born := time.Date(999, 3, 5, 0, 0, 0, 0, time.UTC)
	tutor := &domain.SubscriptionEditAssociate{ID: "a1000000-0000-4000-8000-000000970002", AssociationID: &association,
		BornDate: &born, Sex: "M", FirstName: ptr("Tullio"), LastName: ptr("Estero")}
	person := renewalPerson(tutor, "a1000000-0000-4000-8000-000000970003")
	value := func(name string) any {
		for _, field := range person {
			if field.Name == name {
				return field.Value
			}
		}
		t.Fatalf("no %s field", name)
		return nil
	}
	if value("sport_association") != association {
		t.Fatalf("association %v, want %s", value("sport_association"), association)
	}
	// No account of its own: the serializer names the association's owner (the
	// caller overwrites it with the registration's user).
	if value("user") != "a1000000-0000-4000-8000-000000970003" {
		t.Fatalf("user %v", value("user"))
	}
	if value("born_date") != "05/03/"+dto.PythonStrftimeYear(999) {
		t.Fatalf("born date %v", value("born_date"))
	}
	if value("is_minor") != false || value("first_name") != "Tullio" {
		t.Fatalf("person %v", person)
	}
}

// TestSubscriptionCreateAssociateUpdateIndex pins that a save which moves a row
// indexes through the saved row's association (the row the resolver loads), not
// the association the update started from.
func TestSubscriptionCreateAssociateUpdateIndex(t *testing.T) {
	creates := tutorCreates()
	moved := "a1000000-0000-4000-8000-000000970010"
	row := domain.SubscriptionEditAssociate{ID: "a1000000-0000-4000-8000-000000970011", AssociationID: &moved}
	audit := subscriptionAudit{}
	index, path, err := creates.associateUpdateIndex(context.Background(), nil, audit, row,
		map[string][2]string{"sport_association": {"a1000000-0000-4000-8000-000000970012", moved}})
	if err != nil || index == nil || *index != moved || path != "direct" {
		t.Fatalf("got (%v, %q, %v), want the saved row's association", index, path, err)
	}
}

// preregFillTransaction is the fill's two reads: the account's settings and the
// renewed subscription.
type preregFillTransaction struct {
	domain.SubscriptionCreateTransaction
	source *domain.SubscriptionRenewalSource
}

func (t *preregFillTransaction) Settings(context.Context, string) (*domain.SubscriptionCreateSettings, int, error) {
	return &domain.SubscriptionCreateSettings{AssociationID: preregRenewAssociation, OwnerID: preregRenewAssociation}, 1, nil
}

func (t *preregFillTransaction) RenewalSource(context.Context, string, string) (*domain.SubscriptionRenewalSource, error) {
	return t.source, nil
}

// TestSubscriptionCreateFillWithoutAssociate pins AssociateSerializer(None).data's
// next read: the renewed subscription's associate is NULL (SET_NULL), so
// get_main_tutor runs on None and the AttributeError is the answer.
func TestSubscriptionCreateFillWithoutAssociate(t *testing.T) {
	creates := tutorCreates()
	tx := &preregFillTransaction{source: &domain.SubscriptionRenewalSource{OwnerID: preregRenewAssociation}}
	_, _, err := creates.fillPreregistration(context.Background(), tx, preregRenewIdentity(), preregRenewBody(preregRenewSource),
		SubscriptionCreateRequest{})
	var failure *domain.SubscriptionCreateFailure
	if !errors.As(err, &failure) || failure.Text != "'NoneType' object has no attribute 'get_main_tutor'" {
		t.Fatalf("got %v, want Django's AttributeError text", err)
	}
}

// TestSubscriptionCreateBooleanFloats pins BooleanField's membership tests:
// `1.0 in {1, '1', True, ...}` is true.
func TestSubscriptionCreateBooleanFloats(t *testing.T) {
	cases := []struct {
		value   any
		want    bool
		invalid bool
	}{
		{value: json.Number("1"), want: true},
		{value: json.Number("0")},
		{value: json.Number("1.0"), want: true},
		{value: json.Number("0.0")},
		{value: json.Number("1e0"), want: true},
		{value: json.Number("-0.0")},
		{value: json.Number("1.0000000000000001"), want: true},
		{value: json.Number("0.5"), invalid: true},
		{value: json.Number("2"), invalid: true},
		{value: "1.0", invalid: true},
		{value: nil, invalid: true},
		{value: []any{}, invalid: true},
		{value: []any{json.Number("1")}, invalid: true},
	}
	for _, test := range cases {
		got, code := dtoBoolean(test.value)
		if test.invalid {
			if code == "" {
				t.Fatalf("%#v parsed as %v", test.value, got)
			}
			continue
		}
		if code != "" || got != test.want {
			t.Fatalf("%#v got (%v, %q), want %v", test.value, got, code, test.want)
		}
	}
}
