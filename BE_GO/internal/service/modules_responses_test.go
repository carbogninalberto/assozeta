package service

import (
	"bytes"
	"context"
	"encoding/json"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// The expected values are the view's decode_base64 results on Python 3.13.
func TestPythonB64Decode(t *testing.T) {
	for _, item := range []struct {
		input string
		want  []byte
		fails bool
	}{
		{input: "Y2lhbw==", want: []byte("ciao")},
		{input: "Y2lhbw", want: []byte("ciao")},
		{input: "Y2l!hbw==", want: []byte("ciao")},
		{input: "data:text/plain;base64,Y2lhbw", want: []byte("ciao")},
		{input: "Y2lhbw==Zm", want: []byte("ciao\x06f")},
		{input: "YQ=a=", want: []byte("a\x06")},
		{input: "YW=Jj", want: []byte("abc")},
		{input: "Y2lhbw==Zm9v", fails: true},
		{input: "Y2lhbw==Z", want: []byte("ciao\x06")},
		{input: "abcde", fails: true},
		{input: "Y2l hb w", fails: true},
		{input: "çiao", fails: true},
	} {
		got, err := pythonB64Decode(item.input)
		if item.fails != (err != nil) || !item.fails && !bytes.Equal(got, item.want) {
			t.Fatalf("%q: got %q, %v", item.input, got, err)
		}
	}
}

func TestModuleContains(t *testing.T) {
	if present, _, err := moduleContains([]any{"filename"}, "filename"); !present || err == nil {
		t.Fatal("a list answers membership and raises when indexed")
	}
	if present, _, err := moduleContains([]any{"x"}, "filename"); present || err != nil {
		t.Fatal("a list without the key")
	}
	if _, _, err := moduleContains(nil, "filename"); err == nil {
		t.Fatal("None is not iterable")
	}
}

// attachTx answers the attachment paths under test: the Document create and
// its commit; the other methods stay unimplemented (a call on them panics).
type attachTx struct {
	domain.ModulesResponsesTransaction
	documents []domain.ModuleDocumentCreate
	commits   int
}

func (t *attachTx) CreateDocument(_ context.Context, row domain.ModuleDocumentCreate, _ domain.ModuleResponseAudit) error {
	t.documents = append(t.documents, row)
	return nil
}

func (t *attachTx) Commit() error {
	t.commits++
	return nil
}

func (t *attachTx) Rollback() error { return nil }

// An uploaded file as `filename` stores the name the multipart parser gave it
// (str(UploadedFile)), then raises where os.path.join(storing_path,
// document.filename) does, after the document's commit; a value that is not a
// str keeps its str().
func TestModuleAttachmentFilenameValues(t *testing.T) {
	at := time.Date(2026, time.September, 1, 8, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		name   string
		value  any
		stored string
	}{
		{"upload", domain.RequestUpload{Name: "scan&1.pdf"}, "scan&1.pdf"},
		{"integer", json.Number("5"), "5"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			subject := NewModulesResponses(nil, nil, nil, nil, ModuleAttachmentSettings{},
				func() time.Time { return at }, func() string { return "a1000000-0000-4000-8000-000000644001" })
			tx := &attachTx{}
			body := domain.RequestObject{
				{Name: "filename", Value: tc.value},
				{Name: "base64", Value: "Y2lhbw=="},
			}
			if _, err := subject.attach(context.Background(), tx, "response", body, domain.ModuleResponseAudit{At: at}); err == nil {
				t.Fatal("want the path join's TypeError after the create")
			}
			if len(tx.documents) != 1 || tx.documents[0].Filename != tc.stored {
				t.Fatalf("got %#v, want one document named %q", tx.documents, tc.stored)
			}
			if tx.commits != 1 {
				t.Fatalf("got %d commits, want the document create's commit", tx.commits)
			}
		})
	}
}
