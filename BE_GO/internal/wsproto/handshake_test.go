package wsproto

import (
	"encoding/hex"
	"net/http"
	"testing"
)

// Expected values were produced by base64.b64decode(key.encode(), validate=True)
// on the production interpreter (CPython 3.13.16).
func TestPythonStrictBase64(t *testing.T) {
	cases := []struct{ key, decoded, reason string }{
		{"YXNzb3pldGEtd3MtdGVzdA==", "6173736f7a6574612d77732d74657374", ""},
		{"=abc", "", "Leading padding not allowed"},
		{"ab=c", "", "Discontinuous padding not allowed"},
		{"abc", "", "Incorrect padding"},
		{"a", "", "Invalid base64-encoded string: number of data characters (1) cannot be 1 more than a multiple of 4"},
		{"ab==x", "", "Excess data after padding"},
		{"ab=", "", "Incorrect padding"},
		{"abcd=", "", "Excess padding not allowed"},
		{"ab$c", "", "Only base64 data is allowed"},
		{"ab=a=", "", "Discontinuous padding not allowed"},
		{"YXNz b3pl", "", "Only base64 data is allowed"},
		{"YQ==YQ==", "", "Excess data after padding"},
		{"YQ", "", "Incorrect padding"},
		{"YQ=", "", "Incorrect padding"},
		{"YWJj", "616263", ""},
		{"YWJjZA=", "", "Incorrect padding"},
		{"Y===", "", "Invalid base64-encoded string: number of data characters (1) cannot be 1 more than a multiple of 4"},
		{"", "", ""},
	}
	for _, test := range cases {
		decoded, reason := pythonStrictBase64(test.key)
		if reason != test.reason || hex.EncodeToString(decoded) != test.decoded {
			t.Errorf("%q: got %x %q, want %s %q", test.key, decoded, reason, test.decoded, test.reason)
		}
	}
}

func TestNegotiateExtensions(t *testing.T) {
	cases := []struct{ offer, response string }{
		{"permessage-deflate; client_max_window_bits", "permessage-deflate"},
		{"x-unknown, permessage-deflate; server_max_window_bits=10; server_no_context_takeover; client_no_context_takeover",
			"permessage-deflate; server_no_context_takeover; client_no_context_takeover; server_max_window_bits=10"},
		{"permessage-deflate; server_max_window_bits=16, permessage-deflate; client_max_window_bits=\"9\"", "permessage-deflate; client_max_window_bits=9"},
		{"permessage-deflate; unknown", ""},
	}
	for _, test := range cases {
		request := &http.Request{Header: http.Header{"Sec-Websocket-Extensions": {test.offer}}}
		response, _, failure := NegotiateExtensions(request)
		if failure != nil || response != test.response {
			t.Errorf("%q: got %q %v", test.offer, response, failure)
		}
	}
	request := &http.Request{Header: http.Header{"Sec-Websocket-Extensions": {"permessage-deflate;"}}}
	if _, _, failure := NegotiateExtensions(request); failure == nil || failure.Body !=
		"Failed to open a WebSocket connection: invalid Sec-WebSocket-Extensions header: expected token at 19 in permessage-deflate;.\n" {
		t.Errorf("syntax error: %v", failure)
	}
}

func TestValidUTF8Prefix(t *testing.T) {
	for _, test := range []struct {
		data  string
		final bool
		ok    bool
	}{{"ab\xc3", false, true}, {"ab\xc3", true, false}, {"\xed\xa0", false, false}, {"\xe2\x82", false, true}, {"\xff", false, false}} {
		if validUTF8Prefix([]byte(test.data), test.final) != test.ok {
			t.Errorf("%q final=%v", test.data, test.final)
		}
	}
}
