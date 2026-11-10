package config

import (
	"encoding/json"
	"os"
	"testing"
)

// testdata/parseaddr.json holds email.utils.parseaddr outputs from the
// production interpreter (testdata/parseaddr_corpus.py).
func TestParseAddrMatchesPython(t *testing.T) {
	raw, err := os.ReadFile("testdata/parseaddr.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases [][3]string
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) < 600 {
		t.Fatalf("corpus: %v (%d cases)", err, len(cases))
	}
	for _, c := range cases {
		if name, address := ParseAddr(c[0]); name != c[1] || address != c[2] {
			t.Errorf("ParseAddr(%q) = (%q, %q), Python (%q, %q)", c[0], name, address, c[1], c[2])
		}
	}
}

func TestInstanceAdminSenderFromWhitelabel(t *testing.T) {
	s, err := parseInstanceAdmin(map[string]string{"WHITELABEL_NAME": `A.S.D. Polisportiva "Città" (Aurora)`, "EMAIL_HOST_USER": "segreteria@polisportiva.example"})
	if err != nil || s.FromName != "A.S.D. Polisportiva Città (Aurora)" || s.FromAddress != "segreteria@polisportiva.example" {
		t.Fatalf("got %q %q %v", s.FromName, s.FromAddress, err)
	}
	s, err = parseInstanceAdmin(map[string]string{"WHITELABEL_NAME": "Polisportiva, Città"})
	if err != nil || s.FromName != "" || s.FromAddress != "" {
		t.Fatalf("got %q %q %v", s.FromName, s.FromAddress, err)
	}
	s, err = parseInstanceAdmin(map[string]string{})
	if err != nil || s.FromName != "Bakney Sport" || s.FromAddress != "None" {
		t.Fatalf("got %q %q %v", s.FromName, s.FromAddress, err)
	}
}
