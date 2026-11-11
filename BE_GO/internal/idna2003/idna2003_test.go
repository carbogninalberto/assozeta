package idna2003

import "testing"

// Expected values come from Python 3.13's str.encode('idna'); fails marks a UnicodeError.
var cases = []struct {
	input, want string
	fails       bool
}{
	{"m\U000000fcller.example", "xn--mller-kva.example", false},
	{"B\U000000fccher.DE", "xn--bcher-kva.DE", false},
	{"\U0000ff45\U0000ff58\U0000ff41\U0000ff4d\U0000ff50\U0000ff4c\U0000ff45.com", "example.com", false},
	{"fa\U000000df.de", "fass.de", false},
	{"\U00000130stanbul.tr", "xn--istanbul-o0e.tr", false},
	{"a.b.\U000000e9", "a.b.xn--9ca", false},
	{"\U000065e5\U0000672c\U00008a9e\U00003002jp", "xn--wgv71a119e.jp", false},
	{"\U000065e5\U0000672c\U00008a9e\U0000ff0ejp", "xn--wgv71a119e.jp", false},
	{"\U000065e5\U0000672c\U00008a9e\U0000ff61jp", "xn--wgv71a119e.jp", false},
	{"xn--abc.com", "xn--abc.com", false},
	{"xn--\U000000e9a.com", "", true},
	{"\U000005e9\U000005dc\U000005d5\U000005dd.com", "xn--9dbne9b.com", false},
	{"a\U000005e9\U000005dc\U000005d5\U000005dd.com", "", true},
	{"\U000005e9\U000005dc\U000005d5\U000005dd1.com", "", true},
	{"\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9.com", "", true},
	{"a..\U000000e9", "", true},
	{"\U000000e9.", "xn--9ca.", false},
	{".\U000000e9", "", true},
	{"a\U000000adb.\U000000e9", "ab.xn--9ca", false},
	{"a\U0000200db.\U000000e9", "ab.xn--9ca", false},
	{"\U000000a0\U000000e9.com", "xn-- -bga.com", false},
	{"\U00000340\U000000e9.com", "xn--9ca38h.com", false},
	{"\U000000e9\U0000fffd.com", "", true},
	{"\U000000dcn\U000000ef.c\U000000f6d\U000000e9.example", "xn--n-nga1b.xn--cd-cja4c.example", false},
	{"\U000001c5ungla.com", "xn--dungla-3pb.com", false},
	{"\U00000391\U000003a3.gr", "xn--mxa0b.gr", false},
	{"\U000000df", "ss", false},
	{"\U000002bcn.example", "xn--n-y6a.example", false},
	{"\U00004f8b\U00003048.\U000030c6\U000030b9\U000030c8", "xn--r8jz45g.xn--zckzah", false},
	{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.\U000000e9", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.xn--9ca", false},
	{"\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9\U000000e9.com", "", true},
	{"user:pass@b\U000000fccher.de:8080", "xn--user:pass@bcher-9vb.de:8080", false},
	{"b\U000000fccher.de:8080", "xn--bcher-kva.de:8080", false},
	{"", "", false},
	{"\U000000e9", "xn--9ca", false},
	{"ABC.\U000000e9", "ABC.xn--9ca", false},
	{"\U0000fb01.\U000000e9", "fi.xn--9ca", false},
	{"\U00002126.com", "xn--bya.com", false},
	{"a_b.\U000000e9", "a_b.xn--9ca", false},
	{"\U000000e9-.com", "xn----9fa.com", false},
	{"-\U000000e9.com", "xn----bga.com", false},
	{"\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U0000ff41\U000000e9.com", "xn--aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-4xc.com", false},
	{"\U0001f600.com", "xn--e28h.com", false},
	{"x\U00000301.com", "xn--x-xbb.com", false},
	{"e\U00000301.com", "xn--9ca.com", false},
	{"\U000001f0.com", "xn--ska.com", false},
	{"\U00001e9e.com", "xn--kkg.com", false},
	{"\U000003c2.com", "xn--4xa.com", false},
}

func TestToASCIIMatchesPython(t *testing.T) {
	for _, c := range cases {
		got, err := ToASCII(c.input)
		if (err != nil) != c.fails || got != c.want {
			t.Errorf("ToASCII(%q) = %q, %v; want %q fails=%v", c.input, got, err, c.want, c.fails)
		}
	}
}
