package pilimage

import (
	"encoding/binary"
)

// This file ports DdsImagePlugin's DdsRgbDecoder and BLP's DXT decoders
// (BlpImagePlugin) on top of the bcn decoder.

// loadDDSRGB is DdsImageFile's "dds_rgb" py decoder.
func (im *Image) loadDDSRGB(offset, bitcount int, masks []uint32) error {
	data := im.data
	bytecount := bitcount / 8
	n := im.Width * im.Height * len(masks)
	if bytecount == 0 {
		return valueError("not enough image data")
	}
	var offsets [4]int
	var totals [4]uint32
	for i, mask := range masks {
		off := 0
		if mask != 0 {
			for (mask>>uint(off+1))<<uint(off+1) == mask {
				off++
			}
		}
		offsets[i] = off
		totals[i] = mask >> uint(off)
	}
	out := make([]byte, 0, n)
	pos := offset
	for len(out) < n {
		if pos+bytecount > len(data) {
			break
		}
		var value uint32
		switch bytecount {
		case 1:
			value = uint32(data[pos])
		case 2:
			value = uint32(binary.LittleEndian.Uint16(data[pos:]))
		case 3:
			value = uint32(data[pos]) | uint32(data[pos+1])<<8 | uint32(data[pos+2])<<16
		case 4:
			value = binary.LittleEndian.Uint32(data[pos:])
		default:
			for i := 0; i < bytecount && pos+i < len(data); i++ {
				value |= uint32(data[pos+i]) << (8 * uint(i))
			}
		}
		pos += bytecount
		for i, mask := range masks {
			masked := value & mask
			var v byte
			if totals[i] != 0 {
				v = byte(int(float64(masked>>uint(offsets[i])) / float64(totals[i]) * 255))
			}
			out = append(out, v)
		}
	}
	if len(out) < n {
		return valueError("not enough image data")
	}
	im.pix = out[:n]
	return nil
}

// loadDDSRaw is the raw tile DdsImageFile._open uses for the palette and
// luminance branches. DdsImageFile.load_seek is a no-op, so the decoder reads
// from the position _open left the stream at.
func (im *Image) loadDDSRaw(rawmode string) error {
	return im.rawTile(im.data, im.ddsRawOffset, 0, 1, rawmode)
}

// loadDDSBCN is DdsImageFile's "bcn" tile.
func (im *Image) loadDDSBCN(offset, n int, pixelFormat string) error {
	return im.loadBCNDataSign(im.data, offset, n, im.Width, im.Height, pixelFormat == "BC5S")
}

// --- BLP DXT helpers (BlpImagePlugin.decode_dxt*) -------------------------

// blp565 is BlpImagePlugin.unpack_565: plain shifts, no bit replication.
func blp565(i int) (int, int, int) {
	return ((i >> 11) & 0x1F) << 3, ((i >> 5) & 0x3F) << 2, (i & 0x1F) << 3
}

func blpDXT1(data []byte, alpha bool) []byte {
	blocks := len(data) / 8
	out := make([]byte, 0, blocks*16*4)
	for bi := 0; bi < blocks; bi++ {
		idx := bi * 8
		c0 := int(binary.LittleEndian.Uint16(data[idx:]))
		c1 := int(binary.LittleEndian.Uint16(data[idx+2:]))
		bits := binary.LittleEndian.Uint32(data[idx+4:])
		r0, g0, b0 := blp565(c0)
		r1, g1, b1 := blp565(c1)
		for j := 0; j < 4; j++ {
			for i := 0; i < 4; i++ {
				control := int(bits & 3)
				bits >>= 2
				var r, g, b, a int
				a = 0xFF
				switch control {
				case 0:
					r, g, b = r0, g0, b0
				case 1:
					r, g, b = r1, g1, b1
				case 2:
					if c0 > c1 {
						r = (2*r0 + r1) / 3
						g = (2*g0 + g1) / 3
						b = (2*b0 + b1) / 3
					} else {
						r = (r0 + r1) / 2
						g = (g0 + g1) / 2
						b = (b0 + b1) / 2
					}
				case 3:
					if c0 > c1 {
						r = (2*r1 + r0) / 3
						g = (2*g1 + g0) / 3
						b = (2*b1 + b0) / 3
					} else {
						r, g, b, a = 0, 0, 0, 0
					}
				}
				if alpha {
					out = append(out, byte(r), byte(g), byte(b), byte(a))
				} else {
					out = append(out, byte(r), byte(g), byte(b))
				}
			}
		}
	}
	return out
}

