// Package s3trust preserves the existing Boto3 CA selection at the storage
// transport boundary. It never reads a bundle until a TLS handshake needs it.
package s3trust

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/user"
	"strings"
	"sync"
	"syscall"
	"time"
)

var ErrTrust = errors.New("storage TLS trust unavailable")

// Match net/http's handshake timeout classification. An internal handshake
// deadline is a retryable transport timeout, not cancellation of the caller.
type handshakeTimeoutError struct{}

func (handshakeTimeoutError) Error() string   { return "storage TLS handshake timeout" }
func (handshakeTimeoutError) Timeout() bool   { return true }
func (handshakeTimeoutError) Temporary() bool { return true }

type Policy struct {
	path       string
	configured bool
}

func (Policy) String() string     { return "storage TLS trust (redacted)" }
func (p Policy) GoString() string { return p.String() }

// Select retains presence, including the legacy explicit-empty setting. CA
// paths are literal except for user-prefix expansion; unlike shared config
// filenames, environment variables inside them are not interpolated.
func Select(shared *string) Policy {
	if value, present := os.LookupEnv("AWS_CA_BUNDLE"); present {
		return Policy{path: value, configured: true}
	}
	if shared != nil {
		return Policy{path: *shared, configured: true}
	}
	if value, present := os.LookupEnv("REQUESTS_CA_BUNDLE"); present {
		return Policy{path: value, configured: true}
	}
	return Policy{}
}

// Wrap preserves the caller's transport limits and uses a private TLS config.
// Nonempty configured bundles replace system roots. VerifyConnection performs
// the full chain, hostname/IP, validity and ServerAuth verification on every
// handshake (including resumption), allowing lazy bundle repair and keeping
// normal keepalive behavior. HTTP CONNECT and HTTPS proxies retain distinct
// outer proxy and inner target hostname checks.
//
// InsecureSkipVerify delegates native verification to that complete verifier;
// it is not a fallback. The two legacy opt-out branches are explicit empty CA
// selection and HTTP targets forwarded through HTTPS proxies. An absent setting
// otherwise uses native verification with the pinned Django root bundle.
func (p Policy) Wrap(base *http.Transport) *Transport {
	copy := base.Clone()
	if !p.configured {
		if copy.TLSClientConfig == nil {
			copy.TLSClientConfig = &tls.Config{}
		} else {
			copy.TLSClientConfig = copy.TLSClientConfig.Clone()
		}
		copy.TLSClientConfig.RootCAs = defaultRoots()
	}
	originalProxy := base.Proxy
	copy.Proxy = func(request *http.Request) (*url.URL, error) {
		if choice, ok := request.Context().Value(proxyChoiceKey{}).(proxyChoice); ok {
			return choice.url, nil
		}
		if originalProxy != nil {
			return originalProxy(request)
		}
		return nil, nil
	}
	return &Transport{base: copy, proxy: originalProxy, policy: p, hosts: make(map[string]*http.Transport)}
}

// Transport caches at most eight transports, each retaining the original pool
// limits. Evicted active requests may finish under those limits; this is not a
// global live-connection bound. A per-target TLS config
// is needed because TLS ConnectionState omits IP literals from ServerName/SNI.
// URL hostnames remain the verification name even when SNI is absent.
type Transport struct {
	base   *http.Transport
	proxy  func(*http.Request) (*url.URL, error)
	policy Policy
	mu     sync.Mutex
	hosts  map[string]*http.Transport
	order  []string
}

type proxyChoiceKey struct{}
type proxyChoice struct {
	url        *url.URL
	httpTarget bool
}

func (p proxyChoice) https() bool { return p.url != nil && p.url.Scheme == "https" }

func (t *Transport) RoundTrip(request *http.Request) (*http.Response, error) {
	choice := proxyChoice{httpTarget: request.URL.Scheme == "http"}
	if t.proxy != nil {
		value, err := t.proxy(request)
		if err != nil {
			return nil, err
		}
		choice.url = value
	}
	request = request.WithContext(context.WithValue(request.Context(), proxyChoiceKey{}, choice))
	// Boto3 selects CERT_NONE before TLS for an HTTP target forwarded through
	// an HTTPS proxy. This explicit legacy branch is not a failure fallback.
	if request.URL.Scheme != "https" && !(choice.httpTarget && choice.https()) {
		return t.base.RoundTrip(request)
	}
	if !t.policy.configured && !choice.https() {
		return t.base.RoundTrip(request)
	}
	host := request.URL.Hostname()
	t.mu.Lock()
	transport := t.hosts[host]
	if transport == nil {
		if len(t.order) == 8 {
			old := t.order[0]
			t.order = t.order[1:]
			t.hosts[old].CloseIdleConnections()
			delete(t.hosts, old)
		}
		transport = t.policy.forHost(t.base, host)
		t.hosts[host] = transport
		t.order = append(t.order, host)
	}
	t.mu.Unlock()
	return transport.RoundTrip(request)
}

func (t *Transport) CloseIdleConnections() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.base.CloseIdleConnections()
	for _, transport := range t.hosts {
		transport.CloseIdleConnections()
	}
}

