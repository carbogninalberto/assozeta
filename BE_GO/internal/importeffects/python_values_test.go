package importeffects

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func object(items ...domain.RequestField) domain.RequestObject {
	return domain.RequestObject(items)
}

// TestTextMatchesCPython covers str() of every decoded JSON value. Expected
// values are CPython 3.13's:
//
//	>>> str({'a': 1})
//	"{'a': 1}"
//	>>> str([1, 2.5, None, True])
//	'[1, 2.5, None, True]'
//	>>> str({'a': nan, 'b': inf, 'c': -inf})
//	"{'a': nan, 'b': inf, 'c': -inf}"
//	>>> str(10**100)
//	'10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'
func TestTextMatchesCPython(t *testing.T) {
	huge := "1" + strings.Repeat("0", 100)
	cases := []struct {
		value Value
		want  string
	}{
		{NoneValue, "None"},
		{StrValue("x"), "x"},
		{BoolValue(true), "True"},
		{Value{Kind: Int, S: "-12"}, "-12"},
		{Value{Kind: Int, S: huge}, huge},
		{Value{Kind: Float, S: "1.5"}, "1.5"},
		{Value{Kind: Float, S: "inf"}, "inf"},
		{Value{Kind: Float, S: "-inf"}, "-inf"},
		{Value{Kind: Float, S: "nan"}, "nan"},
		{Value{Kind: Decimal, S: "1E+2"}, "1E+2"},
		{UUIDValue("00000000-0000-0000-0000-000000000001"), "00000000-0000-0000-0000-000000000001"},
		{DateTimeValue(time.Date(2026, 1, 2, 3, 4, 5, 123456000, time.FixedZone("", 0))), "2026-01-02 03:04:05.123456+00:00"},
		{Value{Kind: DateTime, T: time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC), Naive: true}, "2026-01-02 03:04:05"},
		{Value{Kind: Date, S: "2026-01-02"}, "2026-01-02"},
		{Value{Kind: JSON, J: object(domain.RequestField{Name: "a", Value: json.Number("1")})}, "{'a': 1}"},
		{Value{Kind: JSON, J: []any{json.Number("1"), json.Number("2.5"), nil, true}}, "[1, 2.5, None, True]"},
		{Value{Kind: JSON, J: object(
			domain.RequestField{Name: "a", Value: math.NaN()},
			domain.RequestField{Name: "b", Value: math.Inf(1)},
			domain.RequestField{Name: "c", Value: math.Inf(-1)})}, "{'a': nan, 'b': inf, 'c': -inf}"},
		{Value{Kind: JSON, J: object(
			domain.RequestField{Name: "n", Value: json.Number("10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000")})},
			"{'n': 10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000}"},
		{Value{Kind: JSON, J: []any{"q'w\"e\t"}}, `['q\'w"e\t']`},
	}
	for i, c := range cases {
		got, err := c.value.Text()
		if err != nil || got != c.want {
			t.Fatalf("case %d: got %q (%v), want %q", i, got, err, c.want)
		}
	}
}

// TestTextRejectsHugeInt is CPython's limit:
//
//	>>> str(10**4300)
//	ValueError: Exceeds the limit (4300 digits) for integer string conversion; use sys.set_int_max_str_digits() to increase the limit
func TestTextRejectsHugeInt(t *testing.T) {
	value := Value{Kind: Int, S: "1" + strings.Repeat("0", 4300)}
	_, err := value.Text()
	want := "Exceeds the limit (4300 digits) for integer string conversion; use sys.set_int_max_str_digits() to increase the limit"
	if err == nil || err.Error() != want {
		t.Fatalf("err = %v", err)
	}
}

