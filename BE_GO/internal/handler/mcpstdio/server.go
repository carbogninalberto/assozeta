// Package mcpstdio implements the JSON-lines transport used by the existing
// Assozeta MCP server.
package mcpstdio

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	defaultServerName      = "bakney-sport-data"
	defaultMaxConcurrent   = 16
	defaultMaxMessageBytes = 8 * 1024 * 1024
	latestProtocolVersion  = "2025-11-25"
	minimumQueuedTools     = 64
	maximumQueuedTools     = 1024
	maximumIntegerDigits   = 4300
)

var supportedProtocolVersions = map[string]struct{}{
	"2024-11-05": {},
	"2025-03-26": {},
	"2025-06-18": {},
	"2025-11-25": {},
}

// Tool is the MCP tool declaration exposed by tools/list.
type Tool struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	InputSchema map[string]any `json:"inputSchema"`
}

// Options configures a stdio server. Zero values use the compatibility defaults.
type Options struct {
	ServerName      string
	ServerVersion   string
	MaxConcurrent   int
	MaxMessageBytes int
	ValidateJSON    func(string, json.RawMessage) error
}

// Server serves JSON-RPC requests over a newline-delimited stream.
type Server struct {
	tools        []Tool
	toolNames    map[string]struct{}
	execute      func(context.Context, string, map[string]any) (any, error)
	validate     func(string, map[string]any) error
	validateJSON func(string, json.RawMessage) error
	serverName   string
	version      string
	maxActive    int
	maxQueued    int
	maxMessage   int
}

// New constructs a transport over caller-owned tool and service functions. It
// clones the supplied schemas so later caller mutation cannot race tools/list.
func New(
	tools []Tool,
	execute func(context.Context, string, map[string]any) (any, error),
	validate func(string, map[string]any) error,
	opts Options,
) (*Server, error) {
	if execute == nil {
		return nil, errors.New("mcpstdio: executor is required")
	}
	if strings.TrimSpace(opts.ServerVersion) == "" {
		return nil, errors.New("mcpstdio: server version is required")
	}
	if opts.MaxConcurrent < 0 {
		return nil, errors.New("mcpstdio: max concurrency cannot be negative")
	}
	if opts.MaxMessageBytes < 0 {
		return nil, errors.New("mcpstdio: max message size cannot be negative")
	}
	if opts.ServerName == "" {
		opts.ServerName = defaultServerName
	}
	if opts.MaxConcurrent == 0 {
		opts.MaxConcurrent = defaultMaxConcurrent
	}
	if opts.MaxMessageBytes == 0 {
		opts.MaxMessageBytes = defaultMaxMessageBytes
	}

	server := &Server{
		tools:        make([]Tool, 0, len(tools)),
		toolNames:    make(map[string]struct{}, len(tools)),
		execute:      execute,
		validate:     validate,
		validateJSON: opts.ValidateJSON,
		serverName:   opts.ServerName,
		version:      opts.ServerVersion,
		maxActive:    opts.MaxConcurrent,
		maxQueued:    toolQueueCapacity(opts.MaxConcurrent),
		maxMessage:   opts.MaxMessageBytes,
	}
	for _, tool := range tools {
		if tool.Name == "" {
			return nil, errors.New("mcpstdio: tool name is required")
		}
		if _, exists := server.toolNames[tool.Name]; exists {
			return nil, fmt.Errorf("mcpstdio: duplicate tool %q", tool.Name)
		}
		server.toolNames[tool.Name] = struct{}{}
		tool.InputSchema = cloneMap(tool.InputSchema)
		server.tools = append(server.tools, tool)
	}
	return server, nil
}

