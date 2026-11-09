package service

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"math/big"
	"strings"
	"sync"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// This file is BE/application/agent/core.py (Agent) with prompts.py.

//go:embed agent_system_prompt.txt
var agentSystemPrompt string

// AgentSystemPrompt is get_system_prompt(association_name).
func AgentSystemPrompt(name any) string {
	return strings.ReplaceAll(agentSystemPrompt, "{association_name}", dto.PythonStr(name))
}

// ErrAgentInternal is an exception escaping process_message (the consumer
// answers "Si è verificato un errore interno").
var ErrAgentInternal = errors.New("agent processing error")

const (
	agentMaxMessageLength = 2000
	agentMaxToolResult    = 15000
	agentTemporaryError   = "Si e' verificato un problema temporaneo. Riprova tra qualche secondo."
	agentLimitError       = "Ho raggiunto il limite massimo di operazioni. " +
		"Prova a riformulare la richiesta in modo più specifico."
	agentLoopNudge = "[SISTEMA: Stai ripetendo la stessa operazione. " +
		"FERMATI e rispondi all'utente con le informazioni che hai. " +
		"Se non hai dati sufficienti, spiega il problema e suggerisci " +
		"di riformulare la domanda. NON chiamare strumenti.]"
	agentTruncatedSuffix = "\n\n... [risultato troncato, usa filtri più specifici o limit per ridurre i dati]"
)

// AgentCallback is the AgentCallback protocol (WebSocket or CLI).
type AgentCallback interface {
	OnStatus(ctx context.Context, status string) error
	OnToolCall(ctx context.Context, name, arguments any) error
	OnMessageChunk(ctx context.Context, chunk string) error
	OnMessageEnd(ctx context.Context) error
	OnManualSection(ctx context.Context, section any) error
	OnExportReady(ctx context.Context, export domain.RequestObject) error
	OnReportSaved(ctx context.Context, report domain.RequestObject) error
	OnError(ctx context.Context, message string) error
	OnDone(ctx context.Context) error
}

// AgentToolRunner runs the tool functions for one association.
type AgentToolRunner interface {
	// Business calls a business tool; raised is true when the {"error": ...}
	// result stands for an exception (rendered with json.dumps({'error': ...})).
	Business(ctx context.Context, name string, arguments domain.RequestObject) (value any, raised bool, err error)
	// Manual calls a manual tool function; the result keeps Python's key order.
	Manual(ctx context.Context, name string, arguments domain.RequestObject) (any, error)
	// Coordinate is coordinated_sync's lease: busy is the Busy message.
	Coordinate(ctx context.Context) (release func(), busy string, err error)
}

// Agent is application.agent.core.Agent.
type Agent struct {
	associationID string
	userID        string // "" is None
	Provider      AgentProvider
	callback      AgentCallback
	tools         AgentToolRunner
	toolSchemas   []any
	MaxIterations any
	HistoryCap    any
	manual        bool

	schemaCache *string
	recent      []string
	// mu guards messages and pending: clear_history runs between the awaits
	// of a running request in Django and replaces the list it appends to.
	mu       sync.Mutex
	pending  bool
	messages []any
}

func (a *Agent) add(message any) {
	a.mu.Lock()
	a.messages = append(a.messages, message)
	a.mu.Unlock()
}

func (a *Agent) snapshot() []any {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]any(nil), a.messages...)
}

func (a *Agent) setPending(value bool) {
	a.mu.Lock()
	a.pending = value
	a.mu.Unlock()
}

// AgentOptions are Agent.__init__'s arguments besides the provider and callback.
type AgentOptions struct {
	AssociationID   string
	AssociationName any
	UserID          string
	Config          map[string]any // ai_config (nil: {})
	// DefaultIterations and DefaultHistoryCap are MCP_AGENT_MAX_ITERATIONS and MCP_AGENT_HISTORY_CAP.
	DefaultIterations, DefaultHistoryCap int64
	ManualAvailable                      bool
	Tools                                AgentToolRunner
	ToolSchemas                          []any
}

