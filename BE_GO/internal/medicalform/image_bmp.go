// BMP loading with MuPDF's semantics (fz_load_bmp): Windows and OS/2 headers,
// 1/2/4/8/16/24/32/64-bit pixels, RLE4/RLE8/bit fields, top-down rows, the odd
// default palettes and partial rows of a truncated file.
package medicalform

type bmpInfo struct {
	version      uint32
	bitmapOffset uint32
	width        uint32
	height       uint32
	bitcount     uint16
	compression  uint32
	bitmapsize   uint32
	colors       uint32
	rmask        uint32
	gmask        uint32
	bmask        uint32
	amask        uint32
	palette      [256 * 3]byte
	topdown      bool
	rshift       uint
	gshift       uint
	bshift       uint
	ashift       uint
	rbits        uint
	gbits        uint
	bbits        uint
	abits        uint
}

const (
	bmpNone      = 0
	bmpRLE8      = 1
	bmpRLE4      = 2
	bmpBitfields = 3
	bmpHuffman1D = 3
	bmpJPEG      = 4
	bmpRLE24     = 4
	bmpPNG       = 5
	bmpAlphaBits = 6
	bmpSHRTMax   = 32767
)

func le16(p []byte) int { return int(p[1])<<8 | int(p[0]) }
func le32(p []byte) uint32 {
	return uint32(p[3])<<24 | uint32(p[2])<<16 | uint32(p[1])<<8 | uint32(p[0])
}

func decodeBMP(file []byte) (imageAsset, bool) {
	var info bmpInfo
	begin := 0
	end := len(file)
	if end < 14 || file[0] != 'B' || (file[1] != 'M' && file[1] != 'A') {
		return imageAsset{}, false
	}
	info.bitmapOffset = le32(file[10:])
	p := 14
	if end-p < 4 {
		return imageAsset{}, false
	}
	info.version = le32(file[p:])
	size := int(info.version)
	if size != 12 && size != 16 && size != 40 && size != 52 && size != 56 && size != 64 && size != 108 && size != 124 {
		return imageAsset{}, false
	}
	if end-p < size {
		return imageAsset{}, false
	}
	if size == 12 {
		info.width = uint32(le16(file[p+4:]))
		info.height = uint32(le16(file[p+6:]))
		info.bitcount = uint16(le16(file[p+10:]))
	}
	if size >= 16 {
		info.width = le32(file[p+4:])
		info.height = le32(file[p+8:])
		info.bitcount = uint16(le16(file[p+14:]))
	}
	if info.bitcount == 16 {
		info.rmask, info.gmask, info.bmask, info.amask = 0x7c00, 0x03e0, 0x001f, 0
	} else if info.bitcount == 24 || info.bitcount == 32 {
		info.rmask, info.gmask, info.bmask, info.amask = 0xff0000, 0xff00, 0xff, 0
	}
	if size >= 40 {
		info.compression = le32(file[p+16:])
		info.bitmapsize = le32(file[p+20:])
		info.colors = le32(file[p+32:])
		if info.bitcount >= 32 {
			info.colors = 0
		} else if info.colors > 1<<info.bitcount {
			info.colors = 1 << info.bitcount
		}
	}
	if size == 52 || size == 56 || size == 108 || size == 124 {
		info.rmask = le32(file[p+40:])
		info.gmask = le32(file[p+44:])
		info.bmask = le32(file[p+48:])
	}
	if size == 56 || size == 108 || size == 124 {
		info.amask = le32(file[p+52:])
	}
	p += size

	// Clamp the bitmap offset to the buffer.
	if info.bitmapOffset < uint32(p-begin) {
		info.bitmapOffset = uint32(p - begin)
	}
	if uint32(end-begin) < info.bitmapOffset {
		info.bitmapOffset = uint32(end - begin)
	}

	win := info.version == 12 || info.version == 40 || info.version == 52 ||
		info.version == 56 || info.version == 108 || info.version == 124
	os2 := info.version == 12 || info.version == 16 || info.version == 64

	if bmpHasPalette(&info) {
		if !bmpReadPalette(&info, file, begin, end, &p) {
			return imageAsset{}, false
		}
	}
	if info.bitcount == 16 || info.bitcount == 32 {
		if info.version == 40 && (info.compression == bmpBitfields || info.compression == bmpAlphaBits) {
			want := 12
			if info.compression == bmpAlphaBits {
				want = 16
			}
			if end-p < want {
				return imageAsset{}, false
			}
			info.rmask, info.gmask, info.bmask = le32(file[p:]), le32(file[p+4:]), le32(file[p+8:])
			if want == 16 {
				info.amask = le32(file[p+12:])
			}
			p += want
		}
	}

	if win {
		bits := 32
		if info.version == 12 {
			bits = 16
		}
		if (info.height>>uint(bits-1))&1 != 0 {
			info.topdown = true
			if bits == 16 {
				info.height = uint32(-int32(int16(info.height)))
			} else {
				info.height = uint32(-int32(info.height))
			}
		}
	}

	computeMaskInfo(info.rmask, &info.rshift, &info.rbits)
	computeMaskInfo(info.gmask, &info.gshift, &info.gbits)
	computeMaskInfo(info.bmask, &info.bshift, &info.bbits)
	computeMaskInfo(info.amask, &info.ashift, &info.abits)

	if info.width == 0 || info.width > bmpSHRTMax || info.height == 0 || info.height > bmpSHRTMax ||
		uint64(info.width)*uint64(info.height) > 1<<28 {
		return imageAsset{}, false
	}
	if !bmpValidCompression(&info, win, os2) || !bmpValidBitcount(&info, win, os2) {
		return imageAsset{}, false
	}
	if info.rbits > uint(info.bitcount) || info.gbits > uint(info.bitcount) ||
		info.bbits > uint(info.bitcount) || info.abits > uint(info.bitcount) {
		return imageAsset{}, false
	}

	// The bitmap cannot begin before the headers have ended.
	if uint32(p-begin) < info.bitmapOffset {
		p = begin + int(info.bitmapOffset)
	}
	if win && info.compression == bmpJPEG {
		return decodeJPEG(file[p:])
	}
	if win && info.compression == bmpPNG {
		return decodePNG(file[p:])
	}
	return bmpReadBitmap(&info, file, p, end)
}