// TestReprMatchesCPython covers repr() of the attributes CourseSubscription's
// __str__ renders:
//
//	>>> repr(uuid.UUID(int=2))
//	"UUID('00000000-0000-0000-0000-000000000002')"
//	>>> repr(Decimal('1.2300'))
//	"Decimal('1.2300')"
//	>>> repr(datetime.datetime(2026, 1, 2, 3, 4, 5, 123000, tzinfo=datetime.timezone(datetime.timedelta(seconds=9015))))
//	"datetime.datetime(2026, 1, 2, 3, 4, 5, 123000, tzinfo=datetime.timezone(datetime.timedelta(seconds=9015)))"
//	>>> repr(datetime.date(2026, 1, 2))
//	'datetime.date(2026, 1, 2)'
func TestReprMatchesCPython(t *testing.T) {
	moment := Value{Kind: DateTime, T: time.Date(2026, 1, 2, 3, 4, 5, 123000000, time.FixedZone("", 9015))}
	cases := []struct {
		value Value
		want  string
	}{
		{NoneValue, "None"},
		{StrValue("a'b"), `"a'b"`},
		{BoolValue(false), "False"},
		{Value{Kind: Float, S: "1e+16"}, "1e+16"},
		{Value{Kind: Decimal, S: "1.2300"}, "Decimal('1.2300')"},
		{UUIDValue("00000000-0000-0000-0000-000000000002"), "UUID('00000000-0000-0000-0000-000000000002')"},
		{moment, "datetime.datetime(2026, 1, 2, 3, 4, 5, 123000, tzinfo=datetime.timezone(datetime.timedelta(seconds=9015)))"},
		{Value{Kind: DateTime, T: time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC), Naive: true}, "datetime.datetime(2026, 1, 2, 0, 0)"},
		{Value{Kind: Date, S: "2026-01-02"}, "datetime.date(2026, 1, 2)"},
		{Value{Kind: JSON, J: []any{"x", json.Number("1")}}, "['x', 1]"},
	}
	for i, c := range cases {
		got, err := c.value.Repr()
		if err != nil || got != c.want {
			t.Fatalf("case %d: got %q (%v), want %q", i, got, err, c.want)
		}
	}
}

