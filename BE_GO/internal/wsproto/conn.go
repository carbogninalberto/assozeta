package wsproto

import (
	"bufio"
	"bytes"
	"compress/flate"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

// uvicorn's defaults for the websockets protocol.
const (
	MaxMessageSize = 16 * 1024 * 1024
	maxQueue       = 32
	closeTimeout   = 10 * time.Second
	pingInterval   = 20 * time.Second
	pingTimeout    = 20 * time.Second
)

// Close codes used by the protocol itself.
const (
	CloseNormal         = 1000
	CloseProtocolError  = 1002
	CloseNoStatus       = 1005
	CloseAbnormal       = 1006
	CloseInvalidData    = 1007
	CloseMessageTooBig  = 1009
	CloseInternalError  = 1011
	CloseServiceRestart = 1012
)

const (
	opContinuation = 0x0
	opText         = 0x1
	opBinary       = 0x2
	opClose        = 0x8
	opPing         = 0x9
	opPong         = 0xA
)

// Message is one received data message.
type Message struct {
	Text bool
	Data []byte
}

// Disconnect reports how the connection ended, as uvicorn passes it to the
// application: the close code received (1005 without one) or 1006.
type Disconnect struct {
	Code   int
	Reason string
}

type state int

const (
	stateOpen state = iota
	stateClosing
	stateClosed
)

// Conn is one accepted connection. Writes are serialized; the reader runs in
// its own goroutine and delivers messages, then one Disconnect, on Incoming.
type Conn struct {
	netConn  net.Conn
	reader   *bufio.Reader
	deflate  *Deflate
	window   []byte // context takeover: the last 32 KiB of inflated client data
	writeMu  sync.Mutex
	stateMu  sync.Mutex
	state    state
	incoming chan any
	done     chan struct{}
	pings    map[string]chan struct{}
	pingMu   sync.Mutex
	finished sync.Once
	readDone chan struct{}
}

// Accept hijacks the request and writes the 101 response websockets writes:
// Upgrade, Connection, Sec-WebSocket-Accept, the accepted extension and Date.
func Accept(w http.ResponseWriter, key, extensions string, deflate *Deflate) (*Conn, error) {
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		return nil, errors.New("websocket: response does not support hijacking")
	}
	netConn, buffered, err := hijacker.Hijack()
	if err != nil {
		return nil, err
	}
	var response strings.Builder
	response.WriteString("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n")
	response.WriteString("Sec-WebSocket-Accept: " + AcceptValue(key) + "\r\n")
	if extensions != "" {
		response.WriteString("Sec-WebSocket-Extensions: " + extensions + "\r\n")
	}
	response.WriteString("Date: " + time.Now().UTC().Format(http.TimeFormat) + "\r\n\r\n")
	if _, err := netConn.Write([]byte(response.String())); err != nil {
		netConn.Close()
		return nil, err
	}
	c := &Conn{netConn: netConn, reader: buffered.Reader, deflate: deflate, incoming: make(chan any, maxQueue),
		done: make(chan struct{}), pings: map[string]chan struct{}{}, readDone: make(chan struct{})}
	go c.readLoop()
	go c.keepalive()
	return c, nil
}

// Incoming yields *Message values and finally one Disconnect.
func (c *Conn) Incoming() <-chan any { return c.incoming }

// Done is closed once the TCP connection is closed.
func (c *Conn) Done() <-chan struct{} { return c.done }

func (c *Conn) currentState() state {
	c.stateMu.Lock()
	defer c.stateMu.Unlock()
	return c.state
}

func (c *Conn) writeFrame(opcode byte, payload []byte) error {
	header := []byte{0x80 | opcode}
	switch size := len(payload); {
	case size < 126:
		header = append(header, byte(size))
	case size < 1<<16:
		header = append(header, 126, byte(size>>8), byte(size))
	default:
		header = append(header, 127)
		header = binary.BigEndian.AppendUint64(header, uint64(size))
	}
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	_, err := c.netConn.Write(append(header, payload...))
	return err
}

// ErrClosed is returned when sending after the closing handshake started.
var ErrClosed = errors.New("websocket: connection closed")

// WriteText sends one unfragmented text message. Outgoing messages are never
// compressed; permessage-deflate lets a sender leave RSV1 clear.
func (c *Conn) WriteText(data []byte) error {
	if c.currentState() != stateOpen {
		return ErrClosed
	}
	return c.writeFrame(opText, data)
}

func closePayload(code int, reason string) []byte {
	return append(binary.BigEndian.AppendUint16(nil, uint16(code)), reason...)
}

// startClosing writes a close frame if the connection is still open.
func (c *Conn) startClosing(payload []byte) bool {
	c.stateMu.Lock()
	if c.state != stateOpen {
		c.stateMu.Unlock()
		return false
	}
	c.state = stateClosing
	c.stateMu.Unlock()
	_ = c.writeFrame(opClose, payload)
	return true
}

// Close is the application's websocket.close: send the close frame and let
// the reader finish the closing handshake (bounded by the close timeout).
func (c *Conn) Close(code int, reason string) {
	if c.startClosing(closePayload(code, reason)) {
		go func() {
			select {
			case <-c.readDone:
			case <-time.After(closeTimeout):
				c.terminate()
			}
		}()
	}
}

