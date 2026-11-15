package service

import (
	"strings"
	"testing"
	"unicode/utf8"
)

// The package has no fake for domain.SubscriptionMedicalTransaction (nothing in
// the service tests mentions SubscriptionMemberNames or CreateCertificate), so
// set-certificate-expiration's placeholder branch is tested through the
// f-string helper it builds the name with. The branch itself is not exercised:
// the certificate kept before the 500 of a name over the filename column, and
// the refusal of a control character or a storage-rejected name.
func TestSubscriptionMedicalPlaceholderName(t *testing.T) {
	stored, empty := "Anna", ""
	for _, test := range []struct {
		name  string
		value *string
		want  string
	}{
		{"nil first name is str(None)", nil, "None"},
		{"stored name", &stored, "Anna"},
		{"empty name", &empty, ""},
	} {
		if got := placeholderName(test.value); got != test.want {
			t.Errorf("%s: placeholderName = %q, want %q", test.name, got, test.want)
		}
	}
}

// A NULL member name reaches the file name and the image as the text "None"
// (Django's f-string of subscription.associate.first_name), and a name whose
// file name is over the varchar(255) column is the DataError Django answers
// after creating the certificate.
func TestSubscriptionMedicalPlaceholderFilename(t *testing.T) {
	last := "Rossi"
	if got, want := placeholderFilename(placeholderName(nil), placeholderName(&last)), "medical_certificate_None_Rossi.png"; got != want {
		t.Fatalf("filename = %q, want %q", got, want)
	}
	if got, want := placeholderExpirationText(placeholderName(nil), placeholderName(&last), "2027-01-01 00:00:00+00:00"),
		"\nNone Rossi\n\n\n\nQuesta certificazione è valida fino al 2027-01-01 00:00:00+00:00"+
			"\n\n\n\n\n\nAttenzione: questo è un placeholder, il certificato medico deve essere caricato in seguito.\n            "; got != want {
		t.Fatalf("image text = %q, want %q", got, want)
	}
	// placeholderExpiration counts characters, not bytes: 256 accents are 512
	// bytes and still overflow the column.
	long := strings.Repeat("è", 256)
	filename := placeholderFilename(long, last)
	if utf8.RuneCountInString(filename) <= 255 {
		t.Fatalf("filename of %d characters does not overflow the column", utf8.RuneCountInString(filename))
	}
}
