package pdfexport

import (
	"errors"
	"strings"
	"testing"
)

// A colour attribute toColor cannot read fails the paragraph with the text of
// reportlab's exception: toColor raises ValueError('Invalid color value %r')
// (reportlab/lib/colors.py) and the parser's annotation carries str(e), which
// the agent's tool result shows.
func TestParagraphColourExceptionText(t *testing.T) {
	style := paraStyle{fontName: "Helvetica", fontSize: numInt(8)}
	cases := []struct{ text, suffix string }{
		{`<font color="xyz">a</font>`, ` caused exception Invalid color value 'xyz'`},
		{`<font color="nope it">a</font>`, ` caused exception Invalid color value 'nope it'`},
		{`<span backcolor="zz">a</span>`, ` caused exception Invalid color value 'zz'`},
		{`<font color="it's">a</font>`, ` caused exception Invalid color value "it's"`},
	}
	for _, item := range cases {
		_, _, _, err := parseParagraph(item.text, style)
		var failure *LayoutError
		if !errors.Is(err, ErrLayout) || !errors.As(err, &failure) {
			t.Fatalf("%q: expected a LayoutError, got %v", item.text, err)
		}
		if !strings.HasSuffix(failure.Text, item.suffix) {
			t.Fatalf("%q: text %q, want suffix %q", item.text, failure.Text, item.suffix)
		}
	}
}