func bmpHasPalette(info *bmpInfo) bool {
	return info.bitcount == 1 || info.bitcount == 2 || info.bitcount == 4 || info.bitcount == 8
}

func bmpValidCompression(info *bmpInfo, win, os2 bool) bool {
	c := info.compression
	if win && (c == bmpNone || c == bmpRLE8 || c == bmpRLE4 || c == bmpBitfields ||
		c == bmpJPEG || c == bmpPNG || c == bmpAlphaBits || c == bmpRLE24) {
		return true
	}
	if os2 && (c == bmpNone || c == bmpRLE8 || c == bmpRLE4 || c == bmpHuffman1D || c == bmpRLE24) {
		return true
	}
	return false
}

func bmpValidBitcount(info *bmpInfo, win, os2 bool) bool {
	c, b := info.compression, info.bitcount
	rgb := c == bmpNone && (b == 1 || b == 2 || b == 4 || b == 8 || b == 16 || b == 24 || b == 32 || b == 64)
	valid := rgb
	valid = valid || c == bmpRLE8 && b == 8
	valid = valid || c == bmpRLE4 && b == 4
	valid = valid || win && c == bmpBitfields && (b == 16 || b == 32)
	valid = valid || win && c == bmpJPEG && b == 0
	valid = valid || win && c == bmpPNG && b == 0
	valid = valid || win && c == bmpAlphaBits && (b == 16 || b == 32)
	valid = valid || os2 && c == bmpRLE24 && b == 24
	valid = valid || os2 && c == bmpHuffman1D && b == 1
	return valid
}

func computeMaskInfo(mask uint32, shift, bits *uint) {
	*shift, *bits = 0, 0
	for mask != 0 && mask&1 == 0 {
		*shift++
		mask >>= 1
	}
	for mask != 0 && mask&1 == 1 {
		*bits++
		mask >>= 1
	}
}

