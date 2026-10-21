package documentkey

import (
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestMedicalS3Key(t *testing.T) {
	id := uuid.MustParse("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
	for _, tt := range []struct{ date, name, location, want string }{
		{"2024-01-01T00:00:00Z", "certificato.pdf", "", "1704067200.0/" + id.String() + "/certificato.pdf"},
		{"2024-01-01T01:00:00.123456+01:00", "visita 日本語 %.pdf", "media/", "media/1704067200.123456/" + id.String() + "/visita 日本語 %.pdf"},
		{"1970-01-01T00:00:00.000001Z", "a.pdf", "", "1e-06/" + id.String() + "/a.pdf"},
		{"1970-01-01T00:00:00.0001Z", "a.pdf", "", "0.0001/" + id.String() + "/a.pdf"},
		{"1969-12-31T23:59:59.999999Z", "a.pdf", "", "-1e-06/" + id.String() + "/a.pdf"},
		{"1970-01-01T00:00:00Z", "nested/../a.pdf", "", "0.0/" + id.String() + "/a.pdf"},
		{"1970-01-01T00:00:00Z", `nested\a.pdf`, "", "0.0/" + id.String() + "/nested/a.pdf"},
		{"1970-01-01T00:00:00Z", "nested//a.pdf/", "media/files///", "media/files/0.0/" + id.String() + "/nested/a.pdf/"},
		{"1970-01-01T00:00:00Z", "", "", "0.0/" + id.String() + "/"},
		{"1970-01-01T00:00:00Z", "a.pdf", "../media", "../media/0.0/" + id.String() + "/a.pdf"},
	} {
		t.Run(tt.date+tt.name+tt.location, func(t *testing.T) {
			created, err := time.Parse(time.RFC3339Nano, tt.date)
			if err != nil {
				t.Fatal(err)
			}
			got, err := MedicalS3Key(created, id, tt.name, tt.location)
			if err != nil || got != tt.want {
				t.Fatalf("key %q, %v; want %q", got, err, tt.want)
			}
		})
	}
	got, err := MedicalS3Key(time.Unix(0, 0), uuid.Nil, "zero.pdf", "")
	if err != nil || got != "0.0/00000000-0000-0000-0000-000000000000/zero.pdf" {
		t.Fatal("zero UUID changed", got, err)
	}
}

func TestMedicalS3KeyRejectsCrossDocumentNames(t *testing.T) {
	created := time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC)
	for _, name := range []string{"../foreign.pdf", "nested/../../foreign.pdf", "/absolute.pdf", `..\foreign.pdf`, `nested\..\..\foreign.pdf`, "a\x00.pdf", string([]byte{0xff})} {
		if got, err := MedicalS3Key(created, uuid.Nil, name, "media"); got != "" || !errors.Is(err, ErrMedicalKey) {
			t.Fatalf("unsafe name %q: %q, %v", name, got, err)
		}
	}
	for _, location := range []string{"/media", "media/../other", "media//files", ".", "media\x00"} {
		if got, err := MedicalS3Key(created, uuid.Nil, "a.pdf", location); got != "" || !errors.Is(err, ErrMedicalKey) {
			t.Fatalf("invalid location %q: %q, %v", location, got, err)
		}
	}
	for _, date := range []time.Time{created.Add(time.Nanosecond), time.Date(0, 1, 1, 0, 0, 0, 0, time.UTC), time.Date(10000, 1, 1, 0, 0, 0, 0, time.UTC)} {
		if got, err := MedicalS3Key(date, uuid.Nil, "a.pdf", ""); got != "" || !errors.Is(err, ErrMedicalKey) {
			t.Fatalf("invalid timestamp: %q, %v", got, err)
		}
	}
}
