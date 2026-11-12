package mcpstdio_test

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler/mcpstdio"
)

const testTimeout = 3 * time.Second

type harness struct {
	t           *testing.T
	input       *io.PipeWriter
	output      <-chan map[string]any
	outputDone  <-chan error
	serveDone   chan error
	cleanupOnce sync.Once
}

func newHarness(
	t *testing.T,
	opts mcpstdio.Options,
	execute func(context.Context, string, map[string]any) (any, error),
	validate func(string, map[string]any) error,
) *harness {
	t.Helper()
	return newHarnessWithTools(t, []mcpstdio.Tool{{
		Name:        "echo",
		Description: "echo arguments",
		InputSchema: map[string]any{"type": "object"},
	}}, opts, execute, validate)
}

func newHarnessWithTools(
	t *testing.T,
	tools []mcpstdio.Tool,
	opts mcpstdio.Options,
	execute func(context.Context, string, map[string]any) (any, error),
	validate func(string, map[string]any) error,
) *harness {
	t.Helper()
	if opts.ServerVersion == "" {
		opts.ServerVersion = "test-version"
	}
	server, err := mcpstdio.New(tools, execute, validate, opts)
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	inputReader, inputWriter := io.Pipe()
	outputReader, outputWriter := io.Pipe()
	responses := make(chan map[string]any, 512)
	outputDone := make(chan error, 1)
	go func() {
		scanner := bufio.NewScanner(outputReader)
		scanner.Buffer(make([]byte, 1024), 16<<20)
		for scanner.Scan() {
			var response map[string]any
			if err := json.Unmarshal(scanner.Bytes(), &response); err != nil {
				outputDone <- err
				return
			}
			responses <- response
		}
		outputDone <- scanner.Err()
	}()

	serveDone := make(chan error, 1)
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		err := server.Serve(ctx, inputReader, outputWriter)
		_ = outputWriter.Close()
		serveDone <- err
	}()

	h := &harness{
		t:          t,
		input:      inputWriter,
		output:     responses,
		outputDone: outputDone,
		serveDone:  serveDone,
	}
	t.Cleanup(func() {
		h.cleanupOnce.Do(func() {
			cancel()
			_ = inputWriter.Close()
			if h.serveDone == nil {
				return
			}
			select {
			case <-serveDone:
			case <-time.After(testTimeout):
				t.Error("Serve did not stop during cleanup")
			}
		})
	})
	return h
}

func (h *harness) sendRaw(raw string) {
	h.t.Helper()
	if _, err := io.WriteString(h.input, raw); err != nil {
		h.t.Fatalf("write request: %v", err)
	}
}

func (h *harness) send(method string, params any, id any) {
	h.t.Helper()
	request := map[string]any{"jsonrpc": "2.0", "method": method}
	if params != nil {
		request["params"] = params
	}
	if id != nil {
		request["id"] = id
	}
	data, err := json.Marshal(request)
	if err != nil {
		h.t.Fatalf("marshal request: %v", err)
	}
	h.sendRaw(string(data) + "\n")
}

func (h *harness) receive() map[string]any {
	h.t.Helper()
	select {
	case response, ok := <-h.output:
		if !ok {
			h.t.Fatal("stdout closed before response")
		}
		return response
	case <-time.After(testTimeout):
		h.t.Fatal("timed out waiting for response")
		return nil
	}
}

func (h *harness) expectNoOutput() {
	h.t.Helper()
	select {
	case response, ok := <-h.output:
		if ok {
			h.t.Fatalf("unexpected stdout response: %#v", response)
		}
	case <-time.After(100 * time.Millisecond):
	}
}

func (h *harness) closeInput() error {
	h.t.Helper()
	if err := h.input.Close(); err != nil {
		h.t.Fatalf("close input: %v", err)
	}
	select {
	case err := <-h.serveDone:
		h.serveDone = nil
		return err
	case <-time.After(testTimeout):
		h.t.Fatal("Serve did not stop after EOF")
		return nil
	}
}

func assertJSONEqual(t *testing.T, got, want any) {
	t.Helper()
	gotData, err := json.Marshal(got)
	if err != nil {
		t.Fatalf("marshal got: %v", err)
	}
	wantData, err := json.Marshal(want)
	if err != nil {
		t.Fatalf("marshal want: %v", err)
	}
	var gotValue, wantValue any
	if err := json.Unmarshal(gotData, &gotValue); err != nil {
		t.Fatalf("decode got: %v", err)
	}
	if err := json.Unmarshal(wantData, &wantValue); err != nil {
		t.Fatalf("decode want: %v", err)
	}
	if !reflect.DeepEqual(gotValue, wantValue) {
		t.Fatalf("JSON mismatch\n got: %s\nwant: %s", gotData, wantData)
	}
}

