package businessmcp

import (
	"context"
	"encoding/json"
	"errors"
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

// A business tool exception whose text Go does not reproduce refuses the MCP
// command: Django answers str(e).
func TestExecuteRefusesTheUnreproducedException(t *testing.T) {
	raised := fmt.Errorf("tool: %w", service.ErrBusinessToolRaised)
	calls := NewCalls(stubBusiness{err: raised}, nil, "")
	_, err := calls.Execute(context.Background(), "sanitize_text", json.RawMessage(`{"text": "a/b"}`), true)
	if !errors.Is(err, service.ErrBusinessMCPNotPorted) {
		t.Fatalf("expected the refusal, got %v", err)
	}
}

func TestExecuteAnswersTheResult(t *testing.T) {
	calls := NewCalls(stubBusiness{result: domain.RequestObject{{Name: "result", Value: "a-b"}}}, nil, "")
	text, err := calls.Execute(context.Background(), "sanitize_text", json.RawMessage(`{"text": "a/b"}`), true)
	if err != nil || text != `{"result": "a-b"}` {
		t.Fatalf("text %q err %v", text, err)
	}
}