// TestParseDateMatchesCPython is django's parse_datetime/parse_date/parse_time:
//
//	>>> parse_datetime('2026-01-02')                     # fromisoformat, date only
//	datetime.datetime(2026, 1, 2, 0, 0)
//	>>> parse_datetime('2026-01-02T03:04:05+02:30')
//	datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone(datetime.timedelta(seconds=9000)))
//	>>> parse_datetime('2026-02-31 03:04:05')            # regex matched, constructor rejects
//	ValueError: day is out of range for month
//	>>> parse_datetime('hello') is None
//	True
//	>>> parse_date('2026-1-2')
//	datetime.date(2026, 1, 2)
//	>>> parse_date('20260102')
//	datetime.date(2026, 1, 2)
//	>>> parse_time('10:11:12,5')
//	datetime.time(10, 11, 12, 500000)
func TestParseDateMatchesCPython(t *testing.T) {
	cases := []struct {
		text   string
		moment string
		err    string
	}{
		{"2026-01-02", "2026-01-02 00:00:00", ""},
		{"2026-01-02T03:04:05+02:30", "2026-01-02 03:04:05+02:30", ""},
		{"2026-01-02 03:04:05,1234567", "2026-01-02 03:04:05.123456", ""},
		{"2026-01-02T03:04:05+02:30:15", "2026-01-02 03:04:05+02:30:15", ""},
		{"2026-02-31 03:04:05", "", "day is out of range for month"},
		{"2026-01-01T25:00:00", "", "hour must be in 0..23"},
		{"hello", "", ""},
	}
	for _, c := range cases {
		moment, err := parseDateTime(c.text)
		if c.err != "" {
			if err == nil || err.Error() != c.err {
				t.Fatalf("%q: err %v, want %q", c.text, err, c.err)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%q: %v", c.text, err)
		}
		if moment == nil {
			if c.moment != "" {
				t.Fatalf("%q: nil moment, want %q", c.text, c.moment)
			}
			continue
		}
		if got := moment.String(); got != c.moment {
			t.Fatalf("%q: got %q, want %q", c.text, got, c.moment)
		}
	}
	dates := map[string]string{"2026-1-2": "2026-01-02", "20260102": "2026-01-02", "2026-W01-1": "2025-12-29",
		"2026-W53-1": "2026-12-28", "2026-01-01\n": "2026-01-01", "٢٠٢٦-٠١-٠١": "2026-01-01", "2026-01-01 10:00": "", "x": ""}
	for text, want := range dates {
		date, err := parseDate(text)
		if err != nil {
			t.Fatalf("%q: %v", text, err)
		}
		got := ""
		if date != nil {
			got = date.String()
		}
		if got != want {
			t.Fatalf("%q: got %q, want %q", text, got, want)
		}
	}
	if _, err := parseDate("2026-02-31"); err == nil || err.Error() != "day is out of range for month" {
		t.Fatalf("parse_date invalid: %v", err)
	}
	times := map[string]string{"10": "10:00:00", "1030": "10:30:00", "10:11": "10:11:00", "10:11:12,5": "10:11:12.500000",
		"10:11:12,1234567": "10:11:12.123456", "10:11:12+02:00": "10:11:12", "10:11:12Z": "10:11:12"}
	for text, want := range times {
		parsed, err := parseTime(text)
		if err != nil || parsed == nil || parsed.String() != want {
			t.Fatalf("%q: %v %v want %q", text, parsed, err, want)
		}
	}
	if _, err := parseTime("25:00"); err == nil || err.Error() != "hour must be in 0..23" {
		t.Fatalf("parse_time 25:00: %v", err)
	}
}

// TestParseArchivedIsParseValue pins _parse_value(type(field).__name__, value):
//
//	>>> uuid.UUID(7)
//	AttributeError: 'int' object has no attribute 'replace'
//	>>> uuid.UUID('xyz')
//	ValueError: badly formed hexadecimal UUID string
//	>>> Decimal(0.1)
//	Decimal('0.1000000000000000055511151231257827021181583404541015625')
//	>>> int('1' * 5000)
//	ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 5000 digits; use sys.set_int_max_str_digits() to increase the limit
func TestParseArchivedIsParseValue(t *testing.T) {
	origin := time.Date(2026, 11, 17, 0, 0, 0, 0, time.UTC)
	e := &Effects{Now: func() time.Time { return origin }, NewUUID: func() string { return "00000000-0000-0000-0000-00000000000a" }}
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	payment := plan.Model("Payment")
	user := plan.Model("User")
	reminder := plan.Model("Reminder")

	uuidValue, err := ParseArchived(user.Field("user_id"), "ABCDEFABCDEFABCDEFABCDEFABCDEFAB")
	if err != nil || uuidValue.Kind != UUID || uuidValue.S != "abcdefab-cdef-abcd-efab-cdefabcdefab" {
		t.Fatalf("uuid hex: %+v %v", uuidValue, err)
	}
	if _, err := ParseArchived(user.Field("user_id"), json.Number("7")); err == nil ||
		err.Error() != "'int' object has no attribute 'replace'" {
		t.Fatalf("uuid int: %v", err)
	}
	if _, err := ParseArchived(user.Field("user_id"), "xyz"); err == nil || err.Error() != "badly formed hexadecimal UUID string" {
		t.Fatalf("uuid text: %v", err)
	}

	amount, err := ParseArchived(payment.Field("amount"), json.Number("0.1"))
	if err != nil || amount.S != "0.1000000000000000055511151231257827021181583404541015625" {
		t.Fatalf("decimal float: %+v %v", amount, err)
	}
	exp, err := ParseArchived(payment.Field("amount"), "1e2")
	if err != nil || exp.S != "1E+2" {
		t.Fatalf("decimal exponent: %+v %v", exp, err)
	}
	if zero, err := ParseArchived(payment.Field("amount"), json.Number("0")); err != nil || !zero.IsNone() {
		t.Fatalf("decimal zero: %+v %v", zero, err)
	}
	if _, err := ParseArchived(payment.Field("amount"), "abc"); err == nil ||
		err.Error() != "[<class 'decimal.ConversionSyntax'>]" {
		t.Fatalf("decimal syntax: %v", err)
	}

	subject, err := ParseArchived(payment.Field("subject"), json.Number("2.9"))
	if err != nil || subject.S != "2" {
		t.Fatalf("int float: %+v %v", subject, err)
	}
	_, err = ParseArchived(payment.Field("subject"), strings.Repeat("1", 5000))
	if err == nil || !strings.Contains(err.Error(), "value has 5000 digits") {
		t.Fatalf("int digits: %v", err)
	}
	if _, err := ParseArchived(payment.Field("subject"), object(domain.RequestField{Name: "a", Value: json.Number("1")})); err == nil ||
		err.Error() != "int() argument must be a string, a bytes-like object or a real number, not 'dict'" {
		t.Fatalf("int dict: %v", err)
	}

	paid, err := ParseArchived(payment.Field("paid"), "f")
	if err != nil || !paid.B {
		t.Fatalf("bool: %+v %v", paid, err)
	}
	description, err := ParseArchived(payment.Field("description"), object(domain.RequestField{Name: "a", Value: json.Number("1")}))
	if err != nil || description.Kind != JSON {
		t.Fatalf("text keeps the dict: %+v %v", description, err)
	}
	when, err := ParseArchived(payment.Field("payment_date"), "2026-01-02")
	if err != nil || when.Kind != DateTime || !when.Naive || when.T.Format("2006-01-02") != "2026-01-02" {
		t.Fatalf("datetime: %+v %v", when, err)
	}
	if none, err := ParseArchived(payment.Field("payment_date"), "hello"); err != nil || !none.IsNone() {
		t.Fatalf("datetime none: %+v %v", none, err)
	}
	occurs, err := ParseArchived(reminder.Field("time_occurrence"), "10:11:12")
	if err != nil || occurs.Kind != Str || occurs.S != "10:11:12" {
		t.Fatalf("time keeps the text: %+v %v", occurs, err)
	}

	def, err := e.Default(payment.Field("creation_date"))
	if err != nil || def.Kind != DateTime || !def.T.Equal(origin) {
		t.Fatalf("default now: %+v %v", def, err)
	}
	membership, err := e.Default(plan.Model("SportAssociation").Field("additional_sections"))
	if err != nil || membership.Kind != JSON || len(membership.J.([]any)) != 3 {
		t.Fatalf("default sections: %+v %v", membership, err)
	}
	year, err := e.Default(plan.Model("BalanceSheet").Field("year"))
	if err != nil || year.S != "2026" {
		t.Fatalf("default year: %+v %v", year, err)
	}
}

// TestDialogFieldPrep pins get_db_prep_save for the values the archive can
// carry:
//
//	>>> Payment._meta.get_field('amount').get_db_prep_save(Decimal('NaN'), connection)
//	ValidationError: ['Il valore "NaN" deve essere un numero decimale.']
//	>>> ... amount: 1E+2 stays 1E+2 (psycopg2 adapts Decimal with str())
//	>>> Payment._meta.get_field('payment_date').get_db_prep_save('2026-01-02', connection)
//	datetime.datetime(2026, 1, 2, 0, 0, tzinfo=zoneinfo.ZoneInfo(key='UTC'))  -> str()
//	'2026-01-02 00:00:00+00:00'
//	>>> Reminder._meta.get_field('time_occurrence').get_db_prep_save('25:00', connection)
//	ValidationError: ['Il valore di "25:00" ha il corretto formato (HH:MM[:ss[.uuuuuu]]) ma non è un orario valido.']
func TestDialogFieldPrep(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	payment := plan.Model("Payment")
	reminder := plan.Model("Reminder")
	user := plan.Model("User")

	cases := []struct {
		field *Field
		value Value
		want  string
		err   string
	}{
		{payment.Field("description"), Value{Kind: JSON, J: object(domain.RequestField{Name: "a", Value: json.Number("1")})}, "{'a': 1}", ""},
		{payment.Field("amount"), Value{Kind: Decimal, S: "NaN"}, "", `['Il valore "NaN" deve essere un numero decimale.']`},
		{payment.Field("amount"), Value{Kind: Decimal, S: "1E+2"}, "1E+2", ""},
		{payment.Field("payment_date"), Value{Kind: DateTime, T: time.Date(2026, 1, 2, 3, 4, 5, 0, time.FixedZone("", 9000))}, "2026-01-02 03:04:05+02:30", ""},
		{payment.Field("payment_date"), Value{Kind: DateTime, T: time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC), Naive: true}, "2026-01-02 00:00:00+00:00", ""},
		{payment.Field("payment_date"), Value{Kind: Date, S: "2026-01-02"}, "2026-01-02 00:00:00+00:00", ""},
		{payment.Field("payment_date"), Value{Kind: Int, S: "5"}, "", "fromisoformat: argument must be str"},
		{payment.Field("subject"), Value{Kind: Str, S: "abc"}, "", "Field 'subject' expected a number but got 'abc'."},
		{payment.Field("subject"), Value{Kind: Float, S: "nan"}, "", "Field 'subject' expected a number but got nan."},
		{user.Field("user_id"), Value{Kind: Str, S: "xyz"}, "", `['"xyz" non è uno UUID valido.']`},
		{reminder.Field("time_occurrence"), Value{Kind: Str, S: "10:11:12.123456"}, "10:11:12.123456", ""},
		{reminder.Field("time_occurrence"), Value{Kind: Str, S: "25:00"}, "", `['Il valore di "25:00" ha il corretto formato (HH:MM[:ss[.uuuuuu]]) ma non è un orario valido.']`},
		{reminder.Field("time_occurrence"), Value{Kind: JSON, J: []any{}}, "", "fromisoformat: argument must be str"},
	}
	for i, c := range cases {
		got, err := dbValue(c.field, c.value)
		if c.err != "" {
			if err == nil || err.Error() != c.err {
				t.Fatalf("case %d: err %v, want %s", i, err, c.err)
			}
			continue
		}
		if err != nil || got != c.want {
			t.Fatalf("case %d: got %v (%v), want %q", i, got, err, c.want)
		}
	}
}