func blpDXT3(data []byte) []byte {
	blocks := len(data) / 16
	out := make([]byte, 0, blocks*16*4)
	for bi := 0; bi < blocks; bi++ {
		idx := bi * 16
		block := data[idx : idx+16]
		c0v := int(binary.LittleEndian.Uint16(block[8:]))
		c1v := int(binary.LittleEndian.Uint16(block[10:]))
		code := binary.LittleEndian.Uint32(block[12:])
		r0, g0, b0 := blp565(c0v)
		r1, g1, b1 := blp565(c1v)
		for j := 0; j < 4; j++ {
			high := false
			for i := 0; i < 4; i++ {
				ai := (4*j + i) / 2
				a := block[ai]
				if high {
					high = false
					a >>= 4
				} else {
					high = true
					a &= 0xF
				}
				av := a * 17
				cc := int(code>>(2*uint(4*j+i))) & 3
				var r, g, b int
				switch cc {
				case 0:
					r, g, b = r0, g0, b0
				case 1:
					r, g, b = r1, g1, b1
				case 2:
					r = (2*r0 + r1) / 3
					g = (2*g0 + g1) / 3
					b = (2*b0 + b1) / 3
				case 3:
					r = (2*r1 + r0) / 3
					g = (2*g1 + g0) / 3
					b = (2*b1 + b0) / 3
				}
				out = append(out, byte(r), byte(g), byte(b), av)
			}
		}
	}
	return out
}

func blpDXT5(data []byte) []byte {
	blocks := len(data) / 16
	out := make([]byte, 0, blocks*16*4)
	for bi := 0; bi < blocks; bi++ {
		idx := bi * 16
		block := data[idx : idx+16]
		a0 := int(block[0])
		a1 := int(block[1])
		alphacode1 := int(block[4]) | int(block[5])<<8 | int(block[6])<<16 | int(block[7])<<24
		alphacode2 := int(block[2]) | int(block[3])<<8
		c0v := int(binary.LittleEndian.Uint16(block[8:]))
		c1v := int(binary.LittleEndian.Uint16(block[10:]))
		code := binary.LittleEndian.Uint32(block[12:])
		r0, g0, b0 := blp565(c0v)
		r1, g1, b1 := blp565(c1v)
		for j := 0; j < 4; j++ {
			for i := 0; i < 4; i++ {
				ai := 3 * (4*j + i)
				var ac int
				switch {
				case ai <= 12:
					ac = (alphacode2 >> uint(ai)) & 7
				case ai == 15:
					ac = (alphacode2 >> 15) | ((alphacode1 << 1) & 6)
				default:
					ac = (alphacode1 >> uint(ai-16)) & 7
				}
				var a int
				switch {
				case ac == 0:
					a = a0
				case ac == 1:
					a = a1
				case a0 > a1:
					a = ((8-ac)*a0 + (ac-1)*a1) / 7
				case ac == 6:
					a = 0
				case ac == 7:
					a = 255
				default:
					a = ((6-ac)*a0 + (ac-1)*a1) / 5
				}
				cc := int(code>>(2*uint(4*j+i))) & 3
				var r, g, b int
				switch cc {
				case 0:
					r, g, b = r0, g0, b0
				case 1:
					r, g, b = r1, g1, b1
				case 2:
					r = (2*r0 + r1) / 3
					g = (2*g0 + g1) / 3
					b = (2*b0 + b1) / 3
				case 3:
					r = (2*r1 + r0) / 3
					g = (2*g1 + g0) / 3
					b = (2*b1 + b0) / 3
				}
				out = append(out, byte(r), byte(g), byte(b), byte(a))
			}
		}
	}
	return out
}