func TestProtocolNegotiationListAndMalformedRecovery(t *testing.T) {
	executeCalled := atomic.Bool{}
	h := newHarness(t, mcpstdio.Options{}, func(context.Context, string, map[string]any) (any, error) {
		executeCalled.Store(true)
		return nil, nil
	}, nil)

	h.send("initialize", map[string]any{
		"protocolVersion": "2024-11-05",
		"capabilities":    map[string]any{},
		"clientInfo":      map[string]any{"name": "contract", "version": "1"},
	}, "init-supported")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "init-supported",
		"result": map[string]any{
			"protocolVersion": "2024-11-05",
			"capabilities": map[string]any{
				"experimental": map[string]any{},
				"tools":        map[string]any{"listChanged": false},
			},
			"serverInfo": map[string]any{"name": "bakney-sport-data", "version": "test-version"},
		},
	})

	h.send("initialize", map[string]any{
		"protocolVersion": "unsupported-fixture-version",
		"capabilities":    map[string]any{},
		"clientInfo":      map[string]any{"name": "contract", "version": "1"},
	}, "init-fallback")
	fallback := h.receive()
	assertJSONEqual(t, fallback["result"].(map[string]any)["protocolVersion"], "2025-11-25")

	h.send("tools/list", map[string]any{}, "list")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "list",
		"result": map[string]any{"tools": []any{map[string]any{
			"name":        "echo",
			"description": "echo arguments",
			"inputSchema": map[string]any{"type": "object"},
		}}},
	})

	h.send("tools/list", map[string]any{"cursor": nil}, "list-null-cursor")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "list-null-cursor",
		"result": map[string]any{"tools": []any{map[string]any{
			"name":        "echo",
			"description": "echo arguments",
			"inputSchema": map[string]any{"type": "object"},
		}}},
	})

	h.sendRaw("{invalid-json\n")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"method":  "notifications/message",
		"params": map[string]any{
			"level":  "error",
			"data":   "Internal Server Error",
			"logger": "mcp.server.exception_handler",
		},
	})

	h.send("ping", map[string]any{}, 17.0)
	assertJSONEqual(t, h.receive(), map[string]any{"jsonrpc": "2.0", "id": 17.0, "result": map[string]any{}})
	if executeCalled.Load() {
		t.Fatal("protocol-only session invoked the tool executor")
	}
}

func TestToolDispatchValidatesBeforeUseNumberExecution(t *testing.T) {
	type execution struct {
		name      string
		arguments map[string]any
	}
	executions := make(chan execution, 1)
	var validated atomic.Bool
	h := newHarness(t, mcpstdio.Options{}, func(_ context.Context, name string, arguments map[string]any) (any, error) {
		if !validated.Load() {
			t.Error("executor ran before validation")
		}
		executions <- execution{name: name, arguments: arguments}
		return map[string]any{
			"name":  name,
			"limit": arguments["limit"],
			"text":  "caffè <ok>",
		}, nil
	}, func(name string, arguments map[string]any) error {
		if name != "echo" {
			t.Errorf("validation received tool %q", name)
		}
		if _, ok := arguments["limit"].(json.Number); !ok {
			t.Errorf("arguments did not preserve json.Number: %T", arguments["limit"])
		}
		validated.Store(true)
		return nil
	})

	h.send("tools/call", map[string]any{
		"name":      "echo",
		"arguments": map[string]any{"limit": 2},
	}, "call")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "call",
		"result": map[string]any{
			"isError": false,
			"content": []any{map[string]any{
				"type": "text",
				"text": `{"limit":2,"name":"echo","text":"caffè <ok>"}`,
			}},
		},
	})
	got := <-executions
	if got.name != "echo" || got.arguments["limit"] != json.Number("2") {
		t.Fatalf("unexpected execution: %#v", got)
	}
}

