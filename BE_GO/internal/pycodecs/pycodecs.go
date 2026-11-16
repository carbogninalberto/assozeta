// Package pycodecs ports CPython 3.13's text codecs for decoding
// (bytes.decode(codec, errors)). Every decoder reports an undecodable span as
// CPython does (UnicodeDecodeError start, end and reason), and the error
// handler then decides what is written: 'strict' raises, 'replace' writes one
// U+FFFD per span, 'surrogateescape' writes U+DC80+byte for each byte of the
// span (a byte below 0x80 raises instead) and 'ignore' writes nothing.
// Decoding always resumes at the end of the span, with the decoder's state
// kept, as CPython's handlers do.
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

// Fail applies the handler to data[start:end]. A non-nil error ends the
// decode (the decoder returns it unchanged).
func (s *Sink) Fail(start, end int, reason string) error {
	switch s.handler {
	case Replace:
		s.Out = append(s.Out, 0xFFFD)
		return nil
	case Ignore:
		return nil
	case SurrogateEscape:
		for i := start; i < end; i++ {
			if s.data[i] < 0x80 {
				return &DecodeError{Codec: s.codec, Data: s.data, Start: start, End: end, Reason: reason}
			}
		}
		for i := start; i < end; i++ {
			s.Out = append(s.Out, 0xDC00+rune(s.data[i]))
		}
		return nil
	}
	return &DecodeError{Codec: s.codec, Data: s.data, Start: start, End: end, Reason: reason}
}

// Decoder decodes all of data (a final decode) into sink, calling sink.Fail
// for every undecodable span and returning its error when it is not nil.
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
