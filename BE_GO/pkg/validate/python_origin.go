// Package validate supplies transport-independent validation primitives.
package validate

import (
	"net/netip"
	"strings"

	"golang.org/x/text/unicode/norm"
)

type URLOrigin struct{ Scheme, Authority string }

// RawHostname extracts the host without case conversion, percent decoding or
// port validation. The origin parser has already checked bracket syntax.
func (o URLOrigin) RawHostname() string {
	host := o.Authority
	if at := strings.LastIndexByte(host, '@'); at >= 0 {
		host = host[at+1:]
	}
	if strings.HasPrefix(host, "[") {
		host, _, _ = strings.Cut(host[1:], "]")
		return host
	}
	host, _, _ = strings.Cut(host, ":")
	return host
}

// PythonURLOrigin preserves the scheme/netloc and ValueError boundary of shipped
// Python 3.13.16. net/url.Parse is not equivalent: Python accepts invalid ports and
// literal percent escapes here. This is not full endpoint or port validation.
func PythonURLOrigin(raw string) (URLOrigin, bool) {
	value := strings.TrimLeftFunc(raw, func(r rune) bool { return r <= ' ' })
	value = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(value)
	var result URLOrigin
	if colon := strings.IndexByte(value, ':'); colon > 0 && asciiAlpha(value[0]) {
		validScheme := true
		for i := 1; i < colon; i++ {
			b := value[i]
			if !asciiAlpha(b) && !(b >= '0' && b <= '9') && b != '+' && b != '-' && b != '.' {
				validScheme = false
				break
			}
		}
		if validScheme {
			result.Scheme, value = strings.ToLower(value[:colon]), value[colon+1:]
		}
	}
	if strings.HasPrefix(value, "//") {
		result.Authority = value[2:]
		if end := strings.IndexAny(result.Authority, "/?#"); end >= 0 {
			result.Authority = result.Authority[:end]
		}
	}
	left, right := strings.IndexByte(result.Authority, '['), strings.IndexByte(result.Authority, ']')
	if (left < 0) != (right < 0) {
		return URLOrigin{}, false
	}
	if left >= 0 {
		// CPython's bracketed-netloc check uses the hostname after the last
		// userinfo separator, and rejects text around bracketed hosts. Older
		// Python 3.12.6 accepted malformed prefixes/suffixes here.
		hostAndPort := result.Authority
		if at := strings.LastIndexByte(hostAndPort, '@'); at >= 0 {
			hostAndPort = hostAndPort[at+1:]
		}
		before, bracketed, open := strings.Cut(hostAndPort, "[")
		var host string
		if open {
			if before != "" {
				return URLOrigin{}, false
			}
			var port string
			host, port, _ = strings.Cut(bracketed, "]")
			if port != "" && !strings.HasPrefix(port, ":") {
				return URLOrigin{}, false
			}
		} else {
			host, _, _ = strings.Cut(hostAndPort, ":")
		}
		if strings.HasPrefix(host, "v") || strings.HasPrefix(host, "V") {
			version, address, dot := strings.Cut(host[1:], ".")
			if !dot || version == "" || address == "" {
				return URLOrigin{}, false
			}
			for _, r := range version {
				if !(r >= 'a' && r <= 'f' || r >= 'A' && r <= 'F' || r >= '0' && r <= '9') {
					return URLOrigin{}, false
				}
			}
		} else {
			ip, err := netip.ParseAddr(host)
			if err != nil || !ip.Is6() || strings.Count(host, "%") > 1 {
				return URLOrigin{}, false
			}
		}
	}
	stripped := strings.NewReplacer("@", "", ":", "", "#", "", "?", "").Replace(result.Authority)
	if normalized := norm.NFKC.String(stripped); normalized != stripped && strings.ContainsAny(normalized, "/?#@:") {
		return URLOrigin{}, false
	}
	return result, true
}

func asciiAlpha(b byte) bool { return b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' }