func NewAgent(provider AgentProvider, callback AgentCallback, options AgentOptions) *Agent {
	agent := &Agent{associationID: options.AssociationID, userID: options.UserID, Provider: provider, callback: callback,
		tools: options.Tools, toolSchemas: options.ToolSchemas, manual: options.ManualAvailable}
	agent.MaxIterations = json.Number(big.NewInt(options.DefaultIterations).String())
	if value := options.Config["max_iterations"]; dto.PythonTruthy(value) {
		agent.MaxIterations = value
	}
	agent.HistoryCap = json.Number(big.NewInt(options.DefaultHistoryCap).String())
	if value := options.Config["history_cap"]; dto.PythonTruthy(value) {
		agent.HistoryCap = value
	}
	agent.messages = []any{agentObject("role", "system", "content", AgentSystemPrompt(options.AssociationName))}
	return agent
}

func agentObject(fields ...any) domain.RequestObject {
	result := make(domain.RequestObject, 0, len(fields)/2)
	for i := 0; i < len(fields); i += 2 {
		result = append(result, domain.RequestField{Name: fields[i].(string), Value: fields[i+1]})
	}
	return result
}

// ClearHistory is clear_history.
func (a *Agent) ClearHistory() {
	a.mu.Lock()
	a.messages = []any{a.messages[0]}
	a.pending = false
	a.mu.Unlock()
}

// agentInt reads an integer configuration value (range()/comparison operand).
func agentInt(value any) (int, error) {
	number, ok := value.(json.Number)
	if !ok {
		if integer, ok := value.(int64); ok {
			return int(integer), nil
		}
		return 0, ErrAgentNotPorted
	}
	parsed, ok := new(big.Int).SetString(string(number), 10)
	if !ok || !parsed.IsInt64() || parsed.Int64() > 1<<30 || parsed.Int64() < -(1<<30) {
		return 0, ErrAgentNotPorted
	}
	return int(parsed.Int64()), nil
}

// pythonTail is list[-n:].
func pythonTail(items []any, n int) []any {
	switch {
	case n == 0:
		return items
	case n > 0:
		if n >= len(items) {
			return items
		}
		return items[len(items)-n:]
	default:
		if -n >= len(items) {
			return nil
		}
		return items[-n:]
	}
}

func (a *Agent) trimHistory() error {
	a.mu.Lock()
	defer a.mu.Unlock()
	limit, err := agentInt(a.HistoryCap)
	if err != nil {
		return err
	}
	if len(a.messages) <= limit {
		return nil
	}
	system := a.messages[0]
	recent := append([]any(nil), pythonTail(a.messages, limit-1)...)
	a.messages = append([]any{system}, recent...)
	return nil
}

// agentBound is _bound_input: str.strip() then the first 2000 characters.
func agentBound(text string) string {
	points := dto.AgentCodePoints(agentStrip(text))
	if len(points) > agentMaxMessageLength {
		points = points[:agentMaxMessageLength]
	}
	return dto.AgentFromCodePoints(points)
}

// agentHTMLEscape is html.escape(s, quote=True).
func agentHTMLEscape(text string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#x27;").Replace(text)
}

func agentTruncate(result string) string {
	points := dto.AgentCodePoints(result)
	if len(points) <= agentMaxToolResult {
		return result
	}
	return dto.AgentFromCodePoints(points[:agentMaxToolResult]) + agentTruncatedSuffix
}

// ProcessMessage is process_message: unresolved state is discarded when the
// processing fails or is cancelled.
func (a *Agent) ProcessMessage(ctx context.Context, message string) error {
	err := a.processMessage(ctx, message)
	if err != nil {
		a.setPending(false)
	}
	return err
}

