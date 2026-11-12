// Package idna2008 reproduces the label encoder of Python's third-party 'idna' package, IDNA 2008,
// as HTTP clients drive it: encode(s, strict=True, std3_rules=True) with uts46 left off, so no
// UTS #46 mapping happens and a label that is not pure ASCII is validated and Punycode-encoded.
//
// The tables in tables.go come from the pinned interpreter's idna.idnadata and unicodedata, so the
// accepted labels and the A-label bytes match that interpreter exactly. Lengths are counted in code
// points, as len(str) is in Python.
package idna2008

import (
	"errors"
	"fmt"
	"strings"
	"unicode/utf8"
)

// ErrIDNA stands for Python's idna.IDNAError: every failure from this package wraps it.
var ErrIDNA = errors.New("invalid label")

const (
	// maxInputLength is idna.core._max_input_length, a bound on the code points handled at once.
	maxInputLength = 1024
	// maxDomainLength is idna.core._max_domain_length, RFC 1035 octets without a trailing dot.
	maxDomainLength = 253
	// maxLabelLength is the RFC 1035 label limit, in octets of the A-label.
	maxLabelLength = 63
	// viramaCombiningClass is the canonical combining class idna.core treats as a virama.
	viramaCombiningClass = 9
	punycodePrefix       = "xn--"
)

func failure(code string) error {
	return fmt.Errorf("%w: %s", ErrIDNA, code)
}

// Encode is idna.encode(s, strict=True, std3_rules=True): the label separators are U+002E only (a
// trailing one is kept), every label is validated and Punycode-encoded, and the result is the ASCII
// text. An input that Python rejects, invalid UTF-8 included, returns an error wrapping ErrIDNA.
func Encode(s string) (string, error) {
	if !utf8.ValidString(s) {
		return "", failure("invalid_utf8")
	}
	runes := []rune(s)
	if len(runes) > maxInputLength {
		return "", failure("input_too_long")
	}
	// valid_string_length(s, trailing_dot=True).
	if len(runes) > maxDomainLength+1 {
		return "", failure("domain_too_long")
	}

	labels := strings.Split(s, ".")
	if len(labels) == 1 && labels[0] == "" {
		return "", failure("empty_domain")
	}
	trailingDot := false
	if labels[len(labels)-1] == "" {
		labels = labels[:len(labels)-1]
		trailingDot = true
	}

	encoded := make([]string, 0, len(labels)+1)
	for _, label := range labels {
		label, err := alabel(label)
		if err != nil {
			return "", err
		}
		if label == "" {
			return "", failure("empty_label")
		}
		encoded = append(encoded, label)
	}
	if trailingDot {
		encoded = append(encoded, "")
	}

	result := strings.Join(encoded, ".")
	limit := maxDomainLength
	if trailingDot {
		limit++
	}
	if len(result) > limit {
		return "", failure("domain_too_long")
	}
	return result, nil
}

// alabel is idna.core.alabel: an ASCII label is validated and returned as it is, any other label is
// validated, Punycode-encoded and prefixed with "xn--".
func alabel(label string) (string, error) {
	if len([]rune(label)) > maxInputLength {
		return "", failure("input_too_long")
	}
	if isASCII(label) {
		if _, err := ulabelASCII(label); err != nil {
			return "", err
		}
		if len(label) > maxLabelLength {
			return "", failure("label_too_long")
		}
		return label, nil
	}
	if err := checkLabel(label); err != nil {
		return "", err
	}
	encoded, err := punycode(label)
	if err != nil {
		return "", err
	}
	result := punycodePrefix + encoded
	if len(result) > maxLabelLength {
		return "", failure("label_too_long")
	}
	return result, nil
}

