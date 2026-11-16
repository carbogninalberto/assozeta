package pdfexport

import (
	"errors"
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
	frags, _, err := parseParagraph(text, paraStyle{fontName: "Helvetica", fontSize: 9, leading: 12})
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
			if !errors.Is(err, ErrLayout) || errors.Is(err, ErrNotPorted) {
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
	if value := s.this(seqNoKey); value != 1 {
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
	if value := s.this(seqNoKey); value != 4 {
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
			x = counter.value
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
