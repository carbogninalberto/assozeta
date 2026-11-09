package smtpsend

import "net"

// fqdnPlatformAliases: macOS libinfo's gethostbyaddr lists the reverse
// lookup name (<nibbles>.ip6.arpa or <octets>.in-addr.arpa) among the
// aliases of every address it resolves, e.g. ('localhost',
// ['1.0...0.ip6.arpa'], ['::1']), so socket.getfqdn() returns that name when
// the host name has no dot.
func fqdnPlatformAliases(ip net.IP) []string {
	return []string{reverseName(ip)}
}