func bmpReadPalette(info *bmpInfo, file []byte, begin, end int, p *int) bool {
	entry := 4
	if info.version == 12 {
		entry = 3
	}
	expected := int(1) << info.bitcount
	if info.colors != 0 {
		if int(info.colors) < expected {
			expected = int(info.colors)
		}
	}
	var present int
	if info.bitmapOffset == 0 {
		present = (end - *p) / entry
	} else {
		present = (begin + int(info.bitmapOffset) - *p) / entry
	}
	if present < 0 {
		present = 0
	}
	if present > expected {
		present = expected
	}
	for i := 0; i < present; i++ {
		at := *p + i*entry
		if at+2 >= end {
			break
		}
		info.palette[i*3] = file[at+2]
		info.palette[i*3+1] = file[at+1]
		info.palette[i*3+2] = file[at]
	}
	if present < expected {
		bmpDefaultPalette(info, present)
	}
	*p += present * entry
	return true
}

func bmpPaletteGray(info *bmpInfo, n int) bool {
	for i := 0; i < n; i++ {
		r, g, b := int(info.palette[i*3]), int(info.palette[i*3+1]), int(info.palette[i*3+2])
		if abs(r-g) > 2 || abs(g-b) > 2 || abs(r-b) > 2 {
			return false
		}
	}
	return true
}

func bmpDefaultPalette(info *bmpInfo, present int) {
	if present < 0 {
		present = 0
	}
	switch info.bitcount {
	case 8:
		if bmpPaletteGray(info, present) {
			for i := present; i < 256; i++ {
				info.palette[i*3], info.palette[i*3+1], info.palette[i*3+2] = byte(i), byte(i), byte(i)
			}
		} else {
			web := [216 * 3]byte{}
			steps := [6]byte{0x00, 0x33, 0x66, 0x99, 0xcc, 0xff}
			for r := 0; r < 6; r++ {
				for g := 0; g < 6; g++ {
					for b := 0; b < 6; b++ {
						at := (r + 6*g + 36*b) * 3
						web[at], web[at+1], web[at+2] = steps[r], steps[g], steps[b]
					}
				}
			}
			for i := present; i < 256; i++ {
				if i < 216 {
					info.palette[i*3], info.palette[i*3+1], info.palette[i*3+2] = web[i*3], web[i*3+1], web[i*3+2]
				} else {
					info.palette[i*3], info.palette[i*3+1], info.palette[i*3+2] = 0, 0, 0
				}
			}
		}
	case 4:
		if bmpPaletteGray(info, present) {
			for i := present; i < 16; i++ {
				v := byte(i<<4 | i)
				info.palette[i*3], info.palette[i*3+1], info.palette[i*3+2] = v, v, v
			}
		} else {
			vga := [16 * 3]byte{
				0x00, 0x00, 0x00, 0x00, 0x00, 0xAA, 0x00, 0xAA, 0x00, 0x00, 0xAA, 0xAA,
				0xAA, 0x00, 0x00, 0xAA, 0x00, 0xAA, 0xAA, 0x55, 0x00, 0xAA, 0xAA, 0xAA,
				0x55, 0x55, 0x55, 0x55, 0x55, 0xFF, 0x55, 0xFF, 0x55, 0x55, 0xFF, 0xFF,
				0xFF, 0x55, 0x55, 0xFF, 0x55, 0xFF, 0xFF, 0xFF, 0x55, 0xFF, 0xFF, 0xFF,
			}
			for i := present; i < 16; i++ {
				info.palette[i*3], info.palette[i*3+1], info.palette[i*3+2] = vga[i*3], vga[i*3+1], vga[i*3+2]
			}
		}
	case 2:
		gray := [4 * 3]byte{0, 0, 0, 0x54, 0x54, 0x54, 0xA8, 0xA8, 0xA8, 0xFF, 0xFF, 0xFF}
		copy(info.palette[:], gray[:])
	case 1:
		copy(info.palette[:], []byte{0, 0, 0, 0xFF, 0xFF, 0xFF})
	}
}

