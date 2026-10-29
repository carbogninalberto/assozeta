package redisclient

import (
	"context"
	"crypto/tls"
	"net"
	"time"

	"github.com/redis/go-redis/v9"
)

// DialContext preserves go-redis's transport settings while applying the caller's
// context to both connecting and the TLS handshake. Install it as Options.Dialer
// after configuring TLSConfig and before redis.NewClient. As with redis.Options,
// finish configuring these options before use; do not mutate TLSConfig afterward.
func DialContext(options *redis.Options) func(context.Context, string, string) (net.Conn, error) {
	timeout := options.DialTimeout
	if timeout == 0 {
		// NewClient has not applied go-redis's default yet.
		timeout = 5 * time.Second
	}
	dialer := &net.Dialer{Timeout: timeout, KeepAlive: 5 * time.Minute}
	if options.TLSConfig == nil {
		return dialer.DialContext
	}
	tlsDialer := &tls.Dialer{NetDialer: dialer, Config: options.TLSConfig}
	return tlsDialer.DialContext
}
