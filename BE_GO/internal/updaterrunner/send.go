package updaterrunner

import (
	"bufio"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
)

// ErrUploadAborted is upload_stream.UploadAborted: the request body ended
// before its Content-Length (the runner connection is closed unanswered).
var ErrUploadAborted = errors.New("upload aborted before its declared length")

// requestHead is http.client's putrequest/putheader/endheaders: the request
// line, 'Host: localhost', 'Accept-Encoding: identity', then the headers in
// order. A non-ASCII or control character in the path is putrequest's
// ValueError.
func requestHead(method, path string, headers [][2]string) ([]byte, error) {
	for i := 0; i < len(path); i++ {
		if path[i] >= 0x7f || path[i] <= ' ' {
			return nil, unreachable()
		}
	}
	var head strings.Builder
	head.WriteString(method + " " + path + " HTTP/1.1\r\nHost: localhost\r\nAccept-Encoding: identity\r\n")
	for _, header := range headers {
		if strings.ContainsAny(header[1], "\r\n\x00") {
			return nil, unreachable()
		}
		head.WriteString(header[0] + ": " + header[1] + "\r\n")
	}
	head.WriteString("\r\n")
	return []byte(head.String()), nil
}

// dial connects to the runner socket with Python's per-operation timeout.
func dial(ctx context.Context, directory string, timeout time.Duration) (timedConn, error) {
	dialer := net.Dialer{Timeout: timeout}
	conn, err := dialer.DialContext(ctx, "unix", filepath.Join(directory, "runner.sock"))
	if err != nil {
		return timedConn{}, unreachable()
	}
	handoff.MarkEffect(ctx, "updater request")
	return timedConn{Conn: conn, timeout: timeout}, nil
}

// readReply is getresponse() then response.read(): the whole answer.
func readReply(conn timedConn, method string) (*Reply, error) {
	response, err := http.ReadResponse(bufio.NewReader(conn), &http.Request{Method: method})
	if err != nil {
		return nil, unreachable()
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, unreachable()
	}
	return &Reply{Status: response.StatusCode, Body: body}, nil
}

// CallJSON is call_runner(method, path, payload, timeout): payload is
// json.dumps(payload) (nil sends no body and no Content-Length, as for
// payload=None on GET), with the bearer token and Content-Type
// application/json. The caller decodes the reply.
func (c Client) CallJSON(ctx context.Context, method, path string, payload []byte, timeout time.Duration) (*Reply, error) {
	directory, err := c.directory()
	if err != nil {
		return nil, err
	}
	bearer, err := token(directory)
	if err != nil {
		return nil, err
	}
	var headers [][2]string
	if payload != nil {
		headers = append(headers, [2]string{"Content-Length", strconv.Itoa(len(payload))})
	}
	headers = append(headers, [2]string{"Authorization", "Bearer " + bearer}, [2]string{"Content-Type", "application/json"})
	head, err := requestHead(method, path, headers)
	if err != nil {
		return nil, err
	}
	conn, err := dial(ctx, directory, timeout)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	if _, err := conn.Write(append(head, payload...)); err != nil {
		return nil, unreachable()
	}
	return readReply(conn, method)
}

// SendStream is send_runner_stream(path, chunks, length): the headers, then
// exactly length body bytes copied as they arrive. A broken pipe or reset
// while sending stops the copy and the runner's answer is still read (it may
// refuse before reading everything); a body that ends early is
// ErrUploadAborted.
func (c Client) SendStream(ctx context.Context, path string, body io.Reader, length int64) (*Reply, error) {
	directory, err := c.directory()
	if err != nil {
		return nil, err
	}
	bearer, err := token(directory)
	if err != nil {
		return nil, err
	}
	head, err := requestHead(http.MethodPost, path, [][2]string{{"Authorization", "Bearer " + bearer},
		{"Content-Type", "application/gzip"}, {"Content-Length", strconv.FormatInt(length, 10)}})
	if err != nil {
		return nil, err
	}
	conn, err := dial(ctx, directory, 300*time.Second)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	if _, err := conn.Write(head); err != nil {
		return nil, unreachable()
	}
	buffer := make([]byte, 1024*1024)
	remaining := length
	for remaining > 0 {
		size := int64(len(buffer))
		if remaining < size {
			size = remaining
		}
		n, readErr := body.Read(buffer[:size])
		if n > 0 {
			remaining -= int64(n)
			if _, err := conn.Write(buffer[:n]); err != nil {
				if errors.Is(err, syscall.EPIPE) || errors.Is(err, syscall.ECONNRESET) {
					break
				}
				return nil, unreachable()
			}
		}
		if remaining > 0 && readErr != nil {
			return nil, ErrUploadAborted
		}
	}
	return readReply(conn, http.MethodPost)
}
