package service

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/agentllm"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

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

// errAgentProviderValue is a provider argument outside the two writers: the
// instance CharFields (AISettingsSerializer) and environmentIntegration's
// strings.
var errAgentProviderValue = refusal.AppState("AI provider argument is not a string")

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
		return nil, errAgentProviderValue
	}
	config := agentllm.Config{APIKey: keyText, BaseURL: baseText}
	if transport != nil {
		transport(&config)
	}
	client, err := agentllm.New(config)
	var invalid *agentllm.InvalidURLError
	if errors.As(err, &invalid) {
		// AsyncOpenAI(base_url=...) raises httpx.InvalidURL while the provider is
		// built: the consumer closes with 4004 on connect and sends the
		// "Configurazione AI non disponibile" frame on a refresh.
		return nil, err
	}
	if err != nil {
		// A base URL that is not UTF-8 text raises while httpx builds the URL
		// (Invalid IDNA hostname or UnicodeEncodeError); the callers answer it
		// exactly like InvalidURL.
		return nil, err
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
	key       agentCallKey
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
					// Django forwards the non-string chunk first and then
					// ''.join(content_parts) raises; X11's native answer is a
					// failed stream.
					return AgentLLMResponse{}, raise("sequence item: expected str instance")
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

// agentCallKey is one tool_calls_acc key. Numeric keys (including bool) compare
// by value like Python dict keys (1 == 1.0 == True); the first inserted key is
// kept because equal keys find the same entry. A float token outside float64's
// range is the ±Inf json.loads yields: it stays a number, equals only the same
// infinity and sorts outside every finite number.
type agentCallKey struct {
	kind     int // 0 None, 1 str, 2 number
	text     string
	number   *big.Rat // nil for an infinite number
	infinite int      // 0 finite, +1 +Inf, -1 -Inf
}

func agentCallKeyOf(value any) (agentCallKey, error) {
	switch typed := value.(type) {
	case nil:
		return agentCallKey{}, nil
	case string:
		return agentCallKey{kind: 1, text: typed}, nil
	case bool:
		number := big.NewRat(0, 1)
		if typed {
			number = big.NewRat(1, 1)
		}
		return agentCallKey{kind: 2, number: number}, nil
	case json.Number:
		return agentNumberCallKey(string(typed))
	case float64:
		// The Python-compatible parser yields float64 only for NaN and ±Inf.
		if math.IsInf(typed, 0) {
			if typed > 0 {
				return agentCallKey{kind: 2, infinite: 1}, nil
			}
			return agentCallKey{kind: 2, infinite: -1}, nil
		}
		return agentCallKey{}, &agentllm.APIError{Text: "float tool call index"}
	case []any:
		// TypeError: unhashable type: 'list' at tool_calls_acc[idx].
		return agentCallKey{}, &agentllm.APIError{Text: "unhashable type: 'list'"}
	case domain.RequestObject:
		// TypeError: unhashable type: 'dict' at tool_calls_acc[idx].
		return agentCallKey{}, &agentllm.APIError{Text: "unhashable type: 'dict'"}
	default:
		return agentCallKey{}, &agentllm.APIError{Text: "unhashable tool call index"}
	}
}

// agentNumberCallKey is one json.Number index with Python's value: an integer
// token is an exact int (the parser already bounds it to 4300 digits), a float
// token is a float64, and a token beyond float64's range is the ±Inf json.loads
// keeps instead of building an unbounded big.Rat.
func agentNumberCallKey(token string) (agentCallKey, error) {
	if !strings.ContainsAny(token, ".eE") {
		integer, ok := new(big.Int).SetString(token, 10)
		if !ok {
			return agentCallKey{}, &agentllm.APIError{Text: "invalid tool call index"}
		}
		return agentCallKey{kind: 2, number: new(big.Rat).SetInt(integer)}, nil
	}
	value, err := strconv.ParseFloat(token, 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return agentCallKey{}, &agentllm.APIError{Text: "invalid tool call index"}
	}
	switch {
	case math.IsInf(value, 1):
		return agentCallKey{kind: 2, infinite: 1}, nil
	case math.IsInf(value, -1):
		return agentCallKey{kind: 2, infinite: -1}, nil
	case math.IsNaN(value):
		return agentCallKey{}, &agentllm.APIError{Text: "float tool call index"}
	}
	// An exact float, as Python compares int and float values.
	return agentCallKey{kind: 2, number: new(big.Rat).SetFloat64(value)}, nil
}

// agentAccumulate merges one ChoiceDeltaToolCall into tool_calls_acc.
func agentAccumulate(calls *[]*agentAccumulated, delta domain.RequestObject) error {
	key, err := agentCallKeyOf(lookupValue(delta, "index"))
	if err != nil {
		return err
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
			// X11: Django would stream the non-string chunk first; the native
			// answer is a failed stream.
			return &agentllm.APIError{Text: "tool call id and name are strings"}
		}
	}
	return nil
}

func agentSameKey(left, right agentCallKey) bool {
	if left.kind != right.kind {
		return false
	}
	switch left.kind {
	case 0:
		return true
	case 1:
		return left.text == right.text
	}
	return agentCallKeyCompare(left, right) == 0
}

// agentCallKeyCompare orders two numeric keys: ±Inf sits after (or before)
// every finite number and equals only its own infinity.
func agentCallKeyCompare(left, right agentCallKey) int {
	if left.infinite != right.infinite {
		if left.infinite > right.infinite {
			return 1
		}
		return -1
	}
	if left.infinite != 0 {
		return 0
	}
	return left.number.Cmp(right.number)
}

// agentSortCalls is sorted(tool_calls_acc): mixed key types raise TypeError.
func agentSortCalls(calls []*agentAccumulated) ([]*agentAccumulated, error) {
	if len(calls) <= 1 {
		return calls, nil
	}
	kind := calls[0].key.kind
	for _, call := range calls {
		if call.key.kind != kind {
			return nil, &agentllm.APIError{Text: "'<' not supported"}
		}
	}
	if kind == 0 {
		// NoneType has no ordering.
		return nil, &agentllm.APIError{Text: "'<' not supported"}
	}
	sorted := append([]*agentAccumulated(nil), calls...)
	sort.SliceStable(sorted, func(i, j int) bool {
		if kind == 1 {
			return sorted[i].key.text < sorted[j].key.text
		}
		return agentCallKeyCompare(sorted[i].key, sorted[j].key) < 0
	})
	return sorted, nil
}
