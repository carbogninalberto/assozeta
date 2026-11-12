package service

import (
	"testing"
	"time"
)

// Produced by django.core.signing.dumps(payload, salt=...) with SECRET_KEY
// 'fixture-secret' (Django 5.2.18).
const djangoFixtureToken = "eyJkb2N1bWVudF9pZCI6ImQiLCJzcG9ydF9hc3NvY2lhdGlvbl9pZCI6ImEiLCJwdXJwb3NlIjoiYXNzb2NpYXRpb24tZXhwb3J0In0:1xQEpA:GrogVr2gKGvm-nDFIK1g3JXxXrW7j4C_E8Cb_iUBcs0"

func TestDjangoSigningLoads(t *testing.T) {
	stamp, err := base62Decode("1xQEpA")
	if err != nil {
		t.Fatal(err)
	}
	signed := time.Unix(stamp.Int64(), 0)
	loaded, err := djangoSigningLoads(djangoFixtureToken, documentDownloadTokenSalt, "fixture-secret", 3600, signed.Add(10*time.Second))
	if err != nil || !loaded.valid {
		t.Fatalf("fresh token: %+v %v", loaded, err)
	}
	payload, ok := downloadTokenPayload(loaded)
	if !ok || payload["document_id"] != "d" || payload["sport_association_id"] != "a" {
		t.Fatalf("payload %+v", payload)
	}
	for name, check := range map[string]func() (djangoSignedValue, error){
		"expired": func() (djangoSignedValue, error) {
			return djangoSigningLoads(djangoFixtureToken, documentDownloadTokenSalt, "fixture-secret", 3600, signed.Add(3601*time.Second))
		},
		"other secret": func() (djangoSignedValue, error) {
			return djangoSigningLoads(djangoFixtureToken, documentDownloadTokenSalt, "other", 3600, signed)
		},
		"other salt": func() (djangoSignedValue, error) {
			return djangoSigningLoads(djangoFixtureToken, "other", "fixture-secret", 3600, signed)
		},
		"no separator": func() (djangoSignedValue, error) {
			return djangoSigningLoads("garbage", documentDownloadTokenSalt, "fixture-secret", 3600, signed)
		},
	} {
		loaded, err := check()
		if err != nil || loaded.valid {
			t.Errorf("%s: %+v %v", name, loaded, err)
		}
	}
}

func TestPythonURLSafeB64Decode(t *testing.T) {
	for input, want := range map[string]string{"YWJj": "abc", "YWI": "ab", "YQ": "a", "Y W\nJj": "abc", "YQ==YWJj": "a"} {
		got, err := pythonURLSafeB64Decode(input)
		if err != nil || string(got) != want {
			t.Errorf("%q -> %q, %v; want %q", input, got, err, want)
		}
	}
	if _, err := pythonURLSafeB64Decode("YWJjZ"); err == nil {
		t.Error("one extra character must fail")
	}
}

func TestContentDisposition(t *testing.T) {
	for _, tc := range []struct {
		attachment bool
		name, want string
	}{
		{false, "report.pdf", `inline; filename="report.pdf"`},
		{true, `say "hi" \ x.png`, `attachment; filename="say \"hi\" \\ x.png"`},
		{false, "ricevuta è.pdf", "inline; filename*=utf-8''ricevuta%20%C3%A8.pdf"},
		{true, "", "attachment"},
		// One trailing newline stays in the quoted form; the caller answers Django's header check.
		{false, "x\n", "inline; filename=\"x\n\""},
		{true, "è\n", "attachment; filename*=utf-8''%C3%A8%0A"},
		{true, "a\nb", "attachment; filename*=utf-8''a%0Ab"},
	} {
		got, ok := contentDisposition(tc.attachment, tc.name)
		if !ok || got == nil || *got != tc.want {
			t.Errorf("contentDisposition(%v, %q) = %v; want %q", tc.attachment, tc.name, got, tc.want)
		}
	}
	if _, ok := contentDisposition(false, ""); ok {
		t.Error("inline without a filename is refused")
	}
}
