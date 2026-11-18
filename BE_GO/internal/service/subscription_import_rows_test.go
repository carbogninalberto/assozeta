package service

import (
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
)

// Expected values: Python 3.13.16, re.search with re.IGNORECASE on
// process_import_rows' tax_code_regex (see subscriptionImportTaxCode).
func TestSubscriptionImportTaxCodeIgnoreCase(t *testing.T) {
	for _, tc := range []struct {
		code  string
		valid bool
	}{
		{"RSSMRA80A01H501U", true},
		// sre's extra equivalences: 'ſ' is 's', 'ı' is 'i'.
		{"RſSMRA80A01H501U", true},
		{"RSSMRA80A01H501ſ", true},
		{"RSSMRA80A01H501ı", true},
		{"RSSMRA80ſ01H501U", true},
		{"ſſſſſſ80A01H501U", true},
		// Simple lowercase into ASCII: 'İ' is 'i', the KELVIN sign is 'k'.
		{"RİSMRA80A01H501U", true},
		{"RSSMRA80A01H501İ", true},
		{"RSSMRA80A01İ501U", true},
		{"RSSMRA80A01H501\u212a", true},
		// No fold into the month class ('i' and 'k' are not months) or into
		// [0-9].
		{"RSSMRA80K01H501U", false},
		{"RSSMRA80A01H50ıU", false},
		{"RSSMRA80İ01H501U", false},
		{"RSSMRA80\u212a01H501U", false},
		// [0-9] matches ASCII digits only.
		{"RSSMRA8٣A01H501U", false},
		{"RSSMRA8٠A01H501U", false},
		// A character whose lowercase is not in the class.
		{"RSSMRA80A01é501U", false},
		{"RSSMRA80A0é1H501U", false},
		{"RSSMRA80A01H501é", false},
		// The ASCII letters of both cases.
		{"rssmra80a01h501u", true},
		{"RSSMRA80a01H501U", true},
		// The anchors: '$' also matches before one final line feed.
		{"RSSMRA80A01H501U\n", true},
		{"RSSMRA80A01H501U\n\n", false},
		{"RSSMRA80A01H501U ", false},
		{"RSSMRA80A01H501U\u00a0", false},
		{"RSSMRA80A01H501", false},
		{"RSSMRA80A01H501UU", false},
	} {
		if got := subscriptionImportTaxCode(tc.code); got != tc.valid {
			t.Errorf("subscriptionImportTaxCode(%q) = %v, want %v", tc.code, got, tc.valid)
		}
	}
}

// Expected values: Python 3.13.16, bool() and str() of each value.
func TestSubscriptionImportTruthyPythonValues(t *testing.T) {
	for _, tc := range []struct {
		value  any
		truthy bool
		text   string
	}{
		{nil, false, ""},
		{"", false, ""},
		{"x", true, "x"},
		{"ſ", true, "ſ"},
		{true, true, "True"},
		{false, false, ""},
		{json.Number("0"), false, "0"},
		{json.Number("7"), true, "7"},
		{float64(0), false, "0.0"},
		{float64(1.5), true, "1.5"},
		// Values the frames cannot put in a cell: bool() is the non-emptiness
		// and str() the Python repr.
		{[]any{}, false, "[]"},
		{[]any{"s"}, true, "['s']"},
		{[]any{json.Number("1"), domain.RequestObject{{Name: "a", Value: nil}}}, true, "[1, {'a': None}]"},
		{domain.RequestObject{}, false, "{}"},
		{domain.RequestObject{{Name: "a", Value: "ſ"}}, true, "{'a': 'ſ'}"},
	} {
		truthy, text, err := subscriptionImportTruthy(tc.value)
		if err != nil || truthy != tc.truthy || text != tc.text {
			t.Errorf("subscriptionImportTruthy(%#v) = (%v, %q, %v), want (%v, %q)", tc.value, truthy, text, err, tc.truthy,
				tc.text)
		}
	}
}

