package pycodecs

// This file ports the error-handling surface of CPython 3.13's 'idna'
// decoder (Lib/encodings/idna.py).
//
// CPython's idna codec is strict-only: Codec.decode checks the errors
// argument before looking at the input and raises
// UnicodeError("Unsupported error handling: <name>") for anything but
// "strict". That includes the empty input, because this is the codec's
// decode method rather than the bytes.decode() wrapper (which short-circuits
// empty input before any codec runs).
//
// The strict decode (nameprep, ToUnicode, punycode round-trip) is used only
// by callers that decode IDNA names with errors='strict'; none of this
// port's callers do, and the corpus deliberately covers only the non-strict
// handlers. It is therefore not carried here: a strict decode returns a
// plain error saying so, instead of silently producing wrong text.

import "errors"

func init() {
	register("idna", decodeIDNA)
}

// decodeIDNA is the non-strict surface of Codec.decode.
func decodeIDNA(data []byte, sink *Sink) error {
	if sink.Handler() != Strict {
		return errors.New("Unsupported error handling: " + refusedHandlerName(sink.Handler()))
	}
	return errors.New("pycodecs: idna strict decode is not carried")
}
