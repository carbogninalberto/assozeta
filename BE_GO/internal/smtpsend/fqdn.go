package smtpsend

import (
	"net"
	"os"
	"strings"
)

// LocalName is the name smtplib.SMTP sends in EHLO/HELO when no
// local_hostname is given: socket.getfqdn() when it contains a dot,
// otherwise '[<IPv4 of gethostname()>]' ('[127.0.0.1]' when that fails).
func LocalName() string {
	host, _ := os.Hostname()
	if name := FQDN(host); strings.Contains(name, ".") {
		return name
	}
	address := "127.0.0.1"
	if ips, err := net.LookupIP(host); err == nil {
		for _, ip := range ips {
			if v4 := ip.To4(); v4 != nil {
				address = v4.String()
				break
			}
		}
	}
	return "[" + address + "]"
}

// FQDN follows socket.getfqdn(name): the host name and aliases of
// gethostbyaddr(name), the first one containing a dot, else the host name;
// name itself when the lookup fails.
func FQDN(name string) string {
	ips, err := net.LookupIP(name)
	if err != nil || len(ips) == 0 {
		return name
	}
	names, err := net.LookupAddr(ips[0].String())
	if err != nil || len(names) == 0 {
		return name
	}
	for i := range names {
		names[i] = strings.TrimSuffix(names[i], ".")
	}
	// gethostbyaddr's aliases that net.LookupAddr does not return.
	names = append(names, fqdnPlatformAliases(ips[0])...)
	for _, candidate := range names {
		if strings.Contains(candidate, ".") {
			return candidate
		}
	}
	return names[0]
}
