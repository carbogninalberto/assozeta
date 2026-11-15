package service

import (
	"context"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TestImportStartPasswordFile checks start_import's answer when owner_password
// is a file part: kombu's JSON encoder rejects the UploadedFile, whose class
// depends on the request body size (FILE_UPLOAD_MAX_MEMORY_SIZE).
func TestImportStartPasswordFile(t *testing.T) {
	cases := []struct {
		name     string
		bodySize int64
		want     string
	}{
		{"small", 1024, "InMemoryUploadedFile"},
		{"at-limit", domain.RequestUploadMemory, "InMemoryUploadedFile"},
		{"over-limit", domain.RequestUploadMemory + 1, "TemporaryUploadedFile"},
	}
	for _, c := range cases {
		// The branch answers before the saved archive, the cache and the dispatch,
		// so the zero AssociationTransfer with nil options is enough.
		reply, err := (&AssociationTransfer{}).ImportStart(context.Background(), AssociationImportUpload{
			File:     &AssociationImportFile{Name: "archive.zip", Content: []byte("not a zip")},
			Password: &AssociationImportFile{Name: "password.txt", ContentType: "text/plain", Content: []byte("recovery secret")},
			BodySize: c.bodySize,
		})
		if err != nil {
			t.Fatalf("%s: unexpected error %v", c.name, err)
		}
		if reply == nil || reply.Status != 500 {
			t.Fatalf("%s: unexpected reply %+v", c.name, reply)
		}
		body, ok := reply.Body.(map[string]string)
		if !ok {
			t.Fatalf("%s: unexpected body %#v", c.name, reply.Body)
		}
		want := "Errore durante avvio import: Object of type " + c.want + " is not JSON serializable"
		if body["error"] != want {
			t.Errorf("%s: error = %q, want %q", c.name, body["error"], want)
		}
	}
}
