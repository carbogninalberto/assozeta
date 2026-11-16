package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/agentllm"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// Item 1: the environment value 0 is ”; Django raises where it is used
// (range(”), `len(...) > ”`), so the error is plain, not a refusal. Other
// types are states only the two configuration writers could produce.
func TestAgentIntUsesDjangoFailures(t *testing.T) {
	for _, item := range []struct {
		value any
		want  int
		plain bool
		state bool
	}{
		{json.Number("5"), 5, false, false},
		{int64(7), 7, false, false},
		{"", 0, true, false},
		{"5", 0, false, true},
		{[]any{}, 0, false, true},
		{json.Number("1e3"), 0, false, true},
		{json.Number("999999999999999999999999"), 0, false, true},
	} {
		got, err := agentInt(item.value)
		if err == nil {
			if item.plain || item.state || got != item.want {
				t.Fatalf("%v: got %d, %v", item.value, got, err)
			}
			continue
		}
		if got != 0 {
			t.Fatalf("%v: got %d", item.value, got)
		}
		if errors.Is(err, refusal.ErrNotPorted) {
			t.Fatalf("%v: refusal leaked: %v", item.value, err)
		}
		if item.plain && errors.Is(err, refusal.ErrAppState) {
			t.Fatalf("%v: want a plain error, got %v", item.value, err)
		}
		if errors.Is(err, refusal.ErrAppState) != item.state {
			t.Fatalf("%v: AppState = %v, want %v (%v)", item.value, errors.Is(err, refusal.ErrAppState), item.state, err)
		}
	}
}

// Item 4: a collaborator without connected_user makes Django's
// SportAssociation.objects.get(user=None) raise DoesNotExist, so the consumer
// releases the slot and closes 4003 instead of refusing.
func TestAgentAssociationCollaboratorWithoutConnectedUser(t *testing.T) {
	runtime := &AgentRuntime{}
	association, ok, err := runtime.Association(context.Background(), &domain.Account{Role: domain.RoleCollaborator})
	if err != nil || ok || association.ID != "" {
		t.Fatalf("got %#v, %v, %v", association, ok, err)
	}
}

// Item 4: a configuration without a required key is a state only a writer
// outside AISettingsSerializer/environmentIntegration produces.
func TestAgentProviderMissingKeyIsAppState(t *testing.T) {
	runtime := &AgentRuntime{}
	_, err := runtime.Provider(map[string]any{"enabled": true, "api_key": "k", "model": "m"})
	if !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("want AppState, got %v", err)
	}
	if errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("refusal leaked: %v", err)
	}
}

// Item 5: provider arguments that are not strings are states outside the two
// writers; an undecodable base URL is answered like httpx's InvalidURL.
func TestAgentProviderArgumentShapes(t *testing.T) {
	for _, item := range []struct {
		name         string
		apiKey       any
		model        any
		baseURL      any
		wantAppState bool
	}{
		{"key", map[string]any{}, "m", "http://127.0.0.1/v1", true},
		{"model", "k", map[string]any{"x": 1}, "http://127.0.0.1/v1", true},
		{"base", "k", "m", json.Number("1"), true},
		{"bytes", "k", "m", string([]byte{'h', 't', 't', 'p', ':', '/', '/', 'h', '/', 0xff}), false},
	} {
		_, err := NewAgentAIProvider(AgentEnvironment{}, item.apiKey, item.model, item.baseURL, nil)
		if err == nil {
			t.Fatalf("%s: expected an error", item.name)
		}
		if errors.Is(err, refusal.ErrNotPorted) {
			t.Fatalf("%s: refusal leaked: %v", item.name, err)
		}
		if errors.Is(err, refusal.ErrAppState) != item.wantAppState {
			t.Fatalf("%s: AppState = %v (%v)", item.name, errors.Is(err, refusal.ErrAppState), err)
		}
	}
}

func agentTestDelta(t *testing.T, raw string) domain.RequestObject {
	t.Helper()
	value, err := dto.ParsePythonJSON(raw)
	if err != nil {
		t.Fatalf("%s: %v", raw, err)
	}
	object, ok := value.(domain.RequestObject)
	if !ok {
		t.Fatalf("%s: %T", raw, value)
	}
	return object
}

