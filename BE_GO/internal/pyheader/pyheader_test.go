package pyheader

import (
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pycodecs"
)

// TestParseRFC2231Decoders pins parse_header_parameters for RFC 2231 names in
// the decoders the port supports: the CPython 8-bit tables and the stateless
// multibyte codecs of pycodecs. Expected values come from CPython 3.13.16 via
// django.utils.http.parse_header_parameters.
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
		// cp932 is the Windows code page: unlike shift_jis it maps the NEC
		// extension pair 0x93 0xFA (and 0xED 0x40 is not in shift_jis at all).
		{`Content-Disposition: form-data; name="x"; filename*=cp932''%93%FA.pdf`, "x", "日.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=cp932''%ED%40.pdf`, "x", "纊.pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=shift_jis''%ED%40.pdf`, "x", "\ufffd@.pdf"},
		// gb18030's four-byte form 0x81 0x30 0x81 0x30 is U+0080.
		{`Content-Disposition: form-data; name="x"; filename*=gb18030''%81%30%81%30.pdf`, "x", "\u0080.pdf"},
		// big5hkscs maps a pair to two code points (U+00CA U+0304).
		{`Content-Disposition: form-data; name="x"; filename*=big5hkscs''%88%62.pdf`, "x", "Ê̄.pdf"},
		// euc-jp's three-byte 0x8F form: 0x8F 0xA2 0xAF is U+02D8.
		{`Content-Disposition: form-data; name="x"; filename*=euc-jp''%8F%A2%AF.pdf`, "x", "˘.pdf"},
		// An invalid trail byte and a truncated lead become U+FFFD.
		{`Content-Disposition: form-data; name="x"; filename*=shift_jis''%81%20.pdf`, "x", "\ufffd .pdf"},
		{`Content-Disposition: form-data; name="x"; filename*=euc-jp''%8F.pdf`, "x", "\ufffd.pdf"},
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

// TestParseRFC2231EveryCodec: utf-7 and utf-16 decode (a lone surrogate is
// kept as WTF-8 for the caller's v.encode() check), and the codecs that raise
// whatever the handler is make the line invalid. Expected values come from
// CPython 3.13 via django.utils.http.parse_header_parameters.
func TestParseRFC2231EveryCodec(t *testing.T) {
	for _, test := range []struct {
		line, filename string
		outcome        Outcome
	}{
		{`Content-Disposition: form-data; name="x"; filename*=utf-7''a+b.pdf`, "a\ufffdpdf", Parsed},
		{`Content-Disposition: form-data; name="x"; filename*=utf-16''a%00b%00.pdf`, "ab\u702e\u6664", Parsed},
		{`Content-Disposition: form-data; name="x"; filename*=utf-7''%2B2AA-.pdf`, "\xed\xa0\x80.pdf", Parsed},
		{`Content-Disposition: form-data; name="x"; filename*=idna''a.pdf`, "", Invalid},
		{`Content-Disposition: form-data; name="x"; filename*=undefined''a.pdf`, "", Invalid},
	} {
		_, params, outcome := Parse(test.line)
		if outcome != test.outcome {
			t.Fatalf("Parse(%q) outcome = %v, want %v", test.line, outcome, test.outcome)
		}
		for _, param := range params {
			if param.Name == "filename" && param.Value != test.filename {
				t.Fatalf("Parse(%q) filename = %q, want %q", test.line, param.Value, test.filename)
			}
		}
	}
}

// TestEveryTextCodecDecodes: every text codec of the registry table has a
// decoder, so decodeRawUnicodeEscape never meets a codec it cannot decode.
func TestEveryTextCodecDecodes(t *testing.T) {
	for alias, codec := range pythonCodecs {
		if !codec.Text {
			continue
		}
		if !pycodecs.Has(codec.Name) {
			t.Errorf("%s (%s): no pycodecs decoder", alias, codec.Name)
		}
	}
}