func TestValidationFailureUnknownToolAndExecutorErrors(t *testing.T) {
	validationSecret := "validation secret"
	executionSecret := "postgres://user:password@database/private"
	var validateCalls, executeCalls atomic.Int32
	h := newHarness(t, mcpstdio.Options{}, func(_ context.Context, name string, _ map[string]any) (any, error) {
		executeCalls.Add(1)
		if name == "echo" {
			return nil, errors.New(executionSecret)
		}
		t.Fatalf("unknown tool reached executor")
		return nil, nil
	}, func(_ string, arguments map[string]any) error {
		validateCalls.Add(1)
		if arguments["bad"] == true {
			return errors.New("Input validation error: " + validationSecret)
		}
		return nil
	})

	h.send("tools/call", map[string]any{
		"name":      "echo",
		"arguments": map[string]any{"bad": true},
	}, "invalid")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "invalid",
		"result": map[string]any{
			"isError": true,
			"content": []any{map[string]any{
				"type": "text",
				"text": "Input validation error: " + validationSecret,
			}},
		},
	})
	if executeCalls.Load() != 0 {
		t.Fatal("executor ran after validation failure")
	}

	h.send("tools/call", map[string]any{"name": "does_not_exist", "arguments": map[string]any{}}, "unknown")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "unknown",
		"result": map[string]any{
			"isError": false,
			"content": []any{map[string]any{
				"type": "text",
				"text": `{"error":"Unknown tool: does_not_exist"}`,
			}},
		},
	})
	if validateCalls.Load() != 1 || executeCalls.Load() != 0 {
		t.Fatalf("unknown tool made hidden calls: validate=%d execute=%d", validateCalls.Load(), executeCalls.Load())
	}

	h.send("tools/call", map[string]any{"name": "echo", "arguments": map[string]any{}}, "executor-error")
	response := h.receive()
	encoded, err := json.Marshal(response)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(encoded, []byte(executionSecret)) {
		t.Fatalf("executor secret leaked on stdout: %s", encoded)
	}
	result := response["result"].(map[string]any)
	if result["isError"] != true {
		t.Fatalf("executor error was not marked as an error: %#v", result)
	}
	content := result["content"].([]any)[0].(map[string]any)
	if content["text"] != "Tool execution failed" {
		t.Fatalf("unexpected sanitized executor error: %#v", content)
	}
}

func TestJSONValidationReceivesOriginalArgumentsAndTakesPrecedence(t *testing.T) {
	var mapValidateCalls, jsonValidateCalls, executeCalls atomic.Int32
	received := make(chan string, 2)
	h := newHarness(t, mcpstdio.Options{
		ValidateJSON: func(name string, arguments json.RawMessage) error {
			if name != "echo" {
				t.Errorf("JSON validation received tool %q", name)
			}
			jsonValidateCalls.Add(1)
			received <- string(arguments)
			return errors.New("Input validation error: ordered argument rejected")
		},
	}, func(context.Context, string, map[string]any) (any, error) {
		executeCalls.Add(1)
		return nil, nil
	}, func(string, map[string]any) error {
		mapValidateCalls.Add(1)
		return nil
	})

	h.sendRaw(`{"jsonrpc":"2.0","id":"ordered","method":"tools/call","params":{"name":"echo","arguments": { "z" : 1, "a" : { "y" : 2, "x" : 3 } }}}` + "\n")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "ordered",
		"result": map[string]any{
			"isError": true,
			"content": []any{map[string]any{
				"type": "text",
				"text": "Input validation error: ordered argument rejected",
			}},
		},
	})
	if got := <-received; got != `{ "z" : 1, "a" : { "y" : 2, "x" : 3 } }` {
		t.Fatalf("original argument bytes changed before validation: %q", got)
	}

	h.send("tools/call", map[string]any{"name": "echo"}, "missing-arguments")
	h.receive()
	if got := <-received; got != `{}` {
		t.Fatalf("missing arguments should validate as an empty object, got %q", got)
	}
	if mapValidateCalls.Load() != 0 || jsonValidateCalls.Load() != 2 || executeCalls.Load() != 0 {
		t.Fatalf("validation precedence/counts wrong: map=%d json=%d execute=%d",
			mapValidateCalls.Load(), jsonValidateCalls.Load(), executeCalls.Load())
	}
}

