package pilimage

// This file ports IptcImagePlugin: the descriptive fields, the size/mode
// fields, the compression check and load()'s embedded PPM/JPEG extraction.

type iptcInfo struct {
	values map[[2]int][][]byte
	// order keeps (3, 60) style first occurrences for getint.
	keys map[[2]int]bool
}

// iptcField is IptcImageFile.field.
func iptcField(data []byte, pos int) (tag *[2]int, size int, next int, err error) {
	end := pos + 5
	if end > len(data) {
		end = len(data)
	}
	s := data[pos:end]
	pos = end
	if len(bytesTrimZero(s)) == 0 {
		return nil, 0, pos, nil
	}
	if len(s) < 3 {
		return nil, 0, pos, &Error{Class: "IndexError", Text: "index out of range"}
	}
	t := [2]int{int(s[1]), int(s[2])}
	if s[0] != 0x1c || !validIPTCtag(t[0]) {
		return nil, 0, pos, syntaxError("invalid IPTC/NAA file")
	}
	if len(s) < 4 {
		return nil, 0, pos, &Error{Class: "IndexError", Text: "index out of range"}
	}
	size = int(s[3])
	if size > 132 {
		return nil, 0, pos, osError("illegal field length in IPTC/NAA file")
	} else if size == 128 {
		size = 0
	} else if size > 128 {
		n := size - 128
		if pos+n > len(data) {
			n = len(data) - pos
		}
		raw := make([]byte, 0, 4)
		raw = append(raw, make([]byte, max(0, 4-n))...)
		raw = append(raw, data[pos:pos+n]...)
		size = int(uint32(raw[0])<<24 | uint32(raw[1])<<16 | uint32(raw[2])<<8 | uint32(raw[3]))
		pos += n
	} else {
		if len(s) < 5 {
			return nil, 0, pos, &Error{Class: "IndexError", Text: "index out of range"}
		}
		size = be16(s[3:])
	}
	return &t, size, pos, nil
}

func bytesTrimZero(b []byte) []byte {
	out := b[:0:0]
	for _, c := range b {
		if c != 0 {
			out = append(out, c)
		}
	}
	return out
}

// iptcGetint is IptcImageFile.getint: the last four bytes big-endian.
func iptcGetint(v []byte) int {
	var b [4]byte
	if len(v) >= 4 {
		copy(b[:], v[len(v)-4:])
	} else {
		copy(b[4-len(v):], v)
	}
	return int(uint32(b[0])<<24 | uint32(b[1])<<16 | uint32(b[2])<<8 | uint32(b[3]))
}