func bmpReadBitmap(info *bmpInfo, file []byte, p, end int) (imageAsset, bool) {
	source := p
	var decompressed []byte
	switch {
	case info.compression == bmpNone:
	case info.compression == bmpRLE4:
		decompressed = bmpDecompressRLE4(info, file[p:end])
		source, end = 0, len(decompressed)
		info.compression, info.bitcount = bmpNone, 4
	case info.compression == bmpRLE8:
		decompressed = bmpDecompressRLE8(info, file[p:end])
		source, end = 0, len(decompressed)
		info.compression, info.bitcount = bmpNone, 8
	case info.version == 40 && (info.compression == bmpBitfields || info.compression == bmpAlphaBits),
		info.version == 52 || info.version == 56 || info.version == 108 || info.version == 124:
	case info.version == 64 && info.compression == bmpRLE24:
		decompressed = bmpDecompressRLE24(info, file[p:end])
		source, end = 0, len(decompressed)
		info.compression, info.bitcount = bmpNone, 24
	default:
		return imageAsset{}, false
	}
	var data []byte
	if decompressed != nil {
		data = decompressed
	} else {
		data = file
	}
	bitcount := int(info.bitcount)
	width := int(info.width)
	height := int(info.height)
	sstride := (width*bitcount + 31) / 32 * 4
	if sstride <= 0 {
		return imageAsset{}, false
	}
	if source+sstride*height > end {
		h := (end - source) / sstride
		if h == 0 || h > bmpSHRTMax {
			return imageAsset{}, false
		}
	}

	colour := make([]byte, width*height*3)
	alpha := make([]byte, width*height)

	mult := [8]uint32{0, 8191, 2730, 1170, 546, 264, 130, 64}
	mul := func(bits uint) uint32 {
		if bits < 8 {
			return mult[bits]
		}
		return 1
	}
	trunc := func(bits uint) uint32 {
		if bits < 8 {
			return 5
		}
		return uint32(bits - 8)
	}

	if source+sstride*height > end {
		height = (end - source) / sstride
	}
	rmult, gmult, bmult, amult := mul(info.rbits), mul(info.gbits), mul(info.bbits), mul(info.abits)
	rtrunc, gtrunc, btrunc, atrunc := trunc(info.rbits), trunc(info.gbits), trunc(info.bbits), trunc(info.abits)

	for y := 0; y < height; y++ {
		rowStart := source + y*sstride
		if rowStart+sstride > end {
			break
		}
		sp := data[rowStart : rowStart+sstride]
		out := y
		if !info.topdown {
			out = int(info.height) - 1 - y
		}
		at := out * width
		switch bitcount {
		case 64:
			for x := 0; x < width; x++ {
				r := uint32(sp[5])<<8 | uint32(sp[4])
				g := uint32(sp[3])<<8 | uint32(sp[2])
				b := uint32(sp[1])<<8 | uint32(sp[0])
				a := uint32(sp[7])<<8 | uint32(sp[6])
				colour[(at+x)*3] = byte((r*255 + 4096) >> 13)
				colour[(at+x)*3+1] = byte((g*255 + 4096) >> 13)
				colour[(at+x)*3+2] = byte((b*255 + 4096) >> 13)
				alpha[at+x] = byte((a*255 + 4096) >> 13)
				sp = sp[8:]
			}
		case 32:
			for x := 0; x < width; x++ {
				sample := uint32(sp[3])<<24 | uint32(sp[2])<<16 | uint32(sp[1])<<8 | uint32(sp[0])
				r := (sample & info.rmask) >> info.rshift
				g := (sample & info.gmask) >> info.gshift
				b := (sample & info.bmask) >> info.bshift
				a := uint32(255)
				if info.abits != 0 {
					a = (sample & info.amask) >> info.ashift
				}
				colour[(at+x)*3] = byte((r * rmult) >> rtrunc)
				colour[(at+x)*3+1] = byte((g * gmult) >> gtrunc)
				colour[(at+x)*3+2] = byte((b * bmult) >> btrunc)
				if info.abits == 0 {
					alpha[at+x] = byte(a)
				} else {
					alpha[at+x] = byte((a * amult) >> atrunc)
				}
				sp = sp[4:]
			}
		case 24:
			for x := 0; x < width; x++ {
				colour[(at+x)*3] = sp[2]
				colour[(at+x)*3+1] = sp[1]
				colour[(at+x)*3+2] = sp[0]
				alpha[at+x] = 255
				sp = sp[3:]
			}
		case 16:
			for x := 0; x < width; x++ {
				sample := uint32(sp[1])<<8 | uint32(sp[0])
				r := (sample & info.rmask) >> info.rshift
				g := (sample & info.gmask) >> info.gshift
				b := (sample & info.bmask) >> info.bshift
				a := (sample & info.amask) >> info.ashift
				colour[(at+x)*3] = byte((r * rmult) >> rtrunc)
				colour[(at+x)*3+1] = byte((g * gmult) >> gtrunc)
				colour[(at+x)*3+2] = byte((b * bmult) >> btrunc)
				if info.abits == 0 {
					alpha[at+x] = 255
				} else {
					alpha[at+x] = byte((a * amult) >> atrunc)
				}
				sp = sp[2:]
			}
		case 8:
			for x := 0; x < width; x++ {
				index := int(sp[0]) * 3
				colour[(at+x)*3] = info.palette[index]
				colour[(at+x)*3+1] = info.palette[index+1]
				colour[(at+x)*3+2] = info.palette[index+2]
				alpha[at+x] = 255
				sp = sp[1:]
			}
		case 4:
			for x := 0; x < width; x++ {
				index := int(sp[0]>>4) & 15
				if x&1 == 1 {
					index = int(sp[0]) & 15
					sp = sp[1:]
				}
				index *= 3
				colour[(at+x)*3] = info.palette[index]
				colour[(at+x)*3+1] = info.palette[index+1]
				colour[(at+x)*3+2] = info.palette[index+2]
				alpha[at+x] = 255
			}
		case 2:
			for x := 0; x < width; x++ {
				index := int(sp[0]>>(2*uint(3-x&3))) & 3
				if x&3 == 3 {
					sp = sp[1:]
				}
				index *= 3
				colour[(at+x)*3] = info.palette[index]
				colour[(at+x)*3+1] = info.palette[index+1]
				colour[(at+x)*3+2] = info.palette[index+2]
				alpha[at+x] = 255
			}
		case 1:
			for x := 0; x < width; x++ {
				index := int(sp[0]>>(7-uint(x&7))) & 1
				if x&7 == 7 {
					sp = sp[1:]
				}
				index *= 3
				colour[(at+x)*3] = info.palette[index]
				colour[(at+x)*3+1] = info.palette[index+1]
				colour[(at+x)*3+2] = info.palette[index+2]
				alpha[at+x] = 255
			}
		}
	}
	return newSampleAsset(width, int(info.height), 3, colour, alpha, false, false), true
}