// ulabelASCII is idna.core.ulabel for a label that is already ASCII; alabel only uses it to
// validate. The label is lowercased before the checks, and an "xn--" prefix is decoded and required
// to be the canonical Punycode spelling of a valid U-label.
func ulabelASCII(label string) (string, error) {
	if len([]rune(label)) > maxInputLength {
		return "", failure("input_too_long")
	}
	lowered := asciiLower(label)
	if !strings.HasPrefix(lowered, punycodePrefix) {
		if err := checkLabel(lowered); err != nil {
			return "", err
		}
		return lowered, nil
	}
	content := lowered[len(punycodePrefix):]
	if content == "" || strings.HasSuffix(content, "-") {
		return "", failure("invalid_alabel")
	}
	decoded, err := punycodeDecode(content)
	if err != nil {
		return "", failure("invalid_alabel")
	}
	reencoded, err := punycode(decoded)
	if err != nil || reencoded != content {
		return "", failure("non_canonical_alabel")
	}
	if err := checkLabel(decoded); err != nil {
		return "", err
	}
	return decoded, nil
}

// checkLabel is idna.core.check_label for a str: the structural rules, the per code point
// PVALID/CONTEXTJ/CONTEXTO rules and the Bidi Rule.
func checkLabel(label string) error {
	runes := []rune(label)
	if len(runes) > maxInputLength {
		return failure("input_too_long")
	}
	if len(runes) == 0 {
		return failure("empty_label")
	}
	// Like check_nfc, this uses the domain length rather than the label length, which keeps some
	// UTS #46 callers working while still bounding the work the contextual rules below do.
	if len(runes) > maxDomainLength+1 {
		return failure("label_too_long")
	}
	// ASCII is invariant under NFC, so only other labels need normalizing.
	if !isASCII(label) && nfc(label) != label {
		return failure("not_nfc")
	}
	if err := checkHyphenOK(runes); err != nil {
		return err
	}
	if err := checkInitialCombiner(runes); err != nil {
		return err
	}
	for pos, cp := range runes {
		switch {
		case inRanges(pvalid, cp):
		case inRanges(contextj, cp):
			ok, err := validContextJ(runes, pos)
			if err != nil {
				return failure("unknown_codepoint")
			}
			if !ok {
				return failure("contextj")
			}
		case inRanges(contexto, cp):
			ok, err := validContextO(runes, pos)
			if err != nil {
				return err
			}
			if !ok {
				return failure("contexto")
			}
		default:
			return failure("disallowed_codepoint")
		}
	}
	return checkBidi(runes)
}

// checkHyphenOK is idna.core.check_hyphen_ok.
func checkHyphenOK(label []rune) error {
	if len(label) >= 4 && label[2] == '-' && label[3] == '-' {
		return failure("hyphen_3_4")
	}
	if len(label) > 0 && (label[0] == '-' || label[len(label)-1] == '-') {
		return failure("hyphen_start_end")
	}
	return nil
}

// checkInitialCombiner is idna.core.check_initial_combiner.
func checkInitialCombiner(label []rune) error {
	if len(label) > 0 && inRanges(markRanges, label[0]) {
		return failure("leading_combiner")
	}
	return nil
}

// checkBidi is idna.core.check_bidi with check_ltr left off.
func checkBidi(label []rune) error {
	if len(label) > maxInputLength {
		return failure("input_too_long")
	}
	bidiLabel := false
	for _, cp := range label {
		direction := bidiClassOf(cp)
		if direction == bidiUnknown {
			return failure("bidi_unknown_direction")
		}
		if direction == bidiR || direction == bidiAL || direction == bidiAN {
			bidiLabel = true
		}
	}
	if !bidiLabel {
		return nil
	}

	// Bidi rule 1.
	rtl := false
	switch bidiClassOf(label[0]) {
	case bidiR, bidiAL:
		rtl = true
	case bidiL:
		rtl = false
	default:
		return failure("bidi_rule_1")
	}

	validEnding := false
	numberType := bidiUnknown
	for _, cp := range label {
		direction := bidiClassOf(cp)
		if rtl {
			// Bidi rule 2.
			if !rtlAllowed[direction] {
				return failure("bidi_rule_2")
			}
			// Bidi rule 3.
			if rtlValidEnding[direction] {
				validEnding = true
			} else if direction != bidiNSM {
				validEnding = false
			}
			// Bidi rule 4.
			if direction == bidiAN || direction == bidiEN {
				if numberType == bidiUnknown {
					numberType = direction
				} else if numberType != direction {
					return failure("bidi_rule_4")
				}
			}
			continue
		}
		// Bidi rule 5.
		if !ltrAllowed[direction] {
			return failure("bidi_rule_5")
		}
		// Bidi rule 6.
		if ltrValidEnding[direction] {
			validEnding = true
		} else if direction != bidiNSM {
			validEnding = false
		}
	}
	if !validEnding {
		if rtl {
			return failure("bidi_rule_3")
		}
		return failure("bidi_rule_6")
	}
	return nil
}

