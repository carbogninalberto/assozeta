package service

import (
	"context"
	"errors"
)

// ErrBusinessToolRaised is an exception raised inside a business tool whose
// str(e) Go does not reproduce (a PostgreSQL error text carries the statement;
// some exporter texts carry object addresses). A saved-report run answers every
// exception with its JSON 500, the MCP command refuses the call, the agent
// answers X11's generic tool error.
var ErrBusinessToolRaised = errors.New("business tool raised an exception whose text is not reproduced")

// The agent (application.agent.core._execute_tool) renders a tool's result
// and an exception the tool raised differently: json.dumps(result,
// ensure_ascii=False) versus json.dumps({'error': str(e)}) with the field
// hint. The MCP transport renders both alike, so the tools only record that
// the {"error": ...} value they return stands for a raised exception.

type mcpRaisedKey struct{}

// WithMCPRaised returns a context in which the business tools set *raised
// when their {"error": ...} result stands for a raised exception.
func WithMCPRaised(ctx context.Context, raised *bool) context.Context {
	return context.WithValue(ctx, mcpRaisedKey{}, raised)
}

func markMCPRaised(ctx context.Context) {
	if raised, ok := ctx.Value(mcpRaisedKey{}).(*bool); ok && raised != nil {
		*raised = true
	}
}
