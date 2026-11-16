package pycodecs

// This file ports CPython 3.13's UTF-32 decoders
// (PyUnicode_DecodeUTF32Stateful in Objects/unicodeobject.c).
//
// The plain "utf-32" decoder consumes one leading BOM (FF FE 00 00 or
// 00 00 FE FF) and otherwise assumes the host's native order; the "-le"/"-be"
// codecs never consume a BOM (it decodes as U+FEFF). CPython names the
// detected byte order in the exception, so SetCodec selects "utf-32-le" or
// "utf-32-be". This port follows CPython on a little-endian host.
//
// Error spans:
//   - a surrogate: "code point in surrogate code point range(0xd800, 0xe000)",
//     the whole four-byte unit;
//   - a value above U+10FFFF: "code point not in range(0x110000)", the whole
//     four-byte unit;
//   - one to three trailing bytes: "truncated data" from there to the end.
//
// surrogateescape can resume inside such a span (utfDecodeFail explains it).

func init() {
	register("utf-32", func(data []byte, sink *Sink) error {
		return decodeUTF32(data, sink, 0)
	})
	register("utf-32-le", func(data []byte, sink *Sink) error {
		return decodeUTF32(data, sink, -1)
	})
	register("utf-32-be", func(data []byte, sink *Sink) error {
		return decodeUTF32(data, sink, 1)
	})
}

// readUTF32 reads one code point in the requested byte order. It is uint32
// because invalid units can be as large as 0xFFFFFFFF.
func readUTF32(data []byte, i int, little bool) uint32 {
	if little {
		return uint32(data[i]) | uint32(data[i+1])<<8 | uint32(data[i+2])<<16 | uint32(data[i+3])<<24
	}
	return uint32(data[i])<<24 | uint32(data[i+1])<<16 | uint32(data[i+2])<<8 | uint32(data[i+3])
}

// decodeUTF32 is PyUnicode_DecodeUTF32. byteOrder is 0 for the plain
// "utf-32" codec (detect a BOM, otherwise native order), -1 for little
// endian and +1 for big endian.
func decodeUTF32(data []byte, sink *Sink, byteOrder int) error {
	q := 0
	bo := byteOrder

	// A BOM is consumed only when no byte order was fixed by the codec name.
	if bo == 0 && len(data) >= 4 {
		bom := readUTF32(data, 0, true)
		switch bom {
		case 0x0000FEFF: // FF FE 00 00
			q = 4
			bo = -1
		case 0xFFFE0000: // 00 00 FE FF
			q = 4
			bo = 1
		}
	}
	if q == len(data) {
		return nil
	}

	little := bo <= 0
	if little {
		sink.SetCodec("utf-32-le")
	} else {
		sink.SetCodec("utf-32-be")
	}

	for {
		if len(data)-q < 4 {
			if q == len(data) {
				return nil
			}
			// One to three trailing bytes: "truncated data" to the end.
			end := len(data)
			resume, err := utfDecodeFail(data, sink, q, end, "truncated data")
			if err != nil {
				return err
			}
			q = resume
			continue
		}

		ch := readUTF32(data, q, little)
		if ch >= 0xD800 && ch <= 0xDFFF {
			resume, err := utfDecodeFail(data, sink, q, q+4, "code point in surrogate code point range(0xd800, 0xe000)")
			if err != nil {
				return err
			}
			q = resume
			continue
		}
		if ch > 0x10FFFF {
			resume, err := utfDecodeFail(data, sink, q, q+4, "code point not in range(0x110000)")
			if err != nil {
				return err
			}
			q = resume
			continue
		}
		sink.Out = append(sink.Out, rune(ch))
		q += 4
	}
}
