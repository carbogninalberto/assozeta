//go:build redisdeadlineintegration

package redisclient

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"runtime"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// Opt-in transport tests use only owned ephemeral IPv4 loopback listeners and
// in-memory certificates. They do not require Redis, DNS, or external services.
func TestDialContextNetworkStalledTLS(t *testing.T) {
	baseline := runtime.NumGoroutine()
	tests := []struct {
		name        string
		dialTimeout time.Duration
		ctxTimeout  time.Duration
		cancelHello bool
		minElapsed  time.Duration
		maxElapsed  time.Duration
	}{
		{name: "short_request_deadline", dialTimeout: 2 * time.Second, ctxTimeout: 150 * time.Millisecond, maxElapsed: time.Second},
		{name: "cancel_during_handshake", dialTimeout: 2 * time.Second, cancelHello: true, maxElapsed: time.Second},
		{name: "configured_timeout_precedes_context", dialTimeout: 150 * time.Millisecond, ctxTimeout: 2 * time.Second, maxElapsed: time.Second},
		{name: "configured_timeout_without_context_deadline", dialTimeout: 150 * time.Millisecond, maxElapsed: time.Second},
		{name: "zero_timeout_keeps_redis_default", minElapsed: 4 * time.Second, maxElapsed: 7 * time.Second},
	}
	// Repetition makes a connection or handshake goroutine leaked per cancellation
	// visible even with the small runtime-goroutine allowance below.
	for i := 0; i < 8; i++ {
		tests = append(tests, tests[1])
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ctx := context.Background()
			if tt.ctxTimeout > 0 {
				var cancel context.CancelFunc
				ctx, cancel = context.WithTimeout(ctx, tt.ctxTimeout)
				defer cancel()
			}
			ctx, cancel := context.WithCancel(ctx)
			defer cancel()
			hello := make(chan struct{})
			peer := newLoopbackPeer(t, func(raw net.Conn) peerResult {
				// Read an actual TLS handshake record before signaling cancellation.
				var header [5]byte
				if _, err := io.ReadFull(raw, header[:]); err != nil {
					return peerResult{err: err}
				}
				if header[0] != 22 || binary.BigEndian.Uint16(header[3:]) == 0 {
					return peerResult{err: errors.New("expected TLS handshake record")}
				}
				if _, err := io.CopyN(io.Discard, raw, int64(binary.BigEndian.Uint16(header[3:]))); err != nil {
					return peerResult{err: err}
				}
				close(hello)
				if tt.cancelHello {
					cancel()
				}
				// Never send ServerHello. The client must close its socket itself.
				return peerResult{closed: peerClosed(raw)}
			})
			options := &redis.Options{DialTimeout: tt.dialTimeout, TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12}}
			start := time.Now()
			conn, err := DialContext(options)(ctx, "tcp", peer.addr)
			elapsed := time.Since(start)
			if conn != nil && err == nil {
				conn.Close()
			}
			want := context.DeadlineExceeded
			if tt.cancelHello {
				want = context.Canceled
			}
			if !errors.Is(err, want) || conn != nil {
				t.Fatalf("dial returned conn=%v, err=%v; want nil, %v", conn, err, want)
			}
			if elapsed < tt.minElapsed || elapsed >= tt.maxElapsed {
				t.Fatalf("dial took %s; want [%s, %s)", elapsed, tt.minElapsed, tt.maxElapsed)
			}
			select {
			case <-hello:
			default:
				t.Fatal("dial ended before the fixture received ClientHello")
			}
			result := peer.wait(t)
			if result.err != nil || !result.closed {
				t.Fatalf("stalled peer was not closed by client: %+v", result)
			}
		})
	}
	awaitGoroutineBaseline(t, baseline)
}