// TestAuditCreateMatchesAuditlog pins auditlog's create diff (auditlog 3.4.1,
// AUDITLOG_STORE_JSON_CHANGES off):
//
//	>>> model_instance_diff(None, Payment(payment_id=UUID(int=1), description="x", payment_date={'a': 1},
//	...   subject=0, meta={'a': 1}))['meta']
//	('None', '{"a": 1}')
//	>>> ...['payment_date']
//	('None', "{'a': 1}")
func TestAuditCreateMatchesAuditlog(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	payment := plan.Model("Payment")
	cases := []struct {
		field string
		value Value
		text  string
		keep  bool
	}{
		{"payment_date", DateTimeValue(time.Date(2026, 1, 2, 3, 4, 5, 123456000, time.FixedZone("", 3600))), "2026-01-02 02:04:05.123456", true},
		{"payment_date", NoneValue, "None", true},
		{"payment_date", Value{Kind: JSON, J: object(domain.RequestField{Name: "a", Value: json.Number("1")})}, "{'a': 1}", true},
		{"invoice", NoneValue, "None", true},
		{"description", NoneValue, "None", false},
		{"description", StrValue("None"), "None", false},
		{"description", Value{Kind: JSON, J: []any{json.Number("1")}}, "[1]", true},
		{"paid", BoolValue(true), "True", true},
		{"amount", Value{Kind: Decimal, S: "10.50"}, "10.50", true},
		{"meta", NoneValue, "null", true},
		{"meta", Value{Kind: JSON, J: object(
			domain.RequestField{Name: "b", Value: []any{json.Number("1"), json.Number("2.5"), nil, true}},
			domain.RequestField{Name: "a", Value: object(domain.RequestField{Name: "x", Value: "y"})})},
			`{"a": {"x": "y"}, "b": [1, 2.5, null, true]}`, true},
		{"meta", Value{Kind: JSON, J: object(domain.RequestField{Name: "x", Value: "\u00e0\n\u00a0"})}, `{"x": "\u00e0\n\u00a0"}`, true},
		{"meta", StrValue("{bad"), `"{bad"`, true},
		{"meta", Value{Kind: JSON, J: object(
			domain.RequestField{Name: "a", Value: math.NaN()},
			domain.RequestField{Name: "i", Value: math.Inf(1)})}, `{"a": NaN, "i": Infinity}`, true},
	}
	for _, c := range cases {
		text, keep, err := auditCreate(payment.Field(c.field), c.value)
		if err != nil || text != c.text || keep != c.keep {
			t.Fatalf("%s: %q %v %v, want %q %v", c.field, text, keep, err, c.text, c.keep)
		}
	}
}

