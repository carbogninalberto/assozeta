package service

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"sort"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/agentllm"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrAgentNotPorted refuses an agent input whose Django behaviour is not
// reproduced (see ws-agent.md); the caller ends the session.
var ErrAgentNotPorted = refusal.New("agent input not ported")

// AgentEnvironment are the core.settings values AIProvider falls back to.
type AgentEnvironment struct {
	AIAPIKey, AIModel, AIBaseURL string
	// AIAPIKeySet is AI_API_KEY present (env.str default None).
	AIAPIKeySet bool
}

// AgentToolCall is providers.base.ToolCall with Python values.
type AgentToolCall struct {
	ID, Name, Arguments any
}

// AgentLLMResponse is providers.base.LLMResponse.
type AgentLLMResponse struct {
	Content      string
	ToolCalls    []AgentToolCall
	FinishReason any
}

func (r AgentLLMResponse) failed() bool { s, ok := r.FinishReason.(string); return ok && s == "error" }

// AgentProvider is the agent's LLMProvider.chat_stream.
type AgentProvider interface {
	ChatStream(ctx context.Context, messages []any, tools []any, onChunk func(string) error) (AgentLLMResponse, error)
}

// AgentAIProvider is providers.ai_provider.AIProvider.
type AgentAIProvider struct {
	client *agentllm.Client
	model  any
}

// NewAgentAIProvider is AIProvider(api_key=..., model=..., base_url=...):
// errAgentProviderKey is the ValueError for a missing key.
func NewAgentAIProvider(env AgentEnvironment, apiKey, model, baseURL any, transport func(*agentllm.Config)) (*AgentAIProvider, error) {
	key := apiKey
	if !dto.PythonTruthy(key) {
		key = nil
		if env.AIAPIKeySet {
			key = env.AIAPIKey
		}
	}
	if model == nil || !dto.PythonTruthy(model) {
		model = env.AIModel
	}
	if baseURL == nil {
		baseURL = env.AIBaseURL
	}
	if !dto.PythonTruthy(key) {
		return nil, errAgentProviderKey
	}
	keyText, okKey := key.(string)
	baseText, okBase := baseURL.(string)
	if _, okModel := model.(string); !okKey || !okBase || !okModel {
		return nil, ErrAgentNotPorted
	}
	config := agentllm.Config{APIKey: keyText, BaseURL: baseText}
	if transport != nil {
		transport(&config)
	}
	client, err := agentllm.New(config)
	if err != nil {
		return nil, ErrAgentNotPorted
	}
	return &AgentAIProvider{client: client, model: model}, nil
}

var errAgentProviderKey = errors.New("AI_API_KEY is required")

var agentRequestJSON = dto.AgentJSON{Item: ",", Key: ":", RejectNaN: true}

// ChatStream is AIProvider.chat_stream: up to three attempts, retrying tool
// validation errors with a higher temperature; failures become finish_reason
// 'error' with str(exception) as the content.
func (p *AgentAIProvider) ChatStream(ctx context.Context, messages []any, tools []any, onChunk func(string) error) (AgentLLMResponse, error) {
	temperature := json.Number("0.1")
	var last string
	for attempt := 0; attempt <= 2; attempt++ {
		body := domain.RequestObject{{Name: "messages", Value: messages}, {Name: "model", Value: p.model},
			{Name: "max_tokens", Value: json.Number("4096")}, {Name: "stream", Value: true}, {Name: "temperature", Value: temperature}}
		if len(tools) > 0 {
			body = append(body, domain.RequestField{Name: "tool_choice", Value: "auto"}, domain.RequestField{Name: "tools", Value: tools})
		}
		text, err := agentRequestJSON.Dumps(body)
		if err != nil || dto.AgentHasSurrogate(text) {
			// httpx's json encoding raises before any request is sent.
			return AgentLLMResponse{Content: "request encoding", FinishReason: "error"}, nil
		}
		response, failure, err := p.attempt(ctx, []byte(text), onChunk)
		if err != nil {
			return AgentLLMResponse{}, err
		}
		if failure == nil {
			return response, nil
		}
		last = failure.Text
		if attempt < 2 && agentRetryable(last) {
			temperature = json.Number(dto.PythonFloatText(0.3 + float64(attempt)*0.1))
			continue
		}
		break
	}
	return AgentLLMResponse{Content: last, FinishReason: "error"}, nil
}

