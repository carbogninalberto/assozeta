package handler

import (
	"bytes"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"testing"

	"github.com/labstack/echo/v4"
)

// multipartUploadBody is one form: a text field and a file.
func multipartUploadBody(t *testing.T) ([]byte, string) {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if err := writer.WriteField("filename", "regolamento.pdf"); err != nil {
		t.Fatal(err)
	}
	part, err := writer.CreateFormFile("document", "regolamento.pdf")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = part.Write([]byte("Regolamento")); err != nil {
		t.Fatal(err)
	}
	if err = writer.Close(); err != nil {
		t.Fatal(err)
	}
	return body.Bytes(), writer.FormDataContentType()
}

// bufferMultipartContext is the request as a client sends it; length -1 is a
// chunked request that names no Content-Length.
func bufferMultipartContext(body []byte, contentType string, length int64) echo.Context {
	request := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(body))
	request.Header.Set("Content-Type", contentType)
	request.ContentLength = length
	return echo.New().NewContext(request, httptest.NewRecorder())
}

// A body over uploadBodyLimit is spooled to a temporary file and mapped: the
// parse equals the in-memory one and the cleanup deletes the file.
func TestBufferMultipartSpoolsLargeBody(t *testing.T) {
	body, contentType := multipartUploadBody(t)
	_, boundaryParams, err := mime.ParseMediaType(contentType)
	if err != nil {
		t.Fatal(err)
	}
	boundary := boundaryParams["boundary"]
	limit := uploadBodyLimit
	t.Cleanup(func() { uploadBodyLimit = limit })

	// The in-memory path parses the same body.
	uploadBodyLimit = len(body)
	context := bufferMultipartContext(body, contentType, int64(len(body)))
	raw, cleanup, err := bufferMultipart(context)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	reference, err := parseDjangoMultipart(raw, boundary)
	if err != nil {
		t.Fatal(err)
	}

	for _, tc := range []struct {
		name   string
		length int64
	}{
		{"content-length", int64(len(body))},
		{"chunked", -1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			uploadBodyLimit = len(body) - 1
			directory := t.TempDir()
			t.Setenv("TMPDIR", directory)
			context := bufferMultipartContext(body, contentType, tc.length)
			raw, cleanup, err := bufferMultipart(context)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(raw, body) {
				t.Fatalf("buffered %d bytes, want %d", len(raw), len(body))
			}
			// The body is on disk while the mapping is live.
			if files, err := os.ReadDir(directory); err != nil || len(files) != 1 {
				t.Fatalf("temporary files %v, err %v", files, err)
			}
			parsed, err := parseDjangoMultipart(raw, boundary)
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(parsed, reference) {
				t.Fatalf("parsed %+v, want %+v", parsed, reference)
			}
			cleanup()
			if files, err := os.ReadDir(directory); err != nil || len(files) != 0 {
				t.Fatalf("temporary files after cleanup %v, err %v", files, err)
			}
		})
	}
}
