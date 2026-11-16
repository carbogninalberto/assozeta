package businessmcp

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
)

// stubBusiness answers one business call with a fixed result or error.
type stubBusiness struct {
	result any
	err    error
}

func (s stubBusiness) Execute(context.Context, string, domain.RequestObject) (any, error) {
	return s.result, s.err
}

// A business tool exception answers Django's {"error": str(e)}: the raised
// error carries the closest text.
func TestExecuteAnswersTheRaisedExceptionText(t *testing.T) {
	raised := &service.BusinessToolRaised{Text: "operator does not exist: text ~ integer\nHINT:  No operator matches the given name and argument types.\n"}
	calls := NewCalls(stubBusiness{err: raised}, nil, "")
	text, err := calls.Execute(context.Background(), "sanitize_text", json.RawMessage(`{"text": "a/b"}`), true)
	if err != nil {
		t.Fatalf("call: %v", err)
	}
	want := errorText("operator does not exist: text ~ integer\nHINT:  No operator matches the given name and argument types.\n")
	if text != want {
		t.Fatalf("text %q, want %q", text, want)
	}
}

// A raised exception without a text still answers the generic tool error.
func TestExecuteAnswersTheGenericText(t *testing.T) {
	calls := NewCalls(stubBusiness{err: fmt.Errorf("tool: %w", service.ErrBusinessToolRaised)}, nil, "")
	text, err := calls.Execute(context.Background(), "sanitize_text", json.RawMessage(`{"text": "a/b"}`), true)
	if err != nil {
		t.Fatalf("call: %v", err)
	}
	want := errorText("Errore durante l'esecuzione dello strumento.")
	if text != want {
		t.Fatalf("text %q, want %q", text, want)
	}
}

func TestExecuteAnswersTheResult(t *testing.T) {
	calls := NewCalls(stubBusiness{result: domain.RequestObject{{Name: "result", Value: "a-b"}}}, nil, "")
	text, err := calls.Execute(context.Background(), "sanitize_text", json.RawMessage(`{"text": "a/b"}`), true)
	if err != nil || text != `{"result": "a-b"}` {
		t.Fatalf("text %q err %v", text, err)
	}
}
