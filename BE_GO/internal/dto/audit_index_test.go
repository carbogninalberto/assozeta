package dto

import (
	"encoding/json"
	"testing"
)

func TestAuditIndexUUIDText(t *testing.T) {
	one := "00000000-0000-0000-0000-000000000001"
	cases := map[string]string{
		"00000000-0000-0000-0000-000000000001":      one,
		"{00000000-0000-0000-0000-000000000001}":    one,
		"urn:uuid:00000000000000000000000000000001": one,
		"+0000000000000000000000000000001":          one,
		"0x000000000000000000000000000001":          one,
		"0X_00000000000000000000000000001":          one,
		"0_000000000000000000000000000001":          one,
		" 0000000000000000000000000000001":          one,
		"٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠٠1":          one,
		"ABCDEF00-0000-0000-0000-000000000000":      "abcdef00-0000-0000-0000-000000000000",
		"0__00000000000000000000000000001":          "",
		"_0000000000000000000000000000001":          "",
		"0000000000000000000000000000001_":          "",
		"00000000-0000-0000-0000-00000000001":       "",
		"g0000000000000000000000000000001":          "",
		"":                                          "",
	}
	for input, want := range cases {
		got, ok := AuditIndexUUIDText(input)
		if ok != (want != "") || got != want {
			t.Errorf("%q: got %q %v, want %q", input, got, ok, want)
		}
	}
}

func TestAuditIndexUUIDValueAndTruthiness(t *testing.T) {
	if got, ok := AuditIndexUUIDValue(json.Number("777")); !ok || got != "00000000-0000-0000-0000-000000000309" {
		t.Fatal(got)
	}
	for _, value := range []any{json.Number("1.5"), json.Number("-5"), []any{"x"}, map[string]any{}, nil} {
		if _, ok := AuditIndexUUIDValue(value); ok {
			t.Errorf("%v accepted", value)
		}
	}
	for value, want := range map[string]bool{"0": false, "0.0": false, "-0.0": false, "1e-400": false, "2": true, "0.5": true} {
		if AuditIndexTruthy(json.Number(value)) != want {
			t.Errorf("truthiness of %s", value)
		}
	}
	if _, inRange, ok := AuditIndexAutoPK(" 2_147_483_648 "); !ok || inRange {
		t.Fatal("out-of-range integer key")
	}
}
