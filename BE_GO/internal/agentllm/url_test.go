package agentllm

import (
	"bufio"
	"context"
	"errors"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// TestBaseURLMatchesHttpx checks httpx's normalisation of the base URL and the
// request URL the SDK builds from it.
func TestBaseURLMatchesHttpx(t *testing.T) {
	for _, test := range httpxGoldenCases {
		client, err := New(Config{APIKey: "sk-test", BaseURL: test.base})
		if err != nil {
			t.Errorf("New(%q): %v", test.base, err)
			continue
		}
		base, err := urlParse(test.base)
		if err != nil {
			t.Errorf("urlParse(%q): %v", test.base, err)
			continue
		}
		if base, err = base.enforceTrailingSlash(); err != nil {
			t.Errorf("trailing slash for %q: %v", test.base, err)
			continue
		}
		if got := base.String(); got != test.baseURL {
			t.Errorf("base URL for %q = %q, want %q", test.base, got, test.baseURL)
		}
		if client.endpoint.target != test.target {
			t.Errorf("request target for %q = %q, want %q", test.base, client.endpoint.target, test.target)
		}
		if client.endpoint.host != test.host {
			t.Errorf("Host header for %q = %q, want %q", test.base, client.endpoint.host, test.host)
		}
	}
}

// TestInvalidURLMatchesHttpx checks that a URL httpx rejects is an
// InvalidURLError carrying str(httpx.InvalidURL).
func TestInvalidURLMatchesHttpx(t *testing.T) {
	for _, test := range httpxGoldenInvalidURLs {
		_, err := New(Config{APIKey: "sk-test", BaseURL: test.base})
		var invalid *InvalidURLError
		if !errors.As(err, &invalid) {
			t.Errorf("New(%q) error = %v, want InvalidURLError", test.base, err)
			continue
		}
		if invalid.Text != test.text {
			t.Errorf("New(%q) error text = %q, want %q", test.base, invalid.Text, test.text)
		}
	}
}

// TestNewUsesDefaultBaseURL is AsyncOpenAI() without a base URL argument.
func TestNewUsesDefaultBaseURL(t *testing.T) {
	client, err := New(Config{APIKey: "sk-test", Getenv: func(string) (string, bool) { return "", false }})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if client.endpoint.target != "/v1/chat/completions" || client.endpoint.host != "api.openai.com" {
		t.Errorf("default endpoint = %q %q", client.endpoint.host, client.endpoint.target)
	}
}

// TestRequestTargetOnTheWire checks the bytes the transport writes: the target
// is httpx's raw_path, not net/url's re-encoding of the path, and the Host
// header is httpx's netloc. The dialer sends every base URL to a local listener
// that records the request line and the Host header.
func TestRequestTargetOnTheWire(t *testing.T) {
	for _, test := range []struct {
		name, base, target, host string
	}{
		{"plain", "http://api.example.com/v1", "/v1/chat/completions", "api.example.com"},
		{"escapes kept", "http://api.example.com/v1/%41%c3%a9", "/v1/%41%c3%a9/chat/completions", "api.example.com"},
		{"invalid escape", "http://api.example.com/v1/%zz", "/v1/%zz/chat/completions", "api.example.com"},
		{"kept and encoded", "http://api.example.com/v1/\\^`{|}",
			"/v1/\\^%60%7B|%7D/chat/completions", "api.example.com"},
		{"double slash", "http://api.example.com//v1", "//v1/chat/completions", "api.example.com"},
		{"query", "http://api.example.com/v1?x=1", "/v1?x=1/chat/completions", "api.example.com"},
		{"port", "http://api.example.com:8080/v1", "/v1/chat/completions", "api.example.com:8080"},
		{"idna", "http://bücher.example/v1", "/v1/chat/completions", "xn--bcher-kva.example"},
	} {
		listener, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatalf("%s: listen: %v", test.name, err)
		}
		defer listener.Close()
		type captured struct{ line, host string }
		recorded := make(chan captured, 1)
		go func() {
			connection, err := listener.Accept()
			if err != nil {
				recorded <- captured{line: "accept: " + err.Error()}
				return
			}
			defer connection.Close()
			reader := bufio.NewReader(connection)
			line, _ := reader.ReadString('\n')
			line = strings.TrimSuffix(strings.TrimSuffix(line, "\n"), "\r")
			host := ""
			for {
				header, err := reader.ReadString('\n')
				if err != nil || header == "\r\n" || header == "\n" {
					break
				}
				if len(header) > 5 && strings.EqualFold(header[:5], "host:") {
					host = strings.TrimSpace(header[5:])
				}
			}
			_, _ = connection.Write([]byte("HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"))
			recorded <- captured{line: line, host: host}
		}()

		dial := func(ctx context.Context, network, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, network, listener.Addr().String())
		}
		client, err := New(Config{APIKey: "sk-test", BaseURL: test.base,
			Transport: &http.Transport{DialContext: dial, DisableCompression: true}})
		if err != nil {
			t.Fatalf("%s: New(%q): %v", test.name, test.base, err)
		}
		stream, err := client.Create(context.Background(), []byte(`{}`))
		if err != nil {
			t.Fatalf("%s: Create: %v", test.name, err)
		}
		_ = stream.Close()
		got := <-recorded
		if want := "POST " + test.target + " HTTP/1.1"; got.line != want {
			t.Errorf("%s: request line %q, want %q", test.name, got.line, want)
		}
		if want := test.host; got.host != want {
			t.Errorf("%s: Host header %q, want %q", test.name, got.host, want)
		}
	}
}

// TestRequestHasNoHostForRelativeBaseURL is a base URL without a host: httpx
// sends no Host header and Go's transport refuses the request, which the SDK
// reports as a connection error.
func TestRequestHasNoHostForRelativeBaseURL(t *testing.T) {
	client, err := New(Config{APIKey: "sk-test", BaseURL: "v1"})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if client.endpoint.host != "" || client.endpoint.target != "v1/v1/chat/completions" {
		t.Fatalf("endpoint = %q %q", client.endpoint.host, client.endpoint.target)
	}
	client.Sleep = func(context.Context, time.Duration) error { return nil }
	_, err = client.Create(context.Background(), []byte(`{}`))
	var failure *APIError
	if !errors.As(err, &failure) || failure.Text != "Connection error." {
		t.Fatalf("Create error = %v, want the SDK's connection error", err)
	}
}

// TestBaseURLNarrowCases: ports in other scripts are read like int() (httpx
// 0.28.1: 'http://h.example:٨٠/v1' -> 'http://h.example/v1',
// 'https://h.example:１_0/v1' -> port 10), and the two inputs this port does
// not reproduce stay refused: a port past int's range (Python keeps it) and an
// "xn--" host idna.decode rejects (httpx raises while building each request).
func TestBaseURLNarrowCases(t *testing.T) {
	for raw, want := range map[string]string{
		"http://h.example:٨٠/v1":   "h.example",
		"https://h.example:１_0/v1": "h.example:10",
	} {
		target, err := newEndpoint(raw)
		if err != nil || target.host != want {
			t.Errorf("%q: host %q err %v, want %q", raw, target.host, err, want)
		}
	}
	for _, raw := range []string{"http://h.example:99999999999999999999/v1", "https://xn--a.example/v1"} {
		if _, err := newEndpoint(raw); !errors.Is(err, refusal.ErrNotPorted) {
			t.Errorf("%q: err %v, want the refusal", raw, err)
		}
	}
	if _, err := newEndpoint("https://xn--bcher-kva.example/v1"); err != nil {
		t.Errorf("valid A-label: %v", err)
	}
}
