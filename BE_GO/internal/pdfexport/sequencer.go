package pdfexport

import (
	"errors"
	"fmt"
	"math"
	"math/big"
	"strings"
	"sync"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// This file ports reportlab/lib/sequencer.py and the <seq>, <seqreset>,
// <seqchain>, <seqdefault> and <seqformat> handlers of paraparser.py.
//
// getSequencer() answers one Sequencer for the whole process and nothing in a
// running reportlab process resets it (rl_config._reset is only called by test
// utilities), so the numbers continue across paragraphs, documents and
// requests. The Sequencer below is that one object; a paragraph's parse holds
// its lock only while it calls it.

// seqKey is a key of Sequencer._counters: a str, or Python's None. An empty
// name is Python's ” and is a different key from None, though both mean the
// default counter where a counter is tested for truth.
type seqKey struct {
	name string
	none bool
}

// seqNoKey is Python's None.
var seqNoKey = seqKey{none: true}

// seqAttrKey is the id attribute of a seq tag: its value, None when the
// attribute is absent or has no value.
func seqAttrKey(values map[string]htmlAttr, name string) seqKey {
	attr, ok := values[name]
	if !ok || !attr.has {
		return seqNoKey
	}
	return seqKey{name: attr.value}
}

// seqSplit is str.split(): runs of Python's whitespace characters.
func seqSplit(text string) []string {
	return strings.FieldsFunc(text, func(r rune) bool { return strings.ContainsRune(pySpaces, r) })
}

// seqCounter is sequencer._Counter. Its base is the zero every counter starts
// at: nothing changes a base, so _Counter.reset() goes back to 0. The value is
// a Python int, exact at any size.
type seqCounter struct {
	value  *big.Int
	format byte // the key of one of the five formats: '1', 'A', 'a', 'I' or 'i'
	resets []*seqCounter
}

// seqSequencer is sequencer.Sequencer.
type seqSequencer struct {
	counters map[seqKey]*seqCounter
	def      seqKey
	// oversized is set by format when one of the fields of the template was
	// wider than seqExactWidth, and reset at the start of every format.
	oversized bool
}

func newSeqSequencer() *seqSequencer {
	return &seqSequencer{counters: map[seqKey]*seqCounter{}, def: seqNoKey}
}

// counter is Sequencer._getCounter: counters are made on demand at zero.
func (s *seqSequencer) counter(key seqKey) *seqCounter {
	counter, ok := s.counters[key]
	if !ok {
		counter = &seqCounter{format: '1', value: new(big.Int)}
		s.counters[key] = counter
	}
	return counter
}

// key is `if not counter: counter = self._defaultCounter`: no counter and an
// empty one both mean the default.
func (s *seqSequencer) key(counter seqKey) seqKey {
	if counter.none || counter.name == "" {
		return s.def
	}
	return counter
}

// next is _Counter.next: the value goes up by one, then the counters chained
// to this one start again.
func (c *seqCounter) next() *big.Int {
	c.value = new(big.Int).Add(c.value, big.NewInt(1))
	value := new(big.Int).Set(c.value)
	for _, child := range c.resets {
		child.value = new(big.Int) // _Counter.reset(): the base
	}
	return value
}

// nextf is Sequencer.nextf: the formatted value after the increment.
func (s *seqSequencer) nextf(counter seqKey) (string, error) {
	c := s.counter(s.key(counter))
	return formatSeq(c.format, c.next())
}

// thisf is Sequencer.thisf: the formatted value as it stands.
func (s *seqSequencer) thisf(counter seqKey) (string, error) {
	c := s.counter(s.key(counter))
	return formatSeq(c.format, c.value)
}

// this is Sequencer._this: a counter's value, not its text.
func (s *seqSequencer) this(counter seqKey) *big.Int {
	return s.counter(s.key(counter)).value
}

// reset is Sequencer.reset: it sets the value (the base stays zero, so a
// chained reset of the counter goes back to 0 and not to this value).
func (s *seqSequencer) reset(counter seqKey, base int64) {
	s.resetWide(counter, big.NewInt(base))
}

// resetWide is reset for a Python int of any size.
func (s *seqSequencer) resetWide(counter seqKey, base *big.Int) {
	s.counter(s.key(counter)).value = new(big.Int).Set(base)
}

// setDefaultCounter is Sequencer.setDefaultCounter.
func (s *seqSequencer) setDefaultCounter(counter seqKey) { s.def = counter }

// setFormat is Sequencer.setFormat: a name outside the five formats is a
// KeyError, raised before the counter is made.
func (s *seqSequencer) setFormat(counter seqKey, format string) bool {
	switch format {
	case "1", "A", "a", "I", "i":
	default:
		return false
	}
	s.counter(counter).format = format[0]
	return true
}

// chain is Sequencer.chain: a chain is kept once.
func (s *seqSequencer) chain(parent, child seqKey) {
	p, c := s.counter(parent), s.counter(child)
	for _, existing := range p.resets {
		if existing == c {
			return
		}
	}
	p.resets = append(p.resets, c)
}

// getitem is Sequencer.__getitem__: a key ending in '+' takes the next value,
// any other the current one.
func (s *seqSequencer) getitem(key string) (string, error) {
	if strings.HasSuffix(key, "+") {
		return s.nextf(seqKey{name: key[:len(key)-1]})
	}
	return s.thisf(seqKey{name: key})
}

// seqExactWidth is the widest field a template pads exactly. reportlab writes
// the width-len(text) spaces of the field, bounded only by the memory of the
// host (a width of 9223372036854775807 asks for that many bytes). This port
// writes every field up to seqExactWidth exactly; a wider field pads to
// seqExactWidth+1 characters and format records it as oversized
// (seqSequencer.oversized), which the default, RTL and LTR line breaking
// ignores: they draw a run of spaces between words as one space, so the pad
// lays out the same lines at any width. wordWrap 'CJK' lays out every space of
// the pad, so breakCJK answers an oversized field as the MemoryError CPython
// raises when the host runs out of memory; that host-memory bound is not an
// approved divergence yet (pending owner decision).
const seqExactWidth = 1 << 21

// seqObjectText is str(Sequencer()): Sequencer has no __repr__ of its own, so
// CPython prints the default repr with the object's address, which this port
// answers as pystr.ObjectAddress (approved divergence X18).
const seqObjectText = "<reportlab.lib.sequencer.Sequencer object at " + pystr.ObjectAddress + ">"

var rnTemplates = [10]int{0, 1, 9, 73, 10, 2, 17, 137, 1097, 11} // _RN_TEMPLATES, in octal there

const rnLetters = "IVXLCDM" // _RN_LETTERS

// formatRoman is _format_I: the letters of a value in 0..3999.
func formatRoman(value *big.Int) string {
	var letters []byte
	base := -1
	ten := big.NewInt(10)
	number := new(big.Int).Set(value)
	for number.Sign() > 0 {
		remainder := new(big.Int)
		number.QuoRem(number, ten, remainder)
		digit := int(remainder.Int64())
		for template := rnTemplates[digit]; template > 0; {
			letter := template % 8
			template /= 8
			letters = append(letters, rnLetters[letter+base])
		}
		base += 2
	}
	for i, j := 0, len(letters)-1; i < j; i, j = i+1, j-1 {
		letters[i], letters[j] = letters[j], letters[i]
	}
	return string(letters)
}

// formatSeq is one of Sequencer._formatters: registerFormat has no caller, so
// a counter's format is one of the five.
func formatSeq(format byte, value *big.Int) (string, error) {
	switch format {
	case 'A', 'a':
		index := int(new(big.Int).Mod(new(big.Int).Sub(value, big.NewInt(1)), big.NewInt(26)).Int64())
		if format == 'A' {
			return string(rune('A' + index)), nil
		}
		return string(rune('a' + index)), nil
	case 'I':
		// _format_I raises ValueError outside 0..3999.
		if value.Sign() < 0 || value.Cmp(big.NewInt(3999)) > 0 {
			return "", errors.New("illegal value")
		}
		return formatRoman(value), nil
	case 'i':
		if value.Sign() < 0 || value.Cmp(big.NewInt(3999)) > 0 {
			return "", errors.New("illegal value")
		}
		return strings.ToLower(formatRoman(value)), nil
	}
	return value.String(), nil
}

// format is Sequencer.format: `template % self`, a mapping format of the
// values __getitem__ answers with the flags, width and precision that apply
// to a str. The values are always text, so the number conversions raise, as
// they do in Python.
//
// A template without a (key) formats the Sequencer object itself. Python
// prints its default repr, and its first positional conversion consumes the
// object: a second one is "not enough arguments for format string", and a
// conversion after a mapping key is too (`'%(x)s %s' % seq`). A `*` also
// takes a positional argument, so `'%*s' % seq` is int(seq)'s TypeError.
//
// The order is CPython's (unicode_format_arg_parse): a (key) is looked up
// with __getitem__ as soon as it is parsed, so a counter advances even when
// the rest of the conversion then raises, and the looked-up value becomes
// the one positional argument a `*` or the conversion consumes. One length
// modifier (h, l or L) is skipped before the conversion character.
//
// A width is a Py_ssize_t and a precision an int: more digits than fit raise
// ValueError('width too big') or ValueError('precision too big'), as CPython
// checks while reading them. A width up to seqExactWidth pads exactly; a wider
// one pads to seqExactWidth+1 characters and sets oversized, which the caller
// reads after the call.
func (s *seqSequencer) format(template string) (string, error) {
	s.oversized = false
	var out strings.Builder
	// The positional argument a `*` or a conversion takes: the Sequencer
	// itself, or the text of the last (key). consumed is CPython's argidx
	// past it.
	currentText, currentIsSeq, consumed := "", true, false
	take := func() error {
		if consumed {
			return errors.New("not enough arguments for format string")
		}
		consumed = true
		return nil
	}
	for pos := 0; pos < len(template); {
		if template[pos] != '%' {
			out.WriteByte(template[pos])
			pos++
			continue
		}
		pos++
		if pos >= len(template) {
			return "", errors.New("incomplete format")
		}
		if template[pos] == '%' {
			out.WriteByte('%')
			pos++
			continue
		}
		if template[pos] == '(' {
			// Skip over balanced parentheses: the key of the conversion,
			// read with __getitem__ right away.
			keyStart := pos + 1
			depth := 1
			for pos++; depth > 0; pos++ {
				if pos >= len(template) {
					return "", errors.New("incomplete format key")
				}
				switch template[pos] {
				case '(':
					depth++
				case ')':
					depth--
				}
			}
			value, err := s.getitem(template[keyStart : pos-1])
			if err != nil {
				return "", err
			}
			currentText, currentIsSeq, consumed = value, false, false
		}
		left := false
	flags:
		for pos < len(template) {
			switch template[pos] {
			case '-':
				left = true
			case '+', ' ', '#':
			case '0':
				// The zero flag only pads a number: these conversions pad with spaces.
			default:
				break flags
			}
			pos++
		}
		width := int64(0)
		if pos < len(template) && template[pos] == '*' {
			// `*` takes the next positional argument: the Sequencer or a
			// counter's text, which int() refuses, or nothing after it.
			if err := take(); err != nil {
				return "", err
			}
			return "", errors.New("* wants int")
		}
		for pos < len(template) && template[pos] >= '0' && template[pos] <= '9' {
			digit := int64(template[pos] - '0')
			if width > (math.MaxInt64-digit)/10 {
				return "", errors.New("width too big")
			}
			width = width*10 + digit
			pos++
		}
		precision := int64(-1)
		if pos < len(template) && template[pos] == '.' {
			precision, pos = 0, pos+1
			if pos < len(template) && template[pos] == '*' {
				if err := take(); err != nil {
					return "", err
				}
				return "", errors.New("* wants int")
			}
			for pos < len(template) && template[pos] >= '0' && template[pos] <= '9' {
				digit := int64(template[pos] - '0')
				if precision > (math.MaxInt32-digit)/10 {
					return "", errors.New("precision too big")
				}
				precision = precision*10 + digit
				pos++
			}
		}
		if pos < len(template) && (template[pos] == 'h' || template[pos] == 'l' || template[pos] == 'L') {
			pos++
		}
		if pos >= len(template) {
			return "", errors.New("incomplete format")
		}
		conversion := pos
		pos++
		if err := take(); err != nil {
			return "", err
		}
		text := ""
		if currentIsSeq {
			switch template[conversion] {
			case 's', 'r', 'a':
				text = seqObjectText
			case 'c':
				return "", errors.New("%c requires int or char")
			case 'd', 'i', 'u':
				return "", fmt.Errorf("%%%c format: a real number is required, not Sequencer", template[conversion])
			case 'o', 'x', 'X':
				return "", fmt.Errorf("%%%c format: an integer is required, not Sequencer", template[conversion])
			case 'e', 'E', 'f', 'F', 'g', 'G':
				return "", errors.New("must be real number, not Sequencer")
			default:
				return "", seqUnsupportedCharacter(template, conversion)
			}
		} else {
			switch template[conversion] {
			case 's':
				text = currentText
			case 'r', 'a':
				text = pythonAscii(currentText) // repr() and ascii() of the text a counter makes
			case 'c':
				if utf8.RuneCountInString(currentText) != 1 {
					return "", errors.New("%c requires int or char")
				}
				text = currentText
			case 'd', 'i', 'u':
				return "", fmt.Errorf("%%%c format: a real number is required, not str", template[conversion])
			case 'o', 'x', 'X':
				return "", fmt.Errorf("%%%c format: an integer is required, not str", template[conversion])
			case 'e', 'E', 'f', 'F', 'g', 'G':
				return "", errors.New("must be real number, not str")
			default:
				return "", seqUnsupportedCharacter(template, conversion)
			}
		}
		if precision >= 0 {
			if runes := []rune(text); int64(len(runes)) > precision {
				text = string(runes[:precision])
			}
		}
		limit := width
		if limit > seqExactWidth {
			// Past the exact bound every field pads the same way: one
			// character more than the bound, marked for breakCJK.
			limit = seqExactWidth + 1
			s.oversized = true
		}
		if pad := int(limit) - utf8.RuneCountInString(text); pad > 0 {
			spaces := strings.Repeat(" ", pad)
			if left {
				text += spaces
			} else {
				text = spaces + text
			}
		}
		out.WriteString(text)
	}
	return out.String(), nil
}

// seqUnsupportedCharacter is CPython's ValueError for an unknown conversion.
func seqUnsupportedCharacter(template string, conversion int) error {
	char, _ := utf8.DecodeRuneInString(template[conversion:])
	name := string(char)
	if char >= 0x80 {
		name = "?" // Python prints %c of a code point above ASCII as '?'
	}
	return fmt.Errorf("unsupported format character '%s' (0x%x) at index %d", name, char,
		utf8.RuneCountInString(template[:conversion]))
}

// paraSeqMu guards paraSeq: the one Sequencer of the process.
var (
	paraSeqMu sync.Mutex
	paraSeq   = newSeqSequencer()
)

// seqCall runs fn on the process-wide Sequencer. An error of a formatter or a
// format fails the parse the way reportlab's exception does.
//
// None of the five handlers pushes a fragment or changes the stack: their end
// handlers are pass (end_seq and the aliases of paraparser), so a self-closing
// <seq/> and <seq></seq> write the same text and the tag does not take part in
// the markup's nesting.
func (p *paraParser) seqCall(fn func(*seqSequencer) (string, error)) (string, bool) {
	paraSeqMu.Lock()
	defer paraSeqMu.Unlock()
	text, err := fn(paraSeq)
	if err != nil {
		p.fail("%s", err.Error())
		return "", false
	}
	return text, true
}

// startSeq is start_seq: the value of a counter, or the text of a template.
func (p *paraParser) startSeq(attrs []htmlAttr) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	_, values := dedupeAttrs(attrs)
	if template, ok := values["template"]; ok {
		if !template.has {
			// `None % Sequencer`
			return p.fail("%s", "unsupported operand type(s) for %: 'NoneType' and 'Sequencer'")
		}
		oversized := false
		text, ok := p.seqCall(func(s *seqSequencer) (string, error) {
			value, err := s.format(template.value)
			oversized = s.oversized
			return value, err
		})
		if !ok {
			return false
		}
		bullet := p.stack[len(p.stack)-1].isBullet
		frags, bulletFrags := len(p.frags), len(p.bulletFrags)
		if !p.data(text) {
			return false
		}
		// A field wider than seqExactWidth: the fragment that holds the pad
		// is the one breakCJK refuses (see seqExactWidth).
		if oversized {
			if bullet && len(p.bulletFrags) > bulletFrags {
				p.bulletFrags[len(p.bulletFrags)-1].seqOversized = true
			} else if !bullet && len(p.frags) > frags {
				p.frags[len(p.frags)-1].seqOversized = true
			}
		}
		return true
	}
	id := seqAttrKey(values, "id")
	increment, hasIncrement := values["inc"]
	if !hasIncrement || !increment.has || increment.value == "" {
		// `if not increment`: a missing, valueless or empty inc takes the next value.
		text, ok := p.seqCall(func(s *seqSequencer) (string, error) { return s.nextf(id) })
		if !ok {
			return false
		}
		return p.data(text)
	}
	if strings.ToLower(increment.value) == "no" {
		text, ok := p.seqCall(func(s *seqSequencer) (string, error) { return s.thisf(id) })
		if !ok {
			return false
		}
		return p.data(text)
	}
	step, st := pyInt(increment.value, 10)
	switch st {
	case attrOK:
	default:
		return p.fail("invalid literal for int() with base 10: %s", pythonAscii(increment.value))
	}
	text, ok := p.seqCall(func(s *seqSequencer) (string, error) {
		value, err := s.thisf(id)
		if err != nil {
			return "", err
		}
		// start_seq resets the counter to the value of the default counter plus
		// the step, not to the value of id plus the step.
		s.resetWide(id, new(big.Int).Add(s.this(seqNoKey), step))
		return value, nil
	})
	if !ok {
		return false
	}
	return p.data(text)
}