// Item 6: Python dict-key semantics for tool call indexes (1 == 1.0 == True,
// first inserted key kept) and numeric ordering.
func TestAgentToolCallNumericIndexes(t *testing.T) {
	var calls []*agentAccumulated
	for _, raw := range []string{
		`{"index": 1, "id": "a", "function": {"name": "f", "arguments": "{}"}}`,
		`{"index": 1.0, "id": "b", "function": {"name": "f", "arguments": "{}"}}`,
		`{"index": true, "id": "c", "function": {"name": "f", "arguments": "{}"}}`,
	} {
		if err := agentAccumulate(&calls, agentTestDelta(t, raw)); err != nil {
			t.Fatalf("%s: %v", raw, err)
		}
	}
	if len(calls) != 1 || calls[0].id != "c" {
		t.Fatalf("equal keys did not merge: %#v", calls)
	}

	calls = nil
	for _, raw := range []string{
		`{"index": 2, "id": "b", "function": {"name": "f", "arguments": "{}"}}`,
		`{"index": 10, "id": "c", "function": {"name": "f", "arguments": "{}"}}`,
		`{"index": 1.5, "id": "a", "function": {"name": "f", "arguments": "{}"}}`,
	} {
		if err := agentAccumulate(&calls, agentTestDelta(t, raw)); err != nil {
			t.Fatalf("%s: %v", raw, err)
		}
	}
	ordered, err := agentSortCalls(calls)
	if err != nil {
		t.Fatal(err)
	}
	if len(ordered) != 3 || ordered[0].id != "a" || ordered[1].id != "b" || ordered[2].id != "c" {
		t.Fatalf("numeric order: %#v", ordered)
	}
}

// Item 6: container indexes are unhashable and NaN indexes are X11; the native
// answer is the provider's failed stream.
func TestAgentToolCallUnhashableIndexes(t *testing.T) {
	for _, raw := range []string{
		`{"index": [0]}`,
		`{"index": {"a": 0}}`,
		`{"index": NaN}`,
	} {
		var calls []*agentAccumulated
		err := agentAccumulate(&calls, agentTestDelta(t, raw))
		var failure *agentllm.APIError
		if !errors.As(err, &failure) {
			t.Fatalf("%s: want APIError, got %v", raw, err)
		}
		if errors.Is(err, refusal.ErrNotPorted) || errors.Is(err, refusal.ErrAppState) {
			t.Fatalf("%s: refusal leaked: %v", raw, err)
		}
	}
}

// Item 6: mixed key types raise Python's TypeError; the existing APIError path
// answers them as a failed stream.
func TestAgentToolCallSortMixedKeys(t *testing.T) {
	number, err := agentCallKeyOf(json.Number("1"))
	if err != nil || number.number.Cmp(big.NewRat(1, 1)) != 0 {
		t.Fatalf("number key: %#v %v", number, err)
	}
	calls := []*agentAccumulated{
		{key: agentCallKey{kind: 1, text: "0"}},
		{key: number},
	}
	var failure *agentllm.APIError
	if _, err := agentSortCalls(calls); !errors.As(err, &failure) {
		t.Fatalf("want APIError, got %v", err)
	}
	nones := []*agentAccumulated{{key: agentCallKey{}}, {key: agentCallKey{}}}
	if _, err := agentSortCalls(nones); !errors.As(err, &failure) {
		t.Fatalf("two None keys: want APIError, got %v", err)
	}
}

