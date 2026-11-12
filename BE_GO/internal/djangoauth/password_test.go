package djangoauth

import (
	"crypto/sha256"
	"encoding/hex"
	"reflect"
	"testing"
)

func TestCommonPasswordListIsDjango5218(t *testing.T) {
	sum := sha256.Sum256(commonPasswords)
	if hex.EncodeToString(sum[:]) != "3c1baed62596de36860824eb3f436d5932d37ca8b06e59df78f5a44ec175afe4" {
		t.Fatal("embedded common password list differs from Django 5.2.18")
	}
	set, err := loadCommon()
	if err != nil || len(set) < 19000 {
		t.Fatalf("list not loaded: %d %v", len(set), err)
	}
}

func TestValidatePasswordOrderAndMessages(t *testing.T) {
	for _, item := range []struct {
		password string
		user     UserAttributes
		want     []string
	}{
		{"A-long-Test-password-491!", UserAttributes{Username: "ADMIN"}, nil},
		{"1234", UserAttributes{}, []string{"This password is too short. It must contain at least 8 characters.",
			"Questa password è troppo comune.", "Questa password è interamente numerica."}},
		{"²³¹²³¹²³¹", UserAttributes{}, []string{"Questa password è interamente numerica."}},
		{"ADMINISTRATOR1", UserAttributes{Username: "ADMINISTRATOR"}, []string{"La password è troppo simile a nome utente."}},
		{"rossimario99x", UserAttributes{Username: "X", Email: "mario.rossi@example.test"}, nil},
		{"rossi12", UserAttributes{Username: "X", Email: "mario.rossi@example.test"}, []string{
			"La password è troppo simile a indirizzo email.", "This password is too short. It must contain at least 8 characters."}},
	} {
		got, err := ValidatePassword(item.password, item.user)
		if err != nil || !reflect.DeepEqual(got, item.want) {
			t.Errorf("%q: %q %v", item.password, got, err)
		}
	}
}

func TestPythonCaseMapping(t *testing.T) {
	if PythonUpper("straße") != "STRASSE" || PythonLower("İ") != "i̇" || PythonStrip("\x1c a 　") != "a" {
		t.Fatal("Python str case/strip semantics")
	}
}
