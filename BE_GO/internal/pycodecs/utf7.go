package pycodecs

// This file ports CPython 3.13's UTF-7 decoder
// (PyUnicode_DecodeUTF7Stateful in Objects/unicodeobject.c).
//
// The decoder is deliberately liberal: every ASCII byte except '+' decodes
// as itself; '+' starts a modified-base64 shift sequence terminated by '-'
// (which is absorbed) or by any non-base64 byte (which is decoded normally
// on the next iteration, so it may itself be an error).
//
// Shift sequences produce UTF-16 code units. A high surrogate is held back
// until the next unit: a low surrogate completes the pair, otherwise the
// lone high surrogate is written before the next unit. At the end of a
// sequence a pending surrogate is written only if the terminating byte
// decodes directly; at the end of the input a pending surrogate (or six or
// more leftover bits, or non-zero padding bits) is "unterminated shift
// sequence". The codec can therefore emit lone surrogates.
//
// Error spans (the exception names the codec "utf7"):
//   - "partial character in shift sequence": from '+' through the byte that
//     ended the shift, when six or more bits were left over;
//   - "non-zero padding bits in shift sequence": the same span when fewer
//     than six bits with a non-zero value were left over;
//   - "ill-formed sequence": '+' and the non-base64 byte after it;
//   - "unexpected special character": one byte at or above 0x80 outside a
//     shift;
//   - "unterminated shift sequence": from '+' to the end of the input.

func init() {
	register("utf-7", decodeUTF7)
}

// utf7IsBase64 is IS_BASE64.
func utf7IsBase64(c byte) bool {
	return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
		(c >= '0' && c <= '9') || c == '+' || c == '/'
}

// utf7FromBase64 is FROM_BASE64; c must be a base64 byte.
func utf7FromBase64(c byte) uint {
	switch {
	case c >= 'A' && c <= 'Z':
		return uint(c - 'A')
	case c >= 'a' && c <= 'z':
		return uint(c-'a') + 26
	case c >= '0' && c <= '9':
		return uint(c-'0') + 52
	case c == '+':
		return 62
	default: // '/'
		return 63
	}
}

// utf7DecodeDirect is DECODE_DIRECT: the byte decodes as itself.
func utf7DecodeDirect(c byte) bool { return c <= 127 && c != '+' }

// decodeUTF7 is PyUnicode_DecodeUTF7.
func decodeUTF7(data []byte, sink *Sink) error {
	// CPython reports UTF-7 errors under the name "utf7".
	sink.SetCodec("utf7")

	s := 0
	inShift := false
	shiftStart := 0 // position of the '+' that opened the current shift
	base64bits := uint(0)
	var base64buffer uint64
	var surrogate rune

	for s < len(data) {
		ch := data[s]

		if inShift {
			if utf7IsBase64(ch) {
				base64buffer = (base64buffer << 6) | uint64(utf7FromBase64(ch))
				base64bits += 6
				s++
				if base64bits >= 16 {
					out := rune((base64buffer >> (base64bits - 16)) & 0xFFFF)
					base64bits -= 16
					base64buffer &= (uint64(1) << base64bits) - 1
					if surrogate != 0 {
						if utf16IsLowSurrogate(out) {
							sink.Out = append(sink.Out, 0x10000+((surrogate-0xD800)<<10)+(out-0xDC00))
							surrogate = 0
							continue
						}
						// The pending high surrogate had no partner.
						sink.Out = append(sink.Out, surrogate)
						surrogate = 0
					}
					if utf16IsHighSurrogate(out) {
						surrogate = out
					} else {
						sink.Out = append(sink.Out, out)
					}
				}
				continue
			}

			// A non-base64 byte ends the shift. The byte itself is handled
			// on the next iteration (or absorbed if it is '-').
			inShift = false
			if base64bits > 0 {
				if base64bits >= 6 {
					// At least one base64 character has no complete output.
					s++
					if _, err := sink.Fail(shiftStart, s, "partial character in shift sequence"); err != nil {
						return err
					}
					continue
				}
				if base64buffer != 0 {
					s++
					if _, err := sink.Fail(shiftStart, s, "non-zero padding bits in shift sequence"); err != nil {
						return err
					}
					continue
				}
			}
			if surrogate != 0 && utf7DecodeDirect(ch) {
				sink.Out = append(sink.Out, surrogate)
			}
			surrogate = 0
			if ch == '-' {
				s++
			}
			continue
		}

		switch {
		case ch == '+':
			shiftStart = s
			s++
			if s < len(data) && data[s] == '-' {
				// "+-" is the escape for '+'.
				s++
				sink.Out = append(sink.Out, '+')
			} else if s < len(data) && !utf7IsBase64(data[s]) {
				s++
				if _, err := sink.Fail(shiftStart, s, "ill-formed sequence"); err != nil {
					return err
				}
			} else {
				inShift = true
				surrogate = 0
				base64bits = 0
				base64buffer = 0
			}
		case utf7DecodeDirect(ch):
			s++
			sink.Out = append(sink.Out, rune(ch))
		default:
			start := s
			s++
			if _, err := sink.Fail(start, s, "unexpected special character"); err != nil {
				return err
			}
		}
	}

	// The input ended inside a shift sequence. An incomplete group, leftover
	// bits or a pending surrogate makes the sequence invalid; the decoded
	// output before the sequence is kept.
	if inShift {
		inShift = false
		if surrogate != 0 || base64bits >= 6 || (base64bits > 0 && base64buffer != 0) {
			if _, err := sink.Fail(shiftStart, len(data), "unterminated shift sequence"); err != nil {
				return err
			}
		}
	}
	return nil
}
