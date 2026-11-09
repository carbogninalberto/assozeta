//go:build !darwin

package smtpsend

import "net"

// fqdnPlatformAliases: glibc's gethostbyaddr returns only the names of the
// hosts file or the PTR record, which net.LookupAddr already returns.
func fqdnPlatformAliases(net.IP) []string { return nil }
