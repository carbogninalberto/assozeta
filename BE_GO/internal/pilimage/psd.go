package pilimage

// This file ports PsdImagePlugin: the header, the composite image descriptor
// and its raw/packbits tiles.

type psdTile struct {
	rawmode string
	offset  int
	pack    bool
	xsize   int
	ysize   int
}

// loadPSD decodes the tiles PsdImageFile._open's _maketile produced.
func (im *Image) loadPSD(tiles []psdTile) error {
	im.ensurePix()
	for _, t := range tiles {
		if t.pack {
			data := im.data
			pos := t.offset
			for y := 0; y < t.ysize; y++ {
				row, next, err := packbitsRow(data, pos, rawRowBytes(t.rawmode, t.xsize))
				if err != nil {
					return err
				}
				pos = next
				if err := im.rawRow(row, 0, y, im.Width, t.rawmode, baseRawmode(t.rawmode), im.samplesPerPixel(), -1); err != nil {
					return err
				}
			}
			continue
		}
		if err := im.rawDecode(im.data, t.offset, 0, 1, t.rawmode, [4]int{0, 0, im.Width, im.Height}, -1); err != nil {
			return err
		}
	}
	return nil
}

// packbitsRow is one row through ImagingPackbitsDecode: it returns the decoded
// xsize bytes and the position after the row's packets.
func packbitsRow(data []byte, pos, xsize int) ([]byte, int, error) {
	out := make([]byte, 0, xsize)
	for len(out) < xsize {
		if pos >= len(data) {
			return nil, pos, truncatedError(len(data) - pos)
		}
		b := data[pos]
		if b&0x80 != 0 {
			if b == 0x80 {
				pos++
				continue
			}
			if pos+2 > len(data) {
				return nil, pos, truncatedError(len(data) - pos)
			}
			n := 257 - int(b)
			for ; n > 0; n-- {
				if len(out) >= xsize {
					break
				}
				out = append(out, data[pos+1])
			}
			pos += 2
		} else {
			n := int(b) + 2
			if pos+n > len(data) {
				return nil, pos, truncatedError(len(data) - pos)
			}
			for i := 1; i < n; i++ {
				if len(out) >= xsize {
					break
				}
				out = append(out, data[pos+i])
			}
			pos += n
		}
	}
	return out, pos, nil
}