// blpPalette is _BLPBaseDecoder._read_palette: b, g, r, a quads until the
// stream ends.
func blpPalette(data []byte, pos int) ([][4]byte, int) {
	var pal [][4]byte
	for i := 0; i < 256; i++ {
		if pos+4 > len(data) {
			break
		}
		b, g, r, a := data[pos], data[pos+1], data[pos+2], data[pos+3]
		pos += 4
		pal = append(pal, [4]byte{r, g, b, a})
	}
	return pal, pos
}

// loadBLP is BlpImageFile's "BLP1"/"BLP2" py decoder.
func (im *Image) loadBLP(blp1 bool, offset, compression, encoding, alphaEncoding int, alpha bool) error {
	data := im.data
	if offset+16*4*2 > len(data) {
		return osError("Truncated File Read")
	}
	offsets := make([]int, 16)
	lengths := make([]int, 16)
	for i := 0; i < 16; i++ {
		offsets[i] = int(binary.LittleEndian.Uint32(data[offset+4*i:]))
		lengths[i] = int(binary.LittleEndian.Uint32(data[offset+64+4*i:]))
	}
	pos := offset + 128
	bands := 3
	if alpha {
		bands = 4
	}
	width, height := im.Width, im.Height
	if blp1 {
		if compression == 0 {
			return ErrFormatNotPorted
		}
		if compression != 1 {
			return &Error{Class: "BLPFormatError", Text: "Unsupported BLP compression " + itoa(encoding)}
		}
		if encoding != 4 && encoding != 5 {
			return &Error{Class: "BLPFormatError", Text: "Unsupported BLP encoding " + itoa(encoding)}
		}
		pal, afterPalette := blpPalette(data, pos)
		// BLP1 reads the indices from the position after the palette; the
		// offset table the writer leaves is not used.
		return im.blpIndices(pal, afterPalette, lengths[0], bands, width, height)
	}
	// BLP2
	pal, _ := blpPalette(data, pos)
	base := offsets[0]
	if compression != 1 {
		return &Error{Class: "BLPFormatError", Text: "Unknown BLP compression " + itoa(compression)}
	}
	switch encoding {
	case 1:
		return im.blpIndices(pal, base, lengths[0], bands, width, height)
	case 2:
		var out []byte
		switch alphaEncoding {
		case 0:
			linesize := (width + 3) / 4 * 8
			for yb := 0; yb < (height+3)/4; yb++ {
				if base+linesize > len(data) {
					return osError("Truncated File Read")
				}
				out = append(out, blpDXT1(data[base:base+linesize], alpha)...)
				base += linesize
			}
		case 1:
			linesize := (width + 3) / 4 * 16
			for yb := 0; yb < (height+3)/4; yb++ {
				if base+linesize > len(data) {
					return osError("Truncated File Read")
				}
				out = append(out, blpDXT3(data[base:base+linesize])...)
				base += linesize
			}
		case 7:
			linesize := (width + 3) / 4 * 16
			for yb := 0; yb < (height+3)/4; yb++ {
				if base+linesize > len(data) {
					return osError("Truncated File Read")
				}
				out = append(out, blpDXT5(data[base:base+linesize])...)
				base += linesize
			}
		default:
			return &Error{Class: "BLPFormatError", Text: "Unsupported alpha encoding " + itoa(alphaEncoding)}
		}
		need := width * height * bands
		if len(out) < need {
			return valueError("not enough image data")
		}
		im.pix = out[:need]
		return nil
	default:
		return &Error{Class: "BLPFormatError", Text: "Unknown BLP encoding " + itoa(encoding)}
	}
}

// blpIndices is _read_bgra: palette lookups over the first mip level.
func (im *Image) blpIndices(pal [][4]byte, offset, length, bands, width, height int) error {
	data := im.data
	if offset+length > len(data) {
		return osError("Truncated File Read")
	}
	slice := data[offset : offset+length]
	out := make([]byte, 0, len(slice)*bands)
	for _, idx := range slice {
		if int(idx) >= len(pal) {
			return &Error{Class: "IndexError", Text: "list index out of range"}
		}
		p := pal[idx]
		out = append(out, p[0], p[1], p[2])
		if bands == 4 {
			out = append(out, p[3])
		}
	}
	need := width * height * bands
	if len(out) < need {
		return valueError("not enough image data")
	}
	im.pix = out[:need]
	return nil
}

