package handler

import (
	"bytes"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// A multipart file part keeps only its first 64 KiB chunk, except on the route
// that asks for whole uploads: generate-token-link echoes every line of the
// uploaded gym_name file.
func TestReferenceBodyKeepsWholeUploadsOnRequest(t *testing.T) {
	content := strings.Repeat("riga\n", 14000) // 70,000 bytes, over one chunk
	request := func(whole bool) *http.Request {
		buffer := &bytes.Buffer{}
		writer := multipart.NewWriter(buffer)
		part, err := writer.CreateFormFile("gym_name", "palestra.txt")
		if err != nil {
			t.Fatal(err)
		}
		if _, err = part.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
		if err = writer.Close(); err != nil {
			t.Fatal(err)
		}
		result := httptest.NewRequest("POST", "/subscription/generate-token-link", buffer)
		result.Header.Set("Content-Type", writer.FormDataContentType())
		if whole {
			result = result.WithContext(withWholeUploads(result.Context()))
		}
		return result
	}
	upload := func(whole bool) domain.RequestUpload {
		t.Helper()
		parsed, err := readReferenceBody(request(whole))
		if err != nil {
			t.Fatal("readReferenceBody:", err)
		}
		value, present := parsed.(domain.RequestObject).Lookup("gym_name")
		if !present {
			t.Fatal("no gym_name field")
		}
		result, ok := value.(domain.RequestUpload)
		if !ok {
			t.Fatalf("gym_name = %T, want an upload", value)
		}
		return result
	}

	first := upload(false)
	if first.Whole || first.Content != "" || first.Size != int64(len(content)) {
		t.Errorf("without the opt-in: Whole = %v, %d content bytes, size %d", first.Whole, len(first.Content), first.Size)
	}
	whole := upload(true)
	if !whole.Whole || whole.Content != content || whole.Size != int64(len(content)) {
		t.Errorf("with the opt-in: Whole = %v, %d content bytes, size %d", whole.Whole, len(whole.Content), whole.Size)
	}
	if whole.Name != "palestra.txt" {
		t.Errorf("name = %q", whole.Name)
	}
}