func TestMalformedRequestsReturnInvalidParameters(t *testing.T) {
	h := newHarness(t, mcpstdio.Options{}, func(context.Context, string, map[string]any) (any, error) {
		t.Fatal("malformed request reached executor")
		return nil, nil
	}, func(string, map[string]any) error {
		t.Fatal("malformed request reached validation")
		return nil
	})

	cases := []struct {
		name string
		send func()
		id   string
	}{
		{
			name: "unknown method",
			send: func() { h.send("does/not/exist", map[string]any{}, "unknown-method") },
			id:   "unknown-method",
		},
		{
			name: "missing tool name",
			send: func() {
				h.send("tools/call", map[string]any{"arguments": map[string]any{}}, "missing-name")
			},
			id: "missing-name",
		},
		{
			name: "array arguments",
			send: func() {
				h.send("tools/call", map[string]any{"name": "echo", "arguments": []any{}}, "array-arguments")
			},
			id: "array-arguments",
		},
		{
			name: "missing initialize fields",
			send: func() { h.send("initialize", map[string]any{}, "missing-init") },
			id:   "missing-init",
		},
		{
			name: "fractional protocol version",
			send: func() {
				h.send("initialize", map[string]any{
					"protocolVersion": 2.8,
					"capabilities":    map[string]any{},
					"clientInfo":      map[string]any{"name": "contract", "version": "1"},
				}, "fractional-protocol")
			},
			id: "fractional-protocol",
		},
		{
			name: "overflowed protocol version",
			send: func() {
				h.sendRaw(`{"jsonrpc":"2.0","id":"overflow-protocol","method":"initialize","params":{"protocolVersion":1e309,"capabilities":{},"clientInfo":{"name":"contract","version":"1"}}}` + "\n")
			},
			id: "overflow-protocol",
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			testCase.send()
			assertJSONEqual(t, h.receive(), map[string]any{
				"jsonrpc": "2.0",
				"id":      testCase.id,
				"error": map[string]any{
					"code":    -32602,
					"message": "Invalid request parameters",
					"data":    "",
				},
			})
		})
	}
}

func TestInitializeAcceptsObservedIntegerCoercions(t *testing.T) {
	h := newHarness(t, mcpstdio.Options{}, func(context.Context, string, map[string]any) (any, error) {
		t.Fatal("initialize invoked the tool executor")
		return nil, nil
	}, nil)

	cases := []struct {
		name  string
		value string
	}{
		{name: "integer", value: "2"},
		{name: "boolean", value: "true"},
		{name: "integral float", value: "2.0"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			id := "init-" + testCase.name
			h.sendRaw(`{"jsonrpc":"2.0","id":"` + id + `","method":"initialize","params":{"protocolVersion":` +
				testCase.value + `,"capabilities":{},"clientInfo":{"name":"contract","version":"1"}}}` + "\n")
			response := h.receive()
			assertJSONEqual(t, response["id"], id)
			result, ok := response["result"].(map[string]any)
			if !ok {
				t.Fatalf("initialize did not return a result: %#v", response)
			}
			assertJSONEqual(t, result["protocolVersion"], "2025-11-25")
		})
	}
}

func TestIntegerRequestIDUsesObservedSDKDigitBound(t *testing.T) {
	t.Run("4300 digits accepted", func(t *testing.T) {
		id := strings.Repeat("9", 4300)
		server, err := mcpstdio.New(nil, func(context.Context, string, map[string]any) (any, error) {
			return nil, nil
		}, nil, mcpstdio.Options{ServerVersion: "test-version"})
		if err != nil {
			t.Fatalf("New: %v", err)
		}
		var output bytes.Buffer
		input := io.NopCloser(strings.NewReader(
			`{"jsonrpc":"2.0","id":` + id + `,"method":"ping","params":{}}` + "\n",
		))
		if err := server.Serve(context.Background(), input, &output); err != nil {
			t.Fatalf("Serve: %v", err)
		}
		want := `{"id":` + id + `,"jsonrpc":"2.0","result":{}}` + "\n"
		if output.String() != want {
			t.Fatalf("unexpected response for 4300-digit ID: got %d bytes, want %d bytes; prefix %q",
				output.Len(), len(want), output.String()[:min(output.Len(), 80)])
		}
	})

	t.Run("4301 digits take malformed input path", func(t *testing.T) {
		h := newHarness(t, mcpstdio.Options{}, func(context.Context, string, map[string]any) (any, error) {
			t.Fatal("malformed ID invoked the tool executor")
			return nil, nil
		}, nil)
		h.sendRaw(`{"jsonrpc":"2.0","id":` + strings.Repeat("9", 4301) + `,"method":"ping","params":{}}` + "\n")
		assertJSONEqual(t, h.receive(), map[string]any{
			"jsonrpc": "2.0",
			"method":  "notifications/message",
			"params": map[string]any{
				"level":  "error",
				"data":   "Internal Server Error",
				"logger": "mcp.server.exception_handler",
			},
		})

		h.send("ping", map[string]any{}, "after-oversized-id")
		assertJSONEqual(t, h.receive(), map[string]any{
			"jsonrpc": "2.0",
			"id":      "after-oversized-id",
			"result":  map[string]any{},
		})
	})
}

