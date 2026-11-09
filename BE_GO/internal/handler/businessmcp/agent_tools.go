package businessmcp

import (
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// AgentToolSchemas is application.agent.core.TOOLS_FOR_LLM: every
// TOOL_DEFINITIONS entry as an OpenAI function tool, schemas in their order.
func AgentToolSchemas() []any {
	tools := make([]any, 0, len(declarations))
	for _, item := range declarations {
		schema, err := dto.ParsePythonJSON(string(item.InputSchema))
		if err != nil {
			panic("business MCP schema: " + err.Error())
		}
		tools = append(tools, domain.RequestObject{{Name: "type", Value: "function"}, {Name: "function", Value: domain.RequestObject{
			{Name: "name", Value: item.Name}, {Name: "description", Value: item.Description}, {Name: "parameters", Value: schema}}}})
	}
	return tools
}
