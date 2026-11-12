package dto

import (
	"net/netip"
	"regexp"
	"strconv"
	"strings"
)

// Messages of remote_backups.endpoint_problem.
const (
	BackupEndpointInvalid = "Inserisci un indirizzo valido, ad esempio https://s3.example.com."
	BackupEndpointHTTPS   = "Usa un indirizzo https://."
	BackupEndpointBlocked = "Questo indirizzo non può essere usato per la copia remota."
)

var (
	// remote_backups.ENDPOINT: https?://host[/path], no user information,
	// query or fragment (Python's re.ASCII \s spelled out).
	backupEndpointForm      = regexp.MustCompile(`^(https?)://([^/?#@ \t\n\r\f\v]+)(/[^?# \t\n\r\f\v]*)?$`)
	backupEndpointAuthority = regexp.MustCompile(`^(?:\[([0-9A-Fa-f:.]+)\]|([^:\[\]]+))(?::([0-9]{1,5}))?$`)
	backupEndpointQuad      = regexp.MustCompile(`^(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}$`)
	backupEndpointNumeric   = regexp.MustCompile(`^(?:[0-9]+|0[xX][0-9A-Fa-f]*)$`)
	backupEndpointNames     = map[string]bool{"localhost": true, "metadata": true, "metadata.google.internal": true, "instance-data": true}
	backupEndpointBlocked   = backupEndpointPrefixes("0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/3", "100.100.100.200/32",
		"168.63.129.16/32", "::/128", "::1/128", "fe80::/10", "fec0::/10", "ff00::/8", "fd00:ec2::254/128")
	backupEndpointEmbedded = backupEndpointPrefixes("::ffff:0:0/96", "64:ff9b::/96")
)

func backupEndpointPrefixes(values ...string) []netip.Prefix {
	out := make([]netip.Prefix, len(values))
	for index, value := range values {
		out[index] = netip.MustParsePrefix(value)
	}
	return out
}

// backupEndpointASCIILower lowers A-Z only, as remote_backups.ascii_lower.
func backupEndpointASCIILower(value string) string {
	return strings.Map(func(r rune) rune {
		if r >= 'A' && r <= 'Z' {
			return r + 32
		}
		return r
	}, value)
}

func backupEndpointBlockedAddress(address netip.Addr) bool {
	for _, prefix := range backupEndpointEmbedded {
		if prefix.Contains(address) {
			raw := address.As16()
			address = netip.AddrFrom4([4]byte{raw[12], raw[13], raw[14], raw[15]})
		}
	}
	for _, prefix := range backupEndpointBlocked {
		if prefix.Contains(address) {
			return true
		}
	}
	return false
}

// BackupRemoteEndpointProblem is remote_backups.endpoint_problem: "" when the
// storage address may be used, else the owner's message. Private network
// addresses are allowed; names are checked as written.
func BackupRemoteEndpointProblem(value string, allowHTTP bool) string {
	if value == "" {
		return ""
	}
	form := backupEndpointForm.FindStringSubmatch(value)
	if form == nil {
		return BackupEndpointInvalid
	}
	authority := backupEndpointAuthority.FindStringSubmatch(form[2])
	if authority == nil {
		return BackupEndpointInvalid
	}
	if authority[3] != "" {
		if port, err := strconv.Atoi(authority[3]); err != nil || port < 1 || port > 65535 {
			return BackupEndpointInvalid
		}
	}
	var address netip.Addr
	if strings.HasPrefix(form[2], "[") {
		parsed, err := netip.ParseAddr(authority[1])
		if err != nil || !parsed.Is6() || parsed.Zone() != "" {
			return BackupEndpointInvalid
		}
		address = parsed
	} else {
		host := strings.TrimRight(backupEndpointASCIILower(authority[2]), ".")
		if host == "" {
			return BackupEndpointInvalid
		}
		labels := strings.Split(host, ".")
		if backupEndpointNumeric.MatchString(labels[len(labels)-1]) {
			if !backupEndpointQuad.MatchString(host) {
				return BackupEndpointInvalid
			}
			for _, label := range labels {
				if number, _ := strconv.Atoi(label); number > 255 {
					return BackupEndpointInvalid
				}
			}
			address = netip.MustParseAddr(host)
		} else if backupEndpointNames[host] || strings.HasSuffix(host, ".localhost") {
			return BackupEndpointBlocked
		}
	}
	if address.IsValid() && backupEndpointBlockedAddress(address) {
		return BackupEndpointBlocked
	}
	if form[1] == "http" && !allowHTTP {
		return BackupEndpointHTTPS
	}
	return ""
}