// Serve consumes newline-delimited JSON until EOF, cancellation or a transport
// failure. Tool calls use a fixed worker set and bounded queue; excess calls
// receive an isError text result of "Server busy". Protocol controls bypass the
// tool queue so ping and cancellation remain responsive at saturation.
//
// Output is serialized. Shutdown aborts output writers that expose
// SetWriteDeadline or CloseWithError. An arbitrary writer that blocks and offers
// neither mechanism can delay shutdown until that Write call returns.
func (s *Server) Serve(ctx context.Context, input io.ReadCloser, output io.Writer) error {
	if ctx == nil {
		return errors.New("mcpstdio: context is required")
	}
	if input == nil {
		return errors.New("mcpstdio: input is required")
	}
	if output == nil {
		return errors.New("mcpstdio: output is required")
	}

	serveCtx, cancel := context.WithCancel(ctx)
	session := &serveSession{
		server: s,
		ctx:    serveCtx,
		cancel: cancel,
		output: output,
		tools:  make(chan queuedTool, s.maxQueued),
		flight: make(map[string]*inflight),
	}
	session.startWorkers()

	readerDone := make(chan struct{})
	go func() {
		select {
		case <-serveCtx.Done():
			_ = input.Close()
			session.abort()
		case <-readerDone:
		}
	}()

	reader := bufio.NewReaderSize(input, 32*1024)
	var readErr error
	for {
		line, oversized, err := readLine(reader, s.maxMessage)
		if oversized {
			session.sendErrorNotification()
			if session.failed() {
				readErr = session.failure()
				break
			}
			if err != nil && !errors.Is(err, io.EOF) {
				readErr = err
				break
			}
			if errors.Is(err, io.EOF) {
				readErr = io.EOF
				break
			}
			continue
		}
		if err != nil {
			readErr = err
			break
		}

		session.processLine(line)
		if session.failed() {
			readErr = session.failure()
			break
		}
	}

	session.stop()
	cancel()
	close(readerDone)
	_ = input.Close()
	close(session.tools)
	session.wait()

	if err := session.failure(); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if readErr != nil && !errors.Is(readErr, io.EOF) {
		return readErr
	}
	return nil
}

type serveSession struct {
	server *Server
	ctx    context.Context
	cancel context.CancelFunc
	output io.Writer
	tools  chan queuedTool

	mu       sync.Mutex
	outputMu sync.Mutex
	flight   map[string]*inflight
	stopped  bool
	aborting bool
	writeErr error

	workers sync.WaitGroup
}

type queuedTool struct {
	ctx     context.Context
	current *inflight
	params  json.RawMessage
}

type inflight struct {
	key       string
	id        json.RawMessage
	cancel    context.CancelFunc
	responded bool
}