func TestDialContextNetworkTLSVerification(t *testing.T) {
	certs := newTransportCertificates(t)
	rejected := errors.New("configured verification rejected connection")
	tests := []struct {
		name   string
		change func(*tls.Config)
		check  func(error) bool
	}{
		{name: "trusted_root_and_inferred_ip"},
		{name: "explicit_server_name_and_client_certificate_callback", change: func(cfg *tls.Config) {
			cfg.ServerName = "redis.deadline.test"
			cfg.Certificates = nil
			cfg.GetClientCertificate = func(*tls.CertificateRequestInfo) (*tls.Certificate, error) { return &certs.client, nil }
		}},
		{name: "untrusted_root", change: func(cfg *tls.Config) { cfg.RootCAs = x509.NewCertPool() }, check: func(err error) bool {
			var unknown x509.UnknownAuthorityError
			return errors.As(err, &unknown)
		}},
		{name: "wrong_server_name", change: func(cfg *tls.Config) { cfg.ServerName = "wrong.deadline.test" }, check: func(err error) bool {
			var mismatch x509.HostnameError
			return errors.As(err, &mismatch)
		}},
		{name: "configured_verifier_rejects", change: func(cfg *tls.Config) {
			cfg.VerifyConnection = func(tls.ConnectionState) error { return rejected }
		}, check: func(err error) bool { return errors.Is(err, rejected) }},
		{name: "configured_tls_version", change: func(cfg *tls.Config) {
			cfg.MinVersion, cfg.MaxVersion = tls.VersionTLS13, 0
		}, check: func(err error) bool { return err != nil }},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var verified atomic.Bool
			cfg := &tls.Config{
				RootCAs: certs.roots, Certificates: []tls.Certificate{certs.client},
				MinVersion: tls.VersionTLS12, MaxVersion: tls.VersionTLS12,
				VerifyConnection: func(state tls.ConnectionState) error {
					if len(state.VerifiedChains) == 0 {
						return errors.New("configured verifier did not receive verified chains")
					}
					verified.Store(true)
					return nil
				},
			}
			if tt.change != nil {
				tt.change(cfg)
			}
			originalName := cfg.ServerName
			peer := newLoopbackPeer(t, func(raw net.Conn) peerResult {
				secure := tls.Server(raw, &tls.Config{
					Certificates: []tls.Certificate{certs.server}, ClientCAs: certs.roots,
					ClientAuth: tls.RequireAndVerifyClientCert,
					MinVersion: tls.VersionTLS12, MaxVersion: tls.VersionTLS12,
				})
				if err := secure.Handshake(); err != nil {
					return peerResult{err: err, closed: peerClosed(raw)}
				}
				if len(secure.ConnectionState().VerifiedChains) == 0 {
					return peerResult{err: errors.New("client certificate was not verified")}
				}
				_, err := io.Copy(secure, secure)
				return peerResult{err: err, closed: peerClosed(raw)}
			})
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			conn, err := DialContext(&redis.Options{DialTimeout: 2 * time.Second, TLSConfig: cfg})(ctx, "tcp", peer.addr)
			if err == nil && conn != nil {
				defer conn.Close()
			}
			if tt.check != nil {
				if conn != nil || !tt.check(err) {
					t.Fatalf("verification failure returned conn=%v, err=%v", conn, err)
				}
			} else {
				if err != nil || conn == nil {
					t.Fatalf("valid TLS connection failed: %v", err)
				}
				if !verified.Load() {
					t.Fatal("configured VerifyConnection callback was not used")
				}
				// A completed dial must remain usable after its context is canceled.
				cancel()
				echoRoundTrip(t, conn)
				if err := conn.Close(); err != nil {
					t.Fatal(err)
				}
			}
			if cfg.ServerName != originalName || cfg.InsecureSkipVerify {
				t.Fatal("dial mutated TLS server name or disabled verification")
			}
			result := peer.wait(t)
			if !result.closed || (tt.check == nil && result.err != nil) {
				t.Fatalf("TLS peer did not finish and observe client closure: %+v", result)
			}
		})
	}
}

func TestDialContextNetworkPlainTCP(t *testing.T) {
	peer := newLoopbackPeer(t, func(raw net.Conn) peerResult {
		_, err := io.Copy(raw, raw)
		return peerResult{err: err, closed: peerClosed(raw)}
	})
	options := &redis.Options{Addr: "unused.invalid:6379", DialTimeout: 2 * time.Second}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	// Exercise the exact installation order used by the parent, without sending
	// Redis commands or starting a pool connection in the fixture.
	options.Dialer = DialContext(options)
	client := redis.NewClient(options)
	defer client.Close()
	conn, err := client.Options().Dialer(ctx, "tcp4", peer.addr)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	if _, ok := conn.(*net.TCPConn); !ok {
		t.Fatalf("plain TCP returned %T", conn)
	}
	cancel()
	echoRoundTrip(t, conn)
	if err := conn.Close(); err != nil {
		t.Fatal(err)
	}
	result := peer.wait(t)
	if result.err != nil || !result.closed {
		t.Fatalf("plain peer did not finish and observe client closure: %+v", result)
	}
}