// agentRetryable is AIProvider._is_retryable_error.
func agentRetryable(text string) bool {
	return strings.Contains(text, "tool_use_failed") || strings.Contains(agentLower(text), "tool call validation failed")
}

func (p *AgentAIProvider) attempt(ctx context.Context, body []byte, onChunk func(string) error) (AgentLLMResponse, *agentllm.APIError, error) {
	stream, err := p.client.Create(ctx, body)
	var failure *agentllm.APIError
	if errors.As(err, &failure) {
		return AgentLLMResponse{}, failure, nil
	}
	if err != nil {
		return AgentLLMResponse{}, nil, err
	}
	defer stream.Close()
	response, err := agentConsume(ctx, stream, onChunk)
	if errors.As(err, &failure) {
		return AgentLLMResponse{}, failure, nil
	}
	return response, nil, err
}

type agentAccumulated struct {
	key       any
	id, name  any
	arguments string
}

// agentConsume is AIProvider._consume_stream over the SDK's AsyncStream.
func agentConsume(ctx context.Context, stream *agentllm.Stream, onChunk func(string) error) (AgentLLMResponse, error) {
	raise := func(text string) error { return &agentllm.APIError{Text: text} }
	var parts []string
	var calls []*agentAccumulated
	var finish any = ""
	for {
		event, err := stream.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			if ctx.Err() != nil {
				return AgentLLMResponse{}, ctx.Err()
			}
			return AgentLLMResponse{}, err
		}
		if strings.HasPrefix(event.Data, "[DONE]") {
			break
		}
		data, parseErr := dto.ParsePythonJSON(event.Data)
		if parseErr != nil {
			return AgentLLMResponse{}, raise("JSONDecodeError")
		}
		if event.Event != nil && strings.HasPrefix(*event.Event, "thread.") {
			// {"data": ..., "event": ...} has neither choices nor usage (and
			// a thread event is never named "error").
			continue
		}
		object, isObject := data.(domain.RequestObject)
		if isObject {
			if failure, _ := object.Lookup("error"); dto.PythonTruthy(failure) {
				message := "An error occurred during streaming"
				if detail, ok := failure.(domain.RequestObject); ok {
					if text, ok := lookupValue(detail, "message").(string); ok && text != "" {
						message = text
					}
				}
				return AgentLLMResponse{}, raise(message)
			}
		}
		if !isObject {
			return AgentLLMResponse{}, raise("object has no attribute 'choices'")
		}
		choices := lookupValue(object, "choices")
		var choice any
		if dto.PythonTruthy(choices) {
			list, ok := choices.([]any)
			if !ok {
				return AgentLLMResponse{}, raise("invalid choices")
			}
			choice = list[0]
		}
		usageOK := func() error {
			if usage := lookupValue(object, "usage"); dto.PythonTruthy(usage) {
				if _, ok := usage.(domain.RequestObject); !ok {
					return raise("object has no attribute 'prompt_tokens'")
				}
			}
			return nil
		}
		if !dto.PythonTruthy(choice) {
			if _, model := choice.(domain.RequestObject); !model {
				if err := usageOK(); err != nil {
					return AgentLLMResponse{}, err
				}
				continue
			}
		}
		choiceObject, ok := choice.(domain.RequestObject)
		if !ok {
			return AgentLLMResponse{}, raise("object has no attribute 'delta'")
		}
		delta := lookupValue(choiceObject, "delta")
		if reason := lookupValue(choiceObject, "finish_reason"); dto.PythonTruthy(reason) {
			finish = reason
		}
		deltaObject, deltaModel := delta.(domain.RequestObject)
		if dto.PythonTruthy(delta) || deltaModel {
			if !deltaModel {
				return AgentLLMResponse{}, raise("object has no attribute 'content'")
			}
			if content := lookupValue(deltaObject, "content"); dto.PythonTruthy(content) {
				text, ok := content.(string)
				if !ok {
					return AgentLLMResponse{}, ErrAgentNotPorted
				}
				parts = append(parts, text)
				if onChunk != nil {
					if err := onChunk(text); err != nil {
						return AgentLLMResponse{}, err
					}
				}
			}
			if toolCalls := lookupValue(deltaObject, "tool_calls"); dto.PythonTruthy(toolCalls) {
				list, ok := toolCalls.([]any)
				if !ok {
					return AgentLLMResponse{}, raise("invalid tool_calls")
				}
				for _, item := range list {
					call, ok := item.(domain.RequestObject)
					if !ok {
						return AgentLLMResponse{}, raise("object has no attribute 'index'")
					}
					if err := agentAccumulate(&calls, call); err != nil {
						return AgentLLMResponse{}, err
					}
				}
			}
		}
		if err := usageOK(); err != nil {
			return AgentLLMResponse{}, err
		}
	}
	ordered, err := agentSortCalls(calls)
	if err != nil {
		return AgentLLMResponse{}, err
	}
	response := AgentLLMResponse{Content: strings.Join(parts, ""), FinishReason: finish}
	for _, call := range ordered {
		var arguments any = domain.RequestObject{}
		if call.arguments != "" {
			if parsed, err := dto.ParsePythonJSON(call.arguments); err == nil {
				arguments = parsed
			}
		}
		response.ToolCalls = append(response.ToolCalls, AgentToolCall{ID: call.id, Name: call.name, Arguments: arguments})
	}
	return response, nil
}