// Review 1: a float index beyond float64's range is json.loads' ±Inf, not an
// unbounded rational built from the exponent; infinities compare equal and sort
// outside the finite numbers.
func TestAgentToolCallInfiniteIndexes(t *testing.T) {
	start := time.Now()
	huge, err := agentCallKeyOf(json.Number("1e999999999"))
	if err != nil {
		t.Fatalf("1e999999999: %v", err)
	}
	if elapsed := time.Since(start); elapsed > time.Second {
		t.Fatalf("1e999999999 took %s", elapsed)
	}
	if huge.infinite != 1 {
		t.Fatalf("1e999999999: got %#v", huge)
	}
	for _, token := range []string{"1e400", "1.7976931348623159e308"} {
		key, err := agentCallKeyOf(json.Number(token))
		if err != nil {
			t.Fatalf("%s: %v", token, err)
		}
		if !agentSameKey(huge, key) {
			t.Fatalf("%s: not the same infinity", token)
		}
	}
	literal, err := dto.ParsePythonJSON(`Infinity`)
	if err != nil {
		t.Fatal(err)
	}
	fromLiteral, err := agentCallKeyOf(literal)
	if err != nil {
		t.Fatalf("Infinity literal: %v", err)
	}
	if !agentSameKey(huge, fromLiteral) {
		t.Fatal("1e999999999 and the Infinity literal are different keys")
	}
	if agentSameKey(huge, mustAgentCallKey(t, json.Number("1"))) {
		t.Fatal("+Inf equals a finite number")
	}

	// 1 == 1.0 == True.
	one := mustAgentCallKey(t, json.Number("1"))
	if !agentSameKey(one, mustAgentCallKey(t, json.Number("1.0"))) ||
		!agentSameKey(one, mustAgentCallKey(t, true)) {
		t.Fatal("1, 1.0 and true must be one key")
	}

	ordered, err := agentSortCalls([]*agentAccumulated{
		{key: huge, id: "inf"},
		{key: mustAgentCallKey(t, json.Number("1")), id: "one"},
		{key: mustAgentCallKey(t, json.Number("-1e400")), id: "neg"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(ordered) != 3 || ordered[0].id != "neg" || ordered[1].id != "one" || ordered[2].id != "inf" {
		t.Fatalf("infinite order: %#v", ordered)
	}
}

func mustAgentCallKey(t *testing.T, value any) agentCallKey {
	t.Helper()
	key, err := agentCallKeyOf(value)
	if err != nil {
		t.Fatalf("%v: %v", value, err)
	}
	return key
}

// A provider whose tool calls repeat (get_schema, {}) and whose forced answer,
// with no tools, fails like AIProvider.chat_stream does.
type agentForcedFailureProvider struct {
	failure string
	calls   int
}

func (p *agentForcedFailureProvider) ChatStream(_ context.Context, _ []any, tools []any, _ func(string) error) (AgentLLMResponse, error) {
	p.calls++
	if len(tools) == 0 {
		return AgentLLMResponse{Content: p.failure, FinishReason: "error"}, nil
	}
	return AgentLLMResponse{ToolCalls: []AgentToolCall{{ID: "call-1", Name: "get_schema", Arguments: domain.RequestObject{}}}}, nil
}

type agentTestTools struct{}

func (agentTestTools) Business(context.Context, string, domain.RequestObject) (any, bool, error) {
	return domain.RequestObject{{Name: "models", Value: []any{}}}, false, nil
}
func (agentTestTools) Manual(context.Context, string, domain.RequestObject) (any, error) {
	return nil, nil
}
func (agentTestTools) Coordinate(context.Context) (func(), string, error) { return func() {}, "", nil }

type agentTestCallback struct {
	ends, dones int
}

func (c *agentTestCallback) OnStatus(context.Context, string) error       { return nil }
func (c *agentTestCallback) OnToolCall(context.Context, any, any) error   { return nil }
func (c *agentTestCallback) OnMessageChunk(context.Context, string) error { return nil }
func (c *agentTestCallback) OnMessageEnd(context.Context) error           { c.ends++; return nil }
func (c *agentTestCallback) OnManualSection(context.Context, any) error   { return nil }
func (c *agentTestCallback) OnExportReady(context.Context, domain.RequestObject) error {
	return nil
}
func (c *agentTestCallback) OnReportSaved(context.Context, domain.RequestObject) error { return nil }
func (c *agentTestCallback) OnError(context.Context, string) error                     { return nil }
func (c *agentTestCallback) OnDone(context.Context) error                              { c.dones++; return nil }

// Review 2: Django appends a failed forced answer like a successful one, so the
// provider's failure text becomes the assistant message and message_end runs.
func TestAgentForcedAnswerFailureBecomesAssistantMessage(t *testing.T) {
	callback := &agentTestCallback{}
	provider := &agentForcedFailureProvider{failure: "Connection error."}
	agent := NewAgent(provider, callback, AgentOptions{
		AssociationID: "assoc", DefaultIterations: 6, DefaultHistoryCap: 100,
		Tools: agentTestTools{}, ToolSchemas: []any{map[string]any{"type": "function"}},
	})
	if err := agent.ProcessMessage(context.Background(), "crea un report dei soci"); err != nil {
		t.Fatalf("forced failure: %v", err)
	}
	if provider.calls != 4 {
		t.Fatalf("provider calls: got %d", provider.calls)
	}
	history := agent.snapshot()
	last, ok := history[len(history)-1].(domain.RequestObject)
	if !ok || lookupValue(last, "role") != "assistant" || lookupValue(last, "content") != "Connection error." {
		t.Fatalf("history tail: %#v", history[len(history)-1])
	}
	if callback.ends != 1 || callback.dones != 1 {
		t.Fatalf("message_end %d, done %d", callback.ends, callback.dones)
	}
}