// bmpSpot maps a run position to the pixel it paints, including MuPDF's wrap
// when the stream runs past the row.
func bmpSpot(x, y, width, height uint32) (uint32, uint32) {
	if x >= width || y >= height {
		return x % width, y + x/width
	}
	return x, y
}

func bmpDecompressRLE8(info *bmpInfo, stream []byte) []byte {
	width, height := info.width, info.height
	stride := (width + 3) / 4 * 4
	out := make([]byte, int(height)*int(stride))
	x, y := uint32(0), uint32(0)
	sp := 0
	for sp+2 <= len(stream) {
		if stream[sp] == 0 && stream[sp+1] == 0 {
			sp += 2
			x, y = 0, y+1
		} else if stream[sp] == 0 && stream[sp+1] == 1 {
			break
		} else if stream[sp] == 0 && stream[sp+1] == 2 {
			sp += 2
			if sp < len(stream) {
				x += uint32(stream[sp])
				sp++
			}
			if sp < len(stream) {
				y += uint32(stream[sp])
				sp++
			}
		} else if stream[sp] == 0 && stream[sp+1] >= 3 {
			dn := int(stream[sp+1])
			sn := dn
			pad := sn & 1
			sp += 2
			if sn > len(stream)-sp {
				sn = len(stream) - sp
				pad = 0
				dn = sn
			} else if sn+pad > len(stream)-sp {
				pad = 0
			}
			for i := 0; i < dn; i++ {
				ax, ay := bmpSpot(x, y, width, height)
				if ax < width && ay < height {
					out[int(ay)*int(stride)+int(ax)] = stream[sp+i]
				}
				x++
			}
			sp += sn + pad
		} else {
			dn := int(stream[sp])
			sp++
			for i := 0; i < dn; i++ {
				ax, ay := bmpSpot(x, y, width, height)
				if ax < width && ay < height {
					out[int(ay)*int(stride)+int(ax)] = stream[sp]
				}
				x++
			}
			sp++
		}
	}
	return out
}

