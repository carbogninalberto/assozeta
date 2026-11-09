package service

import (
	"encoding/json"
	"testing"
)

func TestTransferRecipientID(t *testing.T) {
	for _, item := range []struct {
		value any
		want  string
		ok    bool
	}{
		{"A1000000-0000-4000-8000-000000700001", "a1000000-0000-4000-8000-000000700001", true},
		{"nessuno", "", false},
		{json.Number("7"), "00000000-0000-0000-0000-000000000007", true},
		{json.Number("1.5"), "", false},
		{json.Number("-1"), "", false},
		{true, "00000000-0000-0000-0000-000000000001", true},
		{[]any{}, "", false},
	} {
		got, ok := transferRecipientID(item.value)
		if got != item.want || ok != item.ok {
			t.Errorf("transferRecipientID(%#v) = %q, %v", item.value, got, ok)
		}
	}
}

func TestTransferEmail(t *testing.T) {
	for _, item := range []struct {
		value any
		want  string
		ok    bool
	}{
		{" Nuovo.Utente@Example.TEST ", "Nuovo.Utente@example.test", true},
		{" senza-chiocciola ", " senza-chiocciola ", true},
		{json.Number("0"), "", true},
		{false, "", true},
		{json.Number("5"), "", false},
	} {
		got, ok := transferEmail(item.value)
		if got != item.want || ok != item.ok {
			t.Errorf("transferEmail(%#v) = %q, %v", item.value, got, ok)
		}
	}
	if pythonPrefix("Nicolò", 3) != "Nic" || pythonPrefix("Èva", 3) != "Èva" || pythonPrefix("Al", 3) != "Al" {
		t.Error("pythonPrefix")
	}
}