func (p Policy) forHost(base *http.Transport, host string) *http.Transport {
	transport := base.Clone()
	transport.TLSClientConfig = p.configForHost(base.TLSClientConfig, host, false)
	// The first TLS dial can terminate at an HTTPS proxy, not the target. Bind
	// that handshake to the actual dial address; net/http still performs CONNECT
	// and its inner target TLS handshake using TLSClientConfig above. Preserve
	// explicitly supplied custom TLS dialers, whose TLS policy is caller-owned.
	if base.DialTLSContext == nil && base.DialTLS == nil {
		transport.DialTLSContext = func(ctx context.Context, network, address string) (net.Conn, error) {
			remoteHost, _, err := net.SplitHostPort(address)
			if err != nil {
				return nil, ErrTrust
			}
			choice, _ := ctx.Value(proxyChoiceKey{}).(proxyChoice)
			policy := p
			strict := choice.https()
			if choice.httpTarget && choice.https() {
				policy = Policy{configured: true}
				strict = false
			}
			config := policy.configForHost(base.TLSClientConfig, remoteHost, strict)
			// net/http may add ALPN protocols during its one-time transport setup.
			config.NextProtos = append([]string(nil), transport.TLSClientConfig.NextProtos...)
			if config.ServerName == "" {
				config.ServerName = remoteHost
			}
			var connection net.Conn
			switch {
			case base.DialContext != nil:
				connection, err = base.DialContext(ctx, network, address)
			case base.Dial != nil:
				connection, err = base.Dial(network, address)
			default:
				connection, err = (&net.Dialer{}).DialContext(ctx, network, address)
			}
			if err != nil {
				return nil, err
			}
			tlsConnection := tls.Client(connection, config)
			handshake := ctx
			cancel := func() {}
			if transport.TLSHandshakeTimeout != 0 {
				handshake, cancel = context.WithTimeout(ctx, transport.TLSHandshakeTimeout)
			}
			defer cancel()
			if err = tlsConnection.HandshakeContext(handshake); err != nil {
				connection.Close()
				if ctx.Err() == nil && errors.Is(handshake.Err(), context.DeadlineExceeded) {
					return nil, handshakeTimeoutError{}
				}
				return nil, err
			}
			return tlsConnection, nil
		}
	}
	return transport
}

func (p Policy) configForHost(original *tls.Config, host string, strictProxy bool) *tls.Config {
	config := original
	if config == nil {
		config = &tls.Config{}
	} else {
		config = config.Clone()
	}
	if config.ServerName != "" {
		host = config.ServerName
	}
	priorPeer, priorConnection := config.VerifyPeerCertificate, config.VerifyConnection
	if !p.configured {
		config.RootCAs = defaultRoots()
		if strictProxy {
			config.VerifyConnection = func(state tls.ConnectionState) error {
				if !validStrictProxyChains(state.VerifiedChains) {
					return ErrTrust
				}
				if priorConnection != nil {
					if err := priorConnection(state); err != nil {
						return ErrTrust
					}
				}
				return nil
			}
		}
		return config
	}
	config.VerifyPeerCertificate = nil
	config.InsecureSkipVerify = true // Complete verification below; explicit empty is a distinct legacy setting.
	config.VerifyConnection = func(state tls.ConnectionState) error {
		var chains [][]*x509.Certificate
		if p.path != "" {
			roots, err := loadRoots(expandUser(p.path))
			if err != nil {
				return ErrTrust
			}
			if len(state.PeerCertificates) == 0 || host == "" {
				return ErrTrust
			}
			intermediates := x509.NewCertPool()
			for _, cert := range state.PeerCertificates[1:] {
				intermediates.AddCert(cert)
			}
			now := time.Now()
			if config.Time != nil {
				now = config.Time()
			}
			chains, err = state.PeerCertificates[0].Verify(x509.VerifyOptions{Roots: roots, Intermediates: intermediates,
				DNSName: host, CurrentTime: now, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}})
			if err != nil {
				return ErrTrust
			}
		}
		if strictProxy && p.path != "" && !validStrictProxyChains(chains) {
			return ErrTrust
		}
		state.VerifiedChains = chains
		if priorPeer != nil && !state.DidResume {
			raw := make([][]byte, len(state.PeerCertificates))
			for index, cert := range state.PeerCertificates {
				raw[index] = cert.Raw
			}
			if err := priorPeer(raw, chains); err != nil {
				return ErrTrust
			}
		}
		if priorConnection != nil {
			if err := priorConnection(state); err != nil {
				return ErrTrust
			}
		}
		return nil
	}
	return config
}

func loadRoots(path string) (*x509.CertPool, error) {
	// Match the existing credential-file boundary: a configured FIFO/device
	// cannot stall every TLS connection before context cancellation can act.
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, ErrTrust
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return nil, ErrTrust
	}
	data, err := io.ReadAll(file)
	if err != nil {
		return nil, ErrTrust
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(data) {
		return nil, ErrTrust
	}
	return roots, nil
}

func expandUser(path string) string {
	if !strings.HasPrefix(path, "~") {
		return path
	}
	name, rest, slash := strings.Cut(path[1:], "/")
	var home string
	if name == "" {
		var present bool
		home, present = os.LookupEnv("HOME")
		if !present {
			account, err := user.Current()
			if err != nil {
				return path
			}
			home = account.HomeDir
		}
	} else {
		account, err := user.Lookup(name)
		if err != nil {
			return path
		}
		home = account.HomeDir
	}
	home = strings.TrimRight(home, "/")
	if slash {
		return home + "/" + rest
	}
	if home == "" {
		return "/"
	}
	return home
}