func (a *Agent) directAnswer(ctx context.Context, answer string, attachment any) error {
	a.add(agentObject("role", "assistant", "content", answer))
	if err := a.callback.OnMessageChunk(ctx, answer); err != nil {
		return err
	}
	if attachment != nil {
		if err := a.callback.OnManualSection(ctx, attachment); err != nil {
			return err
		}
	}
	if err := a.callback.OnMessageEnd(ctx); err != nil {
		return err
	}
	return a.callback.OnDone(ctx)
}

func (a *Agent) processMessage(ctx context.Context, message string) error {
	bounded := agentBound(message)
	a.add(agentObject("role", "user", "content", agentHTMLEscape(bounded)))
	if err := a.trimHistory(); err != nil {
		return err
	}
	a.recent = a.recent[:0]
	var exports, saves []domain.RequestObject
	if err := a.callback.OnStatus(ctx, "thinking"); err != nil {
		return err
	}
	if dto.AgentHasSurrogate(bounded) {
		return ErrAgentNotPorted
	}
	a.mu.Lock()
	pending := a.pending
	a.mu.Unlock()
	route := agentRouteMessage(bounded, pending)
	aiEnabled := a.Provider != nil
	if route.intent == intentAction || (route.intent == intentAmbiguous && aiEnabled) {
		a.setPending(true)
	} else if route.intent != intentAmbiguous {
		a.setPending(false)
	}
	savedReports := a.userID != ""
	switch {
	case route.intent == intentCapabilities:
		return a.directAnswer(ctx, agentCapabilityAnswer(aiEnabled, a.manual, savedReports), nil)
	case route.intent == intentConversation:
		capabilities := agentCapabilityAnswer(aiEnabled, a.manual, savedReports)
		normalized := agentCasefold(route.query)
		answer := "Ciao! " + capabilities
		if agentContainsAny(normalized, "grazie", "perfetto", "ottimo", "capito", "va bene", "ok") {
			answer = "Prego. " + capabilities
		} else if agentContainsAny(normalized, "arrivederci", "a presto", "buona giornata") {
			answer = "A presto. " + capabilities
		}
		return a.directAnswer(ctx, answer, nil)
	case route.intent == intentAction && !aiEnabled:
		a.setPending(false)
		return a.directAnswer(ctx, agentManualOnlyActionAnswer(a.manual), nil)
	case route.intent == intentManualDiagnostic || route.intent == intentManualFAQ || (route.intent == intentAmbiguous && !aiEnabled):
		if err := a.callback.OnStatus(ctx, "querying"); err != nil {
			return err
		}
		diagnostic := route.intent == intentManualDiagnostic
		tool, arguments := "search_manual", agentObject("query", route.query)
		if diagnostic {
			tool, arguments = "get_manual_gaps", domain.RequestObject{}
		}
		if err := a.callback.OnToolCall(ctx, tool, arguments); err != nil {
			return err
		}
		text, err := a.executeTool(ctx, tool, arguments)
		if err != nil {
			return err
		}
		result, err := dto.ParsePythonJSON(text)
		if err != nil {
			return err
		}
		object, ok := result.(domain.RequestObject)
		if !ok {
			return ErrAgentNotPorted
		}
		var answer string
		var attachment any
		if diagnostic {
			answer, err = agentManualDiagnosticAnswer(object)
		} else {
			answer, err = agentGroundedAnswer(object)
			if err == nil {
				attachment, err = agentManualAttachment(object)
			}
		}
		if err != nil {
			return err
		}
		if route.intent == intentAmbiguous && !dto.PythonTruthy(lookupValue(object, "results")) {
			answer = agentAmbiguousManualAnswer
		}
		return a.directAnswer(ctx, answer, attachment)
	}
	iterations, err := agentInt(a.MaxIterations)
	if err != nil {
		return err
	}
	exhausted := true
	for iteration := 0; iteration < iterations; iteration++ {
		response, err := a.Provider.ChatStream(ctx, a.snapshot(), a.toolSchemas, func(chunk string) error {
			return a.callback.OnMessageChunk(ctx, chunk)
		})
		if err != nil {
			return err
		}
		if response.failed() {
			a.setPending(false)
			if err := a.callback.OnError(ctx, agentTemporaryError); err != nil {
				return err
			}
			exhausted = false
			break
		}
		if response.Content != "" {
			if err := a.callback.OnMessageEnd(ctx); err != nil {
				return err
			}
		}
		if len(response.ToolCalls) == 0 {
			if response.Content != "" {
				a.add(agentObject("role", "assistant", "content", response.Content))
				a.setPending(agentRequiresFollowUp(response.Content))
			} else {
				a.setPending(false)
			}
			exhausted = false
			break
		}
		var content any
		if response.Content != "" {
			content = response.Content
		}
		calls := make([]any, 0, len(response.ToolCalls))
		for _, call := range response.ToolCalls {
			arguments, err := dto.AgentJSON{Item: ", ", Key: ": "}.Dumps(call.Arguments)
			if err != nil {
				return err
			}
			calls = append(calls, agentObject("id", call.ID, "type", "function", "function", agentObject("name", call.Name, "arguments", arguments)))
		}
		a.add(agentObject("role", "assistant", "content", content, "tool_calls", calls))
		for _, call := range response.ToolCalls {
			if err := a.callback.OnStatus(ctx, "querying"); err != nil {
				return err
			}
			if err := a.callback.OnToolCall(ctx, call.Name, call.Arguments); err != nil {
				return err
			}
			result, err := a.executeTool(ctx, call.Name, call.Arguments)
			if err != nil {
				return err
			}
			a.recent = append(a.recent, dto.PythonStr(call.Name)+":"+string(dto.PythonJSONDumpsSorted(call.Arguments)))
			a.add(agentObject("role", "tool", "tool_call_id", call.ID, "content", agentTruncate(result)))
			name, _ := call.Name.(string)
			switch name {
			case "export_data", "get_attendance_matrix", "export_multi_sheet":
				if parsed, err := dto.ParsePythonJSON(result); err == nil {
					if object, ok := parsed.(domain.RequestObject); ok {
						if _, present := object.Lookup("data_base64"); present {
							exports = append(exports, object)
						}
					}
				}
			case "save_report":
				if parsed, err := dto.ParsePythonJSON(result); err == nil {
					if object, ok := parsed.(domain.RequestObject); ok {
						if _, present := object.Lookup("saved_report_id"); present {
							saves = append(saves, object)
						}
					}
				}
			}
		}
		if n := len(a.recent); n >= 3 && a.recent[n-1] == a.recent[n-2] && a.recent[n-2] == a.recent[n-3] {
			a.recent = a.recent[:0]
			messages := append(a.snapshot(), agentObject("role", "user", "content", agentLoopNudge))
			forced, err := a.Provider.ChatStream(ctx, messages, nil, func(chunk string) error {
				return a.callback.OnMessageChunk(ctx, chunk)
			})
			if err != nil {
				return err
			}
			if forced.failed() {
				// The error text would become an assistant message.
				return ErrAgentNotPorted
			}
			a.setPending(agentRequiresFollowUp(forced.Content))
			if forced.Content != "" {
				if err := a.callback.OnMessageEnd(ctx); err != nil {
					return err
				}
				a.add(agentObject("role", "assistant", "content", forced.Content))
			}
			exhausted = false
			break
		}
	}
	if exhausted {
		a.setPending(false)
		if err := a.callback.OnError(ctx, agentLimitError); err != nil {
			return err
		}
	}
	for _, export := range exports {
		if err := a.callback.OnExportReady(ctx, export); err != nil {
			return err
		}
	}
	for _, report := range saves {
		if err := a.callback.OnReportSaved(ctx, report); err != nil {
			return err
		}
	}
	return a.callback.OnDone(ctx)
}

