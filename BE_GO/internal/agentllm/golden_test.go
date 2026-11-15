// Golden data read off the reference stack: openai 2.54.0 over httpx 0.28.1,
// idna 3.20 on CPython 3.13.16. Each entry is AsyncOpenAI(base_url=...) and
// then client._client.build_request("POST", client._prepare_url("chat/completions")),
// recorded without sending anything.
//
// No request is made, so these are not separate wire checks: they pin the URL
// normalisation, the request target and the Host header the SDK produces.

package agentllm

// httpxGoldenCases is AsyncOpenAI(base_url=...) followed by
// client._client.build_request("POST", client._prepare_url("chat/completions")):
// str(client.base_url), the request target and the Host header (empty when
// httpx sends none).
var httpxGoldenCases = []struct {
	base, baseURL, target, host string
}{
	{"https://api.openai.com/v1", "https://api.openai.com/v1/", "/v1/chat/completions", "api.openai.com"},
	{"https://api.deepseek.com", "https://api.deepseek.com", "/chat/completions", "api.deepseek.com"},
	{"http://localhost/v1", "http://localhost/v1/", "/v1/chat/completions", "localhost"},
	{"http://localhost:80/v1", "http://localhost/v1/", "/v1/chat/completions", "localhost"},
	{"http://localhost:8080/v1", "http://localhost:8080/v1/", "/v1/chat/completions", "localhost:8080"},
	{"http://127.0.0.1:8000/v1/", "http://127.0.0.1:8000/v1/", "/v1/chat/completions", "127.0.0.1:8000"},
	{"https://api.example.com/v1/", "https://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"https://api.example.com", "https://api.example.com", "/chat/completions", "api.example.com"},
	{"https://api.example.com/", "https://api.example.com/", "/chat/completions", "api.example.com"},
	{"https://EXAMPLE.com:8443", "https://example.com:8443", "/chat/completions", "example.com:8443"},
	{"HTTP://api.example.com/v1", "http://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"Https://API.EXAMPLE.COM/V1", "https://api.example.com/V1/", "/V1/chat/completions", "api.example.com"},
	{"https://Api.Example.Com/V1/Chat", "https://api.example.com/V1/Chat/", "/V1/Chat/chat/completions", "api.example.com"},
	{"https://bücher.example/v1", "https://xn--bcher-kva.example/v1/", "/v1/chat/completions", "xn--bcher-kva.example"},
	{"https://BÜCHER.example:8443/v1", "https://xn--bcher-kva.example:8443/v1/", "/v1/chat/completions", "xn--bcher-kva.example:8443"},
	{"https://xn--bcher-kva.example/v1", "https://xn--bcher-kva.example/v1/", "/v1/chat/completions", "xn--bcher-kva.example"},
	{"https://例え.テスト/v1", "https://xn--r8jz45g.xn--zckzah/v1/", "/v1/chat/completions", "xn--r8jz45g.xn--zckzah"},
	{"https://中国.icom.museum", "https://xn--fiqs8s.icom.museum", "/chat/completions", "xn--fiqs8s.icom.museum"},
	{"https://api.example.com:443/v1", "https://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"http://api.example.com:80/v1", "http://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"https://api.example.com:80/v1", "https://api.example.com:80/v1/", "/v1/chat/completions", "api.example.com:80"},
	{"HTTP://api.example.com:80/v1", "http://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"https://api.example.com:8080/v1", "https://api.example.com:8080/v1/", "/v1/chat/completions", "api.example.com:8080"},
	{"https://api.example.com:99999/v1", "https://api.example.com:99999/v1/", "/v1/chat/completions", "api.example.com:99999"},
	{"https://api.example.com:123456/v1", "https://api.example.com:123456/v1/", "/v1/chat/completions", "api.example.com:123456"},
	{"https://api.example.com:0/v1", "https://api.example.com:0/v1/", "/v1/chat/completions", "api.example.com:0"},
	{"https://api.example.com:/v1", "https://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"https://api.example.com:08080/v1", "https://api.example.com:8080/v1/", "/v1/chat/completions", "api.example.com:8080"},
	{"http://api.example.com: 80/v1", "http://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"http://api.example.com:+80/v1", "http://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"http://api.example.com:-1/v1", "http://api.example.com:-1/v1/", "/v1/chat/completions", "api.example.com:-1"},
	{"http://api.example.com:8_0/v1", "http://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/%41", "https://api.example.com/v1/%41/", "/v1/%41/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/%c3%a9", "https://api.example.com/v1/%c3%a9/", "/v1/%c3%a9/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/%zz", "https://api.example.com/v1/%zz/", "/v1/%zz/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/%", "https://api.example.com/v1/%/", "/v1/%/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/100%", "https://api.example.com/v1/100%/", "/v1/100%/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/é", "https://api.example.com/v1/%C3%A9/", "/v1/%C3%A9/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/a b", "https://api.example.com/v1/a%20b/", "/v1/a%20b/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/\\^`{|}", "https://api.example.com/v1/\\^%60%7B|%7D/", "/v1/\\^%60%7B|%7D/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/<>\"", "https://api.example.com/v1/%3C%3E%22/", "/v1/%3C%3E%22/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/;=+$&,()*!'~-._", "https://api.example.com/v1/;=+$&,()*!'~-._/", "/v1/;=+$&,()*!'~-._/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/[]", "https://api.example.com/v1/[]/", "/v1/[]/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/./x", "https://api.example.com/v1/x/", "/v1/x/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/../x", "https://api.example.com/x/", "/x/chat/completions", "api.example.com"},
	{"https://api.example.com/a/b/../v1", "https://api.example.com/a/v1/", "/a/v1/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/../..", "https://api.example.com", "/chat/completions", "api.example.com"},
	{"https://api.example.com/..", "https://api.example.com", "/chat/completions", "api.example.com"},
	{"https://api.example.com/a/./b/../c/", "https://api.example.com/a/c/", "/a/c/chat/completions", "api.example.com"},
	{"https://api.example.com//v1", "https://api.example.com//v1/", "//v1/chat/completions", "api.example.com"},
	{"https://api.example.com/v1//", "https://api.example.com/v1//", "/v1//chat/completions", "api.example.com"},
	{"https://api.example.com/v1?", "https://api.example.com/v1?/", "/v1?/chat/completions", "api.example.com"},
	{"https://api.example.com/v1?#", "https://api.example.com/v1?/#", "/v1?/chat/completions", "api.example.com"},
	{"https://api.example.com/v1?x=1", "https://api.example.com/v1?x=1/", "/v1?x=1/chat/completions", "api.example.com"},
	{"https://api.example.com/v1#frag", "https://api.example.com/v1/#frag", "/v1/chat/completions", "api.example.com"},
	{"https://api.example.com/v1?x=1#frag", "https://api.example.com/v1?x=1/#frag", "/v1?x=1/chat/completions", "api.example.com"},
	{"http://[::1]:8080/v1", "http://[::1]:8080/v1/", "/v1/chat/completions", "[::1]:8080"},
	{"http://[::1]/v1", "http://[::1]/v1/", "/v1/chat/completions", "[::1]"},
	{"http://[::1]:80/v1", "http://[::1]/v1/", "/v1/chat/completions", "[::1]"},
	{"HTTP://[::1]:80/v1", "http://[::1]/v1/", "/v1/chat/completions", "[::1]"},
	{"http://[::ffff:192.168.0.1]:8000/v1", "http://[::ffff:192.168.0.1]:8000/v1/", "/v1/chat/completions", "[::ffff:192.168.0.1]:8000"},
	{"http://[0:0:0:0:0:0:0:1]/v1", "http://[0:0:0:0:0:0:0:1]/v1/", "/v1/chat/completions", "[0:0:0:0:0:0:0:1]"},
	{"http://[::1%25eth0]/v1", "http://[::1%25eth0]/v1/", "/v1/chat/completions", "[::1%25eth0]"},
	{"http://192.168.0.1:8080/v1", "http://192.168.0.1:8080/v1/", "/v1/chat/completions", "192.168.0.1:8080"},
	{"http://0.0.0.0/v1", "http://0.0.0.0/v1/", "/v1/chat/completions", "0.0.0.0"},
	{"http://1.2.3.4.5/v1", "http://1.2.3.4.5/v1/", "/v1/chat/completions", "1.2.3.4.5"},
	{"http://ex_ample.com/v1", "http://ex_ample.com/v1/", "/v1/chat/completions", "ex_ample.com"},
	{"http://exa%41mple.com/v1", "http://exa%41mple.com/v1/", "/v1/chat/completions", "exa%41mple.com"},
	{"http://exa mple.com/v1", "http://exa%20mple.com/v1/", "/v1/chat/completions", "exa%20mple.com"},
	{"http://a!b$c&d'e(f)g*h+i,j;k=l.com/v1", "http://a!b$c&d'e(f)g*h+i,j;k=l.com/v1/", "/v1/chat/completions", "a!b$c&d'e(f)g*h+i,j;k=l.com"},
	{"https://-bad.example/v1", "https://-bad.example/v1/", "/v1/chat/completions", "-bad.example"},
	{"https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.example/v1", "https://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.example/v1/", "/v1/chat/completions", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.example"},
	{"http://user:pass@api.example.com/v1", "http://user:pass@api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/\"x\"", "https://api.example.com/v1/%22x%22/", "/v1/%22x%22/chat/completions", "api.example.com"},
	{"http:///v1", "http:/v1/", "/v1/v1/chat/completions", ""},
	{"ftp://api.example.com/v1", "ftp://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"//api.example.com/v1", "//api.example.com/v1/", "/v1/v1/chat/completions", "api.example.com"},
	{"v1", "v1/", "v1/v1/chat/completions", ""},
	{"https://api.example.com/v1/%2e%2e/x", "https://api.example.com/v1/%2e%2e/x/", "/v1/%2e%2e/x/chat/completions", "api.example.com"},
	{"HTTP://api.example.com:80/v1/", "http://api.example.com:80/v1/", "/v1/chat/completions", "api.example.com"},
	{"https://api.example.com:443/v1/", "https://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"HTTPS://api.example.com:443/v1", "https://api.example.com/v1/", "/v1/chat/completions", "api.example.com"},
	{"https://api.example.com?x=1", "https://api.example.com/?x=1/", "/?x=1/chat/completions", "api.example.com"},
	{"https://api.example.com#frag", "https://api.example.com#frag", "/chat/completions", "api.example.com"},
	{"https://api.example.com/?x=1", "https://api.example.com/?x=1/", "/?x=1/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/%2541", "https://api.example.com/v1/%2541/", "/v1/%2541/chat/completions", "api.example.com"},
	{"https://BÜCHER.example/V1/É", "https://xn--bcher-kva.example/V1/%C3%89/", "/V1/%C3%89/chat/completions", "xn--bcher-kva.example"},
	{"https://api.example.com/v1/ü?x=ü#ü", "https://api.example.com/v1/%C3%BC?x=%C3%BC/#%C3%BC", "/v1/%C3%BC?x=%C3%BC/chat/completions", "api.example.com"},
	{"http://[::1]:8080/v1/", "http://[::1]:8080/v1/", "/v1/chat/completions", "[::1]:8080"},
	{"http://[::1]:65536/v1", "http://[::1]:65536/v1/", "/v1/chat/completions", "[::1]:65536"},
	{"HTTP://[::1]:80/v1", "http://[::1]/v1/", "/v1/chat/completions", "[::1]"},
	{"https://api.example.com/v1?a=1&b=2", "https://api.example.com/v1?a=1&b=2/", "/v1?a=1&b=2/chat/completions", "api.example.com"},
	{"https://api.example.com/v1?q=a b", "https://api.example.com/v1?q=a%20b/", "/v1?q=a%20b/chat/completions", "api.example.com"},
	{"https://api.example.com/v1#frag ment", "https://api.example.com/v1/#frag%20ment", "/v1/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/%7e", "https://api.example.com/v1/%7e/", "/v1/%7e/chat/completions", "api.example.com"},
	{"https://api.example.com/v1/~", "https://api.example.com/v1/~/", "/v1/~/chat/completions", "api.example.com"},
}

// httpxGoldenInvalidURLs are the base URLs httpx.URL rejects, with str(InvalidURL).
var httpxGoldenInvalidURLs = []struct {
	base, text string
}{
	{"http://[::1%]/v1", "Invalid IPv6 address: '[::1%]'"},
	{"http://[1:2]/v1", "Invalid IPv6 address: '[1:2]'"},
	{"http://[::ffff:010.1.1.1]/v1", "Invalid IPv6 address: '[::ffff:010.1.1.1]'"},
	{"http://[]/v1", "Invalid IPv6 address: '[]'"},
	{"http://[zz]/v1", "Invalid IPv6 address: '[zz]'"},
	{"http://01.2.3.4/v1", "Invalid IPv4 address: '01.2.3.4'"},
	{"http://256.1.1.1/v1", "Invalid IPv4 address: '256.1.1.1'"},
	{"https://b..ücher.example/v1", "Invalid IDNA hostname: 'b..ücher.example'"},
	{"https://api.example.com:99999x/v1", "Invalid port: '99999x'"},
	{"https://api.example.com:abc/v1", "Invalid port: 'abc'"},
	{"http://example.com/a\tb", "Invalid non-printable ASCII character in URL, '\\t' at position 20."},
	{"http://example.com/a\x01b", "Invalid non-printable ASCII character in URL, '\\x01' at position 20."},
	{"http://example.com/\x7f", "Invalid non-printable ASCII character in URL, '\\x7f' at position 19."},
	{"http://[::1/v1", "Invalid port: ':1'"},
}

// AsyncOpenAI accepts these base URLs too, but httpx refuses to build their
// request: URL.host decodes a host that begins "xn--" with idna.decode while
// the SDK builds each request, so the stream call raises idna's error instead
// of sending anything (Create reproduces it as the *APIError the agent reports):
//   https://xn--a.example/v1 -> Codepoint U+0080 at position 1 of '\x80' not allowed
//   https://xn--bcher-kva.my_host/v1 -> Codepoint U+005F at position 3 of 'my_host' not allowed
