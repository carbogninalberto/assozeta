package service

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pilplaceholder"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
)

// The package has no fake for domain.SubscriptionMedicalTransaction (nothing in
// the service tests mentions SubscriptionMemberNames or CreateCertificate), so
// set-certificate-expiration's placeholder branch is tested through the
// helpers it builds the name, the text and the stored object with: the names
// (str of a NULL), the file name, the drawing (a control character is drawn as
// the font's missing-glyph box, as Pillow draws it) and the storage name
// validation that answers the 400 page.
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

// create_subscription's f-string takes str() of whatever the request carried:
// a number is its digits, a NULL member name "None" (the name validation runs
// before this branch in Django, so only a stored or a re-sent row can carry one).
func TestSubscriptionCreatePlaceholderFilename(t *testing.T) {
	for _, test := range []struct {
		name  string
		value any
		want  string
	}{
		{"number", json.Number("7"), "7"},
		{"text", "Pia", "Pia"},
		{"null", nil, "None"},
		{"list", []any{json.Number("1"), "x"}, "[1, 'x']"},
	} {
		if got := placeholderFilename(dto.PythonStr(test.value), "Segnaposto"); got != "medical_certificate_"+test.want+"_Segnaposto.png" {
			t.Errorf("%s: filename = %q", test.name, got)
		}
	}
	if got, want := placeholderCreateText("7", "Segnaposto", "2027-03-31 00:00:00+00:00"),
		"\n            7 Segnaposto\n\n            \n\n            Questa certificazione è valida fino al 2027-03-31 00:00:00+00:00"+
			"\n\n            \n\n            \n\n            Attenzione: questo è un placeholder, il certificato medico deve essere caricato in seguito.\n                        "; got != want {
		t.Fatalf("image text = %q, want %q", got, want)
	}
}

// subscriptionMedicalDate is strptime with the two formats: the date, or the
// exception the view's try block swallows (a non-string, a value that is no
// date, a date the calendar does not have). Unicode digits are parsed where
// strptime parses them ('%Y' and '[12]\d') and rejected where it rejects them.
func TestSubscriptionMedicalDate(t *testing.T) {
	for _, test := range []struct {
		name  string
		value any
		want  string
		ok    bool
	}{
		{"day first", "31/03/2027", "2027-03-31", true},
		{"day first short", "1/2/2027", "2027-02-01", true},
		{"iso", "2027-03-31", "2027-03-31", true},
		{"iso short", "2027-3-1", "2027-03-01", true},
		{"unicode year", "31/03/٢٠٢٧", "2027-03-31", true},
		{"unicode day", "2027-01-1١", "2027-01-11", true},
		{"unicode month is rejected", "31/٠٣/2027", "", false},
		{"impossible", "31/02/2027", "", false},
		{"today's date text is not a date", "oggi", "", false},
		{"number", json.Number("20270331"), "", false},
		{"null", nil, "", false},
	} {
		date, ok := subscriptionMedicalDate(test.value)
		got := ""
		if ok {
			got = date.Format("2006-01-02")
		}
		if ok != test.ok || got != test.want {
			t.Errorf("%s: subscriptionMedicalDate(%v) = %q, %v; want %q, %v", test.name, test.value, got, ok, test.want, test.ok)
		}
	}
}

// plan is generate_image_with_text: a control character in the name is drawn as
// the font's missing-glyph box, as Pillow draws it, so the name is no refusal.
func TestSubscriptionPlaceholderPlan(t *testing.T) {
	placeholders := NewSubscriptionPlaceholders(nil, pilplaceholder.ZlibClassic)
	for _, text := range []string{"Pia Rossi", "\x01 Controllo", "a\x7fb", "x\ty"} {
		image, err := placeholders.plan(text)
		if err != nil {
			t.Fatalf("plan(%q): %v", text, err)
		}
		want, err := pilplaceholder.RenderWith(text, pilplaceholder.ZlibClassic)
		if err != nil || string(image) != string(want) {
			t.Fatalf("plan(%q) differs from the drawing: %v", text, err)
		}
	}
	var unused *SubscriptionPlaceholders
	if _, err := unused.plan("Pia Rossi"); !errors.Is(err, domain.ErrServerComposition) {
		t.Fatalf("unwired plan error = %v, want application state", err)
	}
}

// store is default_storage.save: Storage.save's name validation rejects a path
// traversal as Django's SuspiciousFileOperation, which the medical view answers
// with the 400 page (the storage itself is never reached).
func TestSubscriptionPlaceholderStoreRejectsTraversal(t *testing.T) {
	now := func() time.Time { return time.Date(2026, 5, 1, 12, 0, 0, 123456000, time.UTC) }
	placeholders := NewSubscriptionPlaceholders(NewDocumentUploads(nil, nil, nil, false, now, uuid.NewString), pilplaceholder.ZlibClassic)
	image, err := placeholders.plan("Pia Rossi")
	if err != nil {
		t.Fatal(err)
	}
	for _, filename := range []string{"medical_certificate_/../.._Rossi.png", "/medical_certificate_Pia_Rossi.png"} {
		err = placeholders.store(context.Background(), now(), uuid.NewString(), filename, image)
		var suspicious *documentkey.SuspiciousFileError
		if !errors.As(err, &suspicious) {
			t.Fatalf("store(%q) = %v, want a suspicious file name", filename, err)
		}
	}
	// A name the storage accepts is the one cleanup_storage_keys would delete.
	if name, ok := placeholders.storedName(now(), uuid.NewString(), "medical_certificate_7_Rossi.png"); !ok ||
		!strings.HasPrefix(name, "1777636800.123456/") || !strings.HasSuffix(name, "/medical_certificate_7_Rossi.png") {
		t.Fatalf("storedName = %q, %v", name, ok)
	}
}
