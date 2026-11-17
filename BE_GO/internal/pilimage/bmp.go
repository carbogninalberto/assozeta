package pilimage

import "encoding/binary"

// bmpState holds what BmpImageFile._bitmap records for loading.
type bmpState struct {
	rawmode    string // raw decoder rawmode
	bits       int
	stride     int  // raw decoder stride (0 for bmp_rle)
	direction  int  // 1 top-down, -1 bottom-up
	rle        bool // bmp_rle decoder
	rle4       bool
	dataOffset int
}

func acceptBMP(prefix []byte) bool {
	return len(prefix) >= 2 && prefix[0] == 'B' && prefix[1] == 'M'
}

// acceptDIB is BmpImagePlugin._dib_accept.
func acceptDIB(prefix []byte) bool {
	if len(prefix) < 4 {
		return false
	}
	switch binary.LittleEndian.Uint32(prefix) {
	case 12, 40, 52, 56, 64, 108, 124:
		return true
	}
	return false
}

func openBMP(data []byte) (*Image, error) {
	if len(data) < 2 || !acceptBMP(data[:2]) {
		return nil, syntaxError("Not a BMP file")
	}
	if len(data) < 14 {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	offset := int(binary.LittleEndian.Uint32(data[10:]))
	return bmpBitmap(data, 14, offset)
}

func openDIB(data []byte) (*Image, error) {
	im, err := bmpBitmap(data, 0, 0)
	if err != nil {
		return nil, err
	}
	im.Format = "DIB"
	return im, nil
}

// bmpBitmap is BmpImageFile._bitmap.
func bmpBitmap(data []byte, pos, offset int) (*Image, error) {
	if pos+4 > len(data) {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	headerSize := int(binary.LittleEndian.Uint32(data[pos:]))
	pos += 4
	rest := headerSize - 4
	if rest < 0 {
		rest = 0
	}
	if pos+rest > len(data) {
		return nil, osError("Truncated File Read")
	}
	headerData := data[pos : pos+rest]
	pos += rest

	var (
		width, height int
		bits          int
		compression   int
		colors        int
		palettePad    int
		direction     = -1
	)
	switch headerSize {
	case 12:
		if len(headerData) < 8 {
			return nil, &Error{Class: "IndexError", Text: "index out of range"}
		}
		width = int(binary.LittleEndian.Uint16(headerData[0:]))
		height = int(binary.LittleEndian.Uint16(headerData[2:]))
		bits = int(binary.LittleEndian.Uint16(headerData[6:]))
		compression = 0
		palettePad = 3
	case 40, 52, 56, 64, 108, 124:
		if len(headerData) < 32 {
			return nil, &Error{Class: "IndexError", Text: "index out of range"}
		}
		yFlip := headerData[7] == 0xFF
		if yFlip {
			direction = 1
		}
		width = int(binary.LittleEndian.Uint32(headerData[0:]))
		h := binary.LittleEndian.Uint32(headerData[4:])
		if yFlip {
			height = int(^h + 1)
		} else {
			height = int(h)
		}
		bits = int(binary.LittleEndian.Uint16(headerData[10:]))
		compression = int(binary.LittleEndian.Uint32(headerData[12:]))
		colors = int(binary.LittleEndian.Uint32(headerData[28:]))
		palettePad = 4
	default:
		return nil, osError("Unsupported BMP header type (" + itoa(headerSize) + ")")
	}

	im := newImage(data, "BMP", "", width, height)

	if colors == 0 {
		colors = 1 << bits
	}
	if offset == 14+headerSize && bits <= 8 {
		offset += palettePad * colors
	}

	mode, rawMode := bmpMode(bits)
	if mode == "" {
		return nil, osError("Unsupported BMP pixel depth (" + itoa(bits) + ")")
	}

	if compression == 3 {
		var masks [4]uint32
		if len(headerData) >= 48 {
			n := 3
			if len(headerData) >= 52 {
				n = 4
			}
			for i := 0; i < n; i++ {
				masks[i] = binary.LittleEndian.Uint32(headerData[36+4*i:])
			}
		} else {
			for i := 0; i < 3; i++ {
				if pos+4 > len(data) {
					return nil, &Error{Class: "IndexError", Text: "index out of range"}
				}
				masks[i] = binary.LittleEndian.Uint32(data[pos:])
				pos += 4
			}
		}
		switch bits {
		case 32:
			raw, alpha, ok := bmp32MaskRaw(masks)
			if !ok {
				return nil, osError("Unsupported BMP bitfields layout")
			}
			rawMode = raw
			if alpha {
				mode = "RGBA"
			}
		case 24:
			if masks[0] != 0xFF0000 || masks[1] != 0xFF00 || masks[2] != 0xFF {
				return nil, osError("Unsupported BMP bitfields layout")
			}
			rawMode = "BGR"
		case 16:
			if masks[0] == 0xF800 && masks[1] == 0x7E0 && masks[2] == 0x1F {
				rawMode = "BGR;16"
			} else if masks[0] == 0x7C00 && masks[1] == 0x3E0 && masks[2] == 0x1F {
				rawMode = "BGR;15"
			} else {
				return nil, osError("Unsupported BMP bitfields layout")
			}
		default:
			return nil, osError("Unsupported BMP bitfields layout")
		}
	} else if compression == 0 {
		// CUR files (header 22) open 32-bit bitmaps as BGRA; BMP files do not.
		_ = rawMode
	} else if compression == 1 || compression == 2 {
		// bmp_rle
	} else {
		return nil, osError("Unsupported BMP compression (" + itoa(compression) + ")")
	}

	im.Mode = mode
	im.Width, im.Height = width, height

	if mode == "P" {
		if !(colors > 0 && colors <= 65536) {
			return nil, osError("Unsupported BMP Palette size (" + itoa(colors) + ")")
		}
		n := palettePad * colors
		end := min(pos+n, len(data))
		palette := data[pos:end]
		pos = end
		grayscale := true
		for ind := 0; ind < colors; ind++ {
			val := ind
			if colors == 2 && ind == 1 {
				val = 255
			}
			start := ind * palettePad
			var rgb []byte
			if start < len(palette) {
				rgb = palette[start:min(start+3, len(palette))]
			}
			if len(rgb) != 3 || rgb[0] != byte(val) || rgb[1] != byte(val) || rgb[2] != byte(val) {
				grayscale = false
				break
			}
		}
		if grayscale {
			if colors <= 2 {
				im.Mode = "1"
			} else {
				im.Mode = "L"
			}
			rawMode = im.Mode
		} else {
			im.Mode = "P"
			im.setPaletteCounted(palette, bmpPaletteRGB(palette, palettePad), len(palette)/palettePad)
		}
	}

	dataOffset := offset
	if dataOffset == 0 {
		dataOffset = pos
	}

	stride := 0
	if !(compression == 1 || compression == 2) {
		stride = ((width*bits + 31) >> 3) &^ 3
	}
	im.bmp = &bmpState{
		rawmode:    rawMode,
		bits:       bmpRawBits(rawMode),
		stride:     stride,
		direction:  direction,
		rle:        compression == 1 || compression == 2,
		rle4:       compression == 2,
		dataOffset: dataOffset,
	}
	return im, nil
}

// bmpMode is BmpImagePlugin.BIT2MODE.
func bmpMode(bits int) (string, string) {
	switch bits {
	case 1:
		return "P", "P;1"
	case 4:
		return "P", "P;4"
	case 8:
		return "P", "P"
	case 16:
		return "RGB", "BGR;15"
	case 24:
		return "RGB", "BGR"
	case 32:
		return "RGB", "BGRX"
	}
	return "", ""
}

// bmp32MaskRaw is the 32-bit part of the BITFIELDS MASK_MODES table.
func bmp32MaskRaw(m [4]uint32) (raw string, alpha bool, ok bool) {
	switch m {
	case [4]uint32{0xFF0000, 0xFF00, 0xFF, 0x0}:
		return "BGRX", false, true
	case [4]uint32{0xFF000000, 0xFF0000, 0xFF00, 0x0}:
		return "XBGR", false, true
	case [4]uint32{0xFF000000, 0xFF00, 0xFF, 0x0}:
		return "BGXR", false, true
	case [4]uint32{0xFF000000, 0xFF0000, 0xFF00, 0xFF}:
		return "ABGR", true, true
	case [4]uint32{0xFF, 0xFF00, 0xFF0000, 0xFF000000}:
		return "RGBA", true, true
	case [4]uint32{0xFF0000, 0xFF00, 0xFF, 0xFF000000}:
		return "BGRA", true, true
	case [4]uint32{0xFF000000, 0xFF00, 0xFF, 0xFF0000}:
		return "BGAR", true, true
	case [4]uint32{0x0, 0x0, 0x0, 0x0}:
		return "BGRA", true, true
	}
	return "", false, false
}

// bmpRawBits is the rawmode's bits per pixel.
func bmpRawBits(rawmode string) int {
	switch rawmode {
	case "1":
		return 1
	case "P;1":
		return 1
	case "P;4":
		return 4
	case "P", "L":
		return 8
	case "BGR;15", "BGR;16":
		return 16
	case "BGR":
		return 24
	case "BGRX", "XBGR", "BGXR", "ABGR", "RGBA", "BGRA", "BGAR":
		return 32
	}
	return 0
}

// bmpPaletteRGB realizes a BGR(X) palette.
func bmpPaletteRGB(palette []byte, pad int) []byte {
	rgb := make([]byte, 768)
	n := len(palette) / pad
	if n > 256 {
		n = 256
	}
	for i := 0; i < n; i++ {
		rgb[3*i] = palette[i*pad+2]
		rgb[3*i+1] = palette[i*pad+1]
		rgb[3*i+2] = palette[i*pad]
	}
	return rgb
}

// loadBMP runs either the raw decoder or BmpRleDecoder.
func (im *Image) loadBMP() error {
	st := im.bmp
	if st == nil {
		return osError("cannot load this image")
	}
	n := im.Width * im.Height
	switch im.Mode {
	case "1", "L", "P":
		im.pix = make([]byte, n)
	case "RGB":
		im.pix = make([]byte, 3*n)
	case "RGBA":
		im.pix = make([]byte, 4*n)
	}
	if st.dataOffset > len(im.data) {
		st.dataOffset = len(im.data)
	}
	if st.rle {
		return im.loadBMPRLE(st)
	}
	return im.loadBMPRaw(st)
}

// loadBMPRaw simulates ImagingRawDecode (src/libImaging/RawDecode.c) over the
// bytes that follow the header.
func (im *Image) loadBMPRaw(st *bmpState) error {
	rowBytes := (im.Width*st.bits + 7) / 8
	skip := st.stride - rowBytes
	if skip < 0 {
		return decoderError(-8)
	}
	data := im.data[st.dataOffset:]
	p := 0
	y := 0
	step := 1
	if st.direction < 0 {
		y = im.Height - 1
		step = -1
	}
	stateSkip := false
	for {
		if stateSkip {
			if len(data)-p < skip {
				return truncatedError(len(data) - p)
			}
			p += skip
		}
		if len(data)-p < rowBytes {
			return truncatedError(len(data) - p)
		}
		im.unpackBMPRow(st.rawmode, data[p:p+rowBytes], y)
		p += rowBytes
		y += step
		if y < 0 || y >= im.Height {
			return nil
		}
		stateSkip = true
	}
}

// unpackBMPRow is the raw decoder's shuffler for BMP rawmodes.
func (im *Image) unpackBMPRow(rawmode string, row []byte, y int) {
	base := y * im.Width
	switch rawmode {
	case "1":
		unpackBits(row, im.Width, func(i int, v byte) {
			if v != 0 {
				im.pix[base+i] = 1
			} else {
				im.pix[base+i] = 0
			}
		})
	case "P;1":
		unpackBits(row, im.Width, func(i int, v byte) { im.pix[base+i] = v })
	case "P;4":
		for x := 0; x < im.Width; x++ {
			shift := 4 - 4*uint(x%2)
			im.pix[base+x] = (row[x/2] >> shift) & 15
		}
	case "P", "L":
		copy(im.pix[base:base+im.Width], row[:im.Width])
	case "BGR":
		for x := 0; x < im.Width; x++ {
			r := row[3*x+2]
			g := row[3*x+1]
			b := row[3*x]
			im.pix[3*(base+x)] = r
			im.pix[3*(base+x)+1] = g
			im.pix[3*(base+x)+2] = b
		}
	case "BGRX":
		for x := 0; x < im.Width; x++ {
			im.pix[3*(base+x)] = row[4*x+2]
			im.pix[3*(base+x)+1] = row[4*x+1]
			im.pix[3*(base+x)+2] = row[4*x]
		}
	case "XBGR":
		for x := 0; x < im.Width; x++ {
			im.pix[3*(base+x)] = row[4*x+3]
			im.pix[3*(base+x)+1] = row[4*x+2]
			im.pix[3*(base+x)+2] = row[4*x+1]
		}
	case "BGXR":
		for x := 0; x < im.Width; x++ {
			im.pix[3*(base+x)] = row[4*x+3]
			im.pix[3*(base+x)+1] = row[4*x+1]
			im.pix[3*(base+x)+2] = row[4*x]
		}
	case "ABGR":
		for x := 0; x < im.Width; x++ {
			im.pix[4*(base+x)] = row[4*x+3]
			im.pix[4*(base+x)+1] = row[4*x+2]
			im.pix[4*(base+x)+2] = row[4*x+1]
			im.pix[4*(base+x)+3] = row[4*x]
		}
	case "RGBA":
		copy(im.pix[4*base:4*(base+im.Width)], row[:4*im.Width])
	case "BGRA":
		for x := 0; x < im.Width; x++ {
			im.pix[4*(base+x)] = row[4*x+2]
			im.pix[4*(base+x)+1] = row[4*x+1]
			im.pix[4*(base+x)+2] = row[4*x]
			im.pix[4*(base+x)+3] = row[4*x+3]
		}
	case "BGAR":
		for x := 0; x < im.Width; x++ {
			im.pix[4*(base+x)] = row[4*x+3]
			im.pix[4*(base+x)+1] = row[4*x+1]
			im.pix[4*(base+x)+2] = row[4*x]
			im.pix[4*(base+x)+3] = row[4*x+2]
		}
	case "BGR;15":
		for x := 0; x < im.Width; x++ {
			v := int(row[2*x]) | int(row[2*x+1])<<8
			im.pix[3*(base+x)] = byte(((v >> 10) & 31) * 255 / 31)
			im.pix[3*(base+x)+1] = byte(((v >> 5) & 31) * 255 / 31)
			im.pix[3*(base+x)+2] = byte((v & 31) * 255 / 31)
		}
	case "BGR;16":
		for x := 0; x < im.Width; x++ {
			v := int(row[2*x]) | int(row[2*x+1])<<8
			im.pix[3*(base+x)] = byte(((v >> 11) & 31) * 255 / 31)
			im.pix[3*(base+x)+1] = byte(((v >> 5) & 63) * 255 / 63)
			im.pix[3*(base+x)+2] = byte((v & 31) * 255 / 31)
		}
	}
}

// unpackBits is unpack1/unpackP1: MSB first bit unpacking.
func unpackBits(row []byte, pixels int, set func(i int, v byte)) {
	for i := 0; i < pixels; i++ {
		v := (row[i/8] >> (7 - uint(i%8))) & 1
		set(i, v)
	}
}

// loadBMPRLE is BmpImagePlugin.BmpRleDecoder.decode.
func (im *Image) loadBMPRLE(st *bmpState) error {
	data := im.data[st.dataOffset:]
	p := 0
	var out []byte
	x := 0
	destLength := im.Width * im.Height
	for len(out) < destLength {
		if p >= len(data) {
			break
		}
		pixels := data[p]
		p++
		if p >= len(data) {
			break
		}
		b := data[p]
		p++
		numPixels := int(pixels)
		if numPixels != 0 {
			if x+numPixels > im.Width {
				numPixels = max(0, im.Width-x)
			}
			if st.rle4 {
				first := b >> 4
				second := b & 0x0f
				for i := 0; i < numPixels; i++ {
					if i%2 == 0 {
						out = append(out, first)
					} else {
						out = append(out, second)
					}
				}
			} else {
				for i := 0; i < numPixels; i++ {
					out = append(out, b)
				}
			}
			x += numPixels
		} else {
			switch b {
			case 0: // end of line
				for len(out)%im.Width != 0 {
					out = append(out, 0)
				}
				x = 0
			case 1: // end of bitmap
				goto done
			case 2: // delta
				if p+2 > len(data) {
					goto done
				}
				right, up := int(data[p]), int(data[p+1])
				p += 2
				n := min(destLength-len(out), right+up*im.Width)
				for i := 0; i < n; i++ {
					out = append(out, 0)
				}
				x = len(out) % im.Width
			default: // absolute mode
				dataCount := min(destLength-len(out), int(b))
				var byteCount, read int
				if st.rle4 {
					byteCount = (dataCount + 1) / 2
					end := min(p+byteCount, len(data))
					for i, br := range data[p:end] {
						out = append(out, br>>4)
						if i*2+1 < dataCount {
							out = append(out, br&0x0f)
						}
					}
					read = end - p
					p = end
				} else {
					byteCount = dataCount
					end := min(p+byteCount, len(data))
					out = append(out, data[p:end]...)
					read = end - p
					p = end
				}
				if read < byteCount || len(out) == destLength {
					goto done
				}
				x += int(b)
				if (st.dataOffset+p)%2 != 0 {
					p++
				}
			}
		}
	}
done:
	// set_as_raw(rawmode, (0, direction)).
	rawmode := "P"
	if im.Mode == "1" || im.Mode == "L" {
		rawmode = "L"
	}
	rowBytes := 0
	switch rawmode {
	case "L", "P":
		rowBytes = im.Width
	}
	if len(out) < rowBytes*im.Height {
		return valueError("not enough image data")
	}
	// The raw decoder reads the rows in order and flips when direction < 0.
	for row := 0; row < im.Height; row++ {
		y := row
		if st.direction < 0 {
			y = im.Height - 1 - row
		}
		if rawmode == "L" {
			if im.Mode == "1" {
				for x2 := 0; x2 < im.Width; x2++ {
					if out[row*rowBytes+x2] != 0 {
						im.pix[y*im.Width+x2] = 1
					}
				}
			} else {
				copy(im.pix[y*im.Width:y*im.Width+im.Width], out[row*rowBytes:row*rowBytes+im.Width])
			}
		} else {
			copy(im.pix[y*im.Width:y*im.Width+im.Width], out[row*rowBytes:row*rowBytes+im.Width])
		}
	}
	return nil
}