type rpcEnvelope struct {
	JSONRPC json.RawMessage `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Method  json.RawMessage `json:"method"`
	Params  json.RawMessage `json:"params"`
	Result  json.RawMessage `json:"result"`
	Error   json.RawMessage `json:"error"`
}

type request struct {
	method string
	params json.RawMessage
}

func (s *serveSession) processLine(line []byte) {
	line = bytes.TrimSpace(line)
	if len(line) == 0 {
		s.sendErrorNotification()
		return
	}

	var envelope rpcEnvelope
	if err := json.Unmarshal(line, &envelope); err != nil {
		s.sendErrorNotification()
		return
	}
	if !rawStringEquals(envelope.JSONRPC, "2.0") {
		s.sendErrorNotification()
		return
	}

	if envelope.Method == nil {
		if envelope.Result != nil || envelope.Error != nil {
			// Responses from the peer do not require action from this server.
			return
		}
		s.sendErrorNotification()
		return
	}

	var method string
	if err := json.Unmarshal(envelope.Method, &method); err != nil {
		s.sendErrorNotification()
		return
	}

	if envelope.ID == nil {
		if method == "notifications/cancelled" {
			s.handleCancellation(envelope.Params)
		}
		return
	}
	key, normalizedID, malformed, valid := canonicalID(envelope.ID)
	if malformed {
		s.sendErrorNotification()
		return
	}
	if !valid {
		// MCP models non-integer IDs as notifications with an extra field.
		return
	}

	requestCtx, cancel := context.WithCancel(s.ctx)
	current := &inflight{
		key:    key,
		id:     normalizedID,
		cancel: cancel,
	}

	s.mu.Lock()
	if s.stopped || s.aborting {
		s.mu.Unlock()
		cancel()
		return
	}
	s.flight[key] = current
	s.mu.Unlock()

	if method == "tools/call" {
		queued := queuedTool{ctx: requestCtx, current: current, params: envelope.Params}
		select {
		case s.tools <- queued:
		default:
			s.finishToolError(current, "Server busy")
			current.cancel()
		}
		return
	}

	s.handleControl(requestCtx, current, request{method: method, params: envelope.Params})
	current.cancel()
}

func (s *serveSession) startWorkers() {
	s.workers.Add(s.server.maxActive)
	for range s.server.maxActive {
		go s.toolWorker()
	}
}

func (s *serveSession) toolWorker() {
	defer s.workers.Done()
	for {
		select {
		case <-s.ctx.Done():
			return
		case queued, ok := <-s.tools:
			if !ok {
				return
			}
			s.runTool(queued)
		}
	}
}

func (s *serveSession) runTool(queued queuedTool) {
	defer queued.current.cancel()
	defer func() {
		if recover() != nil {
			s.finishToolError(queued.current, "Tool execution failed")
		}
	}()
	if s.isStopped() || queued.ctx.Err() != nil {
		return
	}
	s.handleTool(queued.ctx, queued.current, queued.params)
}

func (s *serveSession) handleControl(_ context.Context, current *inflight, req request) {
	params, valid := decodeParams(req.params)
	if !valid {
		s.finishInvalidParams(current)
		return
	}

	switch req.method {
	case "ping":
		s.finishResult(current, map[string]any{})
	case "tools/list":
		if cursor, ok := params["cursor"]; ok {
			if cursor != nil {
				if _, valid := cursor.(string); !valid {
					s.finishInvalidParams(current)
					return
				}
			}
		}
		s.finishResult(current, map[string]any{"tools": s.server.tools})
	case "initialize":
		s.handleInitialize(current, params)
	default:
		s.finishInvalidParams(current)
	}
}

func (s *serveSession) handleInitialize(current *inflight, params map[string]any) {
	if params == nil {
		s.finishInvalidParams(current)
		return
	}
	version, versionOK := params["protocolVersion"]
	if !versionOK || !validProtocolVersion(version) {
		s.finishInvalidParams(current)
		return
	}
	if _, ok := params["capabilities"].(map[string]any); !ok {
		s.finishInvalidParams(current)
		return
	}
	clientInfo, ok := params["clientInfo"].(map[string]any)
	if !ok || !isString(clientInfo["name"]) || !isString(clientInfo["version"]) {
		s.finishInvalidParams(current)
		return
	}

	negotiated := latestProtocolVersion
	if requested, ok := version.(string); ok {
		if _, supported := supportedProtocolVersions[requested]; supported {
			negotiated = requested
		}
	}
	s.finishResult(current, map[string]any{
		"protocolVersion": negotiated,
		"capabilities": map[string]any{
			"experimental": map[string]any{},
			"tools":        map[string]any{"listChanged": false},
		},
		"serverInfo": map[string]any{
			"name":    s.server.serverName,
			"version": s.server.version,
		},
	})
}

func (s *serveSession) handleTool(ctx context.Context, current *inflight, rawParams json.RawMessage) {
	params, valid := decodeParams(rawParams)
	if !valid || params == nil {
		s.finishInvalidParams(current)
		return
	}
	name, ok := params["name"].(string)
	if !ok {
		s.finishInvalidParams(current)
		return
	}

	arguments := map[string]any{}
	if rawArguments, exists := params["arguments"]; exists && rawArguments != nil {
		var validArguments bool
		arguments, validArguments = rawArguments.(map[string]any)
		if !validArguments {
			s.finishInvalidParams(current)
			return
		}
	}

	if _, exists := s.server.toolNames[name]; !exists {
		body, err := marshalJSON(map[string]any{"error": "Unknown tool: " + name})
		if err != nil {
			s.finishToolError(current, "Tool execution failed")
			return
		}
		s.finishToolText(current, string(body), false)
		return
	}

	if s.server.validateJSON != nil {
		rawArguments, valid := toolArgumentsJSON(rawParams)
		if !valid {
			s.finishInvalidParams(current)
			return
		}
		if err := s.server.validateJSON(name, rawArguments); err != nil {
			s.finishToolText(current, err.Error(), true)
			return
		}
	} else if s.server.validate != nil {
		if err := s.server.validate(name, arguments); err != nil {
			s.finishToolText(current, err.Error(), true)
			return
		}
	}
	if err := ctx.Err(); err != nil {
		return
	}

	result, err := s.server.execute(ctx, name, arguments)
	if err != nil {
		s.finishToolError(current, "Tool execution failed")
		return
	}
	body, err := marshalJSON(result)
	if err != nil {
		s.finishToolError(current, "Tool execution failed")
		return
	}
	s.finishToolText(current, string(body), false)
}

func (s *serveSession) handleCancellation(rawParams json.RawMessage) {
	params, valid := decodeParams(rawParams)
	if !valid || params == nil {
		return
	}
	rawID, ok := params["requestId"].(json.RawMessage)
	if !ok {
		if number, numberOK := params["requestId"].(json.Number); numberOK {
			rawID = json.RawMessage(number.String())
		} else if text, stringOK := params["requestId"].(string); stringOK {
			encoded, _ := json.Marshal(text)
			rawID = encoded
		} else {
			return
		}
	}
	key, _, malformed, valid := canonicalID(rawID)
	if malformed {
		return
	}
	if !valid {
		return
	}

	s.mu.Lock()
	current := s.flight[key]
	if current == nil || current.responded || s.stopped || s.aborting {
		s.mu.Unlock()
		return
	}
	current.responded = true
	delete(s.flight, key)
	s.mu.Unlock()
	current.cancel()
	s.write(errorResponse(current.id, 0, "Request cancelled", nil, false))
}

func (s *serveSession) finishResult(current *inflight, result any) {
	s.finish(current, map[string]any{
		"jsonrpc": "2.0",
		"id":      current.id,
		"result":  result,
	})
}

func (s *serveSession) finishInvalidParams(current *inflight) {
	s.finish(current, errorResponse(
		current.id,
		-32602,
		"Invalid request parameters",
		"",
		true,
	))
}

func (s *serveSession) finishToolText(current *inflight, text string, isError bool) {
	s.finishResult(current, map[string]any{
		"content": []map[string]any{{"type": "text", "text": text}},
		"isError": isError,
	})
}

func (s *serveSession) finishToolError(current *inflight, text string) {
	s.finishToolText(current, text, true)
}

func (s *serveSession) finish(current *inflight, response any) {
	s.mu.Lock()
	if s.stopped || s.aborting || current.responded {
		s.mu.Unlock()
		return
	}
	current.responded = true
	if s.flight[current.key] == current {
		delete(s.flight, current.key)
	}
	s.mu.Unlock()
	s.write(response)
}

func (s *serveSession) sendErrorNotification() {
	s.write(map[string]any{
		"jsonrpc": "2.0",
		"method":  "notifications/message",
		"params": map[string]any{
			"level":  "error",
			"data":   "Internal Server Error",
			"logger": "mcp.server.exception_handler",
		},
	})
}

func (s *serveSession) write(message any) {
	data, err := marshalJSON(message)
	if err != nil {
		s.fail(fmt.Errorf("mcpstdio: marshal response: %w", err))
		return
	}
	data = append(data, '\n')

	s.mu.Lock()
	if s.stopped || s.aborting {
		s.mu.Unlock()
		return
	}
	s.mu.Unlock()

	s.outputMu.Lock()
	defer s.outputMu.Unlock()

	s.mu.Lock()
	if s.stopped || s.aborting {
		s.mu.Unlock()
		return
	}
	s.mu.Unlock()

	for len(data) > 0 {
		written, err := s.output.Write(data)
		if err != nil {
			if !s.isStopped() {
				s.fail(err)
			}
			return
		}
		if written == 0 {
			if !s.isStopped() {
				s.fail(io.ErrShortWrite)
			}
			return
		}
		data = data[written:]
	}
}

func (s *serveSession) fail(err error) {
	s.mu.Lock()
	if s.stopped || s.aborting {
		s.mu.Unlock()
		return
	}
	recorded := false
	if s.writeErr == nil {
		s.writeErr = err
		recorded = true
	}
	s.mu.Unlock()
	if recorded {
		s.abortOutput()
		s.cancel()
	}
}

func (s *serveSession) failed() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.writeErr != nil
}

func (s *serveSession) failure() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.writeErr
}

func (s *serveSession) stop() {
	s.mu.Lock()
	s.stopped = true
	s.aborting = true
	cancels := make([]context.CancelFunc, 0, len(s.flight))
	for key, current := range s.flight {
		current.responded = true
		cancels = append(cancels, current.cancel)
		delete(s.flight, key)
	}
	s.mu.Unlock()
	for _, cancel := range cancels {
		cancel()
	}
	s.abortOutput()
}

func (s *serveSession) isStopped() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.stopped || s.aborting
}

func (s *serveSession) abort() {
	s.mu.Lock()
	s.aborting = true
	s.mu.Unlock()
	s.abortOutput()
}

func (s *serveSession) abortOutput() {
	type deadlineWriter interface {
		SetWriteDeadline(time.Time) error
	}
	type errorCloser interface {
		CloseWithError(error) error
	}
	if writer, ok := s.output.(deadlineWriter); ok {
		_ = writer.SetWriteDeadline(time.Now())
	}
	if writer, ok := s.output.(errorCloser); ok {
		_ = writer.CloseWithError(errors.New("mcpstdio: output aborted"))
	}
}

func (s *serveSession) wait() {
	s.workers.Wait()
}

func errorResponse(id json.RawMessage, code int, message string, data any, includeData bool) map[string]any {
	errorBody := map[string]any{
		"code":    code,
		"message": message,
	}
	if includeData {
		errorBody["data"] = data
	}
	return map[string]any{
		"jsonrpc": "2.0",
		"id":      id,
		"error":   errorBody,
	}
}

func decodeParams(raw json.RawMessage) (map[string]any, bool) {
	if raw == nil || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return nil, true
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var params map[string]any
	if err := decoder.Decode(&params); err != nil || params == nil {
		return nil, false
	}
	if decoder.More() {
		return nil, false
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, false
	}
	return params, true
}

func toolArgumentsJSON(rawParams json.RawMessage) (json.RawMessage, bool) {
	var fields struct {
		Arguments json.RawMessage `json:"arguments"`
	}
	if err := json.Unmarshal(rawParams, &fields); err != nil {
		return nil, false
	}
	arguments := bytes.TrimSpace(fields.Arguments)
	if len(arguments) == 0 || bytes.Equal(arguments, []byte("null")) {
		return json.RawMessage("{}"), true
	}
	return append(json.RawMessage(nil), fields.Arguments...), true
}

func rawStringEquals(raw json.RawMessage, want string) bool {
	var value string
	return json.Unmarshal(raw, &value) == nil && value == want
}

func canonicalID(raw json.RawMessage) (key string, normalized json.RawMessage, malformed bool, valid bool) {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 || bytes.Equal(raw, []byte("null")) {
		return "", nil, false, false
	}
	if raw[0] == '"' {
		var value string
		if err := json.Unmarshal(raw, &value); err != nil {
			return "", nil, true, false
		}
		return "s:" + value, append(json.RawMessage(nil), raw...), false, true
	}

	integer, malformed, valid := canonicalInteger(raw)
	if malformed || !valid {
		return "", nil, malformed, false
	}
	return "n:" + integer, json.RawMessage(integer), false, true
}

func validProtocolVersion(value any) bool {
	switch typed := value.(type) {
	case string:
		return true
	case bool:
		// Pydantic's int coercion accepts bool values as zero and one.
		return true
	case json.Number:
		return validNumericProtocolVersion(typed.String())
	default:
		return false
	}
}

func validNumericProtocolVersion(raw string) bool {
	if integer, malformed, valid := canonicalInteger([]byte(raw)); valid && !malformed {
		return integer != ""
	}

	value, err := strconv.ParseFloat(raw, 64)
	if err != nil || math.IsInf(value, 0) || math.IsNaN(value) || math.Trunc(value) != value {
		return false
	}

	// The installed Pydantic integer coercion accepts integral floats inside
	// the signed 64-bit range and rejects values that round onto its endpoints.
	const floatInt64Exclusive = float64(uint64(1) << 63)
	return value > -floatInt64Exclusive && value < floatInt64Exclusive
}

func canonicalInteger(raw []byte) (string, bool, bool) {
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 {
		return "", false, false
	}
	start := 0
	if raw[0] == '-' {
		start = 1
		if len(raw) == 1 {
			return "", false, false
		}
	}
	digits := raw[start:]
	if len(digits) == 0 {
		return "", false, false
	}
	for _, digit := range digits {
		if digit < '0' || digit > '9' {
			return "", false, false
		}
	}
	if len(digits) > maximumIntegerDigits {
		return "", true, false
	}
	if len(digits) > 1 && digits[0] == '0' {
		return "", false, false
	}

	allZero := true
	for _, digit := range digits {
		if digit != '0' {
			allZero = false
			break
		}
	}
	if allZero {
		return "0", false, true
	}
	return string(raw), false, true
}

func isString(value any) bool {
	_, ok := value.(string)
	return ok
}

func toolQueueCapacity(maxActive int) int {
	if maxActive >= maximumQueuedTools/4 {
		return maximumQueuedTools
	}
	if maxActive*4 > minimumQueuedTools {
		return maxActive * 4
	}
	return minimumQueuedTools
}

func readLine(reader *bufio.Reader, maximum int) ([]byte, bool, error) {
	var line []byte
	oversized := false
	for {
		fragment, err := reader.ReadSlice('\n')
		complete := len(fragment) > 0 && fragment[len(fragment)-1] == '\n'
		content := fragment
		if complete {
			content = fragment[:len(fragment)-1]
		}
		if len(content) > 0 && !oversized {
			if len(line)+len(content) > maximum {
				oversized = true
				line = nil
			} else {
				line = append(line, content...)
			}
		}

		if complete {
			if oversized {
				return nil, true, nil
			}
			return line, false, nil
		}
		switch {
		case errors.Is(err, bufio.ErrBufferFull):
			continue
		case errors.Is(err, io.EOF):
			if oversized {
				return nil, true, nil
			}
			if len(line) > 0 {
				return line, false, nil
			}
			return nil, false, io.EOF
		case err != nil:
			if oversized {
				return nil, true, nil
			}
			return nil, false, err
		}
	}
}

func marshalJSON(value any) ([]byte, error) {
	var output bytes.Buffer
	encoder := json.NewEncoder(&output)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(output.Bytes(), []byte{'\n'}), nil
}

func cloneMap(source map[string]any) map[string]any {
	if source == nil {
		return nil
	}
	cloned := make(map[string]any, len(source))
	for key, value := range source {
		cloned[key] = cloneValue(value)
	}
	return cloned
}

func cloneValue(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		return cloneMap(typed)
	case []any:
		cloned := make([]any, len(typed))
		for index, item := range typed {
			cloned[index] = cloneValue(item)
		}
		return cloned
	case []string:
		return append([]string(nil), typed...)
	case json.RawMessage:
		return append(json.RawMessage(nil), typed...)
	default:
		return value
	}
}
