package pycodecs

// This file ports CPython 3.13's UTF-16 decoders
// (PyUnicode_DecodeUTF16Stateful in Objects/unicodeobject.c and
// STRINGLIB(utf16_decode) in Objects/stringlib/codecs.h).
//
// The plain "utf-16" decoder consumes one leading byte order mark (FF FE or
// FE FF) and otherwise assumes the host's native order; the "-le"/"-be"
// codecs never consume a BOM (it decodes as U+FEFF). CPython names the
// detected byte order in the exception, so SetCodec changes the reported
// encoding to "utf-16-le" or "utf-16-be". This port follows CPython on a
// little-endian host (the platforms production runs on).
//
// Error spans:
//   - a lone low surrogate: "illegal encoding", the two bytes of that unit;
//   - a high surrogate followed by a non-low unit: "illegal UTF-16 surrogate",
//     only the high surrogate's two bytes (the second unit is decoded again);
//   - a high surrogate at the end: "unexpected end of data" from the high
//     surrogate to the end;
//   - an odd trailing byte: "truncated data" from that byte to the end.

func init() {
	register("utf-16", func(data []byte, sink *Sink) error {
		return decodeUTF16(data, sink, 0)
	})
	register("utf-16-le", func(data []byte, sink *Sink) error {
		return decodeUTF16(data, sink, -1)
	})
	register("utf-16-be", func(data []byte, sink *Sink) error {
		return decodeUTF16(data, sink, 1)
	})
}

// utf16IsSurrogate reports Py_UNICODE_IS_SURROGATE.
func utf16IsSurrogate(c rune) bool { return c >= 0xD800 && c <= 0xDFFF }

// utf16IsHighSurrogate reports Py_UNICODE_IS_HIGH_SURROGATE.
func utf16IsHighSurrogate(c rune) bool { return c >= 0xD800 && c <= 0xDBFF }

// utf16IsLowSurrogate reports Py_UNICODE_IS_LOW_SURROGATE.
func utf16IsLowSurrogate(c rune) bool { return c >= 0xDC00 && c <= 0xDFFF }

// readUTF16 reads one code unit in the requested byte order.
func readUTF16(data []byte, i int, little bool) rune {
	if little {
		return rune(data[i]) | rune(data[i+1])<<8
	}
	return rune(data[i])<<8 | rune(data[i+1])
}

// utfDecodeFail applies the error handler to data[start:end] and returns the
// position at which decoding resumes (Sink.Fail): with surrogateescape it can
// be inside the span, so the decoder recomputes the next error from there,
// which is why e.g. a three-byte "truncated data" span can report its last
// byte only.
func utfDecodeFail(_ []byte, sink *Sink, start, end int, reason string) (int, error) {
	return sink.Fail(start, end, reason)
}

// decodeUTF16 is PyUnicode_DecodeUTF16. byteOrder is 0 for the plain
// "utf-16" codec (detect a BOM, otherwise native order), -1 for little
// endian and +1 for big endian.
func decodeUTF16(data []byte, sink *Sink, byteOrder int) error {
	q := 0
	bo := byteOrder

	// A BOM is consumed only when no byte order was fixed by the codec name.
	if bo == 0 && len(data) >= 2 {
		bom := rune(data[1])<<8 | rune(data[0])
		switch bom {
		case 0xFEFF: // FF FE
			q = 2
			bo = -1
		case 0xFFFE: // FE FF
			q = 2
			bo = 1
		}
	}
	if q == len(data) {
		return nil
	}

	// On a little-endian host native ordering means little endian and
	// byte order 0 (no BOM) also means little endian.
	little := bo <= 0
	if little {
		sink.SetCodec("utf-16-le")
	} else {
		sink.SetCodec("utf-16-be")
	}

	for {
		if len(data)-q < 2 {
			if q == len(data) {
				return nil
			}
			// One trailing byte: "truncated data" up to the end.
			end := len(data)
			resume, err := utfDecodeFail(data, sink, q, end, "truncated data")
			if err != nil {
				return err
			}
			q = resume
			continue
		}

		ch := readUTF16(data, q, little)
		q += 2
		if !utf16IsSurrogate(ch) {
			sink.Out = append(sink.Out, ch)
			continue
		}

		if !utf16IsHighSurrogate(ch) {
			// A low surrogate with no preceding high surrogate.
			resume, err := utfDecodeFail(data, sink, q-2, q, "illegal encoding")
			if err != nil {
				return err
			}
			q = resume
			continue
		}

		if len(data)-q < 2 {
			// The high surrogate is the last complete unit (a possible odd
			// trailing byte is part of the error span).
			end := len(data)
			resume, err := utfDecodeFail(data, sink, q-2, end, "unexpected end of data")
			if err != nil {
				return err
			}
			q = resume
			continue
		}

		ch2 := readUTF16(data, q, little)
		q += 2
		if !utf16IsLowSurrogate(ch2) {
			// The second unit is not a low surrogate: only the two bytes of
			// the high surrogate are the error, and the second unit is
			// decoded again on the next iteration.
			end := q - 2
			resume, err := utfDecodeFail(data, sink, q-4, end, "illegal UTF-16 surrogate")
			if err != nil {
				return err
			}
			q = resume
			continue
		}
		sink.Out = append(sink.Out, 0x10000+((ch-0xD800)<<10)+(ch2-0xDC00))
	}
}