// validContextJ is idna.core.valid_contextj. The error stands for the ValueError that
// idna.core._combining_class raises next to a joiner.
func validContextJ(label []rune, pos int) (bool, error) {
	if len(label) > maxInputLength {
		return false, failure("input_too_long")
	}
	switch label[pos] {
	case 0x200C: // ZERO WIDTH NON-JOINER
		if pos > 0 {
			combining, err := combiningClass(label[pos-1])
			if err != nil {
				return false, err
			}
			if combining == viramaCombiningClass {
				return true, nil
			}
		}
		ok := false
		for i := pos - 1; i >= 0; i-- {
			switch joiningTypeOf(label[i]) {
			case joiningT:
				continue
			case joiningL, joiningD:
				ok = true
			}
			break
		}
		if !ok {
			return false, nil
		}
		ok = false
		for i := pos + 1; i < len(label); i++ {
			switch joiningTypeOf(label[i]) {
			case joiningT:
				continue
			case joiningR, joiningD:
				ok = true
			}
			break
		}
		return ok, nil
	case 0x200D: // ZERO WIDTH JOINER
		if pos == 0 {
			return false, nil
		}
		combining, err := combiningClass(label[pos-1])
		if err != nil {
			return false, err
		}
		return combining == viramaCombiningClass, nil
	}
	return false, nil
}

// validContextO is idna.core.valid_contexto, whose exception argument is unused there.
func validContextO(label []rune, pos int) (bool, error) {
	if len(label) > maxInputLength {
		return false, failure("input_too_long")
	}
	cp := label[pos]
	switch {
	case cp == 0x00B7: // MIDDLE DOT
		return 0 < pos && pos < len(label)-1 && label[pos-1] == 0x006C && label[pos+1] == 0x006C, nil
	case cp == 0x0375: // GREEK LOWER NUMERAL SIGN (KERAIA)
		if pos < len(label)-1 && len(label) > 1 {
			return inRanges(scriptGreek, label[pos+1]), nil
		}
		return false, nil
	case cp == 0x05F3 || cp == 0x05F4: // HEBREW GERESH, GERSHAYIM
		if pos > 0 {
			return inRanges(scriptHebrew, label[pos-1]), nil
		}
		return false, nil
	case cp == 0x30FB: // KATAKANA MIDDLE DOT
		for _, other := range label {
			if other == 0x30FB {
				continue
			}
			if inRanges(scriptHiragana, other) || inRanges(scriptKatakana, other) || inRanges(scriptHan, other) {
				return true, nil
			}
		}
		return false, nil
	case 0x0660 <= cp && cp <= 0x0669: // ARABIC-INDIC DIGITS
		for _, other := range label {
			if 0x06F0 <= other && other <= 0x06F9 {
				return false, nil
			}
		}
		return true, nil
	case 0x06F0 <= cp && cp <= 0x06F9: // EXTENDED ARABIC-INDIC DIGITS
		for _, other := range label {
			if 0x0660 <= other && other <= 0x0669 {
				return false, nil
			}
		}
		return true, nil
	}
	return false, nil
}

// combiningClass is idna.core._combining_class. Only the virama class is told apart, and a code
// point without a name where the class would be zero is the ValueError Python raises there.
func combiningClass(cp rune) (int, error) {
	if inRanges(viramaRanges, cp) {
		return viramaCombiningClass, nil
	}
	if inRanges(namelessRanges, cp) {
		return 0, errors.New("unknown character in unicodedata")
	}
	return 0, nil
}

