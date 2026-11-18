package importeffects

import (
	"encoding/json"
	"testing"
)

// TestStoredAuditText pins smart_str(get_field_value(old, field)) for the row
// auditlog loads from the database: to_jsonb's rendering of the column, then
// the ORM's value. Expected values are CPython 3.13 / Django 5.2.18:
//
//	>>> str(datetime.datetime(2026, 1, 2, 3, 4, 5, 123456,
//	...   tzinfo=datetime.timezone(datetime.timedelta(hours=2))).astimezone(datetime.timezone.utc).replace(tzinfo=None))
//	'2026-01-02 01:04:05.123456'
//	>>> str(datetime.time(10, 11, 12, 120000))
//	'10:11:12.120000'
//	>>> str(Decimal('10.50')), str(True), str(5)
//	('10.50', 'True', '5')
//	>>> json.dumps({'b': [1.5, None], 'a': 'x'}, sort_keys=True)
//	'{"a": "x", "b": [1.5, null]}'
func TestStoredAuditText(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	payment := plan.Model("Payment")
	reminder := plan.Model("Reminder")
	archive := plan.Model("SportAssociationDocumentsArchive")
	cases := []struct {
		field *Field
		value any
		want  string
	}{
		{payment.Field("payment_date"), "2026-01-02T03:04:05.123456+02:00", "2026-01-02 01:04:05.123456"},
		{payment.Field("payment_date"), nil, "None"},
		{payment.Field("amount"), json.Number("10.50"), "10.50"},
		{payment.Field("amount"), nil, "None"},
		{payment.Field("paid"), true, "True"},
		{payment.Field("paid"), false, "False"},
		{payment.Field("subject"), json.Number("5"), "5"},
		{payment.Field("payment_id"), "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000001"},
		{payment.Field("description"), "x", "x"},
		{payment.Field("meta"), map[string]any{"b": []any{json.Number("1.5"), nil}, "a": "x"}, `{"a": "x", "b": [1.5, null]}`},
		{payment.Field("meta"), nil, "null"},
		{reminder.Field("time_occurrence"), "10:11:12.12", "10:11:12.120000"},
		{reminder.Field("time_occurrence"), "10:11:12", "10:11:12"},
		{archive.FieldByAttname("folder_id"), json.Number("7"), "7"},
	}
	for i, c := range cases {
		if c.field == nil {
			t.Fatalf("case %d: missing field", i)
		}
		got, err := storedAuditText(c.field, c.value)
		if err != nil || got != c.want {
			t.Fatalf("case %d (%s): got %q (%v), want %q", i, c.field.Name, got, err, c.want)
		}
	}
}