// fourcc is a D3DFMT four-character code as the plugin's i32(b"...") reads it.
func fourcc(s string) uint32 { return binary.LittleEndian.Uint32([]byte(s)) }

// openDDS is DdsImageFile._open.
func openDDS(data []byte) (*Image, error) {
	if len(data) < 4 || !acceptDDS(data[:4]) {
		return nil, syntaxError("not a DDS file")
	}
	if len(data) < 8 {
		return nil, shortError()
	}
	headerSize := int(binary.LittleEndian.Uint32(data[4:]))
	if headerSize != 124 {
		return nil, osError("Unsupported header size " + itoa(headerSize))
	}
	header := data[min(8, len(data)):]
	if len(header) > 120 {
		header = header[:120]
	}
	if len(header) != 120 {
		return nil, osError("Incomplete header: " + itoa(len(header)) + " bytes")
	}
	flags := binary.LittleEndian.Uint32(header[0:])
	height := int(binary.LittleEndian.Uint32(header[4:]))
	width := int(binary.LittleEndian.Uint32(header[8:]))
	_ = flags
	pfflags := binary.LittleEndian.Uint32(header[72:])
	pfFourCC := binary.LittleEndian.Uint32(header[76:])
	bitcount := int(binary.LittleEndian.Uint32(header[80:]))
	const (
		ddpfAlphaPixels = 0x1
		ddpfRGB         = 0x40
		ddpfLuminance   = 0x20000
		ddpfPalette8    = 0x20
		ddpfFourCC      = 0x4
	)
	var im *Image
	switch {
	case pfflags&ddpfRGB != 0:
		mode := "RGB"
		maskCount := 3
		if pfflags&ddpfAlphaPixels != 0 {
			mode = "RGBA"
			maskCount = 4
		}
		im = newImage(data, "DDS", mode, width, height)
		masks := make([]uint32, maskCount)
		for i := 0; i < maskCount; i++ {
			masks[i] = binary.LittleEndian.Uint32(header[84+4*i:])
		}
		im.loadFn = func() error { return im.loadDDSRGB(128, bitcount, masks) }
		return im, nil
	case pfflags&ddpfLuminance != 0:
		mode := ""
		if bitcount == 8 {
			mode = "L"
		} else if bitcount == 16 && pfflags&ddpfAlphaPixels != 0 {
			mode = "LA"
		} else {
			return nil, osError("Unsupported bitcount " + itoa(bitcount) + " for " + itoa(int(pfflags)))
		}
		im = newImage(data, "DDS", mode, width, height)
		im.ddsRawOffset = 128
		im.loadFn = func() error { return im.loadDDSRaw(mode) }
		return im, nil
	case pfflags&ddpfPalette8 != 0:
		im = newImage(data, "DDS", "P", width, height)
		pal := data[min(128, len(data)):]
		if len(pal) > 1024 {
			pal = pal[:1024]
		}
		raw := append([]byte{}, pal...)
		rgb := make([]byte, 0, 768)
		for i := 0; i+3 < len(raw) && len(rgb) < 768; i += 4 {
			rgb = append(rgb, raw[i], raw[i+1], raw[i+2])
		}
		im.setPaletteRGB(raw, rgb)
		im.ddsRawOffset = 128 + len(raw)
		im.loadFn = func() error { return im.loadDDSRaw("P") }
		return im, nil
	case pfflags&ddpfFourCC != 0:
		offset := headerSize + 4
		n := 0
		pixelFormat := ""
		switch pfFourCC {
		case fourcc("DXT1"):
			im = newImage(data, "DDS", "RGBA", width, height)
			pixelFormat, n = "DXT1", 1
		case fourcc("DXT3"):
			im = newImage(data, "DDS", "RGBA", width, height)
			pixelFormat, n = "DXT3", 2
		case fourcc("DXT5"):
			im = newImage(data, "DDS", "RGBA", width, height)
			pixelFormat, n = "DXT5", 3
		case fourcc("BC4U"), fourcc("ATI1"):
			im = newImage(data, "DDS", "L", width, height)
			pixelFormat, n = "BC4", 4
		case fourcc("BC5S"):
			im = newImage(data, "DDS", "RGB", width, height)
			pixelFormat, n = "BC5S", 5
		case fourcc("BC5U"), fourcc("ATI2"):
			im = newImage(data, "DDS", "RGB", width, height)
			pixelFormat, n = "BC5", 5
		case fourcc("DX10"):
			offset += 20
			if 132 > len(data) {
				return nil, shortError()
			}
			dxgi := int(int32(binary.LittleEndian.Uint32(data[128:])))
			switch dxgi {
			case 70, 71:
				im = newImage(data, "DDS", "RGBA", width, height)
				pixelFormat, n = "BC1", 1
			case 73, 74:
				im = newImage(data, "DDS", "RGBA", width, height)
				pixelFormat, n = "BC2", 2
			case 76, 77:
				im = newImage(data, "DDS", "RGBA", width, height)
				pixelFormat, n = "BC3", 3
			case 79, 80:
				im = newImage(data, "DDS", "L", width, height)
				pixelFormat, n = "BC4", 4
			case 82, 83:
				im = newImage(data, "DDS", "RGB", width, height)
				pixelFormat, n = "BC5", 5
			case 84:
				im = newImage(data, "DDS", "RGB", width, height)
				pixelFormat, n = "BC5S", 5
			case 95:
				im = newImage(data, "DDS", "RGB", width, height)
				pixelFormat, n = "BC6H", 6
			case 96:
				im = newImage(data, "DDS", "RGB", width, height)
				pixelFormat, n = "BC6HS", 6
			case 97, 98, 99:
				im = newImage(data, "DDS", "RGBA", width, height)
				pixelFormat, n = "BC7", 7
			case 27, 28, 29:
				im = newImage(data, "DDS", "RGBA", width, height)
				im.ddsRawOffset = offset
				im.loadFn = func() error { return im.loadDDSRaw("RGBA") }
				return im, nil
			default:
				return nil, &Error{Class: "NotImplementedError", Text: "Unimplemented DXGI format " + itoa(dxgi)}
			}
		default:
			return nil, &Error{Class: "NotImplementedError", Text: "Unimplemented pixel format " + itoa(int(int32(pfFourCC)))}
		}
		if n != 0 {
			im.loadFn = func() error { return im.loadDDSBCN(offset, n, pixelFormat) }
		} else {
			im.loadFn = func() error { return im.loadDDSRaw(im.Mode) }
		}
		_ = pixelFormat
		return im, nil
	}
	return nil, &Error{Class: "NotImplementedError", Text: "Unknown pixel format flags " + itoa(int(pfflags))}
}

