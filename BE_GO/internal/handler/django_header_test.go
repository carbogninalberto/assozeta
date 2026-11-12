package handler

import (
	"errors"

	"encoding/hex"
	"encoding/json"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"os"
	"reflect"
	"strings"
	"testing"
)

// The vectors are the answers of django.utils.http.parse_header_parameters
// (Django 5.2, Python 3.13) to header lines of multipart parts.
func TestDjangoParseHeaderMatchesDjango(t *testing.T) {
	raw, err := os.ReadFile("testdata/django_header_vectors.json")
	if err != nil || len(raw) == 0 {
		t.Fatal("vectors:", err)
	}
	var vectors map[string][]json.RawMessage
	if err = json.Unmarshal(raw, &vectors); err != nil || len(vectors) < 40 {
		t.Fatal("vectors:", err, len(vectors))
	}
	for line, want := range vectors {
		var status string
		_ = json.Unmarshal(want[0], &status)
		main, params, outcome := djangoParseHeader(line)
		if strings.Contains(line, "utf-16") {
			if outcome != headerNotPorted {
				t.Errorf("%q: outcome %v; want not ported", line, outcome)
			}
			continue
		}
		if status == "err" {
			if outcome != headerInvalid {
				t.Errorf("%q: outcome %v; want invalid", line, outcome)
			}
			continue
		}
		var wantMain string
		var wantParams map[string]string
		_ = json.Unmarshal(want[1], &wantMain)
		_ = json.Unmarshal(want[2], &wantParams)
		if outcome != headerParsed || main != wantMain || !reflect.DeepEqual(params, wantParams) {
			t.Errorf("%q: got %q %v %v; want %q %v", line, main, params, outcome, wantMain, wantParams)
		}
	}
}

func TestPyBase64DecodeMatchesPython(t *testing.T) {
	raw, err := os.ReadFile("testdata/py_base64_vectors.json")
	if err != nil || len(raw) == 0 {
		t.Fatal("vectors:", err)
	}
	var vectors map[string][]string
	if err = json.Unmarshal(raw, &vectors); err != nil || len(vectors) < 20 {
		t.Fatal("vectors:", err, len(vectors))
	}
	for input, want := range vectors {
		got, err := pyBase64Decode([]byte(input))
		if want[0] == "err" {
			if err == nil {
				t.Errorf("%q: decoded %x; want an error", input, got)
			}
			continue
		}
		if err != nil || hex.EncodeToString(got) != want[1] {
			t.Errorf("%q: %x %v; want %s", input, got, err, want[1])
		}
	}
}

func TestParseDjangoMultipartHeaderRules(t *testing.T) {
	body := func(lines []string, content string) []byte {
		return []byte("--b\r\n" + strings.Join(lines, "\r\n") + "\r\n\r\n" + content + "\r\n--b--\r\n")
	}
	for name, tc := range map[string]struct {
		lines   []string
		content string
		file    string
		data    string
		skipped bool
		failure string
	}{
		"rfc2231":        {[]string{`Content-Disposition: form-data; name="f"; filename*=utf-8''a%C3%A8.pdf`}, "x", "aè.pdf", "x", false, ""},
		"invalid utf8":   {[]string{"Content-Disposition: form-data; name=\"f\"; filename=\"a\xff.pdf\""}, "x", "", "", true, ""},
		"unknown":        {[]string{`Content-Disposition: form-data; name="f"; filename*=bogus''a.pdf`}, "x", "", "", true, ""},
		"base64 file":    {[]string{`Content-Disposition: form-data; name="f"; filename="a.pdf"`, "Content-Transfer-Encoding: base64"}, "eA==\r\n", "a.pdf", "x", false, ""},
		"base64 failure": {[]string{`Content-Disposition: form-data; name="f"; filename="a.pdf"`, "Content-Transfer-Encoding: base64"}, "eA", "", "", false, "Multipart form parse error - Could not decode base64 data."},
		"other header":   {[]string{`Content-Disposition: form-data; name="f"; filename="a.pdf"`, "Content-Type: a/b\xff"}, "x", "a.pdf", "x", false, ""},
	} {
		object, err := parseDjangoMultipart(body(tc.lines, tc.content), "b")
		if tc.failure != "" {
			var input *restInputError
			if !errors.As(err, &input) || input.Detail != tc.failure {
				t.Errorf("%s: %v", name, err)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %v", name, err)
			continue
		}
		file, found := object.Lookup("f")
		if tc.skipped {
			if found {
				t.Errorf("%s: part kept: %v", name, file)
			}
			continue
		}
		upload, ok := file.(domain.RequestFile)
		if !ok || upload.Name != tc.file || string(upload.Content) != tc.data {
			t.Errorf("%s: %#v", name, file)
		}
	}
}
