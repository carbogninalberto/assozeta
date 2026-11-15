package service

import (
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestPersonaPictureURL(t *testing.T) {
	key := "tenant-files/associates/a1000000-0000-4000-8000-000000000001/picture_1.5.jpg"
	for _, tc := range []struct {
		settings PersonaPictureSettings
		want     string
	}{
		{PersonaPictureSettings{PublicBaseURL: " https://media.example.test/public/ ", Endpoint: "https://objects.example.test", Bucket: "media"},
			"https://media.example.test/public/" + key},
		{PersonaPictureSettings{Endpoint: "https://objects.example.test/", Bucket: "media"}, "https://objects.example.test/media/" + key},
	} {
		if got := personaPictureURL(tc.settings, key); got != tc.want {
			t.Errorf("got %s, want %s", got, tc.want)
		}
	}
}

func TestPersonaPicturePlan(t *testing.T) {
	body := domain.RequestObject{{Name: "associate_id", Value: "../other"}, {Name: "first_name", Value: "Ada"},
		{Name: "picture_path", Value: "data:image/png;base64,QUJD"}}
	decoded, upload, err := personaPicturePlan(body)
	if err != nil || !upload || string(decoded) != "ABC" {
		t.Fatalf("plan: %q %v %v", decoded, upload, err)
	}
	rest := personaWithoutPicture(body)
	if _, present := rest.Lookup("picture_path"); present || len(rest) != 2 {
		t.Fatalf("picture_path kept: %v", rest)
	}
	if _, upload, err = personaPicturePlan(domain.RequestObject{{Name: "picture_path", Value: "https://cdn.example.test/a.jpg"}}); upload || err != nil {
		t.Fatalf("stored URL planned as an upload: %v %v", upload, err)
	}
	var invalid *PersonaPictureInvalid
	if _, _, err = personaPicturePlan(domain.RequestObject{{Name: "picture_path", Value: "data:image/png;base64,QUJDQ"}}); err == nil {
		t.Fatal("invalid text accepted")
	} else if picture, ok := err.(*PersonaPictureInvalid); !ok {
		t.Fatalf("unexpected error %v", err)
	} else {
		invalid = picture
	}
	if invalid.Text == "" {
		t.Fatal("no text")
	}
}

// A list or dict that reaches the base64 branch answers the exception text of
// value.split (a ',' element or key) or of base64.b64decode.
func TestPersonaPicturePlanListAndDict(t *testing.T) {
	for _, tc := range []struct {
		value any
		want  string
	}{
		{[]any{"base64"}, "Error processing image: argument should be a bytes-like object or ASCII string, not 'list'"},
		{[]any{"base64", ","}, "Error processing image: 'list' object has no attribute 'split'"},
		{domain.RequestObject{{Name: "base64", Value: "x"}}, "Error processing image: argument should be a bytes-like object or ASCII string, not 'dict'"},
		{domain.RequestObject{{Name: "base64", Value: "x"}, {Name: ",", Value: nil}}, "Error processing image: 'dict' object has no attribute 'split'"},
	} {
		_, upload, err := personaPicturePlan(domain.RequestObject{{Name: "picture_path", Value: tc.value}})
		invalid, ok := err.(*PersonaPictureInvalid)
		if upload || !ok || invalid.Text != tc.want {
			t.Errorf("%v: upload %v, error %v, want %q", tc.value, upload, err, tc.want)
		}
	}
	for _, value := range []any{[]any{"base"}, domain.RequestObject{{Name: "x", Value: "base64"}}} {
		if _, upload, err := personaPicturePlan(domain.RequestObject{{Name: "picture_path", Value: value}}); upload || err != nil {
			t.Errorf("%v outside the branch: %v %v", value, upload, err)
		}
	}
}
