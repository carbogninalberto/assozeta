package dto

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Expected values are Python 3.13 / DRF 3.17 results for the same inputs.
func TestBackupActionsIntegerField(t *testing.T) {
	cases := map[any]string{
		"7": "7", " 3 ": "3", "7.0": "7", "7.": "7", "5.00 \n": "5", "1_0": "10", "-0": "0", "+4": "4",
		"٣": "3", "\U0001D7D7": "9", json.Number("5.0"): "5", json.Number("1e3"): "1000", json.Number("-0.0"): "0",
	}
	for input, want := range cases {
		number, code := backupActionsInteger(input)
		if code != "" || number.String() != want {
			t.Errorf("%#v: got %v %q, want %s", input, number, code, want)
		}
	}
	for _, input := range []any{"", "1__0", "_1", "1_", "1.5", "1e0", "0x1", true, json.Number("1e16"), json.Number("5.5"), []any{}} {
		if _, code := backupActionsInteger(input); code != BackupActionsInteger {
			t.Errorf("%#v: got %q, want invalid", input, code)
		}
	}
}

func TestBackupActionsBooleanField(t *testing.T) {
	for input, want := range map[any]bool{"TRUE": true, "Yes": true, "on": true, "1": true, "N": false, "off": false,
		json.Number("1"): true, json.Number("1.0"): true, json.Number("0"): false, json.Number("-0.0"): false, false: false} {
		if got, ok := backupActionsBoolean(input); !ok || got != want {
			t.Errorf("%#v: got %v %v", input, got, ok)
		}
	}
	for _, input := range []any{"", "null", "2", json.Number("2"), json.Number("0.5"), []any{}, domain.RequestObject{}} {
		if _, ok := backupActionsBoolean(input); ok {
			t.Errorf("%#v: want invalid", input)
		}
	}
}

func TestBackupActionsScheduleOrderAndDefault(t *testing.T) {
	body := domain.RequestObject{{Name: "keep", Value: "7"}, {Name: "time", Value: " 1٢:3٠ "},
		{Name: "frequency", Value: "weekly"}, {Name: "enabled", Value: "f"}}
	value, errs, err := ValidateBackupActions(body, BackupScheduleFields)
	if err != nil || errs != nil {
		t.Fatalf("unexpected %v %v", errs, err)
	}
	want := strings.ReplaceAll(`{"enabled": false, "frequency": "weekly", "time": "1~u0662:3~u0660", "weekday": 6, "keep": 7}`, "~", `\`)
	if got := string(PythonJSONDumps(value)); got != want {
		t.Fatalf("payload %q", got)
	}
}
