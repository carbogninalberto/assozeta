package pdfexport

import (
	"strings"
	"testing"
)

// TestParaColorIdentifiers is toColor of the expressions the tokenizer limits
// and of the identifiers it normalises, with the answers of CPython 3.13 and
// reportlab 4.2.5: a non-ASCII identifier is NFKC-normalised ('ｒｅｄ' is the
// name 'red'), a named colour takes the full str.lower() mapping ('İvory' is
// not 'ivory'), the tokenizer keeps at most 200 brackets open, and a decimal
// int literal has at most 4300 digits.
func TestParaColorIdentifiers(t *testing.T) {
	red := rgbColor(1, 0, 0)
	ivory := rgbColor(1, 1, 240.0/255.0)
	ones := rgbColor(28.0/255.0, 113.0/255.0, 199.0/255.0)
	bracket := func(open int) string {
		return "Color(" + strings.Repeat("(", open-1) + "1" + strings.Repeat(")", open-1) + ",0,0)"
	}
	items := []struct {
		value string
		want  *color
	}{
		{"ｒｅｄ", &red},
		{"ʳed", &red},
		{"İvory", nil},
		{"ivory", &ivory},
		{bracket(199), &red},
		{bracket(200), &red},
		{bracket(201), nil},
		{strings.Repeat("1", 4300), &ones},
		{strings.Repeat("1", 4301), nil},
		{"-" + strings.Repeat("1", 4301), nil},
		{strings.Repeat("1_", 2150) + "1", &ones},
	}
	for _, item := range items {
		got, status := paraColor(item.value)
		if item.want == nil {
			if status != attrInvalid {
				t.Errorf("%.20q...: reportlab raises; got %v", item.value, got)
			}
			continue
		}
		if status != attrOK {
			t.Errorf("%.20q...: reportlab answers a colour; got %v", item.value, status)
			continue
		}
		if !colorEq(got, *item.want) {
			t.Errorf("%.20q...: want %v, got %v", item.value, *item.want, got)
		}
	}
}