// openBLP is BlpImageFile._open.
func openBLP(data []byte) (*Image, error) {
	if len(data) < 4 || !acceptBLP(data[:4]) {
		return nil, &Error{Class: "BLPFormatError", Text: "Bad BLP magic " + pyReprBytes(data[:4])}
	}
	pos := 4
	if len(data) < pos+4 {
		return nil, shortError()
	}
	compression := int(int32(binary.LittleEndian.Uint32(data[pos:])))
	pos += 4
	alpha := false
	encoding := 0
	alphaEncoding := 0
	blp1 := string(data[:4]) == "BLP1"
	if blp1 {
		if pos+4 > len(data) {
			return nil, shortError()
		}
		alpha = binary.LittleEndian.Uint32(data[pos:]) != 0
		pos += 4
	} else {
		if pos+4 > len(data) {
			return nil, shortError()
		}
		encoding = int(int8(data[pos]))
		alpha = int8(data[pos+1]) != 0
		alphaEncoding = int(int8(data[pos+2]))
		pos += 4
	}
	if pos+8 > len(data) {
		return nil, shortError()
	}
	w := int(binary.LittleEndian.Uint32(data[pos:]))
	h := int(binary.LittleEndian.Uint32(data[pos+4:]))
	pos += 8
	if blp1 {
		if pos+8 > len(data) {
			return nil, shortError()
		}
		encoding = int(int32(binary.LittleEndian.Uint32(data[pos:])))
		pos += 8
	}
	offset := pos
	mode := "RGB"
	if alpha {
		mode = "RGBA"
	}
	im := newImage(data, "BLP", mode, w, h)
	im.loadFn = func() error {
		return im.loadBLP(blp1, offset, compression, encoding, alphaEncoding, alpha)
	}
	return im, nil
}