func TestPipelineIsBoundedAndResponsesRemainSerialized(t *testing.T) {
	var active, maxActive atomic.Int32
	release := make(chan struct{})
	h := newHarness(t, mcpstdio.Options{MaxConcurrent: 2}, func(ctx context.Context, _ string, arguments map[string]any) (any, error) {
		current := active.Add(1)
		for {
			maximum := maxActive.Load()
			if current <= maximum || maxActive.CompareAndSwap(maximum, current) {
				break
			}
		}
		defer active.Add(-1)
		select {
		case <-release:
			return map[string]any{"n": arguments["n"]}, nil
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}, func(string, map[string]any) error { return nil })

	for index := 1; index <= 5; index++ {
		h.send("tools/call", map[string]any{
			"name":      "echo",
			"arguments": map[string]any{"n": index},
		}, index)
	}

	deadline := time.Now().Add(testTimeout)
	for active.Load() != 2 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if active.Load() != 2 {
		t.Fatalf("expected two active handlers, got %d", active.Load())
	}
	time.Sleep(30 * time.Millisecond)
	if active.Load() != 2 {
		t.Fatalf("concurrency bound exceeded: active=%d", active.Load())
	}
	close(release)

	seen := make(map[float64]bool)
	for range 5 {
		response := h.receive()
		id, ok := response["id"].(float64)
		if !ok {
			t.Fatalf("response id was not preserved: %#v", response)
		}
		seen[id] = true
		result := response["result"].(map[string]any)
		text := result["content"].([]any)[0].(map[string]any)["text"].(string)
		var body map[string]any
		if err := json.Unmarshal([]byte(text), &body); err != nil {
			t.Fatalf("tool text was not JSON: %v", err)
		}
		if body["n"].(float64) != id {
			t.Fatalf("response correlation mismatch: id=%v body=%#v", id, body)
		}
	}
	if len(seen) != 5 || maxActive.Load() > 2 {
		t.Fatalf("pipeline bound/correlation failed: seen=%v max=%d", seen, maxActive.Load())
	}
}

func TestToolAdmissionQueueIsBoundedAndReportsOverload(t *testing.T) {
	started := make(chan struct{}, 1)
	h := newHarness(t, mcpstdio.Options{MaxConcurrent: 1}, func(ctx context.Context, _ string, arguments map[string]any) (any, error) {
		if arguments["block"] == true {
			select {
			case started <- struct{}{}:
			default:
			}
			<-ctx.Done()
			return nil, ctx.Err()
		}
		return nil, nil
	}, nil)

	h.send("tools/call", map[string]any{
		"name":      "echo",
		"arguments": map[string]any{"block": true},
	}, "active")
	select {
	case <-started:
	case <-time.After(testTimeout):
		t.Fatal("active tool did not start")
	}

	for index := 0; index < 100; index++ {
		h.send("tools/call", map[string]any{
			"name":      "echo",
			"arguments": map[string]any{"index": index},
		}, index)
	}
	h.send("ping", map[string]any{}, "queue-status")

	overloads := 0
	for {
		response := h.receive()
		if response["id"] == "queue-status" {
			break
		}
		result, ok := response["result"].(map[string]any)
		if !ok || result["isError"] != true {
			continue
		}
		content := result["content"].([]any)[0].(map[string]any)
		if content["text"] == "Server busy" {
			overloads++
		}
	}
	if overloads == 0 {
		t.Fatal("bounded admission did not report an explicit overload")
	}
}

func TestCancellationReturnsDjangoErrorAndSessionRecovers(t *testing.T) {
	started := make(chan struct{}, 1)
	observed := make(chan struct{})
	h := newHarness(t, mcpstdio.Options{}, func(ctx context.Context, _ string, _ map[string]any) (any, error) {
		started <- struct{}{}
		<-ctx.Done()
		close(observed)
		return map[string]any{"late": true}, nil
	}, nil)

	h.send("tools/call", map[string]any{"name": "echo", "arguments": map[string]any{}}, "blocked")
	select {
	case <-started:
	case <-time.After(testTimeout):
		t.Fatal("tool did not start")
	}
	h.send("notifications/cancelled", map[string]any{
		"requestId": "blocked",
		"reason":    "contract cancellation",
	}, nil)
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "blocked",
		"error":   map[string]any{"code": 0, "message": "Request cancelled"},
	})
	select {
	case <-observed:
	case <-time.After(testTimeout):
		t.Fatal("executor context was not cancelled")
	}

	h.send("ping", map[string]any{}, "after-cancel")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "after-cancel",
		"result":  map[string]any{},
	})
	h.expectNoOutput()
}

