package pycodecs

// This file ports CPython 3.13's UTF-8 decoders: the DFA in
// Objects/stringlib/codecs.h (STRINGLIB(utf8_decode)) plus the error spans
// chosen by unicode_decode_utf8 in Objects/unicodeobject.c.
//
// CPython rejects surrogates (ED A0 80 - ED BF BF), overlong forms and
// code points above U+10FFFF, and on an error consumes the "maximal subpart"
// of the sequence: the lead byte plus every continuation byte that was
// legal for it. The byte that broke the sequence is decoded again by the
// next iteration, so 'replace' can write several U+FFFD for one bad
// sequence (b'\xe0\x80\x80' becomes three replacement characters).
//
// 'utf-8-sig' drops one leading UTF-8 BOM (EF BB BF) and then runs the plain
// UTF-8 decoder; CPython reports its errors under the name "utf-8" and
// relative to the bytes after the BOM.

func init() {
	register("utf-8", decodeUTF8)
	register("utf-8-sig", decodeUTF8Sig)
}

// utf8IsContinuation is IS_CONTINUATION_BYTE: 10xxxxxx.
func utf8IsContinuation(b byte) bool { return b >= 0x80 && b < 0xC0 }

// decodeUTF8 is PyUnicode_DecodeUTF8. It appends the code points to
// sink.Out and reports every undecodable span through sink.Fail.
func decodeUTF8(data []byte, sink *Sink) error {
	s := 0
	for s < len(data) {
		b0 := data[s]

		// ASCII fast path (a run of ASCII bytes is copied verbatim).
		if b0 < 0x80 {
			sink.Out = append(sink.Out, rune(b0))
			s++
			continue
		}

		// fail reports data[s:end] and resumes at end. Every UTF-8 error
		// span starts at the lead byte of the sequence.
		fail := func(end int, reason string) error {
			resume, err := sink.Fail(s, end, reason)
			s = resume
			return err
		}

		if b0 < 0xC2 {
			// 80-BF: continuation byte without a lead byte;
			// C0-C1: overlong two-byte forms of 0000-007F.
			if err := fail(s+1, "invalid start byte"); err != nil {
				return err
			}
			continue
		}

		if b0 < 0xE0 {
			// C2-DF 80-BF: U+0080-U+07FF.
			if len(data)-s < 2 {
				// The caller decides what an incomplete sequence means;
				// for a final decode it is an error.
				if err := fail(len(data), "unexpected end of data"); err != nil {
					return err
				}
				continue
			}
			b1 := data[s+1]
			if !utf8IsContinuation(b1) {
				if err := fail(s+1, "invalid continuation byte"); err != nil {
					return err
				}
				continue
			}
			sink.Out = append(sink.Out, rune(b0&0x1F)<<6|rune(b1&0x3F))
			s += 2
			continue
		}

		if b0 < 0xF0 {
			// E0-EF 80-BF 80-BF: U+0800-U+FFFF. E0 below A0 is overlong;
			// ED at or above A0 would decode to a surrogate.
			if len(data)-s < 3 {
				if len(data)-s < 2 {
					if err := fail(len(data), "unexpected end of data"); err != nil {
						return err
					}
					continue
				}
				b1 := data[s+1]
				// CPython tests (ch2 < 0xA0 ? ch == 0xE0 : ch == 0xED).
				if !utf8IsContinuation(b1) ||
					(b1 < 0xA0 && b0 == 0xE0) || (b1 >= 0xA0 && b0 == 0xED) {
					if err := fail(s+1, "invalid continuation byte"); err != nil {
						return err
					}
					continue
				}
				if err := fail(len(data), "unexpected end of data"); err != nil {
					return err
				}
				continue
			}
			b1, b2 := data[s+1], data[s+2]
			if !utf8IsContinuation(b1) {
				if err := fail(s+1, "invalid continuation byte"); err != nil {
					return err
				}
				continue
			}
			if b0 == 0xE0 && b1 < 0xA0 {
				// E0 80 80 - E0 9F BF: overlong form of U+0000-U+07FF.
				if err := fail(s+1, "invalid continuation byte"); err != nil {
					return err
				}
				continue
			}
			if b0 == 0xED && b1 >= 0xA0 {
				// ED A0 80 - ED BF BF: surrogates D800-DFFF are not UTF-8.
				if err := fail(s+1, "invalid continuation byte"); err != nil {
					return err
				}
				continue
			}
			if !utf8IsContinuation(b2) {
				if err := fail(s+2, "invalid continuation byte"); err != nil {
					return err
				}
				continue
			}
			sink.Out = append(sink.Out, rune(b0&0x0F)<<12|rune(b1&0x3F)<<6|rune(b2&0x3F))
			s += 3
			continue
		}

		if b0 < 0xF5 {
			// F0-F4 80-BF 80-BF 80-BF: U+10000-U+10FFFF. F0 below 90 is
			// overlong; F4 at or above 90 overflows U+10FFFF.
			if len(data)-s < 4 {
				if len(data)-s < 2 {
					if err := fail(len(data), "unexpected end of data"); err != nil {
						return err
					}
					continue
				}
				b1 := data[s+1]
				// CPython tests (ch2 < 0x90 ? ch == 0xF0 : ch == 0xF4).
				if !utf8IsContinuation(b1) ||
					(b1 < 0x90 && b0 == 0xF0) || (b1 >= 0x90 && b0 == 0xF4) {
					if err := fail(s+1, "invalid continuation byte"); err != nil {
						return err
					}
					continue
				}
				if len(data)-s < 3 {
					if err := fail(len(data), "unexpected end of data"); err != nil {
						return err
					}
					continue
				}
				if !utf8IsContinuation(data[s+2]) {
					if err := fail(s+2, "invalid continuation byte"); err != nil {
						return err
					}
					continue
				}
				if err := fail(len(data), "unexpected end of data"); err != nil {
					return err
				}
				continue
			}
			b1, b2, b3 := data[s+1], data[s+2], data[s+3]
			if !utf8IsContinuation(b1) {
				if err := fail(s+1, "invalid continuation byte"); err != nil {
					return err
				}
				continue
			}
			if b0 == 0xF0 && b1 < 0x90 {
				// F0 80 80 80 - F0 8F BF BF: overlong form of U+0000-U+FFFF.
				if err := fail(s+1, "invalid continuation byte"); err != nil {
					return err
				}
				continue
			}
			if b0 == 0xF4 && b1 >= 0x90 {
				// F4 90 80 80 -: above U+10FFFF.
				if err := fail(s+1, "invalid continuation byte"); err != nil {
					return err
				}
				continue
			}
			if !utf8IsContinuation(b2) {
				if err := fail(s+2, "invalid continuation byte"); err != nil {
					return err
				}
				continue
			}
			if !utf8IsContinuation(b3) {
				if err := fail(s+3, "invalid continuation byte"); err != nil {
					return err
				}
				continue
			}
			sink.Out = append(sink.Out, rune(b0&0x07)<<18|rune(b1&0x3F)<<12|rune(b2&0x3F)<<6|rune(b3&0x3F))
			s += 4
			continue
		}

		// F5-FF can never start a valid sequence.
		if err := fail(s+1, "invalid start byte"); err != nil {
			return err
		}
	}
	return nil
}

// decodeUTF8Sig is the 'utf-8-sig' decoder: drop one leading BOM and decode
// the rest as plain UTF-8. CPython's utf_8_sig.py slices the input before
// calling codecs.utf_8_decode, so an exception names "utf-8" and positions
// are relative to the sliced bytes. A temporary Sink over the slice gives
// exactly that behaviour.
func decodeUTF8Sig(data []byte, sink *Sink) error {
	if len(data) >= 3 && data[0] == 0xEF && data[1] == 0xBB && data[2] == 0xBF {
		data = data[3:]
	}
	sub := &Sink{codec: "utf-8", handler: sink.Handler(), data: data, Out: sink.Out}
	if err := decodeUTF8(data, sub); err != nil {
		return err
	}
	sink.Out = sub.Out
	return nil
}
