package pycodecs

// This file ports CPython 3.13's strict 'idna' decoder, the bytes.decode
// path of Lib/encodings/idna.py (Codec.decode, ToUnicode and ToASCII), with
// nameprep in nameprep.go.
//
// CPython's idna codec is strict-only: Codec.decode checks the errors
// argument before looking at the input and raises
// UnicodeError("Unsupported error handling: <name>") for anything but
// "strict". That includes the empty input, because this is the codec's
// decode method rather than the bytes.decode() wrapper (which short-circuits
// empty input before any codec runs).
//
// The strict decode: when "xn--" does not occur (case-insensitively) the
// input must be ASCII and is returned unchanged; otherwise it splits at
// b'.', keeps a trailing dot, decodes each label with ToUnicode and reports
// the first failure as the UnicodeDecodeError of the whole input.

import (
	"bytes"
	"errors"
	"fmt"
)

func init() {
	register("idna", decodeIDNA)
}

// idnaError is the UnicodeEncodeError or UnicodeDecodeError a step inside
// the codec raises: the span, in code points for a str label and in bytes
// for a bytes label, and CPython's reason. Codec.decode turns the first one
// into a *DecodeError over the whole input.
type idnaError struct {
	start, end int
	reason     string
}

func (e *idnaError) Error() string {
	return fmt.Sprintf("%s (positions %d-%d)", e.reason, e.start, e.end-1)
}

// decodeIDNA is Codec.decode.
func decodeIDNA(data []byte, sink *Sink) error {
	if sink.Handler() != Strict {
		return errors.New("Unsupported error handling: " + refusedHandlerName(sink.Handler()))
	}
	if len(data) == 0 {
		return nil
	}
	if !bytes.Contains(asciiLower(data), []byte("xn--")) {
		// Fast path: a pure ASCII name is returned unchanged.
		if asciiData(data) {
			sink.Out = append(sink.Out, []rune(string(data))...)
			return nil
		}
	}
	labels := bytes.Split(data, []byte("."))
	trailing := false
	if len(labels) > 0 && len(labels[len(labels)-1]) == 0 {
		trailing = true
		labels = labels[:len(labels)-1]
	}
	tables := idnaTablesOf()
	result := make([]rune, 0, len(data))
	offset := 0 // sum(len(x) for x in labels[:i]) + len(labels[:i])
	for i, label := range labels {
		decoded, failure := toUnicode(tables, label)
		if failure != nil {
			return &DecodeError{
				Codec: "idna", Data: data,
				Start: offset + failure.start, End: offset + failure.end,
				Reason: failure.reason,
			}
		}
		if i > 0 {
			result = append(result, '.')
		}
		result = append(result, decoded...)
		offset += len(label) + 1
	}
	if trailing {
		result = append(result, '.')
	}
	sink.Out = append(sink.Out, result...)
	return nil
}

// toUnicode is encodings.idna.ToUnicode for a bytes label.
func toUnicode(tables *idnaTables, label []byte) ([]rune, *idnaError) {
	if len(label) > 1024 {
		// Protection from https://github.com/python/cpython/issues/98433.
		return nil, &idnaError{start: 0, end: len(label), reason: "label way too long"}
	}
	// Step 3: check for the ACE prefix (a bytes label is "pure ascii": there
	// is no nameprep step for it).
	if !bytes.HasPrefix(asciiLower(label), []byte("xn--")) {
		for i, data := range label {
			if data >= 0x80 {
				return nil, &idnaError{start: i, end: i + 1, reason: "ordinal not in range(128)"}
			}
		}
		return []rune(string(label)), nil
	}
	// Steps 4 and 5: remove the prefix and decode the rest with punycode.
	decoded, failure := punycodeDecodeStrict(label[len("xn--"):])
	if failure != nil {
		failure.start += len("xn--")
		failure.end += len("xn--")
		return nil, failure
	}
	// Step 6: apply ToASCII.
	encoded, failure := toASCII(tables, decoded)
	if failure != nil {
		return nil, failure
	}
	// Step 7: compare ToASCII's result with the label (CPython lowercases the
	// input label but not the re-encoded one).
	if !bytes.Equal(asciiLower(label), encoded) {
		return nil, &idnaError{
			start: 0, end: len(label),
			reason: fmt.Sprintf("IDNA does not round-trip, '%s' != '%s'",
				pythonBytesRepr(label), pythonBytesRepr(encoded)),
		}
	}
	// Step 8: the punycode result.
	return decoded, nil
}

// toASCII is encodings.idna.ToASCII for a str label.
func toASCII(tables *idnaTables, label []rune) ([]byte, *idnaError) {
	// Step 1: try ASCII.
	if ascii, ok := encodeASCII(label); ok {
		if len(ascii) > 0 && len(ascii) < 64 {
			return ascii, nil
		}
		if len(label) == 0 {
			return nil, &idnaError{start: 0, end: 1, reason: "label empty"}
		}
		return nil, &idnaError{start: 0, end: len(label), reason: "label too long"}
	}
	// Step 2: nameprep.
	prepped, failure := nameprep(label)
	if failure != nil {
		return nil, failure
	}
	// Step 4: try ASCII again.
	if ascii, ok := encodeASCII(prepped); ok {
		if len(prepped) > 0 && len(prepped) < 64 {
			return ascii, nil
		}
		if len(prepped) == 0 {
			return nil, &idnaError{start: 0, end: 1, reason: "label empty"}
		}
		return nil, &idnaError{start: 0, end: len(prepped), reason: "label too long"}
	}
	// Step 5: the label must not look like an ACE label.
	if hasASCIIPrefixFold(prepped, "xn--") {
		return nil, &idnaError{start: 0, end: 4, reason: "Label starts with ACE prefix"}
	}
	// Steps 6 and 7: punycode and the ACE prefix.
	encoded := append([]byte("xn--"), punycodeEncode(prepped)...)
	// Step 8: check the size.
	if len(encoded) < 64 {
		return encoded, nil
	}
	return nil, &idnaError{start: 0, end: len(prepped), reason: "label too long"}
}

// asciiLower is bytes.lower(): ASCII letters only.
func asciiLower(data []byte) []byte {
	out := make([]byte, len(data))
	for i, b := range data {
		if b >= 'A' && b <= 'Z' {
			b += 'a' - 'A'
		}
		out[i] = b
	}
	return out
}

// asciiData reports whether every byte is below 0x80.
func asciiData(data []byte) bool {
	for _, b := range data {
		if b >= 0x80 {
			return false
		}
	}
	return true
}

// encodeASCII is str.encode("ascii", "strict").
func encodeASCII(label []rune) ([]byte, bool) {
	out := make([]byte, len(label))
	for i, r := range label {
		if r >= 0x80 {
			return nil, false
		}
		out[i] = byte(r)
	}
	return out, true
}

// hasASCIIPrefixFold is s.lower().startswith(prefix) for an ASCII prefix.
func hasASCIIPrefixFold(text []rune, prefix string) bool {
	if len(text) < len(prefix) {
		return false
	}
	for i := 0; i < len(prefix); i++ {
		r := text[i]
		if r >= 'A' && r <= 'Z' {
			r += 'a' - 'A'
		}
		if r != rune(prefix[i]) {
			return false
		}
	}
	return true
}
