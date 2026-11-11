package service

import "testing"

// Expected values come from protocol.normalize_origin on Python 3.13 (https only); fails marks configuration_required.
func TestNormalizeBakneyOriginInternationalizedHosts(t *testing.T) {
	cases := []struct {
		input, want string
		fails       bool
	}{
		{"https://B\U000000fccher.example", "https://xn--bcher-kva.example", false},
		{"https://\U000065e5\U0000672c\U00008a9e\U00003002example:8443", "https://xn--wgv71a119e.example:8443", false},
		{"https://\U0000ff45\U0000ff58\U0000ff41\U0000ff4d\U0000ff50\U0000ff4c\U0000ff45.com", "https://example.com", false},
		{"https://\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9.example", "", true},
		{"https://\U00000130stanbul.example", "https://xn--istanbul-o0e.example", false},
		{"https://fa\U000000df.de/", "https://fass.de", false},
		{"https://a.\U000000e9", "https://a.xn--9ca", false},
		{"https://xn--bcher-kva.example", "https://xn--bcher-kva.example", false},
		{"https://\U0000042f\U0000041d\U00000414\U00000415\U0000041a\U00000421.\U00000440\U00000444", "https://xn--d1acpjx3f.xn--p1ai", false},
		{"https://\U000000e9\U000000a0x.example", "", true},
		{"https://user@b\U000000fccher.example", "", true},
		{"https://b\U000000fccher.example:0", "", true},
		{"https://\U000005e9\U000005dc\U000005d5\U000005dd.example", "https://xn--9dbne9b.example", false},
		{"https://a\U00002100b.example", "", true},
		{"https://a.example", "https://a.example", false},
	}
	for _, c := range cases {
		got, err := normalizeBakneyOrigin(c.input, BakneySSOSettings{})
		if (err != nil) != c.fails || got != c.want {
			t.Errorf("normalizeBakneyOrigin(%q) = %q, %v; want %q fails=%v", c.input, got, err, c.want, c.fails)
		}
	}
}