func lookupValue(object domain.RequestObject, name string) any {
	value, _ := object.Lookup(name)
	return value
}

// agentAccumulate merges one ChoiceDeltaToolCall into tool_calls_acc.
func agentAccumulate(calls *[]*agentAccumulated, delta domain.RequestObject) error {
	key := lookupValue(delta, "index")
	switch key.(type) {
	case nil, string:
	case json.Number:
		if strings.ContainsAny(string(key.(json.Number)), ".eE") {
			return ErrAgentNotPorted
		}
	default:
		return ErrAgentNotPorted // unhashable, float or bool keys (dict key equality) are not ported
	}
	id, function := lookupValue(delta, "id"), lookupValue(delta, "function")
	fn, fnModel := function.(domain.RequestObject)
	if dto.PythonTruthy(function) && !fnModel {
		return &agentllm.APIError{Text: "object has no attribute 'name'"}
	}
	var current *agentAccumulated
	for _, call := range *calls {
		if agentSameKey(call.key, key) {
			current = call
		}
	}
	if current == nil {
		var name any = ""
		if fnModel {
			if value := lookupValue(fn, "name"); dto.PythonTruthy(value) {
				name = value
			}
		}
		var initial any = ""
		if dto.PythonTruthy(id) {
			initial = id
		}
		current = &agentAccumulated{key: key, id: initial, name: name}
		*calls = append(*calls, current)
	}
	if dto.PythonTruthy(id) {
		current.id = id
	}
	if fnModel {
		if value := lookupValue(fn, "name"); dto.PythonTruthy(value) {
			current.name = value
		}
		if value := lookupValue(fn, "arguments"); dto.PythonTruthy(value) {
			text, ok := value.(string)
			if !ok {
				return &agentllm.APIError{Text: "can only concatenate str"}
			}
			current.arguments += text
		}
	}
	for _, value := range []any{current.id, current.name} {
		if _, ok := value.(string); !ok {
			return ErrAgentNotPorted
		}
	}
	return nil
}

func agentSameKey(left, right any) bool {
	switch l := left.(type) {
	case nil:
		return right == nil
	case string:
		r, ok := right.(string)
		return ok && l == r
	case json.Number:
		r, ok := right.(json.Number)
		return ok && dto.PythonEqual(l, r)
	}
	return false
}

// agentSortCalls is sorted(tool_calls_acc): mixed key types raise TypeError.
func agentSortCalls(calls []*agentAccumulated) ([]*agentAccumulated, error) {
	if len(calls) <= 1 {
		return calls, nil
	}
	kind := func(value any) int {
		switch value.(type) {
		case nil:
			return 0
		case string:
			return 1
		}
		return 2
	}
	first := kind(calls[0].key)
	for _, call := range calls {
		if kind(call.key) != first || first == 0 {
			return nil, &agentllm.APIError{Text: "'<' not supported"}
		}
	}
	sorted := append([]*agentAccumulated(nil), calls...)
	var failed bool
	sort.SliceStable(sorted, func(i, j int) bool {
		if first == 1 {
			return sorted[i].key.(string) < sorted[j].key.(string)
		}
		a, okA := new(big.Int).SetString(string(sorted[i].key.(json.Number)), 10)
		b, okB := new(big.Int).SetString(string(sorted[j].key.(json.Number)), 10)
		if !okA || !okB {
			failed = true
			return false
		}
		return a.Cmp(b) < 0
	})
	if failed {
		return nil, ErrAgentNotPorted
	}
	return sorted, nil
}
