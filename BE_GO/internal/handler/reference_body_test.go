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
