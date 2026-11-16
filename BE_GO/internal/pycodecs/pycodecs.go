// Package pycodecs ports CPython 3.13's text codecs for decoding
// (bytes.decode(codec, errors)). Every decoder reports an undecodable span as
// CPython does (UnicodeDecodeError start, end and reason), and the error
// handler then decides what is written and where decoding resumes
// (Sink.Fail): 'strict' raises, 'replace' writes one U+FFFD per span,
// 'surrogateescape' writes U+DC80+byte for up to four leading high bytes and
// 'ignore' writes nothing. The decoder keeps its state across a handled
// error, as CPython's decoders do.
//
// Output is a []rune because several codecs (utf-7, unicode_escape,
// raw_unicode_escape, surrogateescape) produce lone surrogates.
package pycodecs

import (
	"fmt"
	"sort"
)

// Handler is the errors argument of bytes.decode.
type Handler int

const (
	Strict Handler = iota
	Replace
	SurrogateEscape
	Ignore
	// SurrogatePass decodes one surrogate code point encoded in the codec's
	// own form at the span start (utf-8, utf-16 and utf-32 only); anything
	// else raises. json.loads(bytes) decodes with it.
	SurrogatePass
)

// DecodeError is the UnicodeDecodeError a decoder raises (strict, or a
// surrogateescape span holding a byte below 0x80). Data is the whole input.
type DecodeError struct {
	Codec      string
	Data       []byte
	Start, End int
	Reason     string
}

// Error is str(UnicodeDecodeError).
func (e *DecodeError) Error() string {
	if e.End == e.Start+1 && e.Start < len(e.Data) {
		return fmt.Sprintf("'%s' codec can't decode byte 0x%02x in position %d: %s", e.Codec, e.Data[e.Start], e.Start, e.Reason)
	}
	return fmt.Sprintf("'%s' codec can't decode bytes in position %d-%d: %s", e.Codec, e.Start, e.End-1, e.Reason)
}

// Sink collects the output of one decode and applies the error handler.
type Sink struct {
	codec   string
	handler Handler
	data    []byte
	Out     []rune
}

// Handler is the error handler of this decode (idna refuses every handler
// but strict before it reads the input).
func (s *Sink) Handler() Handler { return s.handler }

// SetCodec changes the codec name an error reports (CPython's utf-16 and
// utf-32 decoders name the byte order they detected, "utf-16-le" or
// "utf-16-be").
func (s *Sink) SetCodec(name string) { s.codec = name }

// Fail applies the handler to data[start:end] and returns the position the
// decoder resumes at (its state kept). A non-nil error ends the decode (the
// decoder returns it unchanged). strict raises; replace writes one U+FFFD
// and ignore nothing, both resuming at end. surrogateescape
// (PyCodec_SurrogateEscapeErrors) escapes at most four leading bytes of the
// span that are 0x80 or above, as U+DC80+byte, and resumes right after them,
// which can be inside the span (the decoder then reports the next error from
// there); when the first byte is below 0x80 it raises the exception.
func (s *Sink) Fail(start, end int, reason string) (int, error) {
	switch s.handler {
	case Replace:
		s.Out = append(s.Out, 0xFFFD)
		return end, nil
	case Ignore:
		return end, nil
	case SurrogateEscape:
		consumed := 0
		for consumed < 4 && start+consumed < end && s.data[start+consumed] >= 0x80 {
			s.Out = append(s.Out, 0xDC00+rune(s.data[start+consumed]))
			consumed++
		}
		if consumed > 0 {
			return start + consumed, nil
		}
	case SurrogatePass:
		if r, size := surrogatePass(s.codec, s.data[start:]); size > 0 {
			s.Out = append(s.Out, r)
			return start + size, nil
		}
	}
	return 0, &DecodeError{Codec: s.codec, Data: s.data, Start: start, End: end, Reason: reason}
}

// surrogatePass is PyCodec_SurrogatePassErrors for a decode: the surrogate
// encoded at the start of rest in the form of the codec the exception names,
// with its byte length, or size 0 when it is not one (the exception is
// raised). "utf-16" and "utf-32" are the native (little-endian) forms.
func surrogatePass(codec string, rest []byte) (rune, int) {
	var r rune
	size := 0
	switch codec {
	case "utf-8", "utf8":
		if len(rest) >= 3 && rest[0]&0xF0 == 0xE0 && rest[1]&0xC0 == 0x80 && rest[2]&0xC0 == 0x80 {
			r, size = rune(rest[0]&0x0F)<<12|rune(rest[1]&0x3F)<<6|rune(rest[2]&0x3F), 3
		}
	case "utf-16", "utf-16-le":
		if len(rest) >= 2 {
			r, size = rune(rest[1])<<8|rune(rest[0]), 2
		}
	case "utf-16-be":
		if len(rest) >= 2 {
			r, size = rune(rest[0])<<8|rune(rest[1]), 2
		}
	case "utf-32", "utf-32-le":
		if len(rest) >= 4 {
			r, size = rune(rest[3])<<24|rune(rest[2])<<16|rune(rest[1])<<8|rune(rest[0]), 4
		}
	case "utf-32-be":
		if len(rest) >= 4 {
			r, size = rune(rest[0])<<24|rune(rest[1])<<16|rune(rest[2])<<8|rune(rest[3]), 4
		}
	}
	if size == 0 || r < 0xD800 || r > 0xDFFF {
		return 0, 0
	}
	return r, size
}

// Decoder decodes all of data (a final decode) into sink, calling sink.Fail
// for every undecodable span, resuming at the position it returns, and
// returning its error when it is not nil.
// A decoder may also return a non-DecodeError error for an exception CPython
// raises regardless of the handler (UnicodeError, for example).
type Decoder func(data []byte, sink *Sink) error

var decoders = map[string]Decoder{}

// register adds the decoder of a codec, by codecs.lookup(name).name.
func register(name string, decoder Decoder) {
	if _, taken := decoders[name]; taken {
		panic("pycodecs: codec registered twice: " + name)
	}
	decoders[name] = decoder
}

// Has reports whether the codec (by its codecs.lookup name) is ported.
func Has(name string) bool { return decoders[name] != nil }

// Names lists the ported codecs, sorted.
func Names() []string {
	names := make([]string, 0, len(decoders))
	for name := range decoders {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// Decode is bytes.decode(name, errors) for a codec named by its
// codecs.lookup(...).name. ok is false for a codec this package does not carry.
func Decode(name string, data []byte, handler Handler) (out []rune, err error, ok bool) {
	decoder := decoders[name]
	if decoder == nil {
		return nil, nil, false
	}
	sink := &Sink{codec: name, handler: handler, data: data, Out: make([]rune, 0, len(data))}
	if err := decoder(data, sink); err != nil {
		return nil, err, true
	}
	return sink.Out, nil, true
}