// Hangul syllable composition, Unicode 15.1 section 3.12.
const (
	hangulSBase  = 0xAC00
	hangulLBase  = 0x1100
	hangulVBase  = 0x1161
	hangulTBase  = 0x11A7
	hangulLCount = 19
	hangulVCount = 21
	hangulTCount = 28
	hangulNCount = hangulVCount * hangulTCount
	hangulSCount = hangulLCount * hangulNCount
)

// nfc is unicodedata.normalize("NFC"): the canonical decomposition from tables.go, canonical
// ordering, then canonical composition, with Hangul handled arithmetically.
func nfc(text string) string {
	decomposed := make([]rune, 0, len(text))
	for _, r := range text {
		decomposed = appendDecomposed(decomposed, r)
	}
	canonicalOrder(decomposed)
	return string(composeDecomposed(decomposed))
}

// appendDecomposed appends the full canonical decomposition of r.
func appendDecomposed(out []rune, r rune) []rune {
	if hangulSBase <= r && r < hangulSBase+hangulSCount {
		index := int(r - hangulSBase)
		out = append(out, hangulLBase+rune(index/hangulNCount), hangulVBase+rune((index%hangulNCount)/hangulTCount))
		if trailing := index % hangulTCount; trailing != 0 {
			out = append(out, hangulTBase+rune(trailing))
		}
		return out
	}
	if decomposition, ok := nfcDecompositions[r]; ok {
		return append(out, []rune(decomposition)...)
	}
	return append(out, r)
}

// canonicalOrder sorts every run of non-starters by combining class, keeping equal classes in order.
func canonicalOrder(runes []rune) {
	for i := 1; i < len(runes); i++ {
		class := int(combiningClassOf(runes[i]))
		if class == 0 {
			continue
		}
		for j := i; j > 0; j-- {
			previous := int(combiningClassOf(runes[j-1]))
			if previous <= class {
				break
			}
			runes[j], runes[j-1] = runes[j-1], runes[j]
		}
	}
}

// composeDecomposed is the canonical composition of a canonically ordered decomposition.
func composeDecomposed(decomposed []rune) []rune {
	composed := make([]rune, 0, len(decomposed))
	starter := -1
	lastClass := 0
	for _, r := range decomposed {
		class := int(combiningClassOf(r))
		// r is not blocked from the starter when nothing but lower classes precedes it.
		if starter >= 0 && (lastClass == 0 || lastClass < class) {
			if combined, ok := composePair(composed[starter], r); ok {
				composed[starter] = combined
				continue
			}
		}
		if class == 0 {
			starter = len(composed)
		}
		composed = append(composed, r)
		lastClass = class
	}
	return composed
}

// composePair returns the composed code point of a canonical composition pair, Hangul included.
func composePair(first, second rune) (rune, bool) {
	if hangulLBase <= first && first < hangulLBase+hangulLCount &&
		hangulVBase <= second && second < hangulVBase+hangulVCount {
		return hangulSBase + (first-hangulLBase)*hangulNCount + (second-hangulVBase)*hangulTCount, true
	}
	if hangulSBase <= first && first < hangulSBase+hangulSCount &&
		(first-hangulSBase)%hangulTCount == 0 &&
		hangulTBase < second && second < hangulTBase+hangulTCount {
		return first + second - hangulTBase, true
	}
	if combined, ok := nfcCompositions[uint64(first)<<32|uint64(second)]; ok {
		return combined, true
	}
	return 0, false
}

// combiningClassOf is unicodedata.combining(), which is zero for code points outside the table.
func combiningClassOf(cp rune) uint8 {
	low, high := 0, len(combiningClasses)-1
	for low <= high {
		middle := int(uint(low+high) >> 1)
		switch {
		case cp < combiningClasses[middle].lo:
			high = middle - 1
		case cp > combiningClasses[middle].hi:
			low = middle + 1
		default:
			return combiningClasses[middle].class
		}
	}
	return 0
}

// bidiClassOf is unicodedata.bidirectional reduced to the classes check_bidi tells apart.
func bidiClassOf(cp rune) bidiClass {
	low, high := 0, len(bidiTable)-1
	for low <= high {
		middle := int(uint(low+high) >> 1)
		switch {
		case cp < bidiTable[middle].lo:
			high = middle - 1
		case cp > bidiTable[middle].hi:
			low = middle + 1
		default:
			return bidiTable[middle].class
		}
	}
	return bidiUnknown
}

