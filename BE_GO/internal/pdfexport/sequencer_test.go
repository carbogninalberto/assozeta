package pdfexport

import (
	"errors"
	"fmt"
	"math/big"
	"strings"
	"testing"
)

// resetParaSequencer is the fresh Sequencer a scenario starts from:
// reportlab's setSequencer(Sequencer()) before each parse.
func resetParaSequencer() {
	paraSeqMu.Lock()
	defer paraSeqMu.Unlock()
	paraSeq = newSeqSequencer()
}

// paragraphText is the text of the paragraphs of a parse.
func paragraphText(t *testing.T, text string) (string, error) {
	t.Helper()
	frags, _, _, err := parseParagraph(text, paraStyle{fontName: "Helvetica", fontSize: numInt(9), leading: numInt(12)})
	if err != nil {
		return "", err
	}
	var out strings.Builder
	for _, f := range frags {
		out.WriteString(f.text)
	}
	return out.String(), nil
}

// TestSequencerParagraphs compares the text of paragraphs with <seq> and its
// tags with reportlab 4.2.5's ParaParser on a fresh Sequencer: the same list of
// texts was parsed there and the text it produced is the want below.
func TestSequencerParagraphs(t *testing.T) {
	cases := []struct {
		name, text, want string
		raises           bool
	}{
		{"plain", "A<seq/>B<seq/>C", "A1B2C", false},
		{"default counter and ids", "A<seq id=\"x\"/>B<seq id=\"x\"/>C<seq id=\"y\"/>D<seq id=\"x\"/>E", "A1B2C1D3E", false},
		{"inc no", "A<seq id=\"x\" inc=\"no\"/>B<seq id=\"x\" inc=\"NO\"/>C<seq id=\"x\"/>D", "A0B0C1D", false},
		{"inc 5", "A<seq id=\"x\" inc=\"5\"/>B<seq id=\"x\"/>C", "A0B6C", false},
		{"inc 0", "A<seq id=\"x\" inc=\"0\"/>B<seq id=\"x\"/>C", "A0B1C", false},
		{"inc uses the default counter", "A<seq id=\"q\"/><seq id=\"q\"/><seq id=\"q\" inc=\"3\"/>B<seq id=\"q\"/>C<seq/>D", "A122B4C1D", false},
		{"inc spaced", "A<seq inc=\" 5 \"/>B<seq/>C", "A0B6C", false},
		{"inc signed", "A<seq inc=\"+2\"/>B<seq/>C", "A0B3C", false},
		{"inc underscore", "A<seq inc=\"1_0\"/>B<seq/>C", "A0B11C", false},
		{"inc unicode digit", "A<seq inc=\"\u0665\"/>B<seq/>C", "A0B6C", false},
		{"inc big int", "A<seq inc=\"99999999999999999999\"/>B<seq/>C", "A0B100000000000000000000C", false},
		{"seqreset big base", "A<seq id=\"x\"/><seqreset id=\"x\" base=\"9007199254740993\"/><seq id=\"x\"/>B", "A19007199254740994B", false},
		{"seqreset base", "A<seq id=\"x\"/><seq id=\"x\"/><seqreset id=\"x\" base=\"3\"/>B<seq id=\"x\"/>C", "A12B4C", false},
		{"seqreset default base", "A<seq/><seq/><seqreset base=\"7\"/>B<seq/>C", "A12B8C", false},
		{"seqreset bad base", "A<seq id=\"x\"/><seqreset id=\"x\" base=\"zz\"/>B<seq id=\"x\"/>C", "A1B1C", false},
		{"seqreset negative base", "A<seqreset id=\"x\" base=\"-5\"/><seq id=\"x\" inc=\"no\"/>C", "A-5C", false},
		{"seqchain", "A<seq id=\"a\"/><seq id=\"a\"/><seq id=\"b\"/><seq id=\"b\"/><seqchain order=\"a b c\"/><seq id=\"a\"/>B<seq id=\"b\"/>C<seq id=\"c\"/>D", "A12123B1C1D", false},
		{"seqchain resets to zero", "A<seqreset id=\"b\" base=\"4\"/><seq id=\"b\"/><seq id=\"b\"/><seqchain order=\"a b\"/><seq id=\"a\"/>B<seq id=\"b\"/>C", "A561B1C", false},
		{"seqchain not recursive", "A<seqchain order=\"a b\"/><seqchain order=\"b c\"/><seq id=\"c\"/><seq id=\"c\"/><seq id=\"b\"/>B<seq id=\"c\"/>C<seq id=\"a\"/>D<seq id=\"c\"/>E", "A121B1C1D2E", false},
		{"seqchain empty order", "A<seqchain order=\"a  b\tc\"/>B<seq/>C", "AB1C", false},
		{"seqdefault", "A<seq id=\"x\"/>B<seq/>C<seqdefault id=\"x\"/>D<seq/>E", "A1B1CD2E", false},
		{"seqdefault empty id", "A<seq/>B<seqdefault id=\"\"/>C<seq/>D", "A1BC1D", false},
		{"seqdefault valueless id", "A<seq/>B<seqdefault id/>C<seq/>D", "A1BC2D", false},
		{"seqformat i", "A<seq id=\"x\"/><seq id=\"x\"/><seqformat id=\"x\" value=\"i\"/>B<seq id=\"x\"/>C<seq id=\"x\"/>D", "A12BiiiCivD", false},
		{"seqformat A", "A<seq id=\"x\"/><seqformat id=\"x\" value=\"A\"/>B<seq id=\"x\"/>C", "A1BBC", false},
		{"seqformat I", "A<seq id=\"x\"/><seqformat id=\"x\" value=\"I\"/>B<seq id=\"x\"/>C", "A1BIIC", false},
		{"seqformat a", "A<seqformat value=\"a\"/><seq inc=\"no\"/>B", "AzB", false},
		{"seqformat missing value", "A<seq id=\"x\"/><seqformat id=\"x\"/>B<seq id=\"x\"/>C", "A1B2C", false},
		{"seqformat zero", "A<seqformat value=\"A\"/><seq inc=\"no\"/>B", "AZB", false},
		{"seqformat I zero", "A<seqformat value=\"I\"/><seq inc=\"no\"/>B", "AB", false},
		{"template s", "A<seq template=\"%(x)s\"/>B<seq template=\"%(x+)s\"/>C<seq template=\"%(x+)s\"/>D", "A0B1C2D", false},
		{"template id and default", "A<seq id=\"y\"/><seq template=\"%(x)s.%(y+)s\"/>B<seq template=\"%(x)s.%(y)s\"/>C", "A10.2B0.2C", false},
		{"template flags and width", "A<seq template=\"[%(x)5s][%(x)-5s][%(x).0s][%(x)05s][%(x)+5s]\"/>B", "A[    0][0    ][][    0][    0]B", false},
		{"template percent", "A<seq template=\"100%% %(x)s\"/>B", "A100% 0B", false},
		{"template repr", "A<seq template=\"%(x)r\"/>B", "A'0'B", false},
		{"template counter key", "A<seq template=\"%()s%(x+)s\"/>B", "A01B", false},
		{"template text", "A<seq template=\"none\"/>B", "AnoneB", false},
		{"template roman", "A<seqformat value=\"I\"/><seq inc=\"4\"/>B", "AB", false},
		{"seq content", "A<seq id=\"x\">zz</seq>B", "A1zzB", false},
		{"seq unknown attribute", "A<seq foo=\"bar\"/>B<seq/>C", "A1B2C", false},
		{"seq upper", "A<SEQ ID=\"x\"/>B", "A1B", false},
		{"seq unclosed", "A<seq id=\"x\">B", "A1B", false},
		{"seq in b", "A<b><seq id=\"x\"/></b>B", "A1B", false},
		{"template ascii", "A<seq template=\"%(x)a\"/>B", "A'0'B", false},
		{"template char", "A<seq template=\"%(x)c\"/>B", "A0B", false},
		{"template precision", "A<seq id=\"x\"/><seq inc=\"9\"/><seq template=\"[%(x).1s][%(x)-4.1s][%(x)4.1s]\"/>B", "A10[1][1   ][   1]B", false},
		{"template empty", "A<seq template=\"\"/>B", "AB", false},
		{"template unicode", "citt\u00e0 <seq template=\"\u00e0%(x)s\u00e8\"/>", "citt\u00e0 \u00e00\u00e8", false},
		{"template balanced key", "A<seq template=\"%((x))s\"/>B", "A0B", false},
		{"seqreset empty base", "A<seq id=\"x\"/><seq id=\"x\"/><seqreset id=\"x\" base=\"\"/>B<seq id=\"x\"/>C", "A12B1C", false},
		{"empty id inc no", "A<seq id=\"\"/>B<seq id=\"\" inc=\"no\"/>C", "A1B1C", false},
		{"inc x raises", "A<seq inc=\"x\"/>B", "", true},
		{"seqformat unknown raises", "A<seqformat id=\"x\" value=\"z\"/>B", "", true},
		{"seqformat valueless raises", "A<seqformat id=\"x\" value/>B", "", true},
		{"roman past 3999 raises", "A<seqreset id=\"x\" base=\"3998\"/><seqformat id=\"x\" value=\"I\"/><seq id=\"x\"/><seq id=\"x\"/>B", "", true},
		{"roman negative raises", "A<seqreset id=\"x\" base=\"-1\"/><seqformat id=\"x\" value=\"I\"/><seq id=\"x\" inc=\"no\"/>B", "", true},
		{"template d raises", "A<seq template=\"%(x)d\"/>B", "", true},
		{"template star raises", "A<seq template=\"%(x)*s\"/>B", "", true},
		{"template bad conversion raises", "A<seq template=\"%(x)y\"/>B", "", true},
		{"template incomplete raises", "A<seq template=\"%(x)\"/>B", "", true},
		{"template key raises", "A<seq template=\"%(x\"/>B", "", true},
		{"seqchain valueless raises", "A<seqchain order/>B", "", true},
		{"template valueless raises", "A<seq template/>B", "", true},
	}
	for _, item := range cases {
		resetParaSequencer()
		text, err := paragraphText(t, item.text)
		if item.raises {
			if !errors.Is(err, ErrLayout) {
				t.Errorf("%s: %q: %v", item.name, item.text, err)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %q: %v", item.name, item.text, err)
			continue
		}
		if text != item.want {
			t.Errorf("%s: %q: text %q, want %q", item.name, item.text, text, item.want)
		}
	}
}

// TestSequencerOperations is the Sequencer class itself: the values reportlab
// answered for the same operations on a fresh Sequencer.
func TestSequencerOperations(t *testing.T) {
	s := newSeqSequencer()
	for _, want := range []string{"1", "2", "3"} {
		if value, _ := s.nextf(seqKey{name: "Bullets"}); value != want {
			t.Fatalf("next Bullets = %q, want %q", value, want)
		}
	}
	s.reset(seqKey{name: "Bullets"}, 0)
	if value, _ := s.nextf(seqKey{name: "Bullets"}); value != "1" {
		t.Fatalf("Bullets after reset = %q", value)
	}
	for _, want := range []string{"1", "2"} {
		if value, _ := s.nextf(seqKey{name: "A"}); value != want {
			t.Fatalf("nextf A = %q, want %q", value, want)
		}
	}
	if value, _ := s.thisf(seqKey{name: "A"}); value != "2" {
		t.Fatalf("thisf A = %q", value)
	}
	for _, item := range []struct{ key, want string }{{"A", "2"}, {"A+", "3"}, {"A+", "4"}, {"A", "4"}} {
		if value, _ := s.getitem(item.key); value != item.want {
			t.Fatalf("getitem %q = %q, want %q", item.key, value, item.want)
		}
	}
	if text, err := s.format("F %(A)s-%(A+)s %%"); err != nil || text != "F 4-5 %" {
		t.Fatalf("format = %q, %v", text, err)
	}
	if value, _ := s.nextf(seqNoKey); value != "1" {
		t.Fatalf("default nextf = %q", value)
	}
	if value := s.this(seqNoKey).Int64(); value != 1 {
		t.Fatalf("default this = %d", value)
	}
	if value, _ := s.thisf(seqNoKey); value != "1" {
		t.Fatalf("default thisf = %q", value)
	}
	s.setDefaultCounter(seqKey{name: "d"})
	for _, want := range []string{"1", "2"} {
		if value, _ := s.nextf(seqNoKey); value != want {
			t.Fatalf("default d nextf = %q, want %q", value, want)
		}
	}
	if value, _ := s.thisf(seqNoKey); value != "2" {
		t.Fatalf("default d thisf = %q", value)
	}
	if !s.setFormat(seqKey{name: "d"}, "i") {
		t.Fatal("format i")
	}
	for _, want := range []string{"iii", "iv"} {
		if value, _ := s.nextf(seqNoKey); value != want {
			t.Fatalf("format i nextf = %q, want %q", value, want)
		}
	}
	s.reset(seqKey{name: "x"}, 10)
	if value, _ := s.thisf(seqKey{name: "x"}); value != "10" {
		t.Fatalf("x = %q", value)
	}
	if value := s.this(seqNoKey).Int64(); value != 4 {
		t.Fatalf("default d = %d", value)
	}
	if s.setFormat(seqNoKey, "z") {
		t.Fatal("an unknown format is a KeyError")
	}
}

// TestSequenceTemplateSequenceObject is a template that formats the Sequencer
// object itself: reportlab's `template % self._seq` prints its default repr,
// '<reportlab.lib.sequencer.Sequencer object at 0x...>', which this port
// answers with pystr.ObjectAddress (approved divergence X18). The want texts
// are reportlab 4.2.5's output for the same templates on a fresh Sequencer,
// with that address replaced.
func TestSequenceTemplateSequenceObject(t *testing.T) {
	const object = "<reportlab.lib.sequencer.Sequencer object at 0x7f0000000000>"
	cases := []struct {
		name, template, want string
	}{
		{"s", "%s", object},
		{"r", "%r", object},
		{"a", "%a", object},
		{"width", "%5s", object},
		{"left width", "%-20r", object},
		{"plus", "%+s", object},
		{"space", "% s", object},
		{"zero", "%05s", object},
		{"hash", "%#s", object},
		{"precision", "%.3s", "<re"},
		{"width and precision", "%10.2r", "        <r"},
		{"left width and precision", "%-8.3r", "<re     "},
		{"width and precision 2", "%5.3s", "  <re"},
		{"text around", "x%s%%", "x" + object + "%"},
		{"key first", "%s %(x)s", object + " 0"},
	}
	for _, item := range cases {
		resetParaSequencer()
		text, err := paragraphText(t, `A<seq template="`+item.template+`"/>B`)
		if err != nil {
			t.Errorf("%s: %q: %v", item.name, item.template, err)
			continue
		}
		if want := "A" + item.want + "B"; text != want {
			t.Errorf("%s: %q: text %q, want %q", item.name, item.template, text, want)
		}
	}

	// The object is one positional argument: a second conversion, before or
	// after a mapping key, is Python's argument exhaustion; the number and
	// character conversions are the TypeErrors str() raises for the object.
	raises := []struct{ template, want string }{
		{"%s %s", "not enough arguments for format string"},
		{"%s %5d", "not enough arguments for format string"},
		{"%(x)s %s", "not enough arguments for format string"},
		{"%(x)s %5y", "not enough arguments for format string"},
		{"%s %*s", "not enough arguments for format string"},
		{"%*s", "* wants int"},
		{"%(x)*s", "* wants int"},
		{"%c", "%c requires int or char"},
		{"%d", "%d format: a real number is required, not Sequencer"},
		{"%u", "%u format: a real number is required, not Sequencer"},
		{"%x", "%x format: an integer is required, not Sequencer"},
		{"%o", "%o format: an integer is required, not Sequencer"},
		{"%f", "must be real number, not Sequencer"},
		{"%e", "must be real number, not Sequencer"},
	}
	for _, item := range raises {
		resetParaSequencer()
		_, err := paragraphText(t, `A<seq template="`+item.template+`"/>B`)
		var failure *LayoutError
		if !errors.As(err, &failure) {
			t.Errorf("%q: %v", item.template, err)
			continue
		}
		if want := "\nparagraph text '<para>A<seq template=\"" + item.template + "\"/>B</para>' caused exception " + item.want; failure.Text != want {
			t.Errorf("%q:\nwant %q\n got %q", item.template, want, failure.Text)
		}
	}
}

// TestSequenceTemplateWidthLimits compares a width and a precision field with
// CPython 3.13.16 (`template % Sequencer()`): a width is a Py_ssize_t, so more
// digits than 9223372036854775807 raise ValueError('width too big'), while a
// precision is an int, so more than 2147483647 raise
// ValueError('precision too big'). A width beyond seqMaxWidth pads to
// seqMaxWidth: reportlab writes the spaces, which change no layout (the corpus
// compares widths of 5 and 50), and its own allocation is bounded by the host's
// memory (approved divergence, group "platform").
func TestSequenceTemplateWidthLimits(t *testing.T) {
	cases := []struct {
		template string
		want     string // when err is empty and length is 0
		length   int    // when want is empty: the padded field ends in "0"
		err      string
	}{
		{"%(w)5s", "    0", 0, ""},
		{"%(w)-5s", "0    ", 0, ""},
		{"%(w)50s", strings.Repeat(" ", 49) + "0", 0, ""},
		{"%(w)1048575s", "", 1048575, ""},
		{"%(w)1048576s", "", seqMaxWidth, ""},
		{"%(w)1048577s", "", seqMaxWidth, ""},
		{"%(w)1000000000000s", "", seqMaxWidth, ""},
		{"%(w)9223372036854775807s", "", seqMaxWidth, ""},
		{"%(w)9223372036854775808s", "", 0, "width too big"},
		{"%(w)18446744073709551616s", "", 0, "width too big"},
		{"%(w)9999999999999999999999999999s", "", 0, "width too big"},
		{"%(w).5s", "0", 0, ""},
		{"%(w).0s", "", 0, ""},
		{"%(w)5.0s", "     ", 0, ""},
		{"%(w)-5.0s", "     ", 0, ""},
		{"%(w).2147483647s", "0", 0, ""},
		{"%(w).2147483648s", "", 0, "precision too big"},
		{"%(w).9223372036854775807s", "", 0, "precision too big"},
		{"%(w)3.9223372036854775808s", "", 0, "precision too big"},
		{"%(w)9223372036854775808.1s", "", 0, "width too big"},
		{"%(w)9223372036854775808d", "", 0, "width too big"},
	}
	for _, item := range cases {
		s := newSeqSequencer()
		got, err := s.format(item.template)
		if item.err != "" {
			if err == nil || err.Error() != item.err {
				t.Errorf("%q: err %v, want %q", item.template, err, item.err)
			}
			continue
		}
		if err != nil {
			t.Errorf("%q: %v", item.template, err)
			continue
		}
		if item.length == 0 {
			if got != item.want {
				t.Errorf("%q: %q, want %q", item.template, got, item.want)
			}
			continue
		}
		body := strings.TrimSuffix(got, "0")
		if len(got) != item.length || body == got || strings.Trim(body, " ") != "" {
			t.Errorf("%q: %d bytes ending %q", item.template, len(got), got[max(0, len(got)-5):])
		}
	}

	// The (key) is read before the digits of the width, so the counter moves
	// even when the width then raises.
	s := newSeqSequencer()
	if _, err := s.format("%(w+)9223372036854775808s"); err == nil || err.Error() != "width too big" {
		t.Fatalf("width overflow: %v", err)
	}
	if value := s.counters[seqKey{name: "w"}].value; value.Cmp(big.NewInt(1)) != 0 {
		t.Fatalf("counter w = %d, want the lookup to have moved it to 1", value)
	}
}

// A width beyond seqMaxWidth does not fail the paragraph: reportlab pads
// with that many spaces, this port with seqMaxWidth (its lines break between
// the same words).
func TestSequenceTemplateWiderThanSeqMaxWidth(t *testing.T) {
	resetParaSequencer()
	text, err := paragraphText(t, `A<seq template="%(w)1048577s"/>B`)
	if err != nil {
		t.Fatal(err)
	}
	if want := "A" + strings.Repeat(" ", seqMaxWidth-1) + "0B"; text != want {
		t.Fatalf("%d bytes, %d spaces before the value %q", len(text), len(text)-2, text[max(0, len(text)-3):])
	}
}

// layoutWords is the word list a paragraph's line breaking works from: the
// single-fragment path trims and splits the text, the several-fragment path
// _getFragWords splits each fragment on whitespace. The dump keeps the fonts,
// the words and the space flags, so two texts with the same words after a run
// of spaces lay out the same lines.
func layoutWords(t *testing.T, text string) string {
	t.Helper()
	p, err := newParagraph(text, paraStyle{fontName: "Helvetica", fontSize: numInt(9), leading: numInt(12)})
	if err != nil {
		t.Fatal(err)
	}
	var out strings.Builder
	if p.simpleKind() {
		p.breakSimple([]float64{200, 200})
		for _, line := range p.lines {
			for _, word := range line.words {
				out.WriteString(string(word.text))
				out.WriteString("|")
			}
			out.WriteString("\n")
		}
		return out.String()
	}
	for _, word := range p.fragWords(200, true) {
		for _, part := range word.parts {
			fmt.Fprintf(&out, "%s:%s|", part.f.font.name, string(part.text.s))
		}
		fmt.Fprintf(&out, "hs=%t\n", word.hs)
	}
	return out.String()
}

// TestSequenceTemplatePaddingWords checks that the padding of a template only
// separates words: reportlab's breakLines splits a text on whitespace, so a run
// of N >= 1 spaces lays out like one space in the one-fragment path (the text
// is stripped and split) and in the several-fragment path (_getFragWords
// collapses a run at the start or end of a fragment into one empty word). The
// corpus records the fragments of the 5 and 50 pairs.
func TestSequenceTemplatePaddingWords(t *testing.T) {
	cases := []struct{ name, five, fifty string }{
		{"one fragment", `<seq template="%(w)5s"/>`, `<seq template="%(w)50s"/>`},
		{"between fragments", `A<seq template="%(w)5s"/>B`, `A<seq template="%(w)50s"/>B`},
		{"after a fragment", `<b>A</b><seq template="%(w)5s"/>`, `<b>A</b><seq template="%(w)50s"/>`},
		{"before a fragment", `A<seq template="%(w)5s"/><i>B</i>`, `A<seq template="%(w)50s"/><i>B</i>`},
		{"all spaces", `<b>A</b><seq template="%(w)5.0s"/><i>B</i>`, `<b>A</b><seq template="%(w)50.0s"/><i>B</i>`},
		{"left padded", `A<seq template="%(w)-5s"/>Z`, `A<seq template="%(w)-50s"/>Z`},
	}
	for _, item := range cases {
		resetParaSequencer()
		five := layoutWords(t, item.five)
		resetParaSequencer()
		fifty := layoutWords(t, item.fifty)
		if five != fifty {
			t.Errorf("%s:\n 5: %q\n50: %q", item.name, five, fifty)
		}
	}
}

// The numbers continue across paragraphs, documents and requests: one
// Sequencer serves the process, as reportlab's getSequencer does.
func TestSequencerContinuesAcrossParagraphs(t *testing.T) {
	resetParaSequencer()
	for i, want := range []string{"A1B", "A2B", "A3B"} {
		text, err := paragraphText(t, "A<seq/>B")
		if err != nil || text != want {
			t.Fatalf("paragraph %d = %q, %v, want %q", i, text, err, want)
		}
	}
}

// TestSequencerExceptionTexts compares str(exception) of a failed parse with
// the text reportlab's ParaParser annotates it with.
func TestSequencerExceptionTexts(t *testing.T) {
	cases := []struct{ text, want string }{
		{"A<seq inc=\"x\"/>B", "\nparagraph text '<para>A<seq inc=\"x\"/>B</para>' caused exception invalid literal for int() with base 10: 'x'"},
		{"A<seqreset id=\"x\" base=\"3998\"/><seqformat id=\"x\" value=\"I\"/><seq id=\"x\"/><seq id=\"x\"/>B", "\nparagraph text '<para>A<seqreset id=\"x\" base=\"3998\"/><seqformat id=\"x\" value=\"I\"/><seq id=\"x\"/><seq id=\"x\"/>B</para>' caused exception illegal value"},
		{"A<seqchain order/>B", "\nparagraph text '<para>A<seqchain order/>B</para>' caused exception 'NoneType' object has no attribute 'split'"},
		{"A<seq template/>B", "\nparagraph text '<para>A<seq template/>B</para>' caused exception unsupported operand type(s) for %: 'NoneType' and 'Sequencer'"},
		{"A<seq template=\"%(x)d\"/>B", "\nparagraph text '<para>A<seq template=\"%(x)d\"/>B</para>' caused exception %d format: a real number is required, not str"},
		{"A<seq template=\"%(x)*s\"/>B", "\nparagraph text '<para>A<seq template=\"%(x)*s\"/>B</para>' caused exception * wants int"},
		{"A<seq template=\"%(x)y\"/>B", "\nparagraph text '<para>A<seq template=\"%(x)y\"/>B</para>' caused exception unsupported format character 'y' (0x79) at index 4"},
		{"A<seq template=\"%(x)\"/>B", "\nparagraph text '<para>A<seq template=\"%(x)\"/>B</para>' caused exception incomplete format"},
		{"A<seq template=\"%(x\"/>B", "\nparagraph text '<para>A<seq template=\"%(x\"/>B</para>' caused exception incomplete format key"},
		{"A<seqreset id=\"x\" base=\"10\"/><seq template=\"%(x)c\"/>B", "\nparagraph text '<para>A<seqreset id=\"x\" base=\"10\"/><seq template=\"%(x)c\"/>B</para>' caused exception %c requires int or char"},
		{"A<seq template=\"%(w)9223372036854775808s\"/>B", "\nparagraph text '<para>A<seq template=\"%(w)9223372036854775808s\"/>B</para>' caused exception width too big"},
		{"A<seq template=\"%(w).2147483648s\"/>B", "\nparagraph text '<para>A<seq template=\"%(w).2147483648s\"/>B</para>' caused exception precision too big"},
	}
	for _, item := range cases {
		resetParaSequencer()
		_, err := paragraphText(t, item.text)
		var failure *LayoutError
		if !errors.As(err, &failure) {
			t.Errorf("%q: %v", item.text, err)
			continue
		}
		if failure.Text != item.want {
			t.Errorf("%q:\nwant %q\n got %q", item.text, item.want, failure.Text)
		}
	}
}

// TestSequenceTemplateCPythonOrder replays `template % Sequencer()` as
// CPython 3.13.16 runs it with reportlab 4.2.5 (the object address replaced
// by pystr.ObjectAddress, X18): a (key) is looked up as soon as it is parsed, so
// its counter advances even when the conversion then raises, and one h, l
// or L length modifier is skipped. x is the value of counter x afterwards
// (-1: never made).
func TestSequenceTemplateCPythonOrder(t *testing.T) {
	cases := []struct {
		template, text, err string
		x                   int64
	}{
		{"%s", "<reportlab.lib.sequencer.Sequencer object at 0x7f0000000000>", "", -1},
		{"%(x)s %s", "", "not enough arguments for format string", 0},
		{"%s %(x)s", "<reportlab.lib.sequencer.Sequencer object at 0x7f0000000000> 0", "", 0},
		{"%(x+)*s", "", "* wants int", 1},
		{"%(x+)5*s", "", "unsupported format character '*' (0x2a) at index 6", 1},
		{"%(x+).*s", "", "* wants int", 1},
		{"%(x+)5", "", "incomplete format", 1},
		{"%(y+)s%(y)s", "11", "", -1},
		{"%(x)5.2s|", "    0|", "", 0},
		{"%(x+)-4s|", "1   |", "", 1},
		{"%(x)r", "'0'", "", 0},
		{"%(x)a", "'0'", "", 0},
		{"%(x+)c", "1", "", 1},
		{"%(x)", "", "incomplete format", 0},
		{"%(x)5", "", "incomplete format", 0},
		{"%s%", "", "incomplete format", -1},
		{"%(x)0s", "0", "", 0},
		{"%.s", "", "", -1},
		{"%(x).s|", "|", "", 0},
		{"%(x)10.0s|", "          |", "", 0},
		{"%c", "", "%c requires int or char", -1},
		{"%(x)ld", "", "%d format: a real number is required, not str", 0},
		{"%ls", "<reportlab.lib.sequencer.Sequencer object at 0x7f0000000000>", "", -1},
		{"%(x)ls", "0", "", 0},
		{"%hs", "<reportlab.lib.sequencer.Sequencer object at 0x7f0000000000>", "", -1},
		{"%Ls", "<reportlab.lib.sequencer.Sequencer object at 0x7f0000000000>", "", -1},
		{"%(x)hd", "", "%d format: a real number is required, not str", 0},
		{"%(x)%s", "", "unsupported format character '%' (0x25) at index 4", 0},
		{"abc", "abc", "", -1},
		{"%(x)s%(x+)s%(x)s", "011", "", 1},
		{"%(x+)d", "", "%d format: a real number is required, not str", 1},
		{"%*s", "", "* wants int", -1},
		{"%(x+)ls|%(x+)Ls|%(x+)hs", "1|2|3", "", 3},
		{"%(x+)l", "", "incomplete format", 1},
		{"%(x+)5.3l", "", "incomplete format", 1},
		{"%lls", "", "unsupported format character 'l' (0x6c) at index 2", -1},
		{"%(x+)s %*s", "", "not enough arguments for format string", 1},
		{"%5%", "", "unsupported format character '%' (0x25) at index 2", -1},
	}
	for _, item := range cases {
		s := newSeqSequencer()
		got, err := s.format(item.template)
		x := int64(-1)
		if counter, ok := s.counters[seqKey{name: "x"}]; ok {
			x = counter.value.Int64()
		}
		gotErr := ""
		if err != nil {
			gotErr = err.Error()
		}
		if got != item.text || gotErr != item.err || x != item.x {
			t.Errorf("%q: got (%q, %q, x=%d), want (%q, %q, x=%d)", item.template, got, gotErr, x, item.text, item.err, item.x)
		}
	}
}
