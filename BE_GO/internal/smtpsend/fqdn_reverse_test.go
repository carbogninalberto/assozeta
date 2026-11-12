package smtpsend

import (
	"net"
	"testing"
)

// Values from Python's socket.gethostbyaddr on macOS.
func TestReverseName(t *testing.T) {
	for address, want := range map[string]string{
		"::1":       "1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.ip6.arpa",
		"fe80::1":   "1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.8.e.f.ip6.arpa",
		"127.0.0.1": "1.0.0.127.in-addr.arpa",
	} {
		if got := reverseName(net.ParseIP(address)); got != want {
			t.Fatalf("%s: %s", address, got)
		}
	}
}