// openIPTC is IptcImageFile._open.
func openIPTC(data []byte) (*Image, error) {
	info := map[[2]int][][]byte{}
	pos := 0
	var offset int
	for {
		offset = pos
		tag, size, next, err := iptcField(data, pos)
		if err != nil {
			return nil, err
		}
		pos = next
		if tag == nil || *tag == [2]int{8, 10} {
			break
		}
		var tagdata []byte
		if size != 0 {
			end := pos + size
			if end > len(data) {
				end = len(data)
			}
			tagdata = append([]byte{}, data[pos:end]...)
			pos = end
		}
		info[*tag] = append(info[*tag], tagdata)
	}
	get := func(k [2]int) [][]byte { return info[k] }
	if len(get([2]int{3, 60})) < 2 {
		// Python indexes the value; a one-byte value raises IndexError.
		if len(get([2]int{3, 60})) == 0 {
			return nil, &Error{Class: "KeyError", Text: itoa(int(3))}
		}
	}
	layersBytes := get([2]int{3, 60})[0]
	if len(layersBytes) < 2 {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	layers, component := int(layersBytes[0]), int(layersBytes[1])
	var mode string
	band := 0
	bandSet := false
	if layers == 1 && component == 0 {
		mode = "L"
	} else {
		if layers == 3 && component != 0 {
			mode = "RGB"
		} else if layers == 4 && component != 0 {
			mode = "CMYK"
		}
		if v := get([2]int{3, 65}); len(v) > 0 {
			band = iptcGetint(v[0]) - 1
			bandSet = true
		}
		_ = bandSet
	}
	wField := get([2]int{3, 20})
	hField := get([2]int{3, 30})
	if len(wField) == 0 || len(hField) == 0 {
		return nil, &Error{Class: "KeyError", Text: itoa(int(3))}
	}
	w := iptcGetint(wField[0])
	h := iptcGetint(hField[0])
	compField := get([2]int{3, 120})
	if len(compField) == 0 {
		return nil, osError("Unknown IPTC image compression")
	}
	compression := iptcGetint(compField[0])
	if compression != 1 && compression != 5 {
		return nil, osError("Unknown IPTC image compression")
	}
	im := newImage(data, "IPTC", mode, w, h)
	im.loadFn = func() error { return im.loadIPTC(offset, compression == 5, band, layers == 1 && component == 0) }
	return im, nil
}

// loadIPTC is IptcImageFile.load: extract the image fields, prepend a PPM
// header for raw data, and merge the selected band.
func (im *Image) loadIPTC(offset int, jpeg bool, band int, plainL bool) error {
	pos := offset
	var payload []byte
	if !jpeg {
		payload = append(payload, []byte("P5\n"+itoa(im.Width)+" "+itoa(im.Height)+"\n255\n")...)
	}
	for {
		tag, size, next, err := iptcField(im.data, pos)
		if err != nil {
			return err
		}
		pos = next
		if tag == nil || *tag != [2]int{8, 10} {
			break
		}
		remaining := size
		for remaining > 0 {
			n := min(remaining, 8192)
			if pos+n > len(im.data) {
				n = len(im.data) - pos
			}
			if n <= 0 {
				break
			}
			payload = append(payload, im.data[pos:pos+n]...)
			pos += n
			remaining -= n
		}
	}
	if jpeg {
		// IptcImageFile.load: the extracted bytes are opened again and merged
		// into the band the (3, 65) field names (or replace the image).
		sub, err := Open(payload)
		if err != nil {
			return err
		}
		if err := sub.load(); err != nil {
			return err
		}
		if plainL {
			im.Width, im.Height = sub.Width, sub.Height
			im.pix = append([]byte{}, sub.pix...)
			return nil
		}
		// Image.merge checks every band but the first against the mode's
		// band type; the placeholder bands are "L", so a decoded image that
		// is not L fails there (core.merge reports the same mismatch when
		// the first band is replaced).
		if sub.Mode != "L" {
			return valueError("mode mismatch")
		}
		bands := len(im.Mode)
		if band < 0 || band >= bands {
			return &Error{Class: "IndexError", Text: "list index out of range"}
		}
		im.Width, im.Height = sub.Width, sub.Height
		im.pix = make([]byte, sub.Width*sub.Height*bands)
		copy(im.pix[band:], sub.pix[:sub.Width*sub.Height])
		return nil
	}
	sub, err := openPPM(payload)
	if err != nil {
		return err
	}
	if err := sub.load(); err != nil {
		return err
	}
	if sub.Mode != "L" {
		if err := sub.convertToModeL(); err != nil {
			return err
		}
	}
	if plainL {
		im.Mode = "L"
		im.pix = append([]byte{}, sub.pix[:im.Width*im.Height]...)
		return nil
	}
	// bands = [L]*getmodebands(mode); bands[band] = _im; merge.
	bands := len(im.Mode)
	im.pix = make([]byte, im.Width*im.Height*bands)
	if band < 0 || band >= bands {
		return &Error{Class: "IndexError", Text: "list index out of range"}
	}
	copy(im.pix[band:], sub.pix[:im.Width*im.Height])
	return nil
}

// convertToModeL turns a decoded embedded image into L.
func (im *Image) convertToModeL() error {
	gray, err := im.convertGray()
	if err != nil {
		return err
	}
	im.Mode = "L"
	im.pix = gray
	return nil
}