// TestDictReprMatchesCourseSubscription pins CourseSubscription.__str__ =
// str(self.__dict__): fields in concrete order, then ModelState.
func TestDictReprMatchesCourseSubscription(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	model := plan.Model("CourseSubscription")
	instance := &Instance{Model: model, Values: map[string]Value{}, Adding: true}
	for _, field := range model.Fields {
		value, err := (&Effects{Now: func() time.Time { return time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC) },
			NewUUID: func() string { return "00000000-0000-0000-0000-000000000001" }}).Default(field)
		if err != nil {
			t.Fatal(err)
		}
		instance.Values[field.Attname] = value
	}
	instance.Values["course_id"] = UUIDValue("00000000-0000-0000-0000-000000000003")
	instance.Values["multiple_quote"] = Value{Kind: JSON, J: object(domain.RequestField{Name: "a", Value: json.Number("1")})}
	instance.Values["membership_fee"] = Value{Kind: Decimal, S: "0.00"}
	repr, err := instance.dictRepr()
	if err != nil {
		t.Fatal(err)
	}
	want := "{'_state': <django.db.models.base.ModelState object at 0x7f0000000000>, 'course_subscription_id': UUID('00000000-0000-0000-0000-000000000001'), " +
		"'course_id': UUID('00000000-0000-0000-0000-000000000003'), 'paid': False, " +
		"'creation_date': datetime.datetime(2026, 1, 2, 0, 0, tzinfo=datetime.timezone.utc), " +
		"'subscription_id': None, 'payment_id': None, 'multi_payments': False, 'one_fee_payment': False, " +
		"'archived': False, 'type': 1, 'multiple_quote': {'a': 1}, 'billed_frequency': 1, " +
		"'billed_from': None, 'billed_until': None, 'billed_from_day_of_month': 1, " +
		"'membership_active': True, 'membership_fee': Decimal('0.00'), 'auto_renewal': False, " +
		"'deleted': False}"
	if repr != want {
		t.Fatalf("got  %s\nwant %s", repr, want)
	}
	if text, err := (&Effects{}).Repr(instance); err != nil || text != want {
		t.Fatalf("Repr = %q %v", text, err)
	}
}