// openPSD is PsdImageFile._open.
func openPSD(data []byte) (*Image, error) {
	if len(data) < 26 || !acceptPSD(data[:4]) {
		return nil, syntaxError("not a PSD file")
	}
	if be16(data[4:]) != 1 {
		return nil, syntaxError("not a PSD file")
	}
	psdBits := be16(data[22:])
	psdChannels := be16(data[12:])
	psdMode := be16(data[24:])
	type psdModeInfo struct {
		mode     string
		channels int
	}
	modes := map[[2]int]psdModeInfo{
		{0, 1}: {"1", 1},
		{0, 8}: {"L", 1},
		{1, 8}: {"L", 1},
		{2, 8}: {"P", 1},
		{3, 8}: {"RGB", 3},
		{4, 8}: {"CMYK", 4},
		{7, 8}: {"L", 1},
		{8, 8}: {"L", 1},
		{9, 8}: {"LAB", 3},
	}
	m, ok := modes[[2]int{psdMode, psdBits}]
	if !ok {
		return nil, &Error{Class: "KeyError", Text: itoa(psdMode)}
	}
	channels := m.channels
	if channels > psdChannels {
		return nil, osError("not enough channels")
	}
	mode := m.mode
	if mode == "RGB" && psdChannels == 4 {
		mode = "RGBA"
		channels = 4
	}
	w := be32(data[18:])
	h := be32(data[14:])
	pos := 26

	// Color mode data.
	if pos+4 > len(data) {
		return nil, shortError()
	}
	size := be32(data[pos:])
	pos += 4
	var palette []byte
	if size != 0 {
		end := pos + size
		if end > len(data) {
			end = len(data)
		}
		if mode == "P" && size == 768 {
			palette = append([]byte{}, data[pos:end]...)
		}
		pos = end
	}
	im := newImage(data, "PSD", mode, w, h)
	if palette != nil {
		im.setPaletteRGB(palette, realizePalette("RGB;L", palette))
	}

	// Image resources.
	if pos+4 > len(data) {
		return nil, shortError()
	}
	size = be32(data[pos:])
	pos += 4
	if size != 0 {
		end := pos + size
		for pos < end {
			if pos+4 > len(data) {
				return nil, shortError()
			}
			pos += 4 // signature
			if pos+2 > len(data) {
				return nil, shortError()
			}
			pos += 2 // id
			if pos >= len(data) {
				return nil, shortError()
			}
			nameLen := int(data[pos])
			pos++
			if pos+nameLen > len(data) {
				return nil, shortError()
			}
			pos += nameLen
			if nameLen%2 == 0 {
				pos++
			}
			if pos+4 > len(data) {
				return nil, shortError()
			}
			dataLen := be32(data[pos:])
			pos += 4
			if pos+dataLen > len(data) {
				return nil, shortError()
			}
			pos += dataLen
			if dataLen%2 == 1 {
				pos++
			}
		}
		if pos > len(data) {
			pos = len(data)
		}
	}

	// Layer and mask information: skipped, but its size moves the offset.
	if pos+4 > len(data) {
		return nil, shortError()
	}
	size = be32(data[pos:])
	pos += 4
	if size != 0 {
		end := pos + size
		if pos+4 > len(data) {
			return nil, shortError()
		}
		inner := be32(data[pos:])
		pos += 4
		_ = inner
		if end > len(data) {
			end = len(data)
		}
		pos = end
	}

	// Image descriptor and _maketile.
	if pos+2 > len(data) {
		return nil, shortError()
	}
	compression := be16(data[pos:])
	pos += 2
	xsize, ysize := w, h
	if xsize < 0 || ysize < 0 {
		return nil, valueError("negative tile size")
	}
	var tiles []psdTile
	if compression == 0 {
		offset := pos
		for c := 0; c < channels; c++ {
			rawmode := string(mode[c])
			if mode == "CMYK" {
				rawmode += ";I"
			}
			tiles = append(tiles, psdTile{rawmode: rawmode, offset: offset, xsize: xsize, ysize: ysize})
			offset += xsize * ysize
		}
		pos = offset
	} else if compression == 1 {
		if pos+channels*ysize*2 > len(data) {
			return nil, osError("Truncated File Read")
		}
		bytecount := data[pos : pos+channels*ysize*2]
		offset := pos + channels*ysize*2
		i := 0
		for c := 0; c < channels; c++ {
			rawmode := string(mode[c])
			if mode == "CMYK" {
				rawmode += ";I"
			}
			tiles = append(tiles, psdTile{rawmode: rawmode, offset: offset, pack: true, xsize: xsize, ysize: ysize})
			for y := 0; y < ysize; y++ {
				offset += be16(bytecount[i:])
				i += 2
			}
		}
		pos = offset
	} else {
		// No tile: loading raises "cannot load this image".
		im.loadFn = func() error { return osError("cannot load this image") }
		return im, nil
	}
	if pos%2 == 1 {
		pos++
	}
	im.loadFn = func() error { return im.loadPSD(tiles) }
	return im, nil
}

// realizePalette applies putpalette's rawmode conversion to palette bytes.
func realizePalette(rawmode string, raw []byte) []byte {
	switch rawmode {
	case "RGB;L", "RGB":
		n := len(raw) / 3
		rgb := make([]byte, 3*n)
		for i := 0; i < n; i++ {
			if rawmode == "RGB;L" {
				rgb[3*i] = raw[i]
				rgb[3*i+1] = raw[n+i]
				rgb[3*i+2] = raw[2*n+i]
			} else {
				rgb[3*i] = raw[3*i]
				rgb[3*i+1] = raw[3*i+1]
				rgb[3*i+2] = raw[3*i+2]
			}
		}
		return rgb
	case "BGR":
		n := len(raw) / 3
		rgb := make([]byte, 3*n)
		for i := 0; i < n; i++ {
			rgb[3*i] = raw[3*i+2]
			rgb[3*i+1] = raw[3*i+1]
			rgb[3*i+2] = raw[3*i]
		}
		return rgb
	case "BGRA":
		n := len(raw) / 4
		rgb := make([]byte, 3*n)
		for i := 0; i < n; i++ {
			rgb[3*i] = raw[4*i+2]
			rgb[3*i+1] = raw[4*i+1]
			rgb[3*i+2] = raw[4*i]
		}
		return rgb
	case "BGRA;15Z":
		n := len(raw) / 2
		rgb := make([]byte, 3*n)
		for i := 0; i < n; i++ {
			v := int(raw[2*i]) | int(raw[2*i+1])<<8
			rgb[3*i] = byte((v >> 10 & 31) * 255 / 31)
			rgb[3*i+1] = byte((v >> 5 & 31) * 255 / 31)
			rgb[3*i+2] = byte((v & 31) * 255 / 31)
		}
		return rgb
	}
	return nil
}