// agentManualParameters is MANUAL_TOOL_PARAMETERS; agentManualRequired the
// positional parameters of the manual tool functions.
var agentManualParameters = map[string][]string{"search_manual": {"query", "limit"}, "get_manual_section": {"section_id"},
	"get_manual_evidence": {"section_id"}, "get_manual_gaps": {"page"}}
var agentManualRequired = map[string]string{"search_manual": "query", "get_manual_section": "section_id", "get_manual_evidence": "section_id"}

func agentKnownTool(name string) bool {
	if _, manual := agentManualParameters[name]; manual {
		return true
	}
	for _, tool := range BusinessToolNames {
		if tool == name {
			return true
		}
	}
	return false
}

var agentResultJSON = dto.AgentJSON{Item: ", ", Key: ": "}

func agentRaisedText(message string) string {
	if strings.Contains(message, "Cannot resolve keyword") && strings.Contains(message, "Choices are:") {
		message += ". HINT: To access fields on related models, use double underscore " +
			"traversal (e.g. subscription__associate__first_name). " +
			"Check get_schema for the correct relation names."
	}
	return string(dto.PythonJSONDumps(agentObject("error", message)))
}

// executeTool is _execute_tool: the JSON text the tool message carries.
func (a *Agent) executeTool(ctx context.Context, name any, arguments any) (string, error) {
	tool, isString := name.(string)
	if !isString || !agentKnownTool(tool) {
		return string(dto.PythonJSONDumps(agentObject("error", "Unknown tool: "+dto.PythonStr(name)))), nil
	}
	object, isObject := arguments.(domain.RequestObject)
	if tool == "get_schema" && a.schemaCache != nil {
		if !isObject {
			return "", ErrAgentInternal
		}
		if !dto.PythonTruthy(lookupValue(object, "model_name")) {
			return *a.schemaCache, nil
		}
	}
	if !isObject {
		return "", ErrAgentInternal // arguments.items() on a non-dict
	}
	prepared := domain.RequestObject{}
	for _, field := range dto.PythonDict(object) {
		if field.Value != nil {
			prepared = append(prepared, field)
		}
	}
	allowed, manual := agentManualParameters[tool]
	if manual {
		filtered := domain.RequestObject{}
		for _, field := range prepared {
			for _, key := range allowed {
				if field.Name == key {
					filtered = append(filtered, field)
				}
			}
		}
		var user any
		if a.userID != "" {
			user = a.userID
		}
		prepared = dto.PythonSet(filtered, "user_id", user)
	}
	prepared = dto.PythonSet(prepared, "sport_association_id", a.associationID)
	if a.userID != "" && (tool == "save_report" || tool == "list_reports") {
		prepared = dto.PythonSet(prepared, "user_id", a.userID)
	}
	release, busy, err := a.tools.Coordinate(ctx)
	if err != nil {
		return "", err
	}
	if busy != "" {
		return agentRaisedText(busy), nil
	}
	defer release()
	var value any
	raised := false
	if manual {
		if required, ok := agentManualRequired[tool]; ok {
			if _, present := prepared.Lookup(required); !present {
				return agentRaisedText("tool_" + tool + "() missing 1 required positional argument: '" + required + "'"), nil
			}
		}
		value, err = a.tools.Manual(ctx, tool, prepared)
	} else {
		if message, missing := BusinessBindingError(tool, prepared); missing {
			return agentRaisedText(message), nil
		}
		value, raised, err = a.tools.Business(ctx, tool, prepared)
	}
	if err != nil {
		return "", err
	}
	if raised {
		message, ok := lookupValue(value.(domain.RequestObject), "error").(string)
		if !ok {
			return "", ErrAgentNotPorted
		}
		return agentRaisedText(message), nil
	}
	text, err := agentResultJSON.Dumps(value)
	if err != nil {
		return "", err
	}
	if tool == "get_schema" && !dto.PythonTruthy(lookupValue(prepared, "model_name")) {
		a.schemaCache = &text
	}
	return text, nil
}
