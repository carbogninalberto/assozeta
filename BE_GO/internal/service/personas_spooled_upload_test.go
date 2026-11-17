package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// AssociateSerializer.to_internal_value starts with data.copy(). For a form body
// that is QueryDict.copy(), a deepcopy: a spooled file (TemporaryUploadedFile,
// a request over FILE_UPLOAD_MAX_MEMORY_SIZE) cannot be copied and the view
// raises before anything is written (Django's 500 page). An in-memory file
// copies fine.
func TestPersonaStockCreateSpooledUpload(t *testing.T) {
	spooled := domain.RequestUpload{Name: "doc.pdf", Size: 3 << 20, Temp: true}
	inMemory := domain.RequestUpload{Name: "doc.pdf", Size: 10}
	cases := []struct {
		name string
		body domain.RequestObject
		want bool
	}{
		{"spooled value", domain.RequestObject{{Name: "name", Value: "A"}, {Name: "doc", Value: spooled}}, true},
		{"spooled in a repeated key", domain.RequestObject{{Name: "doc", Value: "x", All: []any{"x", spooled}}}, true},
		{"spooled picture_path", domain.RequestObject{{Name: "picture_path", Value: spooled}}, true},
		{"in memory", domain.RequestObject{{Name: "doc", Value: inMemory}}, false},
		{"no file", domain.RequestObject{{Name: "name", Value: "A"}}, false},
	}
	for _, tc := range cases {
		if got := personaFormHasSpooledUpload(tc.body); got != tc.want {
			t.Fatalf("%s: personaFormHasSpooledUpload = %v, want %v", tc.name, got, tc.want)
		}
	}
	// The copy fails before any other check and before the transaction starts:
	// a zero PersonaWrites has no database, so reaching begin would panic.
	writes := &PersonaWrites{}
	body := domain.RequestObject{{Name: "picture_path", Value: spooled}, {Name: "tutors_data", Value: spooled}}
	if _, err := writes.StockCreate(context.Background(), domain.ResolvedIdentity{}, body, true, "", domain.LoginAuditContext{}); !errors.Is(err, ErrPersonaUnhandled) {
		t.Fatalf("StockCreate with a spooled form file: err %v, want ErrPersonaUnhandled", err)
	}
}