// startSeqReset is start_seqreset: the value a counter starts again at, 0 for
// a base that is not an integer (start_seqreset catches every exception).
func (p *paraParser) startSeqReset(attrs []htmlAttr) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	_, values := dedupeAttrs(attrs)
	id := seqAttrKey(values, "id")
	base := big.NewInt(0)
	if attr, ok := values["base"]; ok {
		if value, st := pyInt(attr.value, 10); st == attrOK {
			base = value
		}
	}
	_, ok := p.seqCall(func(s *seqSequencer) (string, error) {
		s.resetWide(id, base)
		return "", nil
	})
	return ok
}

// startSeqChain is start_seqchain: each counter of the order resets the next.
func (p *paraParser) startSeqChain(attrs []htmlAttr) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	_, values := dedupeAttrs(attrs)
	order := ""
	if attr, ok := values["order"]; ok {
		if !attr.has {
			// `None.split()`
			return p.fail("%s", "'NoneType' object has no attribute 'split'")
		}
		order = attr.value
	}
	names := seqSplit(order)
	_, ok := p.seqCall(func(s *seqSequencer) (string, error) {
		for i := 0; i+1 < len(names); i++ {
			s.chain(seqKey{name: names[i]}, seqKey{name: names[i+1]})
		}
		return "", nil
	})
	return ok
}

// startSeqDefault is start_seqdefault: the counter <seq> counts without an id.
func (p *paraParser) startSeqDefault(attrs []htmlAttr) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	_, values := dedupeAttrs(attrs)
	id := seqAttrKey(values, "id")
	_, ok := p.seqCall(func(s *seqSequencer) (string, error) {
		s.setDefaultCounter(id)
		return "", nil
	})
	return ok
}

// startSeqFormat is start_seqformat: the format of a counter, "1" when the
// attribute is missing.
func (p *paraParser) startSeqFormat(attrs []htmlAttr) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	_, values := dedupeAttrs(attrs)
	id := seqAttrKey(values, "id")
	format := "1"
	if attr, ok := values["value"]; ok {
		if !attr.has {
			// setFormat(id, None): KeyError(None)
			return p.fail("%s", "None")
		}
		format = attr.value
	}
	_, ok := p.seqCall(func(s *seqSequencer) (string, error) {
		if !s.setFormat(id, format) {
			return "", errors.New(pythonAscii(format)) // KeyError
		}
		return "", nil
	})
	return ok
}
