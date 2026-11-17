// PSD loading with MuPDF's semantics (fz_load_psd): version 1, RGB/gray/CMYK
// at 8 or 16 bits per channel, raw or PackBits rows, channels stored as planes,
// the grayscale/CMYK inversion and the extra RGB channel as alpha.
package medicalform

type psdSource struct {
	data []byte
	pos  int
}

func (s *psdSource) get8() (byte, bool) {
	if s.pos >= len(s.data) {
		return 0, false
	}
	b := s.data[s.pos]
	s.pos++
	return b, true
}

func (s *psdSource) get16() (int, bool) {
	if len(s.data)-s.pos < 2 {
		s.pos = len(s.data)
		return 0, false
	}
	v := int(s.data[s.pos])<<8 | int(s.data[s.pos+1])
	s.pos += 2
	return v, true
}

func (s *psdSource) get32() (int, bool) {
	if len(s.data)-s.pos < 4 {
		s.pos = len(s.data)
		return 0, false
	}
	v := int(s.data[s.pos])<<24 | int(s.data[s.pos+1])<<16 | int(s.data[s.pos+2])<<8 | int(s.data[s.pos+3])
	s.pos += 4
	return v, true
}

func decodePSD(file []byte) (imageAsset, bool) {
	s := &psdSource{data: file}
	if len(file) < 26 {
		return imageAsset{}, false
	}
	if string(file[:4]) != "8BPS" {
		return imageAsset{}, false
	}
	s.pos = 4
	if version, ok := s.get16(); !ok || version != 1 {
		return imageAsset{}, false
	}
	if _, ok := s.get16(); !ok {
		return imageAsset{}, false
	}
	if _, ok := s.get32(); !ok {
		return imageAsset{}, false
	}
	channels, ok := s.get16()
	if !ok {
		return imageAsset{}, false
	}
	height, ok := s.get32()
	if !ok {
		return imageAsset{}, false
	}
	width, ok := s.get32()
	if !ok {
		return imageAsset{}, false
	}
	bpc, ok := s.get16()
	if !ok {
		return imageAsset{}, false
	}
	if bpc != 8 && bpc != 16 {
		return imageAsset{}, false
	}
	colourType, ok := s.get16()
	if !ok {
		return imageAsset{}, false
	}
	gray, cmyk := false, false
	alpha := false
	switch colourType {
	case 4:
		if channels != 4 {
			return imageAsset{}, false
		}
		cmyk = true
	case 3:
		switch channels {
		case 4:
			alpha = true
		case 3:
		default:
			return imageAsset{}, false
		}
	case 1:
		if channels != 1 {
			return imageAsset{}, false
		}
		gray = true
	default:
		return imageAsset{}, false
	}
	if v, ok := s.get32(); !ok || v != 0 {
		return imageAsset{}, false
	}
	// Image resources.
	resourceLength, ok := s.get32()
	if !ok {
		return imageAsset{}, false
	}
	for resourceLength >= 12 {
		start := s.pos
		signature, ok := s.get32()
		if !ok || signature != 0x3842494d {
			return imageAsset{}, false
		}
		if _, ok := s.get16(); !ok {
			return imageAsset{}, false
		}
		// Pascal name, padded to an even length.
		nameLength, ok := s.get8()
		if !ok || s.pos+int(nameLength)+int((nameLength+1)&1) > len(s.data) {
			return imageAsset{}, false
		}
		s.pos += int(nameLength) + int((nameLength+1)&1)
		dataLength, ok := s.get32()
		if !ok {
			return imageAsset{}, false
		}
		resourceLength -= s.pos - start
		if dataLength&1 != 0 {
			dataLength++
		}
		resourceLength -= dataLength
		if dataLength < 0 || s.pos+dataLength > len(s.data) {
			return imageAsset{}, false
		}
		s.pos += dataLength
	}
	// Layer data.
	layerLength, ok := s.get32()
	if !ok {
		return imageAsset{}, false
	}
	if layerLength != 0 {
		if len(s.data)-s.pos < layerLength {
			return imageAsset{}, false
		}
		s.pos += layerLength
	}
	if s.pos >= len(s.data) {
		return imageAsset{}, false
	}
	compression, ok := s.get16()
	if !ok {
		return imageAsset{}, false
	}
	packbits := false
	switch compression {
	case 0:
	case 1:
		packbits = true
		skip := height * channels * 2
		if skip < 0 || len(s.data)-s.pos < skip {
			return imageAsset{}, false
		}
		s.pos += skip
	default:
		return imageAsset{}, false
	}
	unpack := &psdUnpack{source: s, packbits: packbits, packed: 128}
	pixels := uint64(width) * uint64(height)
	if width <= 0 || height <= 0 || pixels > 1<<28 {
		return imageAsset{}, false
	}
	read := func() (int, bool) { return unpack.next() }
	planes := channels - boolInt(alpha)
	colourChannels := planes
	grayValue := gray
	cmykValue := cmyk
	colour := make([]byte, int(pixels)*colourChannels)
	var alphaPlane []byte
	if alpha {
		alphaPlane = make([]byte, int(pixels))
	}
	for plane := 0; plane < planes; plane++ {
		for i := 0; i < int(pixels); i++ {
			v, ok := read()
			if !ok {
				return imageAsset{}, false
			}
			if bpc == 16 {
				if _, ok := read(); !ok {
					return imageAsset{}, false
				}
			}
			if grayValue || cmykValue {
				v = 255 - v
			}
			colour[plane*int(pixels)+i] = byte(v)
		}
	}
	if alpha {
		for i := 0; i < int(pixels); i++ {
			v, ok := read()
			if !ok {
				return imageAsset{}, false
			}
			if bpc == 16 {
				if _, ok := read(); !ok {
					return imageAsset{}, false
				}
			}
			alphaPlane[i] = byte(v)
		}
	}
	// The planes are interleaved for the asset.
	interleaved := make([]byte, 0, int(pixels)*colourChannels)
	for i := 0; i < int(pixels); i++ {
		for plane := 0; plane < colourChannels; plane++ {
			interleaved = append(interleaved, colour[plane*int(pixels)+i])
		}
	}
	return newSampleAsset(width, height, colourChannels, interleaved, alphaPlane, grayValue, cmykValue), true
}

type psdUnpack struct {
	source   *psdSource
	packbits bool
	packed   int
	repeat   byte
}

// next is unpack8 from load-psd.c: literal and repeated runs, with 128 as the
// end-of-data marker.
func (u *psdUnpack) next() (int, bool) {
	if !u.packbits {
		b, ok := u.source.get8()
		return int(b), ok
	}
	i := u.packed
	if i == 128 {
		for {
			b, ok := u.source.get8()
			if !ok {
				return 0, false
			}
			i = int(b)
			u.packed = i
			if i != 128 {
				break
			}
		}
		if i > 128 {
			b, ok := u.source.get8()
			if !ok {
				return 0, false
			}
			u.repeat = b
		}
	}
	if i < 128 {
		i--
		if i < 0 {
			i = 128
		}
		u.packed = i
		b, ok := u.source.get8()
		return int(b), ok
	}
	i++
	if i == 257 {
		i = 128
	}
	u.packed = i
	return int(u.repeat), true
}
