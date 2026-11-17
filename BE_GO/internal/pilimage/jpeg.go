package pilimage

import "encoding/binary"

// acceptJPEG is JpegImagePlugin._accept.
func acceptJPEG(prefix []byte) bool {
	return len(prefix) >= 3 && prefix[0] == 0xFF && prefix[1] == 0xD8 && prefix[2] == 0xFF
}

// jpegMarker reports whether JPEGMARKER contains the marker and whether its
// handler is Skip (the others are implemented for their side effects/errors).
func jpegMarkerInfo(i int) (known bool, skip bool, sof bool, app bool, com bool, dqt bool) {
	switch i {
	case 0xFFC0, 0xFFC1, 0xFFC2, 0xFFC3, 0xFFC5, 0xFFC6, 0xFFC7,
		0xFFC9, 0xFFCA, 0xFFCB, 0xFFCD, 0xFFCE, 0xFFCF, 0xFFDE:
		return true, false, true, false, false, false
	case 0xFFC4, 0xFFCC, 0xFFDA, 0xFFDC, 0xFFDD, 0xFFDF:
		return true, true, false, false, false, false
	case 0xFFDB:
		return true, false, false, false, false, true
	case 0xFFFE:
		return true, false, false, false, true, false
	case 0xFFC8, 0xFFD0, 0xFFD1, 0xFFD2, 0xFFD3, 0xFFD4, 0xFFD5,
		0xFFD6, 0xFFD7, 0xFFD8, 0xFFD9, 0xFFF0, 0xFFF1, 0xFFF2,
		0xFFF3, 0xFFF4, 0xFFF5, 0xFFF6, 0xFFF7, 0xFFF8, 0xFFF9,
		0xFFFA, 0xFFFB, 0xFFFC, 0xFFFD:
		return true, false, false, false, false, false
	}
	if i >= 0xFFE0 && i <= 0xFFEF {
		return true, false, false, true, false, false
	}
	return false, false, false, false, false, false
}

// openJPEG is JpegImageFile._open: the marker scan up to the first SOS.
func openJPEG(data []byte) (*Image, error) {
	if len(data) < 3 || !acceptJPEG(data[:3]) {
		return nil, syntaxError("not a JPEG file")
	}
	im := newImage(data, "JPEG", "", 0, 0)
	pos := 3
	s := []byte{0xFF}
	readByte := func() (byte, bool) {
		if pos >= len(data) {
			return 0, false
		}
		b := data[pos]
		pos++
		return b, true
	}
	read2 := func() (int, bool) {
		b, ok := readByte()
		if !ok {
			return 0, false
		}
		b2, ok := readByte()
		if !ok {
			return 0, false
		}
		return int(b)<<8 | int(b2), true
	}
	safeRead := func(n int) ([]byte, error) {
		if n < 0 {
			n = 0
		}
		if pos+n > len(data) {
			return nil, osError("Truncated File Read")
		}
		out := data[pos : pos+n]
		pos += n
		return out, nil
	}
	for len(s) > 0 {
		i := int(s[0])
		if i == 0xFF {
			b, _ := readByte()
			s = append(s, b)
			if len(s) < 2 {
				break
			}
			i = int(s[0])<<8 | int(s[1])
		} else {
			b, ok := readByte()
			if !ok {
				s = nil
			} else {
				s = []byte{b}
			}
			continue
		}
		if i == 0 || i == 0xFFFF {
			s = []byte{0xFF}
			continue
		}
		if i == 0xFF00 {
			// Skip extraneous data (escaped 0xFF): s = fp.read(1).
			b, ok := readByte()
			if !ok {
				s = nil
			} else {
				s = []byte{b}
			}
			continue
		}
		known, skip, sof, app, com, dqt := jpegMarkerInfo(i)
		if !known {
			return nil, syntaxError("no marker found")
		}
		if skip {
			n, ok := read2()
			if !ok {
				return nil, shortError()
			}
			if _, err := safeRead(n - 2); err != nil {
				return nil, err
			}
			if i == 0xFFDA {
				break
			}
		} else if sof {
			n, ok := read2()
			if !ok {
				return nil, shortError()
			}
			sv, err := safeRead(n - 2)
			if err != nil {
				return nil, err
			}
			if len(sv) < 6 {
				return nil, shortError()
			}
			im.Height = int(binary.BigEndian.Uint16(sv[1:]))
			im.Width = int(binary.BigEndian.Uint16(sv[3:]))
			bits := sv[0]
			if bits != 8 {
				return nil, syntaxError("cannot handle " + itoa(int(bits)) + "-bit layers")
			}
			layers := int(sv[5])
			switch layers {
			case 1:
				im.Mode = "L"
			case 3:
				im.Mode = "RGB"
			case 4:
				im.Mode = "CMYK"
			default:
				return nil, syntaxError("cannot handle " + itoa(layers) + "-layer images")
			}
			for off := 6; off < len(sv); off += 3 {
				if off+3 > len(sv) {
					return nil, shortError()
				}
			}
		} else if dqt {
			n, ok := read2()
			if !ok {
				return nil, shortError()
			}
			sv, err := safeRead(n - 2)
			if err != nil {
				return nil, err
			}
			for len(sv) > 0 {
				v := sv[0]
				precision := 1
				if v/16 != 0 {
					precision = 2
				}
				qtLength := 1 + precision*64
				if len(sv) < qtLength {
					return nil, syntaxError("bad quantization table marker")
				}
				sv = sv[qtLength:]
			}
		} else if app {
			n, ok := read2()
			if !ok {
				return nil, shortError()
			}
			sv, err := safeRead(n - 2)
			if err != nil {
				return nil, err
			}
			switch {
			case i == 0xFFE0 && len(sv) >= 4 && string(sv[:4]) == "JFIF":
				if len(sv) < 7 {
					return nil, shortError()
				}
			case i == 0xFFE1 && len(sv) >= 6 && string(sv[:6]) == "Exif\x00\x00":
			case i == 0xFFEE && len(sv) >= 5 && string(sv[:5]) == "Adobe":
				if len(sv) < 7 {
					return nil, shortError()
				}
			case i == 0xFFED && len(sv) >= 14 && string(sv[:14]) == "Photoshop 3.0\x00":
				// The resource block scan swallows struct errors.
			}
		} else if com {
			n, ok := read2()
			if !ok {
				return nil, shortError()
			}
			if _, err := safeRead(n - 2); err != nil {
				return nil, err
			}
		} else {
			// A marker with a payload but no handler consumes nothing; the
			// next byte read continues the scan.
		}
		if i == 0xFFDA {
			break
		}
		b, ok := readByte()
		if !ok {
			s = nil
		} else {
			s = []byte{b}
		}
	}
	return im, nil
}
