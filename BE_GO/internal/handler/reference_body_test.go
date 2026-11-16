package handler

import (
	"bytes"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/textproto"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// readReferenceMultipart is readReferenceBodyJSON with a fixed boundary.
func readReferenceMultipart(t *testing.T, body []byte) domain.RequestObject {
	t.Helper()
	request := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(body))
	request.Header.Set("Content-Type", "multipart/form-data; boundary=fixture")
	value, err := readReferenceBodyJSON(request, dto.ParseReferenceJSON)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	object, ok := value.(domain.RequestObject)
	if !ok {
		t.Fatalf("got %T, want a form object", value)
	}
	return object
}

func referenceMultipartBody(t *testing.T, headers []textproto.MIMEHeader, contents []string) []byte {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if err := writer.SetBoundary("fixture"); err != nil {
		t.Fatal(err)
	}
	for index, header := range headers {
		part, err := writer.CreatePart(header)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := part.Write([]byte(contents[index])); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return body.Bytes()
}

func TestReferenceBodyMultipartTextDecoding(t *testing.T) {
	for _, tc := range []struct {
		name    string
		header  textproto.MIMEHeader
		content string
		want    string
	}{
		{"invalid utf-8 replaced", textproto.MIMEHeader{
			"Content-Disposition": {`form-data; name="a"`}}, "a\xe2\x82b", "a\ufffdb"},
		{"base64 decoded", textproto.MIMEHeader{
			"Content-Disposition":       {`form-data; name="a"`},
			"Content-Transfer-Encoding": {"base64"}}, "eno=", "zz"},
		{"quoted-printable kept", textproto.MIMEHeader{
			"Content-Disposition":       {`form-data; name="a"`},
			"Content-Transfer-Encoding": {"quoted-printable"}}, "a=3Db", "a=3Db"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			object := readReferenceMultipart(t, referenceMultipartBody(t,
				[]textproto.MIMEHeader{tc.header}, []string{tc.content}))
			value, present := object.Lookup("a")
			if got, _ := value.(string); !present || got != tc.want {
				t.Fatalf("got (%#v, %v), want %q", value, present, tc.want)
			}
		})
	}
}

// Django skips a part whose Content-Disposition line is not valid UTF-8.
func TestReferenceBodyMultipartInvalidDispositionSkipped(t *testing.T) {
	body := []byte("--fixture\r\nContent-Disposition: form-data; name=\"bad\xff\"\r\n\r\nx\r\n" +
		"--fixture\r\nContent-Disposition: form-data; name=\"ok\"\r\n\r\ny\r\n--fixture--\r\n")
	object := readReferenceMultipart(t, body)
	if len(object) != 1 {
		t.Fatalf("got %d fields, want only the part with a valid disposition", len(object))
	}
	if value, present := object.Lookup("ok"); !present || value != "y" {
		t.Fatalf("got (%#v, %v), want the valid part", value, present)
	}
}

// Django's MultiPartParser names an uploaded file sanitize_file_name's name
// (entities unescaped, the text after the last separator, non-printables
// dropped, 255 characters keeping the extension); Go's filepath.Base keeps
// what Django rewrites or skips.
func TestReferenceBodyMultipartUploadName(t *testing.T) {
	for _, tc := range []struct{ name, disposition, want string }{
		{"backslash path", `form-data; name="f"; filename="..\a.txt"`, "a.txt"},
		{"slash path", `form-data; name="f"; filename="dir/sub/a.txt"`, "a.txt"},
		{"html entity", `form-data; name="f"; filename="&amp;b.txt"`, "&b.txt"},
		// The Go reader refuses a raw control byte in a header line; the
		// RFC 2231 form decodes to the same name Django sanitizes.
		{"control character", `form-data; name="f"; filename*=UTF-8''x%01y.txt`, "xy.txt"},
		{"300 characters", `form-data; name="f"; filename="` + strings.Repeat("y", 296) + `.txt"`,
			strings.Repeat("y", 251) + ".txt"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			header := textproto.MIMEHeader{"Content-Disposition": {tc.disposition}}
			object := readReferenceMultipart(t, referenceMultipartBody(t,
				[]textproto.MIMEHeader{header}, []string{"z"}))
			value, present := object.Lookup("f")
			upload, isUpload := value.(domain.RequestUpload)
			if !present || !isUpload || upload.Name != tc.want {
				t.Fatalf("got (%#v, %v), want an upload named %q", value, present, tc.want)
			}
		})
	}
}

