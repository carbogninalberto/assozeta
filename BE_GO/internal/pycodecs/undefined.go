package pycodecs

// This file ports CPython 3.13's 'undefined' codec
// (Lib/encodings/undefined.py): every decode, including one of an empty
// input, raises UnicodeError("undefined encoding"). The error is not a
// UnicodeDecodeError, so the codec ignores the errors handler entirely.

import "errors"

func init() {
	register("undefined", decodeUndefined)
}

// decodeUndefined is Codec.decode.
func decodeUndefined(data []byte, sink *Sink) error {
	return errors.New("undefined encoding")
}