type peerResult struct {
	err    error
	closed bool
}

type loopbackPeer struct {
	addr   string
	result chan peerResult
	done   chan struct{}
}

func newLoopbackPeer(t *testing.T, serve func(net.Conn) peerResult) *loopbackPeer {
	t.Helper()
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	p := &loopbackPeer{addr: listener.Addr().String(), result: make(chan peerResult, 1), done: make(chan struct{})}
	accepted := make(chan net.Conn, 1)
	go func() {
		defer close(p.done)
		raw, err := listener.Accept()
		if err != nil {
			p.result <- peerResult{err: err}
			return
		}
		defer raw.Close()
		accepted <- raw
		if err := raw.SetDeadline(time.Now().Add(9 * time.Second)); err != nil {
			p.result <- peerResult{err: err}
			return
		}
		p.result <- serve(raw)
	}()
	t.Cleanup(func() {
		listener.Close()
		select {
		case <-p.done:
		case raw := <-accepted:
			raw.Close()
		case <-time.After(2 * time.Second):
			t.Error("fixture neither accepted nor stopped")
		}
		select {
		case <-p.done:
		case <-time.After(2 * time.Second):
			t.Error("fixture goroutine did not stop after socket cleanup")
		}
	})
	return p
}

func (p *loopbackPeer) wait(t *testing.T) peerResult {
	t.Helper()
	select {
	case <-p.done:
		return <-p.result
	case <-time.After(2 * time.Second):
		t.Fatal("fixture did not observe client closure and stop")
		return peerResult{}
	}
}

// Drain to EOF (or a peer reset); a read timeout does not count as closure.
func peerClosed(conn net.Conn) bool {
	_, err := io.Copy(io.Discard, conn)
	return err == nil || errors.Is(err, syscall.ECONNRESET)
}

func echoRoundTrip(t *testing.T, conn net.Conn) {
	t.Helper()
	if err := conn.SetDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	const payload = "redis-dial-context"
	if _, err := io.WriteString(conn, payload); err != nil {
		t.Fatal(err)
	}
	buffer := make([]byte, len(payload))
	if _, err := io.ReadFull(conn, buffer); err != nil {
		t.Fatal(err)
	}
	if string(buffer) != payload {
		t.Fatalf("echo = %q; want %q", buffer, payload)
	}
}

func awaitGoroutineBaseline(t *testing.T, baseline int) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for runtime.NumGoroutine() > baseline+1 {
		if time.Now().After(deadline) {
			stack := make([]byte, 1<<20)
			n := runtime.Stack(stack, true)
			t.Fatalf("goroutines did not return to baseline %d (+1 runtime allowance):\n%s", baseline, stack[:n])
		}
		time.Sleep(10 * time.Millisecond)
	}
}

type transportCertificates struct {
	roots          *x509.CertPool
	server, client tls.Certificate
}

func newTransportCertificates(t *testing.T) transportCertificates {
	t.Helper()
	now := time.Now()
	_, caKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	ca := &x509.Certificate{
		SerialNumber: big.NewInt(1), NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, caKey.Public(), caKey)
	if err != nil {
		t.Fatal(err)
	}
	ca, err = x509.ParseCertificate(caDER)
	if err != nil {
		t.Fatal(err)
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	issue := func(serial int64, usage x509.ExtKeyUsage) tls.Certificate {
		_, key, err := ed25519.GenerateKey(rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
		template := &x509.Certificate{
			SerialNumber: big.NewInt(serial), NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour),
			KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{usage},
			DNSNames: []string{"redis.deadline.test"}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")},
		}
		der, err := x509.CreateCertificate(rand.Reader, template, ca, key.Public(), caKey)
		if err != nil {
			t.Fatal(fmt.Errorf("issue transport certificate: %w", err))
		}
		return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}
	}
	return transportCertificates{roots: roots, server: issue(2, x509.ExtKeyUsageServerAuth), client: issue(3, x509.ExtKeyUsageClientAuth)}
}