func TestControlAndCancellationRemainResponsiveAtSaturation(t *testing.T) {
	started := make(chan string, 2)
	releaseFirst := make(chan struct{})
	cancelled := make(chan struct{})
	var inFlight atomic.Int32
	h := newHarness(t, mcpstdio.Options{MaxConcurrent: 1}, func(ctx context.Context, _ string, arguments map[string]any) (any, error) {
		inFlight.Add(1)
		defer inFlight.Add(-1)
		value, ok := arguments["id"].(string)
		if !ok {
			t.Errorf("executor received unexpected id argument %#v", arguments["id"])
			return nil, errors.New("invalid id argument")
		}
		id := value
		started <- id
		if id == "first" {
			select {
			case <-releaseFirst:
				return map[string]any{"id": id}, nil
			case <-ctx.Done():
				close(cancelled)
				return nil, ctx.Err()
			}
		}
		return map[string]any{"id": id}, nil
	}, func(string, map[string]any) error { return nil })

	h.send("tools/call", map[string]any{"name": "echo", "arguments": map[string]any{"id": "first"}}, "first")
	select {
	case id := <-started:
		if id != "first" {
			t.Fatalf("unexpected first execution %q", id)
		}
	case response := <-h.output:
		t.Fatalf("first tool failed instead of starting: %#v", response)
	case <-time.After(testTimeout):
		t.Fatal("first tool did not start")
	}

	// Queue another tool call behind the saturated slot. Ping must bypass the
	// tool-call limit, and cancellation must be read while both calls are pending.
	h.send("tools/call", map[string]any{"name": "echo", "arguments": map[string]any{"id": "second"}}, "second")
	h.send("ping", map[string]any{}, "status")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "status",
		"result":  map[string]any{},
	})
	if inFlight.Load() != 1 {
		t.Fatalf("tool execution bypassed saturation bound: %d", inFlight.Load())
	}

	h.send("notifications/cancelled", map[string]any{"requestId": "first"}, nil)
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "first",
		"error":   map[string]any{"code": 0, "message": "Request cancelled"},
	})
	select {
	case <-cancelled:
	case <-time.After(testTimeout):
		t.Fatal("saturated executor did not observe cancellation")
	}

	second := h.receive()
	assertJSONEqual(t, second["id"], "second")
	result := second["result"].(map[string]any)
	if result["isError"] != false {
		t.Fatalf("queued call failed after cancellation: %#v", result)
	}
}

func TestNumericCancellationIDPreservesOriginalRequestID(t *testing.T) {
	started := make(chan struct{}, 1)
	h := newHarness(t, mcpstdio.Options{}, func(ctx context.Context, _ string, _ map[string]any) (any, error) {
		started <- struct{}{}
		<-ctx.Done()
		return nil, ctx.Err()
	}, nil)

	h.send("tools/call", map[string]any{"name": "echo", "arguments": map[string]any{}}, 41)
	select {
	case <-started:
	case <-time.After(testTimeout):
		t.Fatal("tool did not start")
	}
	h.send("notifications/cancelled", map[string]any{"requestId": 41}, nil)
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      41.0,
		"error":   map[string]any{"code": 0, "message": "Request cancelled"},
	})
}

func TestNegativeZeroRequestIDCancelsAsZero(t *testing.T) {
	started := make(chan struct{}, 1)
	observed := make(chan struct{})
	h := newHarness(t, mcpstdio.Options{}, func(ctx context.Context, _ string, _ map[string]any) (any, error) {
		started <- struct{}{}
		<-ctx.Done()
		close(observed)
		return nil, ctx.Err()
	}, nil)

	h.sendRaw(`{"jsonrpc":"2.0","id":-0,"method":"tools/call","params":{"name":"echo","arguments":{}}}` + "\n")
	select {
	case <-started:
	case <-time.After(testTimeout):
		t.Fatal("tool did not start")
	}
	h.send("notifications/cancelled", map[string]any{
		"requestId": 0,
		"reason":    "negative zero correlation",
	}, nil)
	response := h.receive()
	assertJSONEqual(t, response, map[string]any{
		"jsonrpc": "2.0",
		"id":      0,
		"error":   map[string]any{"code": 0, "message": "Request cancelled"},
	})
	id, ok := response["id"].(float64)
	if !ok || id != 0 || math.Signbit(id) {
		t.Fatalf("negative-zero request ID was not normalized to positive zero: %#v", response["id"])
	}
	select {
	case <-observed:
	case <-time.After(testTimeout):
		t.Fatal("executor context was not cancelled")
	}

	h.send("ping", map[string]any{}, "after-negative-zero")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "after-negative-zero",
		"result":  map[string]any{},
	})
}