// Expected values: Python 3.13.16, `key in data['default']` and
// data['default'][key] of process_import_rows (raises is the view's 400).
func TestSubscriptionImportDefault(t *testing.T) {
	for _, tc := range []struct {
		defaults any
		key      string
		value    any
		present  bool
		raises   bool
	}{
		{domain.RequestObject{{Name: "cap", Value: "000"}}, "cap", "000", true, false},
		{domain.RequestObject{{Name: "cap", Value: "000"}}, "nome", nil, false, false},
		// A parsed JSON object keeps a repeated key's last value.
		{domain.RequestObject{{Name: "cap", Value: "one"}, {Name: "cap", Value: "two"}}, "cap", "two", true, false},
		{domain.RequestObject{{Name: "cap", Value: nil}}, "cap", nil, true, false},
		// A str is a substring test, then indexed with a text key (TypeError).
		{"nome cap", "cap", nil, true, true},
		{"nome cap", "cognome", nil, false, false},
		{"cap", "cap", nil, true, true},
		// A list is a membership test (Python equality: only equal text), then
		// indexed with a text key (TypeError).
		{[]any{"cap", json.Number("5")}, "cap", nil, true, true},
		{[]any{"nome", json.Number("5")}, "cap", nil, false, false},
		{[]any{}, "cap", nil, false, false},
		// Not iterable: TypeError before any index.
		{nil, "cap", nil, false, true},
		{json.Number("5"), "cap", nil, false, true},
		{float64(5), "cap", nil, false, true},
		{true, "cap", nil, false, true},
	} {
		value, present, raises := subscriptionImportDefault(tc.defaults, tc.key)
		if present != tc.present || raises != tc.raises || !sameValue(value, tc.value) {
			t.Errorf("subscriptionImportDefault(%#v, %q) = (%#v, %v, %v), want (%#v, %v, %v)", tc.defaults, tc.key, value,
				present, raises, tc.value, tc.present, tc.raises)
		}
	}
}

// sameValue is == for the comparable values of the table (a nil interface
// included).
func sameValue(left, right any) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	return left == right
}

// Expected values: Django 5 / django-storages 1.14.5, import_file_path under
// default_storage.exists with AWS_LOCATION 'import-uploads' (measured on the
// migration interpreter: safe_join(location, clean_name(path))).
func TestSubscriptionImportStoredName(t *testing.T) {
	association := "a1000000-0000-4000-8000-000000701001"
	id := "a1000000-0000-4000-8000-000000984201"
	created := time.Date(2026, 4, 1, 8, 0, 0, 0, time.UTC)
	folder := "subscription-import/" + association + "/1775030400.0/" + id + "/"
	for _, tc := range []struct {
		filename string
		name     string
		key      string // "" for SuspiciousOperation (OutsideLocationError)
	}{
		{"elenco.xlsx", folder + "elenco.xlsx", "import-uploads/" + folder + "elenco.xlsx"},
		{"soci.csv", folder + "soci.csv", "import-uploads/" + folder + "soci.csv"},
		// No validation on the read path: the name is cleaned and looked up.
		{"", folder, "import-uploads/" + folder},
		{".", folder + ".", "import-uploads/subscription-import/" + association + "/1775030400.0/" + id},
		{"..", folder + "..", "import-uploads/subscription-import/" + association + "/1775030400.0"},
		{"../elenco.csv", folder + "../elenco.csv",
			"import-uploads/subscription-import/" + association + "/1775030400.0/elenco.csv"},
		{"sub/x.csv", folder + "sub/x.csv", "import-uploads/" + folder + "sub/x.csv"},
		// An absolute filename replaces the whole path: outside the location.
		{"/tmp/x.csv", "/tmp/x.csv", ""},
		// Above the location.
		{"../../../../../x.csv", folder + "../../../../../x.csv", ""},
	} {
		name, err := subscriptionImportStoredName(association, created, id, tc.filename)
		if err != nil || name != tc.name {
			t.Errorf("subscriptionImportStoredName(%q) = (%q, %v), want %q", tc.filename, name, err, tc.name)
			continue
		}
		key, err := documentkey.S3Key(name, "import-uploads")
		if tc.key == "" {
			var outside *documentkey.OutsideLocationError
			if !errors.As(err, &outside) {
				t.Errorf("S3Key(%q) = (%q, %v), want an OutsideLocationError", name, key, err)
			}
			continue
		}
		if err != nil || key != tc.key {
			t.Errorf("S3Key(%q) = (%q, %v), want %q", name, key, err, tc.key)
		}
	}
	// A creation date outside Python's range is the only failure: the
	// application writes timezone.now() at microsecond precision.
	if _, err := subscriptionImportStoredName(association, time.Date(10000, 1, 1, 0, 0, 0, 0, time.UTC), id, "x.csv"); err == nil {
		t.Error("subscriptionImportStoredName accepted a creation date outside Python's range")
	}
}

