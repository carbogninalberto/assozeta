package pyheader

import "testing"

// TestParseRFC2231Decoders pins parse_header_parameters for RFC 2231 names in
// the decoders the port supports: 8-bit tables generated from CPython and the
// multibyte codecs x/text shares with Python. Expected values come from
// CPython 3.13 via django.utils.http.parse_header_parameters.
func TestParseRFC2231Decoders(t *testing.T) {
	cases := []struct{ line, name, value string }{
		{`Content-Disposition: form-data; name="x"; filename*=iso-8859-2''scan%E8.pdf`, "x", "scanč.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=windows-1251''%CF%F0%E8%EA%E0%E7.pdf`, "x", "Приказ.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=koi8-r''%D0%D2%C9.pdf`, "x", "при.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=cp437''caf%E9.pdf`, "x", "cafΘ.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=mac-roman''caf%E9.pdf`, "x", "cafÈ.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=big5''%A4%A4.pdf`, "x", "中.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=gbk''%D6%D0.pdf`, "x", "中.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=euc-jp''%C6%FC.pdf`, "x", "日.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=shift_jis''%93%FA.pdf`, "x", "日.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=euc-kr''%C7%D1.pdf`, "x", "한.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=utf-8-sig''scan%20%E8.pdf`, "x", "scan �.pdf"},
	}
	for _, test := range cases {
		main, params, outcome := Parse(test.line)
		if outcome != Parsed || main != "content-disposition: form-data" {
			t.Fatalf("Parse(%q) = %q, %v, %v", test.line, main, outcome, params)
		}
		got := map[string]string{}
		for _, param := range params {
			got[param.Name] = param.Value
		}
		if got["name"] != test.name || got["filename"] != test.value {
			t.Fatalf("Parse(%q) params = %q, want name %q filename %q", test.line, got, test.name, test.value)
		}
	}
}

// TestParseRFC2231UnportedDecoder: codecs without a faithful decoder stay
// NotPorted rather than answering text the Python decoder would not produce.
func TestParseRFC2231UnportedDecoder(t *testing.T) {
	for _, line := range []string{
		`Content-Disposition: form-data; name="x"; filename*=cp932''%93%FA.pdf`,
		`Content-Disposition: form-data; name="x"; filename*=utf-7''a+b.pdf`,
		`Content-Disposition: form-data; name="x"; filename*=utf-16''a%00b%00.pdf`,
	} {
		if _, _, outcome := Parse(line); outcome != NotPorted {
			t.Fatalf("Parse(%q) outcome = %v, want NotPorted", line, outcome)
		}
	}
}
