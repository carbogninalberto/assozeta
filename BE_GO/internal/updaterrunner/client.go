// Package updaterrunner is BE/instance/updater_client.py: the private,
// authenticated Unix-socket HTTP transport to the independent update runner.
package updaterrunner

import (
	"bufio"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

const (
	// DevelopmentText is _runner_directory()'s refusal outside production.
	DevelopmentText = "Gli aggiornamenti dell’installazione sono disabilitati in sviluppo."
	// UnreachableText is every transport failure (OSError, ValueError, HTTPException).
	UnreachableText = "Il servizio di aggiornamento non è raggiungibile. Verifica l’esito della configurazione automatica."
)

// Unavailable is UpdaterUnavailable(message).
type Unavailable struct{ Message string }

func (e *Unavailable) Error() string { return e.Message }

// Client reaches '<Directory>/runner.sock' with the bearer token of
// '<Directory>/token' (ASSOZETA_UPDATER_DIRECTORY), only in production mode.
type Client struct {
	Production bool
	Directory  string
}

// Reply is a runner answer read to the end (call_runner).
type Reply struct {
	Status int
	Body   []byte
}

// Stream is a runner answer whose body the caller relays and closes
// (open_runner_stream).
type Stream struct {
	Status int
	Header http.Header
	// ContentLength is -1 when the header is absent.
	ContentLength int64
	Body          io.ReadCloser
}

func unreachable() error { return &Unavailable{Message: UnreachableText} }

// directory is _runner_directory().
func (c Client) directory() (string, error) {
	if !c.Production {
		return "", &Unavailable{Message: DevelopmentText}
	}
	return c.Directory, nil
}

// token is (directory / 'token').read_text().strip(): a missing, unreadable,
// non-UTF-8 or blank file is an OSError/ValueError.
func token(directory string) (string, error) {
	raw, err := os.ReadFile(filepath.Join(directory, "token"))
	if err != nil || !utf8.Valid(raw) {
		return "", unreachable()
	}
	value := strings.TrimFunc(string(raw), unicode.IsSpace)
	if value == "" {
		return "", unreachable()
	}
	return value, nil
}

// timedConn applies Python's socket timeout: every read and write gets the
// full timeout, not the request as a whole.
type timedConn struct {
	net.Conn
	timeout time.Duration
}

func (c timedConn) Read(p []byte) (int, error) {
	if err := c.Conn.SetReadDeadline(time.Now().Add(c.timeout)); err != nil {
		return 0, err
	}
	return c.Conn.Read(p)
}

func (c timedConn) Write(p []byte) (int, error) {
	if err := c.Conn.SetWriteDeadline(time.Now().Add(c.timeout)); err != nil {
		return 0, err
	}
	return c.Conn.Write(p)
}

// do sends one http.client request: 'Host: localhost', 'Accept-Encoding:
// identity', the given headers, no User-Agent and no body.
func do(ctx context.Context, directory, method, path string, headers map[string]string, timeout time.Duration) (*http.Response, error) {
	for i := 0; i < len(path); i++ {
		if path[i] >= 0x7f || path[i] <= ' ' {
			return nil, unreachable() // putrequest: non-ASCII or control characters in the URL (ValueError)
		}
	}
	dialer := net.Dialer{Timeout: timeout}
	conn, err := dialer.DialContext(ctx, "unix", filepath.Join(directory, "runner.sock"))
	if err != nil {
		return nil, unreachable()
	}
	timed := timedConn{Conn: conn, timeout: timeout}
	request, err := http.NewRequestWithContext(ctx, method, "http://localhost"+path, nil)
	if err != nil {
		_ = conn.Close()
		return nil, unreachable()
	}
	request.Host = "localhost"
	request.Header["User-Agent"] = []string{""}
	request.Header.Set("Accept-Encoding", "identity")
	for name, value := range headers {
		if strings.ContainsAny(value, "\r\n\x00") {
			_ = conn.Close()
			return nil, unreachable() // http.client: invalid header value (ValueError)
		}
		request.Header.Set(name, value)
	}
	if err := request.Write(timed); err != nil {
		_ = conn.Close()
		return nil, unreachable()
	}
	response, err := http.ReadResponse(bufio.NewReader(timed), request)
	if err != nil {
		_ = conn.Close()
		return nil, unreachable()
	}
	response.Body = closer{ReadCloser: response.Body, conn: conn}
	return response, nil
}

type closer struct {
	io.ReadCloser
	conn net.Conn
}

func (c closer) Close() error {
	err := c.ReadCloser.Close()
	if closeErr := c.conn.Close(); err == nil {
		err = closeErr
	}
	return err
}

// Call is call_runner(method, path) without a payload: the whole answer, or
// Unavailable for the directory and transport failures. The caller decodes
// the JSON body (a decoding ValueError is Unavailable too).
func (c Client) Call(ctx context.Context, method, path string) (*Reply, error) {
	directory, err := c.directory()
	if err != nil {
		return nil, err
	}
	bearer, err := token(directory)
	if err != nil {
		return nil, err
	}
	response, err := do(ctx, directory, method, path, map[string]string{
		"Authorization": "Bearer " + bearer, "Content-Type": "application/json"}, 5*time.Second)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, unreachable()
	}
	return &Reply{Status: response.StatusCode, Body: body}, nil
}

// Open is open_runner_stream(path) up to the response head (timeout 300 s).
func (c Client) Open(ctx context.Context, path string) (*Stream, error) {
	directory, err := c.directory()
	if err != nil {
		return nil, err
	}
	bearer, err := token(directory)
	if err != nil {
		return nil, err
	}
	response, err := do(ctx, directory, http.MethodGet, path, map[string]string{"Authorization": "Bearer " + bearer}, 300*time.Second)
	if err != nil {
		return nil, err
	}
	length := response.ContentLength
	if len(response.TransferEncoding) > 0 || response.Header.Get("Content-Length") == "" {
		length = -1
	}
	return &Stream{Status: response.StatusCode, Header: response.Header, ContentLength: length, Body: response.Body}, nil
}

// ReadError reads a refused stream's body (the caller decodes it); a read
// failure is Unavailable.
func ReadError(stream *Stream) ([]byte, error) {
	defer stream.Body.Close()
	body, err := io.ReadAll(stream.Body)
	if err != nil {
		return nil, unreachable()
	}
	return body, nil
}

// IsUnavailable reports an Unavailable error and its message.
func IsUnavailable(err error) (string, bool) {
	var unavailable *Unavailable
	if errors.As(err, &unavailable) {
		return unavailable.Message, true
	}
	return "", false
}