// Abort closes the TCP connection without a close frame, as uvicorn does when
// the application raises after accepting.
func (c *Conn) Abort() { c.terminate() }

func (c *Conn) terminate() {
	c.finished.Do(func() {
		c.stateMu.Lock()
		c.state = stateClosed
		c.stateMu.Unlock()
		c.netConn.Close()
		close(c.done)
	})
}

// fail is websockets' fail_connection: a close frame (unless 1006 or already
// closing); the reader then exits, half-closes and, after the peer closes,
// closes the connection (finish).
func (c *Conn) fail(code int) {
	if code != CloseAbnormal {
		c.startClosing(closePayload(code, ""))
	}
}

// finish half-closes the TCP connection and waits for the peer's close.
func (c *Conn) finish() {
	c.stateMu.Lock()
	if c.state != stateClosed {
		c.state = stateClosing
	}
	c.stateMu.Unlock()
	if tcp, ok := c.netConn.(interface{ CloseWrite() error }); ok {
		_ = tcp.CloseWrite()
		_ = c.netConn.SetReadDeadline(time.Now().Add(closeTimeout))
		_, _ = io.Copy(io.Discard, c.reader)
	}
	c.terminate()
}

func (c *Conn) deliver(value any) {
	select {
	case c.incoming <- value:
	case <-c.done:
	}
}

type protocolError struct{ code int }

func (e protocolError) Error() string { return "websocket protocol error" }

type frame struct {
	fin    bool
	rsv    byte
	opcode byte
	data   []byte
}

func (c *Conn) readFrame(limit int) (frame, error) {
	var head [2]byte
	if _, err := io.ReadFull(c.reader, head[:]); err != nil {
		return frame{}, err
	}
	f := frame{fin: head[0]&0x80 != 0, rsv: head[0] >> 4 & 7, opcode: head[0] & 0x0f}
	switch f.opcode {
	case opContinuation, opText, opBinary, opClose, opPing, opPong:
	default:
		return f, protocolError{CloseProtocolError}
	}
	if head[1]&0x80 == 0 {
		return f, protocolError{CloseProtocolError}
	}
	length := uint64(head[1] & 0x7f)
	switch length {
	case 126:
		var extended [2]byte
		if _, err := io.ReadFull(c.reader, extended[:]); err != nil {
			return f, err
		}
		length = uint64(binary.BigEndian.Uint16(extended[:]))
	case 127:
		var extended [8]byte
		if _, err := io.ReadFull(c.reader, extended[:]); err != nil {
			return f, err
		}
		length = binary.BigEndian.Uint64(extended[:])
	}
	if length > uint64(limit) {
		return f, protocolError{CloseMessageTooBig}
	}
	var mask [4]byte
	if _, err := io.ReadFull(c.reader, mask[:]); err != nil {
		return f, err
	}
	f.data = make([]byte, length)
	if _, err := io.ReadFull(c.reader, f.data); err != nil {
		return f, err
	}
	for i := range f.data {
		f.data[i] ^= mask[i%4]
	}
	return f, nil
}

var emptyBlock = []byte{0x00, 0x00, 0xff, 0xff}

// inflate decompresses one compressed message with the context kept by the
// previous messages, bounded by limit bytes of output.
func (c *Conn) inflate(data []byte, limit int) ([]byte, error) {
	data = append(append([]byte(nil), data...), emptyBlock...)
	reader := flate.NewReaderDict(bytes.NewReader(data), c.window)
	out, err := io.ReadAll(io.LimitReader(reader, int64(limit)+1))
	if err != nil && !errors.Is(err, io.ErrUnexpectedEOF) {
		return nil, protocolError{CloseProtocolError}
	}
	if len(out) > limit {
		return nil, protocolError{CloseMessageTooBig}
	}
	return out, nil
}

func (c *Conn) remember(data []byte) {
	if c.deflate.ClientNoContextTakeover {
		return
	}
	c.window = append(c.window, data...)
	if len(c.window) > 32*1024 {
		c.window = append([]byte(nil), c.window[len(c.window)-32*1024:]...)
	}
}

// validUTF8Prefix reports whether data can still become valid UTF-8, as
// Python's incremental strict decoder decides fragment by fragment.
func validUTF8Prefix(data []byte, final bool) bool {
	if final {
		return utf8.Valid(data)
	}
	for start := len(data) - 1; start >= 0 && start >= len(data)-3; start-- {
		if data[start]&0xc0 != 0x80 {
			if !utf8.Valid(data[:start]) {
				return false
			}
			return incompleteRuneOK(data[start:])
		}
	}
	return utf8.Valid(data)
}

