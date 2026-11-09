package businessmcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler/manualmcp"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler/mcpstdio"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
)

// ServerName is create_mcp_server's Server("bakney-sport-data").
const ServerName = "bakney-sport-data"

// SDKVersion is the serverInfo version the Python server reports: the
// installed MCP SDK's distribution version (mcp 1.30.0, BE/requirements).
const SDKVersion = "1.30.0"

// manualParameters is MANUAL_TOOL_PARAMETERS: the only arguments call_tool
// forwards to a manual tool.
var manualParameters = map[string][]string{"search_manual": {"query", "limit"}, "get_manual_section": {"section_id"},
	"get_manual_evidence": {"section_id"}, "get_manual_gaps": {"page"}}

// BusinessTools runs one business tool function.
type BusinessTools interface {
	Execute(ctx context.Context, name string, arguments domain.RequestObject) (any, error)
}

// ManualTools runs one manual tool with the public stdio identity.
type ManualTools interface {
	Execute(ctx context.Context, identity domain.ResolvedIdentity, associationID, name string, arguments map[string]any) (map[string]any, error)
}

// Calls is create_mcp_server's call_tool for one association.
type Calls struct {
	business    BusinessTools
	manual      ManualTools
	association string
}

func NewCalls(business BusinessTools, manual ManualTools, association string) *Calls {
	return &Calls{business: business, manual: manual, association: association}
}

func dumps(value any) string { return string(dto.PythonJSONDumps(value)) }

func errorText(message string) string {
	return dumps(domain.RequestObject{{Name: "error", Value: message}})
}

// Validate is the SDK's input validation: the manual tools' authored
// validation, jsonschema with best_match for the business tools.
func Validate(name string, raw json.RawMessage) error {
	if _, manual := manualParameters[name]; manual {
		return manualmcp.ValidateJSON(name, raw)
	}
	message, err := ValidateArguments(name, raw)
	if err != nil {
		return err
	}
	if message != "" {
		return errors.New(message)
	}
	return nil
}

// Execute answers a call with call_tool's text content: the result as
// json.dumps(result, default=str), {"error": str(e)} for exceptions.
func (c *Calls) Execute(ctx context.Context, name string, raw json.RawMessage, known bool) (string, error) {
	if !known {
		return errorText("Unknown tool: " + name), nil
	}
	arguments := domain.RequestObject{}
	if len(raw) > 0 && string(raw) != "null" {
		parsed, err := pythonLoads(raw)
		if err != nil {
			return "", err
		}
		object, ok := parsed.(domain.RequestObject)
		if !ok {
			return "", errors.New("business MCP arguments must be an object")
		}
		arguments = object
	}
	if allowed, manual := manualParameters[name]; manual {
		return c.executeManual(ctx, name, raw, allowed)
	}
	// _call(fn, sport_association_id=..., **arguments): the keyword merge
	// fails before binding; sanitize_text gets no association.
	if _, present := arguments.Lookup("sport_association_id"); present && name != "sanitize_text" {
		return errorText("application.mcp_server.server.create_mcp_server.<locals>.call_tool.<locals>._call() " +
			"got multiple values for keyword argument 'sport_association_id'"), nil
	}
	if _, present := arguments.Lookup("fn"); present {
		return errorText("create_mcp_server.<locals>.call_tool.<locals>._call() got multiple values for argument 'fn'"), nil
	}
	if message, missing := service.BusinessBindingError(name, arguments); missing {
		return errorText(message), nil
	}
	result, err := c.business.Execute(ctx, name, arguments)
	if err != nil {
		return "", err
	}
	return dumps(result), nil
}

// executeManual forwards only MANUAL_TOOL_PARAMETERS; the manual result keeps
// the Go service's key order (its parity is semantic, see manual-reader.md).
func (c *Calls) executeManual(ctx context.Context, name string, raw json.RawMessage, allowed []string) (string, error) {
	var all map[string]any
	if len(raw) > 0 && string(raw) != "null" {
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		if err := decoder.Decode(&all); err != nil {
			return "", err
		}
	}
	arguments := map[string]any{}
	for _, key := range allowed {
		if value, present := all[key]; present {
			arguments[key] = value
		}
	}
	result, err := c.manual.Execute(ctx, domain.ResolvedIdentity{}, c.association, name, arguments)
	if err != nil {
		return "", err
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		return "", err
	}
	parsed, err := dto.ParseReferenceJSON(encoded)
	if err != nil {
		return "", err
	}
	return dumps(parsed), nil
}

// Tools are the declared tools for the stdio transport.
func Tools() []mcpstdio.Tool {
	tools := make([]mcpstdio.Tool, 0, len(declarations))
	for _, item := range declarations {
		var schema map[string]any
		_ = json.Unmarshal(item.InputSchema, &schema)
		tools = append(tools, mcpstdio.Tool{Name: item.Name, Description: item.Description, InputSchema: schema,
			RawInputSchema: item.InputSchema})
	}
	return tools
}