// joiningTypeOf is idna.core._joining_type.
func joiningTypeOf(cp rune) joiningType {
	low, high := 0, len(joiningTable)-1
	for low <= high {
		middle := int(uint(low+high) >> 1)
		switch {
		case cp < joiningTable[middle].lo:
			high = middle - 1
		case cp > joiningTable[middle].hi:
			low = middle + 1
		default:
			return joiningTable[middle].join
		}
	}
	return joiningNone
}

func inRanges(table []runeRange, cp rune) bool {
	low, high := 0, len(table)-1
	for low <= high {
		middle := int(uint(low+high) >> 1)
		switch {
		case cp < table[middle].lo:
			high = middle - 1
		case cp > table[middle].hi:
			low = middle + 1
		default:
			return true
		}
	}
	return false
}

// isASCII reports whether text is pure ASCII, which is what str.encode("ascii") accepts in alabel.
func isASCII(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] >= utf8.RuneSelf {
			return false
		}
	}
	return true
}

// asciiLower is bytes.lower() for ASCII text.
func asciiLower(text string) string {
	lowered := []byte(text)
	for i, b := range lowered {
		if b >= 'A' && b <= 'Z' {
			lowered[i] = b + 'a' - 'A'
		}
	}
	return string(lowered)
}

// punycode is RFC 3492 encoding as Python's 'punycode' codec writes it.
func punycode(text string) (string, error) {
	const (
		base        = 36
		tMin, tMax  = 1, 26
		skew, damp  = 38, 700
		initialBias = 72
		initialN    = 128
	)
	runes := []rune(text)
	var out []byte
	basic := 0
	for _, r := range runes {
		if r < 128 {
			out = append(out, byte(r))
			basic++
		}
	}
	handled := basic
	if basic > 0 {
		out = append(out, '-')
	}
	digit := func(d int) byte {
		if d < 26 {
			return byte('a' + d)
		}
		return byte('0' + d - 26)
	}
	adapt := func(delta, points int, first bool) int {
		if first {
			delta /= damp
		} else {
			delta /= 2
		}
		delta += delta / points
		k := 0
		for delta > ((base-tMin)*tMax)/2 {
			delta /= base - tMin
			k += base
		}
		return k + (base-tMin+1)*delta/(delta+skew)
	}
	n, delta, bias := initialN, 0, initialBias
	for handled < len(runes) {
		next := 0x110000
		for _, r := range runes {
			if int(r) >= n && int(r) < next {
				next = int(r)
			}
		}
		delta += (next - n) * (handled + 1)
		n = next
		for _, r := range runes {
			switch {
			case int(r) < n:
				delta++
			case int(r) == n:
				q := delta
				for k := base; ; k += base {
					t := k - bias
					if t < tMin {
						t = tMin
					} else if t > tMax {
						t = tMax
					}
					if q < t {
						break
					}
					out = append(out, digit(t+(q-t)%(base-t)))
					q = (q - t) / (base - t)
				}
				out = append(out, digit(q))
				bias = adapt(delta, handled+1, handled == basic)
				delta = 0
				handled++
			}
		}
		delta++
		n++
	}
	return string(out), nil
}

// punycodeDecode is Python's 'punycode' codec in strict mode, restricted to the ASCII input that an
// A-label body is; every decoding error becomes ErrIDNA at the caller.
func punycodeDecode(text string) (string, error) {
	var base []rune
	extended := text
	if pos := strings.LastIndexByte(text, '-'); pos >= 0 {
		base = []rune(text[:pos])
		extended = text[pos+1:]
	}
	return insertionSort(base, asciiUpper(extended))
}

// asciiUpper is bytes.upper() for ASCII text.
func asciiUpper(text string) string {
	upper := []byte(text)
	for i, b := range upper {
		if b >= 'a' && b <= 'z' {
			upper[i] = b - 'a' + 'A'
		}
	}
	return string(upper)
}

// errPunycode stands for the UnicodeDecodeError a malformed Punycode body raises in Python.
var errPunycode = errors.New("invalid punycode")