func incompleteRuneOK(tail []byte) bool {
	if utf8.FullRune(tail) {
		return utf8.Valid(tail)
	}
	lead := tail[0]
	var need int
	low, high := byte(0x80), byte(0xbf)
	switch {
	case lead >= 0xc2 && lead <= 0xdf:
		need = 2
	case lead == 0xe0:
		need, low = 3, 0xa0
	case lead == 0xed:
		need, high = 3, 0x9f
	case lead >= 0xe1 && lead <= 0xef:
		need = 3
	case lead == 0xf0:
		need, low = 4, 0x90
	case lead == 0xf4:
		need, high = 4, 0x8f
	case lead >= 0xf1 && lead <= 0xf3:
		need = 4
	default:
		return false
	}
	if len(tail) >= need {
		return false
	}
	for i, b := range tail[1:] {
		if i == 0 && (b < low || b > high) {
			return false
		}
		if b&0xc0 != 0x80 {
			return false
		}
	}
	return true
}

func validCloseCode(code int) bool {
	switch code {
	case 1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014:
		return true
	}
	return code >= 3000 && code < 5000
}

// readLoop is websockets' transfer_data: messages until the closing handshake
// or a failure, then the TCP close and the application's disconnect event.
func (c *Conn) readLoop() {
	disconnect := Disconnect{Code: CloseAbnormal}
	defer func() {
		close(c.readDone)
		// The application sees the disconnect as soon as the closing handshake
		// ends, while the TCP close is still in progress.
		c.deliver(disconnect)
		c.finish()
	}()
	var fragments []byte
	text, compressed, inMessage := false, false, false
	for {
		limit := MaxMessageSize - len(fragments)
		f, err := c.readFrame(limit)
		if err != nil {
			var failure protocolError
			if errors.As(err, &failure) {
				c.fail(failure.code)
			}
			return
		}
		// permessage-deflate clears RSV1 on the first frame of a compressed
		// message before the reserved-bit check; every other RSV bit fails.
		if c.deflate != nil && (f.opcode == opText || f.opcode == opBinary) && f.rsv&4 != 0 {
			f.rsv &^= 4
			compressed = true
		}
		if f.rsv != 0 {
			c.fail(CloseProtocolError)
			return
		}
		switch f.opcode {
		case opClose, opPing, opPong:
			if len(f.data) > 125 || !f.fin {
				c.fail(CloseProtocolError)
				return
			}
		}
		switch f.opcode {
		case opClose:
			switch {
			case len(f.data) == 0:
				disconnect = Disconnect{Code: CloseNoStatus}
			case len(f.data) == 1:
				c.fail(CloseProtocolError)
				return
			default:
				code := int(binary.BigEndian.Uint16(f.data))
				reason := f.data[2:]
				if !utf8.Valid(reason) {
					c.fail(CloseInvalidData)
					return
				}
				if !validCloseCode(code) {
					c.fail(CloseProtocolError)
					return
				}
				disconnect = Disconnect{Code: code, Reason: string(reason)}
			}
			// Echo the received close payload unless the server closed first.
			c.startClosing(f.data)
			return
		case opPing:
			if c.currentState() == stateOpen {
				_ = c.writeFrame(opPong, f.data)
			}
			continue
		case opPong:
			c.pingMu.Lock()
			if waiter, ok := c.pings[string(f.data)]; ok {
				delete(c.pings, string(f.data))
				close(waiter)
			}
			c.pingMu.Unlock()
			continue
		case opText, opBinary:
			if inMessage {
				c.fail(CloseProtocolError)
				return
			}
			inMessage, text, fragments = true, f.opcode == opText, nil
		case opContinuation:
			if !inMessage {
				c.fail(CloseProtocolError)
				return
			}
		}
		fragments = append(fragments, f.data...)
		if !compressed && text && !validUTF8Prefix(fragments, f.fin) {
			c.fail(CloseInvalidData)
			return
		}
		if !f.fin {
			continue
		}
		data := fragments
		if compressed {
			if data, err = c.inflate(fragments, MaxMessageSize); err != nil {
				c.fail(err.(protocolError).code)
				return
			}
			c.remember(data)
			if text && !utf8.Valid(data) {
				c.fail(CloseInvalidData)
				return
			}
		}
		message := &Message{Text: text, Data: data}
		inMessage, compressed, fragments = false, false, nil
		if c.currentState() != stateOpen {
			// Messages after our close frame are read but not delivered.
			continue
		}
		c.deliver(message)
	}
}

// keepalive pings every 20 s and fails the connection with 1011 when the pong
// does not arrive within 20 s.
func (c *Conn) keepalive() {
	ticker := time.NewTicker(pingInterval)
	defer ticker.Stop()
	for {
		select {
		case <-c.done:
			return
		case <-ticker.C:
		}
		if c.currentState() != stateOpen {
			return
		}
		payload := make([]byte, 4)
		_, _ = rand.Read(payload)
		waiter := make(chan struct{})
		c.pingMu.Lock()
		c.pings[string(payload)] = waiter
		c.pingMu.Unlock()
		if c.writeFrame(opPing, payload) != nil {
			return
		}
		select {
		case <-waiter:
		case <-c.done:
			return
		case <-time.After(pingTimeout):
			// fail_connection(1011, "keepalive ping timeout"); the reader then
			// ends on the closed socket.
			if c.startClosing(closePayload(CloseInternalError, "keepalive ping timeout")) {
				c.terminate()
			}
			return
		}
	}
}