// djangoUploadName is sanitize_file_name followed by UploadedFile's name
// handling, on the names the parser hands it.
func TestDjangoUploadName(t *testing.T) {
	long := strings.Repeat("y", 296) + ".txt"
	for _, tc := range []struct{ name, filename, want string }{
		{"backslash path", `..\a.txt`, "a.txt"},
		{"html entity", "&amp;b.txt", "&b.txt"},
		{"control character", "x\x01y.txt", "xy.txt"},
		{"current directory", ".", ""},
		{"parent directory", "..", ""},
		{"empty", "", ""},
		{"300 characters", long, strings.Repeat("y", 251) + ".txt"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, ok := djangoUploadName(tc.filename)
			if !ok || got != tc.want {
				t.Fatalf("got (%q, %v), want %q", got, ok, tc.want)
			}
		})
	}
}

// A filename Django sanitizes away is no part at all: it is not a field,
// and it does not replace a field of the same name.
func TestReferenceBodyMultipartUploadNameSkipped(t *testing.T) {
	for _, filename := range []string{"..", ".", "../", "dir/.."} {
		t.Run(filename, func(t *testing.T) {
			file := textproto.MIMEHeader{"Content-Disposition": {`form-data; name="f"; filename="` + filename + `"`}}
			text := textproto.MIMEHeader{"Content-Disposition": {`form-data; name="f"`}}
			object := readReferenceMultipart(t, referenceMultipartBody(t,
				[]textproto.MIMEHeader{text, file}, []string{"keep", "z"}))
			value, present := object.Lookup("f")
			if !present || value != "keep" {
				t.Fatalf("got (%#v, %v), want the text field only", value, present)
			}
		})
	}
}

func TestReferenceBodyMultipartUploadContentType(t *testing.T) {
	plain, params := "text/plain", "Image/PNG; charset=x"
	for _, tc := range []struct {
		name        string
		contentType *string
		want        string
	}{
		{"plain", &plain, "text/plain"},
		{"parameters lower-cased", &params, "image/png"},
		{"absent", nil, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			header := textproto.MIMEHeader{"Content-Disposition": {`form-data; name="f"; filename="a.txt"`}}
			if tc.contentType != nil {
				header["Content-Type"] = []string{*tc.contentType}
			}
			object := readReferenceMultipart(t, referenceMultipartBody(t,
				[]textproto.MIMEHeader{header}, []string{"z"}))
			value, present := object.Lookup("f")
			upload, isUpload := value.(domain.RequestUpload)
			if !present || !isUpload {
				t.Fatalf("got (%#v, %v), want an upload", value, present)
			}
			if upload.ContentType != tc.want || upload.Size != 1 || upload.Content != "z" || !upload.Whole {
				t.Fatalf("got %#v, want content type %q and the content z", upload, tc.want)
			}
		})
	}
}

// QueryDict falls back to Latin-1 for the raw body, while percent escapes use
// UTF-8 with replacement; a malformed escape is kept.
func TestReferenceBodyURLEncodedReplacement(t *testing.T) {
	request := httptest.NewRequest(http.MethodPost, "/", strings.NewReader("a=%FF&b=x%zz&c=%E2%82"))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	value, err := readReferenceBodyJSON(request, dto.ParseReferenceJSON)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	object, ok := value.(domain.RequestObject)
	if !ok {
		t.Fatalf("got %T, want a form object", value)
	}
	for name, want := range map[string]string{"a": "\ufffd", "b": "x%zz", "c": "\ufffd"} {
		got, present := object.Lookup(name)
		if !present || got != want {
			t.Fatalf("%s: got (%#v, %v), want %q", name, got, present, want)
		}
	}
}

// parseReferenceForm reads a query string as QueryDict does: a malformed escape
// stays as written, each valid escape is decoded, and repeated keys keep every
// value (getlist) with the last one as get().
func TestParseReferenceFormMatchesQueryDict(t *testing.T) {
	query := parseReferenceForm("a=%FF&b=x%zz&c=a%20b%zz&d=1+2&d=3")
	want := []struct {
		name  string
		value string
		all   []any
	}{
		{name: "a", value: "\ufffd"},
		{name: "b", value: "x%zz"},
		{name: "c", value: "a b%zz"},
		{name: "d", value: "3", all: []any{"1 2", "3"}},
	}
	if len(query) != len(want) {
		t.Fatalf("fields = %v, want %d fields", query, len(want))
	}
	for i, field := range want {
		got := query[i]
		if got.Name != field.name || got.Value != field.value {
			t.Fatalf("field %d = %q=%v, want %q=%q", i, got.Name, got.Value, field.name, field.value)
		}
		if len(got.All) != len(field.all) {
			t.Fatalf("field %q All = %v, want %v", field.name, got.All, field.all)
		}
		for j, value := range field.all {
			if got.All[j] != value {
				t.Fatalf("field %q All[%d] = %v, want %v", field.name, j, got.All[j], value)
			}
		}
	}
}
