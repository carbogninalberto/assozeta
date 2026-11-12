package redisclient

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// These controls never open sockets: negative dial timeouts expire before the
// transport is entered, and unknown networks fail during address resolution.
// Real handshake timing, verification and cleanup require the tagged suite.
func TestDialContextAlreadyCanceled(t *testing.T) {
	for _, useTLS := range []bool{false, true} {
		t.Run(transportName(useTLS), func(t *testing.T) {
			options := offlineOptions(useTLS)
			ctx, cancel := context.WithCancel(context.Background())
			cancel()
			conn, err := DialContext(options)(ctx, "tcp", "127.0.0.1:6379")
			if !errors.Is(err, context.Canceled) {
				t.Fatalf("got %v; want caller cancellation", err)
			}
			if conn != nil {
				t.Fatal("canceled dial returned a non-nil connection")
			}
		})
	}
}

func TestDialContextAlreadyExpired(t *testing.T) {
	for _, useTLS := range []bool{false, true} {
		t.Run(transportName(useTLS), func(t *testing.T) {
			ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
			defer cancel()
			conn, err := DialContext(offlineOptions(useTLS))(ctx, "tcp", "127.0.0.1:6379")
			if !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("got %v; want deadline exceeded", err)
			}
			if conn != nil {
				t.Fatal("expired dial returned a non-nil connection")
			}
		})
	}
}

func TestDialContextUsesCallNetwork(t *testing.T) {
	for _, useTLS := range []bool{false, true} {
		t.Run(transportName(useTLS), func(t *testing.T) {
			options := offlineOptions(useTLS)
			options.Network, options.Addr = "tcp", "127.0.0.1:6379"
			const network = "redis-deadline-offline"
			conn, err := DialContext(options)(context.Background(), network, "unused")
			var unknown net.UnknownNetworkError
			if !errors.As(err, &unknown) || string(unknown) != network {
				t.Fatalf("got %v; want unknown network %q", err, network)
			}
			if conn != nil {
				t.Fatal("unknown network returned a non-nil connection")
			}
		})
	}
}

func offlineOptions(useTLS bool) *redis.Options {
	options := &redis.Options{DialTimeout: -time.Second}
	if useTLS {
		options.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
	}
	return options
}

func transportName(useTLS bool) string {
	if useTLS {
		return "tls"
	}
	return "plain"
}
