package pdfexport

import (
	"errors"
	"testing"
)

// reportlab's paragraph failures carry str(exception): the agent's tool
// result answers {"error": str(e)} (reportlab 4.2.5 without the accelerator).
func TestParagraphExceptionTexts(t *testing.T) {
	style := paraStyle{fontName: "Helvetica", fontSize: 8}
	cases := []struct{ text, want string }{
		// _complete_parse: the tags left open, no annotation.
		{"A<B", "paraparser: syntax error: parse ended with 1 unclosed tags\n para"},
		{"<b>x", "\nparagraph text '<para><b>x</para>' caused exception Parse error: saw </para> instead of expected </b>"},
		{"a</i>", "\nparagraph text '<para>a</i></para>' caused exception Parse error: saw </i> instead of expected </para>"},
		{"a <br> b", "\nparagraph text '<para>a <br> b</para>' caused exception paraparser: syntax error: No content allowed in br tag"},
	}
	for _, item := range cases {
		_, _, err := parseParagraph(item.text, style)
		if !errors.Is(err, ErrLayout) {
			t.Fatalf("%q: expected ErrLayout, got %v", item.text, err)
		}
		var failure *LayoutError
		if !errors.As(err, &failure) {
			t.Fatalf("%q: ErrLayout without its text: %v", item.text, err)
		}
		if failure.Text != item.want {
			t.Fatalf("%q: text %q, want %q", item.text, failure.Text, item.want)
		}
	}
}

// ascii() of a paragraph escapes every non-ASCII character in the annotation.
func TestPythonAscii(t *testing.T) {
	cases := []struct{ text, want string }{
		{"<para>a</para>", "'<para>a</para>'"},
		{"città", "'citt\\xe0'"},
		{"日本", "'\\u65e5\\u672c'"},
		{"a'b", "'a\\'b'"},
		{"tab\tnew\nline", "'tab\\tnew\\nline'"},
	}
	for _, item := range cases {
		if got := pythonAscii(item.text); got != item.want {
			t.Fatalf("ascii %q = %q, want %q", item.text, got, item.want)
		}
	}
}
