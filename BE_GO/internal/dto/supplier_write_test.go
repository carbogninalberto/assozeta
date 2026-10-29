package dto

import (
	"strings"
	"testing"
)

// The verdicts were produced by Django 5.2.18's EmailValidator.
func TestSupplierEmailMatchesDjangoEmailValidator(t *testing.T) {
	cases := map[string]bool{
		"a@b.it": true, "a@localhost": true, "a@b": false, "a@b.c": false, "a@b.co": true, "A@B.IT": true, "a.b@c.it": true,
		".a@b.it": false, "a..b@c.it": false, "a@-b.it": false, "a@b-.it": false, "a@b.-it": false, "a@b.it-": false, "a@b.1t": false,
		"a@b.xn--p1ai": true, "a@b.xn--": false, "a@[127.0.0.1]": true, "a@[::1]": true, "a@[1.2.3]": false, "a@[::1%eth0]": false,
		`"a b"@c.it`: false, `"a\"b"@c.it`: true, "a@b.è": false, "a@è.it": true, "a@b..it": false, "a@.b.it": false, "@b.it": false,
		"a@": false, "ab": false, "a@b.it.": false, "a+tag@b.it": true, "a@b_c.it": false,
		"a@" + strings.Repeat("x", 63) + ".it": true, "a@" + strings.Repeat("x", 64) + ".it": false, "é@b.it": false,
		"a@b.i" + strings.Repeat("t", 62): true, "a@b.i" + strings.Repeat("t", 63): false, "a@1.2.3.4": false, "a@b.c0m": false,
		"a@b.co-m": true, "a@xn--ls8h.la": true,
	}
	for value, want := range cases {
		if got := SupplierEmail(value); got != want {
			t.Errorf("SupplierEmail(%q) = %v, Django %v", value, got, want)
		}
	}
}

func TestSupplierValidationErrorKeepsSerializerFieldOrder(t *testing.T) {
	invalid := &SupplierValidationError{}
	invalid.Add("email", SupplierValidationEmail)
	invalid.Add("name", "max:255")
	invalid.Add("supplier_id", SupplierValidationUnique)
	invalid.Add("email", "max:254")
	got := []string{}
	for _, field := range invalid.Fields {
		got = append(got, field.Name+"="+strings.Join(field.Codes, ","))
	}
	if strings.Join(got, ";") != "supplier_id=supplier-unique;name=max:255;email=supplier-email,max:254" {
		t.Fatal(got)
	}
}