func TestConstructorClonesStringSlicesInSchemas(t *testing.T) {
	required := []string{"query"}
	h := newHarnessWithTools(t, []mcpstdio.Tool{{
		Name:        "search",
		Description: "search",
		InputSchema: map[string]any{
			"type":     "object",
			"required": required,
		},
	}}, mcpstdio.Options{}, func(context.Context, string, map[string]any) (any, error) {
		return nil, nil
	}, nil)
	required[0] = "mutated-by-caller"

	h.send("tools/list", map[string]any{}, "schema")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "schema",
		"result": map[string]any{"tools": []any{map[string]any{
			"name":        "search",
			"description": "search",
			"inputSchema": map[string]any{
				"type":     "object",
				"required": []any{"query"},
			},
		}}},
	})
}

func TestExponentRequestIDIsRejectedWithoutNumericExpansion(t *testing.T) {
	h := newHarness(t, mcpstdio.Options{}, func(context.Context, string, map[string]any) (any, error) {
		return nil, nil
	}, nil)

	h.sendRaw(`{"jsonrpc":"2.0","id":1e1000000000,"method":"ping","params":{}}` + "\n")
	h.expectNoOutput()
	h.send("ping", map[string]any{}, "after-exponent")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "after-exponent",
		"result":  map[string]any{},
	})
}

func TestEOFAndReadFailureCancelAndJoinHandlers(t *testing.T) {
	t.Run("EOF", func(t *testing.T) {
		started := make(chan struct{}, 1)
		observed := make(chan struct{})
		h := newHarness(t, mcpstdio.Options{}, func(ctx context.Context, _ string, _ map[string]any) (any, error) {
			started <- struct{}{}
			<-ctx.Done()
			close(observed)
			return nil, ctx.Err()
		}, nil)

		h.send("tools/call", map[string]any{"name": "echo", "arguments": map[string]any{}}, "pending")
		select {
		case <-started:
		case <-time.After(testTimeout):
			t.Fatal("tool did not start")
		}
		if err := h.closeInput(); err != nil {
			t.Fatalf("Serve after EOF: %v", err)
		}
		select {
		case <-observed:
		case <-time.After(testTimeout):
			t.Fatal("EOF did not cancel executor")
		}
		h.expectNoOutput()
	})

	t.Run("read error", func(t *testing.T) {
		readFailure := errors.New("read failed")
		server, err := mcpstdio.New(nil, func(context.Context, string, map[string]any) (any, error) {
			return nil, nil
		}, nil, mcpstdio.Options{ServerVersion: "test-version"})
		if err != nil {
			t.Fatalf("New: %v", err)
		}
		var output bytes.Buffer
		err = server.Serve(context.Background(), &failingReader{err: readFailure}, &output)
		if !errors.Is(err, readFailure) {
			t.Fatalf("Serve error = %v, want %v", err, readFailure)
		}
		if output.Len() != 0 {
			t.Fatalf("read failure wrote stdout: %q", output.String())
		}
	})
}

func TestCallerCancellationClosesBlockedInput(t *testing.T) {
	server, err := mcpstdio.New(nil, func(context.Context, string, map[string]any) (any, error) {
		return nil, nil
	}, nil, mcpstdio.Options{ServerVersion: "test-version"})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	input := &blockingReadCloser{closed: make(chan struct{})}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- server.Serve(ctx, input, &bytes.Buffer{})
	}()
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("Serve error = %v, want context.Canceled", err)
		}
	case <-time.After(testTimeout):
		t.Fatal("Serve did not stop after context cancellation")
	}
	select {
	case <-input.closed:
	default:
		t.Fatal("Serve did not close the blocked input")
	}
}

func TestOversizedInputIsDiscardedAndSessionRecovers(t *testing.T) {
	h := newHarness(t, mcpstdio.Options{MaxMessageBytes: 100}, func(context.Context, string, map[string]any) (any, error) {
		return nil, nil
	}, nil)

	h.sendRaw(strings.Repeat("x", 101) + "\n")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"method":  "notifications/message",
		"params": map[string]any{
			"level":  "error",
			"data":   "Internal Server Error",
			"logger": "mcp.server.exception_handler",
		},
	})
	h.send("ping", map[string]any{}, "after-oversize")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"id":      "after-oversize",
		"result":  map[string]any{},
	})

	h.sendRaw(strings.Repeat(" ", 100) + "\n")
	assertJSONEqual(t, h.receive(), map[string]any{
		"jsonrpc": "2.0",
		"method":  "notifications/message",
		"params": map[string]any{
			"level":  "error",
			"data":   "Internal Server Error",
			"logger": "mcp.server.exception_handler",
		},
	})
}