func bmpDecompressRLE4(info *bmpInfo, stream []byte) []byte {
	width, height := info.width, info.height
	stride := ((width+1)/2 + 3) / 4 * 4
	out := make([]byte, int(height)*int(stride))
	x, y := uint32(0), uint32(0)
	sp := 0
	for sp+2 <= len(stream) {
		if stream[sp] == 0 && stream[sp+1] == 0 {
			sp += 2
			x, y = 0, y+1
		} else if stream[sp] == 0 && stream[sp+1] == 1 {
			break
		} else if stream[sp] == 0 && stream[sp+1] == 2 {
			sp += 2
			if sp < len(stream) {
				x += uint32(stream[sp])
				sp++
			}
			if sp < len(stream) {
				y += uint32(stream[sp])
				sp++
			}
		} else if stream[sp] == 0 && stream[sp+1] >= 3 {
			dn := int(stream[sp+1])
			sn := (dn + 1) / 2
			pad := sn & 1
			sp += 2
			if sn > len(stream)-sp {
				sn = len(stream) - sp
				pad = 0
				dn = sn * 2
			} else if sn+pad > len(stream)-sp {
				pad = 0
			}
			for i := 0; i < dn; i++ {
				var val byte
				if i&1 == 1 {
					val = stream[sp+(i>>1)] & 0x0f
				} else {
					val = stream[sp+(i>>1)] >> 4
				}
				ax, ay := bmpSpot(x, y, width, height)
				if ax < width && ay < height {
					at := int(ay)*int(stride) + int(ax)/2
					if x&1 == 1 {
						out[at] |= val
					} else {
						out[at] |= val << 4
					}
				}
				x++
			}
			sp += sn + pad
		} else {
			dn := int(stream[sp])
			sp++
			for i := 0; i < dn; i++ {
				var val byte
				if i&1 == 1 {
					val = stream[sp] & 0x0f
				} else {
					val = stream[sp] >> 4
				}
				ax, ay := bmpSpot(x, y, width, height)
				if ax < width && ay < height {
					at := int(ay)*int(stride) + int(ax)/2
					if x&1 == 1 {
						out[at] |= val
					} else {
						out[at] |= val << 4
					}
				}
				x++
			}
			sp++
		}
	}
	return out
}

func bmpDecompressRLE24(info *bmpInfo, stream []byte) []byte {
	width, height := info.width, info.height
	stride := (width*3 + 3) / 4 * 4
	out := make([]byte, int(height)*int(stride))
	x, y := uint32(0), uint32(0)
	sp := 0
	for sp+2 <= len(stream) {
		if stream[sp] == 0 && stream[sp+1] == 0 {
			sp += 2
			x, y = 0, y+1
		} else if stream[sp] == 0 && stream[sp+1] == 1 {
			break
		} else if stream[sp] == 0 && stream[sp+1] == 2 {
			sp += 2
			if sp < len(stream) {
				x += uint32(stream[sp])
				sp++
			}
			if sp < len(stream) {
				y += uint32(stream[sp])
				sp++
			}
		} else if stream[sp] == 0 && stream[sp+1] >= 3 {
			dn := int(stream[sp+1])
			sn := (dn*3 + 1) / 2 * 2
			pad := sn & 1
			sp += 2
			if sn > len(stream)-sp {
				sn = (len(stream) - sp) / 3 * 3
				pad = (len(stream) - sp) % 3
				dn = sn / 3
			} else if sn+pad > len(stream)-sp {
				pad = 0
			}
			for i := 0; i < dn; i++ {
				ax, ay := bmpSpot(x, y, width, height)
				if ax < width && ay < height {
					at := int(ay)*int(stride) + int(ax)*3
					out[at], out[at+1], out[at+2] = stream[sp+i*3], stream[sp+i*3+1], stream[sp+i*3+2]
				}
				x++
			}
			sp += sn + pad
		} else {
			dn := int(stream[sp])
			sp++
			ok := len(stream)-sp >= 3
			if !ok {
				dn = 0
			}
			for i := 0; i < dn; i++ {
				ax, ay := bmpSpot(x, y, width, height)
				if ax < width && ay < height {
					at := int(ay)*int(stride) + int(ax)*3
					out[at], out[at+1], out[at+2] = stream[sp], stream[sp+1], stream[sp+2]
				}
				x++
			}
			if ok {
				sp += 3
			}
		}
	}
	return out
}