// subscriptionImportPlanTestUpload is process_import_rows without a store: the
// associations ids and the clock are fixed.
func subscriptionImportPlanTestUpload() *SubscriptionImportUpload {
	return &SubscriptionImportUpload{uploads: &DocumentUploads{
		now: func() time.Time { return time.Date(2026, 5, 1, 8, 0, 0, 0, time.UTC) },
		ids: func() string { return "a1000000-0000-4000-8000-000000984201" },
	}}
}

// The frame's own error (an application state the package reports for a lookup
// it cannot make) is process_import_rows' exception and is handed on as the
// package returns it, not joined with a service error.
func TestSubscriptionImportPlanReturnsFrameError(t *testing.T) {
	lookupErr := refusal.AppState("test: a lookup this package cannot make")
	lookup := func(any) (int, bool, bool, error) { return 0, false, false, lookupErr }
	data := domain.RequestObject{{Name: "map", Value: domain.RequestObject{{Name: "nome", Value: "Nome"}}}}
	effects, err := subscriptionImportPlanTestUpload().subscriptionImportPlan(lookup, [][]any{{"Ada"}}, data)
	if err != lookupErr {
		t.Fatalf("subscriptionImportPlan(lookup error) = %v, want the sentinel itself", err)
	}
	if !errors.Is(err, refusal.ErrAppState) {
		t.Error("the frame's error does not match refusal.ErrAppState")
	}
	if effects.Failed || len(effects.Drafts) != 0 {
		t.Errorf("a frame error reported row effects: %#v", effects)
	}
}

// `key in data['default']` and data['default'][key] through the row loop: an
// object applies its value, a str or a list raises the view's 400 when the key
// is present, and a list without the key leaves the row as it is.
func TestSubscriptionImportPlanDefault(t *testing.T) {
	columns := []string{"Nome", "CAP"}
	lookup := func(column any) (int, bool, bool, error) {
		for index, name := range columns {
			if column == name {
				return index, true, false, nil
			}
		}
		return 0, false, false, nil
	}
	mapping := domain.RequestObject{{Name: "nome", Value: "Nome"}, {Name: "cap", Value: "CAP"}}
	for _, tc := range []struct {
		defaults any
		failed   bool
		drafts   []string
		valid    []bool
	}{
		{domain.RequestObject{{Name: "cap", Value: "000"}}, false,
			[]string{`{"associate": {"first_name": "Ada", "address_cap": "000"}, "associate_tutor": {}}`}, []bool{false}},
		{"xcapx", true, nil, nil},
		{[]any{"cap"}, true, nil, nil},
		{json.Number("5"), true, nil, nil},
		{nil, true, nil, nil},
		{[]any{"xcapx"}, false, []string{`{"associate": {"first_name": "Ada"}, "associate_tutor": {}}`}, []bool{false}},
	} {
		data := domain.RequestObject{{Name: "map", Value: mapping}, {Name: "default", Value: tc.defaults}}
		effects, err := subscriptionImportPlanTestUpload().subscriptionImportPlan(lookup, [][]any{{"Ada", nil}}, data)
		if err != nil {
			t.Errorf("subscriptionImportPlan(default %#v) = error %v", tc.defaults, err)
			continue
		}
		if effects.Failed != tc.failed || len(effects.Drafts) != len(tc.drafts) {
			t.Errorf("subscriptionImportPlan(default %#v) = failed %v with %d drafts, want failed %v with %d", tc.defaults,
				effects.Failed, len(effects.Drafts), tc.failed, len(tc.drafts))
			continue
		}
		for index, draft := range effects.Drafts {
			if string(draft.JSON) != tc.drafts[index] || draft.Valid != tc.valid[index] {
				t.Errorf("subscriptionImportPlan(default %#v) draft %d = %s valid %v, want %s valid %v", tc.defaults, index,
					draft.JSON, draft.Valid, tc.drafts[index], tc.valid[index])
			}
		}
	}
}

// The removed sentinel's sites are ported or application state: none of them
// is handed off as a refusal.
func TestSubscriptionImportPortedStateErrors(t *testing.T) {
	for name, err := range map[string]error{
		"errSubscriptionImportStoredDocument": errSubscriptionImportStoredDocument,
		"errSubscriptionImportTaxCodeData":    errSubscriptionImportTaxCodeData,
	} {
		if !errors.Is(err, refusal.ErrAppState) {
			t.Errorf("%s does not match refusal.ErrAppState", name)
		}
		if errors.Is(err, refusal.ErrNotPorted) {
			t.Errorf("%s would be handed off to Django", name)
		}
	}
}