func TestWriteFailureCancelsHandlersAndIsReturned(t *testing.T) {
	writeFailure := errors.New("write failed")
	server, err := mcpstdio.New(nil, func(ctx context.Context, _ string, _ map[string]any) (any, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	}, nil, mcpstdio.Options{ServerVersion: "test-version"})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	inputReader, inputWriter := io.Pipe()
	defer inputWriter.Close()
	done := make(chan error, 1)
	go func() {
		done <- server.Serve(context.Background(), inputReader, failingWriter{err: writeFailure})
	}()
	if _, err := io.WriteString(inputWriter, `{"jsonrpc":"2.0","id":"ping","method":"ping","params":{}}`+"\n"); err != nil {
		t.Fatalf("write request: %v", err)
	}
	select {
	case err := <-done:
		if !errors.Is(err, writeFailure) {
			t.Fatalf("Serve error = %v, want %v", err, writeFailure)
		}
	case <-time.After(testTimeout):
		t.Fatal("Serve did not stop after output failure")
	}
}

func TestBackpressuredOutputCanBeAborted(t *testing.T) {
	server, err := mcpstdio.New(nil, func(context.Context, string, map[string]any) (any, error) {
		return nil, nil
	}, nil, mcpstdio.Options{ServerVersion: "test-version"})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	inputReader, inputWriter := io.Pipe()
	outputReader, outputWriter := io.Pipe()
	defer outputReader.Close()
	writer := &observingPipeWriter{
		PipeWriter: outputWriter,
		started:    make(chan struct{}),
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- server.Serve(ctx, inputReader, writer)
	}()
	if _, err := io.WriteString(inputWriter, `{"jsonrpc":"2.0","id":"ping","method":"ping","params":{}}`+"\n"); err != nil {
		t.Fatalf("write request: %v", err)
	}
	select {
	case <-writer.started:
	case <-time.After(testTimeout):
		t.Fatal("output did not reach the backpressured writer")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("Serve error = %v, want context.Canceled", err)
		}
	case <-time.After(testTimeout):
		_ = outputReader.Close()
		t.Fatal("backpressured Serve did not stop after cancellation")
	}
}

func TestNewRejectsInvalidConfiguration(t *testing.T) {
	execute := func(context.Context, string, map[string]any) (any, error) { return nil, nil }
	cases := []struct {
		name  string
		tools []mcpstdio.Tool
		exec  func(context.Context, string, map[string]any) (any, error)
		opts  mcpstdio.Options
	}{
		{name: "missing executor", opts: mcpstdio.Options{ServerVersion: "v"}},
		{name: "missing version", exec: execute},
		{name: "duplicate tool", tools: []mcpstdio.Tool{{Name: "echo"}, {Name: "echo"}}, exec: execute, opts: mcpstdio.Options{ServerVersion: "v"}},
		{name: "empty tool name", tools: []mcpstdio.Tool{{}}, exec: execute, opts: mcpstdio.Options{ServerVersion: "v"}},
		{name: "negative concurrency", exec: execute, opts: mcpstdio.Options{ServerVersion: "v", MaxConcurrent: -1}},
		{name: "negative message size", exec: execute, opts: mcpstdio.Options{ServerVersion: "v", MaxMessageBytes: -1}},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			if _, err := mcpstdio.New(testCase.tools, testCase.exec, nil, testCase.opts); err == nil {
				t.Fatal("New accepted invalid configuration")
			}
		})
	}
}

type failingReader struct {
	err error
}

func (r *failingReader) Read([]byte) (int, error) { return 0, r.err }
func (r *failingReader) Close() error             { return nil }

type failingWriter struct {
	err error
}

func (w failingWriter) Write([]byte) (int, error) { return 0, w.err }

type blockingReadCloser struct {
	closed chan struct{}
	once   sync.Once
}

func (r *blockingReadCloser) Read([]byte) (int, error) {
	<-r.closed
	return 0, io.ErrClosedPipe
}

func (r *blockingReadCloser) Close() error {
	r.once.Do(func() { close(r.closed) })
	return nil
}

type observingPipeWriter struct {
	*io.PipeWriter
	once    sync.Once
	started chan struct{}
}

func (w *observingPipeWriter) Write(data []byte) (int, error) {
	w.once.Do(func() { close(w.started) })
	return w.PipeWriter.Write(data)
}
