package service

import "context"

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