// insertionSort is the decoding half of RFC 3492, insertion_sort in encodings/punycode.py.
func insertionSort(base []rune, extended string) (string, error) {
	char := 0x80
	pos := -1
	bias := 72
	extpos := 0
	for extpos < len(extended) {
		newpos, delta, err := decodeGeneralizedNumber(extended, extpos, bias)
		if err != nil {
			return "", err
		}
		pos += delta + 1
		char += pos / (len(base) + 1)
		if char > 0x10FFFF {
			return "", errPunycode
		}
		// A lone surrogate has no IDNA class, so Python rejects the label after decoding it.
		if char >= 0xD800 && char <= 0xDFFF {
			return "", errPunycode
		}
		pos = pos % (len(base) + 1)
		shifted := make([]rune, 0, len(base)+1)
		shifted = append(shifted, base[:pos]...)
		shifted = append(shifted, rune(char))
		shifted = append(shifted, base[pos:]...)
		base = shifted
		bias = adaptDecode(delta, extpos == 0, len(base))
		extpos = newpos
	}
	return string(base), nil
}

// decodeGeneralizedNumber is decode_generalized_number in strict mode. Python uses unbounded
// integers; a delta this large makes the caller's character exceed U+10FFFF, so it is refused.
func decodeGeneralizedNumber(extended string, extpos, bias int) (int, int, error) {
	// deltaLimit is far above the largest delta that can still leave the caller's character at or
	// below U+10FFFF, so any digit product above it is one Python rejects. w is capped one past it:
	// multiplying it out exactly would only matter for a non-zero digit, which is refused anyway.
	const deltaLimit = 1 << 40
	result := 0
	w := 1
	j := 0
	for {
		if extpos >= len(extended) {
			return 0, 0, errPunycode
		}
		c := extended[extpos]
		extpos++
		var digit int
		switch {
		case c >= 'A' && c <= 'Z':
			digit = int(c) - 0x41
		case c >= '0' && c <= '9':
			digit = int(c) - 22
		default:
			return 0, 0, errPunycode
		}
		t := threshold(j, bias)
		if digit > 0 {
			if w > deltaLimit {
				return 0, 0, errPunycode
			}
			result += digit * w
			if result > deltaLimit {
				return 0, 0, errPunycode
			}
		}
		if digit < t {
			return extpos, result, nil
		}
		w *= 36 - t
		if w > deltaLimit {
			w = deltaLimit + 1
		}
		j++
	}
}

// threshold is T(j, bias) from RFC 3492.
func threshold(j, bias int) int {
	t := 36*(j+1) - bias
	if t < 1 {
		return 1
	}
	if t > 26 {
		return 26
	}
	return t
}

// adaptDecode is adapt(delta, first, numchars) in encodings/punycode.py.
func adaptDecode(delta int, first bool, numchars int) int {
	if first {
		delta /= 700
	} else {
		delta /= 2
	}
	delta += delta / numchars
	divisions := 0
	for delta > 455 {
		delta /= 35
		divisions += 36
	}
	return divisions + 36*delta/(delta+38)
}

// bidiClassCount is the number of bidiClass values; the rule tables are indexed by class.
const bidiClassCount = int(bidiOther) + 1

var (
	// _bidi_rtl_allowed.
	rtlAllowed = [bidiClassCount]bool{
		bidiR: true, bidiAL: true, bidiAN: true, bidiEN: true, bidiES: true,
		bidiCS: true, bidiET: true, bidiON: true, bidiBN: true, bidiNSM: true,
	}
	// _bidi_rtl_valid_ending.
	rtlValidEnding = [bidiClassCount]bool{bidiR: true, bidiAL: true, bidiEN: true, bidiAN: true}
	// _bidi_ltr_allowed.
	ltrAllowed = [bidiClassCount]bool{
		bidiL: true, bidiEN: true, bidiES: true, bidiCS: true,
		bidiET: true, bidiON: true, bidiBN: true, bidiNSM: true,
	}
	// _bidi_ltr_valid_ending.
	ltrValidEnding = [bidiClassCount]bool{bidiL: true, bidiEN: true}
)
