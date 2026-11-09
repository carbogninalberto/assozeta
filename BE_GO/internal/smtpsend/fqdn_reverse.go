package smtpsend

import (
	"net"
	"strconv"
	"strings"
)

// reverseName is the DNS reverse lookup name of ip.
func reverseName(ip net.IP) string {
	if v4 := ip.To4(); v4 != nil {
		return strconv.Itoa(int(v4[3])) + "." + strconv.Itoa(int(v4[2])) + "." + strconv.Itoa(int(v4[1])) + "." +
			strconv.Itoa(int(v4[0])) + ".in-addr.arpa"
	}
	const hex = "0123456789abcdef"
	var b strings.Builder
	v6 := ip.To16()
	for i := len(v6) - 1; i >= 0; i-- {
		b.WriteByte(hex[v6[i]&0xf])
		b.WriteByte('.')
		b.WriteByte(hex[v6[i]>>4])
		b.WriteByte('.')
	}
	b.WriteString("ip6.arpa")
	return b.String()
}
