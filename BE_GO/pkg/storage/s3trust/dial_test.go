package s3trust

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
)

func TestSuppliedDialHooksAndALPNArePreserved(t *testing.T) {
	for _, mode := range []string{"DialContext", "Dial", "DialTLSContext", "DialTLS", "HTTP2"} {
		t.Run(mode, func(t *testing.T) {
			clearEnvironment(t)
			server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
			server.EnableHTTP2 = mode == "HTTP2"
			server.StartTLS()
			defer server.Close()
			path := filepath.Join(t.TempDir(), "ca.pem")
			if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0600); err != nil {
				t.Fatal(err)
			}
			t.Setenv("AWS_CA_BUNDLE", path)
			base := http.DefaultTransport.(*http.Transport).Clone()
			var calls atomic.Int32
			roots := x509.NewCertPool()
			roots.AddCert(server.Certificate())
			tlsDial := func(network, address string) (net.Conn, error) {
				calls.Add(1)
				return tls.Dial(network, address, &tls.Config{RootCAs: roots})
			}
			switch mode {
			case "DialContext":
				base.DialContext = func(c context.Context, n, a string) (net.Conn, error) {
					calls.Add(1)
					return (&net.Dialer{}).DialContext(c, n, a)
				}
			case "Dial":
				base.DialContext = nil
				base.Dial = func(n, a string) (net.Conn, error) { calls.Add(1); return net.Dial(n, a) }
			case "DialTLSContext":
				base.DialTLSContext = func(_ context.Context, n, a string) (net.Conn, error) { return tlsDial(n, a) }
			case "DialTLS":
				base.DialTLS = tlsDial
			case "HTTP2":
				base.ForceAttemptHTTP2 = true
			}
			transport := Select(nil).Wrap(base)
			defer transport.CloseIdleConnections()
			response, err := (&http.Client{Transport: transport, Timeout: 2 * time.Second}).Get(server.URL)
			if err != nil {
				t.Fatal(err)
			}
			response.Body.Close()
			if mode == "HTTP2" {
				if response.ProtoMajor != 2 {
					t.Fatal("ALPN lost", response.Proto)
				}
			} else if calls.Load() != 1 {
				t.Fatal("supplied dial hook bypassed", mode, calls.Load())
			}
		})
	}
}

func TestTLSHandshakeTimeoutAndCallerCancellationBound(t *testing.T) {
	for _, cancelCaller := range []bool{false, true} {
		t.Run(map[bool]string{false: "timeout", true: "caller-cancel"}[cancelCaller], func(t *testing.T) {
			clearEnvironment(t)
			t.Setenv("AWS_CA_BUNDLE", "")
			listener, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			entered, closed := make(chan struct{}), make(chan struct{})
			go func() {
				defer close(closed)
				connection, e := listener.Accept()
				if e != nil {
					return
				}
				defer connection.Close()
				buffer := make([]byte, 4096)
				_, e = connection.Read(buffer)
				if e != nil {
					return
				}
				close(entered)
				for {
					if _, e = connection.Read(buffer); e != nil {
						return
					}
				}
			}()
			base := http.DefaultTransport.(*http.Transport).Clone()
			base.TLSHandshakeTimeout = 75 * time.Millisecond
			if cancelCaller {
				// net/http may finish a shared connection dial after its waiting
				// request is canceled; it must still obey this handshake bound.
				base.TLSHandshakeTimeout = 250 * time.Millisecond
			}
			transport := Select(nil).Wrap(base)
			defer transport.CloseIdleConnections()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			request, _ := http.NewRequestWithContext(ctx, http.MethodGet, "https://"+listener.Addr().String(), nil)
			done := make(chan error, 1)
			go func() {
				response, e := (&http.Client{Transport: transport}).Do(request)
				if response != nil {
					response.Body.Close()
				}
				done <- e
			}()
			select {
			case <-entered:
			case <-time.After(time.Second):
				t.Fatal("real TLS handshake did not begin")
			}
			if cancelCaller {
				cancel()
			}
			select {
			case e := <-done:
				if e == nil {
					t.Fatal("stalled TLS succeeded")
				}
				if cancelCaller && !errors.Is(e, context.Canceled) {
					t.Fatal("caller cancellation changed", e)
				}
				if !cancelCaller {
					var timeout net.Error
					if !errors.As(e, &timeout) || !timeout.Timeout() || errors.Is(e, context.DeadlineExceeded) {
						t.Fatal("internal handshake timeout became caller cancellation", e)
					}
				}
			case <-time.After(time.Second):
				t.Fatal("TLS handshake bound lost")
			}
			select {
			case <-closed:
			case <-time.After(time.Second):
				t.Fatal("TLS connection remained open after failure")
			}
			if !cancelCaller && ctx.Err() != nil {
				t.Fatal("timeout test accidentally canceled caller")
			}
		})
	}
}

func TestTLS13ResumptionRechecksConfiguredRoots(t *testing.T) {
	clearEnvironment(t)
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13}
	server.StartTLS()
	defer server.Close()
	path := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AWS_CA_BUNDLE", path)
	var resumed atomic.Int32
	base := http.DefaultTransport.(*http.Transport).Clone()
	base.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13,
		ClientSessionCache: tls.NewLRUClientSessionCache(2), VerifyConnection: func(state tls.ConnectionState) error {
			if state.DidResume {
				resumed.Add(1)
			}
			return nil
		}}
	transport := Select(nil).Wrap(base)
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport}
	for range 2 {
		response, err := client.Get(server.URL)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		transport.CloseIdleConnections()
	}
	if resumed.Load() != 1 {
		t.Fatal("TLS1.3 resumption was not observed")
	}
	if err := os.WriteFile(path, []byte("invalid changed bundle"), 0600); err != nil {
		t.Fatal(err)
	}
	response, err := client.Get(server.URL)
	if response != nil {
		response.Body.Close()
	}
	if err == nil {
		t.Fatal("TLS1.3 resumption bypassed changed trust")
	}
}