// TestPaymentConditionHelpers pins the Python operators Payment.save uses:
//
//	>>> 'label' in {'label': 1}
//	True
//	>>> 'label' in 'xlabel'
//	True
//	>>> 'label' in 5
//	TypeError: argument of type 'int' is not iterable
//	>>> {}.lower()
//	AttributeError: 'dict' object has no attribute 'lower'
//	>>> ", ".join([1, ''])
//	TypeError: sequence item 0: expected str instance, int found
func TestPaymentConditionHelpers(t *testing.T) {
	label := Value{Kind: JSON, J: object(domain.RequestField{Name: "label", Value: json.Number("1")})}
	if ok, err := pyContains(label, "label"); err != nil || !ok {
		t.Fatalf("dict contains: %v %v", ok, err)
	}
	if ok, err := pyContains(StrValue("xlabel"), "label"); err != nil || !ok {
		t.Fatalf("str contains: %v %v", ok, err)
	}
	if _, err := pyContains(Value{Kind: Int, S: "5"}, "label"); err == nil ||
		err.Error() != "argument of type 'int' is not iterable" {
		t.Fatalf("int contains: %v", err)
	}
	if _, err := pyLower(Value{Kind: JSON, J: object()}); err == nil ||
		err.Error() != "'dict' object has no attribute 'lower'" {
		t.Fatalf("dict lower: %v", err)
	}
	if got, err := pyLower(StrValue("QUOTA CORSO")); err != nil || got != "quota corso" {
		t.Fatalf("lower: %q %v", got, err)
	}
	if _, err := pyJoin([]Value{{Kind: Int, S: "1"}, StrValue("")}); err == nil ||
		err.Error() != "sequence item 0: expected str instance, int found" {
		t.Fatalf("join: %v", err)
	}
	if got, err := pyJoin([]Value{StrValue("x"), StrValue("")}); err != nil || got != "x, " {
		t.Fatalf("join strings: %q %v", got, err)
	}
	if _, err := pyIndex(StrValue("x"), "label"); err == nil ||
		err.Error() != "string indices must be integers, not 'str'" {
		t.Fatalf("index str: %v", err)
	}
}
