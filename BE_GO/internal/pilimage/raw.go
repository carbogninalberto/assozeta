package pilimage

import (
	"encoding/binary"
	"math"
	"strings"
)

// This file ports Pillow's generic raw decoder (src/libImaging/RawDecode.c and
// Unpack.c) for the modes the plugins use: rows of samples, optionally planar
// within the row, with a stride and a bottom-up orientation.

// rawRowBytes is the number of bytes one row of rawmode occupies when no
// explicit stride is given.
func rawRowBytes(rawmode string, xsize int) int {
	switch rawmode {
	case "P;1":
		return (xsize + 7) / 8
	case "RGBa":
		return 4 * xsize
	case "RGBaX":
		return 5 * xsize
	case "RGBaXX":
		return 6 * xsize
	case "P;2L":
		return 2 * ((xsize + 7) / 8)
	case "P;4L":
		return 4 * ((xsize + 7) / 8)
	case "RGB;16B", "RGB;16L", "RGB;16N":
		return 6 * xsize
	case "RGBA;16B", "RGBA;16L", "RGBA;16N":
		return 8 * xsize
	case "RGBX;16B", "RGBX;16L", "RGBX;16N", "RGBa;16B", "RGBa;16L", "RGBa;16N":
		return 8 * xsize
	case "L;16", "L;16B", "L;16L", "L;16N":
		return 2 * xsize
	case "I;16", "I;16B", "I;16L", "I;16N", "I;16S", "I;16BS", "I;16NS":
		return 2 * xsize
	case "I;8", "I;8S":
		return xsize
	case "I", "I;32", "I;32B", "I;32L", "F", "F;32F", "F;32BF":
		return 4 * xsize
	case "F;64BF", "F;64F":
		return 8 * xsize
	case "BGR;15", "BGR;16", "BGRA;15Z":
		return 2 * xsize
	}
	if rawmode == "1" || rawmode == "1;I" {
		return (xsize + 7) / 8
	}
	if strings.HasSuffix(rawmode, ";4") || strings.HasSuffix(rawmode, ";2") {
		bits := 4
		if strings.HasSuffix(rawmode, ";2") {
			bits = 2
		}
		return (xsize*bits + 7) / 8
	}
	base := rawmode
	if i := strings.IndexByte(base, ';'); i >= 0 {
		base = base[:i]
	}
	planar := strings.HasSuffix(rawmode, ";L")
	switch base {
	case "1":
		return (xsize + 7) / 8
	case "L", "P", "R", "G", "B", "A", "C", "M", "Y", "K":
		if planar {
			return xsize
		}
		return xsize
	case "LA", "PA":
		return 2 * xsize
	case "RGB", "BGR", "YCbCr", "LAB", "HSV":
		return 3 * xsize
	case "RGBA", "BGRA", "RGBX", "BGRX", "CMYK":
		return 4 * xsize
	case "BGR;15", "BGR;16", "BGRA;15Z", "I;16", "I;16B":
		return 2 * xsize
	case "I", "I;32", "I;32B", "F", "F;32F", "F;32BF":
		return 4 * xsize
	case "F;64BF":
		return 8 * xsize
	}
	return xsize
}

// rawSamples is the number of samples per pixel a rawmode produces.
func rawSamples(rawmode string) int {
	base := rawmode
	if i := strings.IndexByte(base, ';'); i >= 0 {
		base = base[:i]
	}
	switch base {
	case "LA", "PA":
		return 2
	case "RGB", "BGR", "YCbCr", "LAB", "HSV":
		return 3
	case "RGBA", "BGRA", "RGBX", "BGRX", "CMYK":
		return 4
	}
	return 1
}

// rawPlanar reports whether the row holds one plane per sample (the ";L"
// variants).
func rawPlanar(rawmode string) bool {
	if strings.HasPrefix(rawmode, "P;2L") || strings.HasPrefix(rawmode, "P;4L") {
		return false
	}
	return strings.HasSuffix(rawmode, ";L")
}

// rawDecode applies one raw tile to im: data[offset:] holds ysize rows of
// stride bytes (or the natural row size when stride is zero), laid out per
// rawmode, with Pillow's orientation (1 top-down, -1 bottom-up). band is the
// destination band for single-band rawmodes ("R", "G", "A", ...), or -1 when
// the rawmode writes whole pixels.
func (im *Image) rawDecode(data []byte, offset, stride, orientation int, rawmode string, bbox [4]int, band int) error {
	x0, y0 := bbox[0], bbox[1]
	xsize := bbox[2] - bbox[0]
	ysize := bbox[3] - bbox[1]
	if xsize <= 0 || ysize <= 0 {
		return nil
	}
	natural := rawRowBytes(rawmode, xsize)
	skip := 0
	if stride != 0 {
		skip = stride - natural
		if skip < 0 {
			return decoderError(-8)
		}
	}
	if offset > len(data) {
		return truncatedError(0)
	}
	available := len(data) - offset
	consumed := 0
	samples := im.samplesPerPixel()
	base := rawmode
	if i := strings.IndexByte(base, ';'); i >= 0 {
		base = base[:i]
	}
	for row := 0; row < ysize; row++ {
		if available-consumed < natural {
			return truncatedError(available - consumed)
		}
		start := offset + consumed
		r := data[start : start+natural]
		y := row
		if orientation < 0 {
			y = ysize - 1 - row
		}
		yy := y0 + y
		if yy >= 0 && yy < im.Height {
			if err := im.rawRow(r, x0, yy, xsize, rawmode, base, samples, band); err != nil {
				return err
			}
		}
		consumed += natural
		if row == ysize-1 {
			break
		}
		if available-consumed < skip {
			return truncatedError(available - consumed)
		}
		consumed += skip
	}
	return nil
}

// rawRow unpacks one row of rawmode samples into the image.
func (im *Image) rawRow(r []byte, x0, y int, xsize int, rawmode, base string, samples, band int) error {
	switch rawmode {
	case "L;IR", "P;1R", "P;2R", "P;4R":
		return valueError("unknown raw mode for given image mode")
	}
	put := func(x, s int, v byte) {
		i := y*im.Width + x0 + x
		if im.pix == nil {
			im.pix = make([]byte, im.Width*im.Height*samples)
		}
		im.pix[i*samples+s] = v
	}
	putBand := func(x, bnd int, v byte) {
		i := y*im.Width + x0 + x
		if im.pix == nil {
			im.pix = make([]byte, im.Width*im.Height*samples)
		}
		im.pix[i*samples+bnd] = v
	}
	setI := func(x int, v int32) {
		if im.pixI == nil {
			im.pixI = make([]int32, im.Width*im.Height)
		}
		im.pixI[y*im.Width+x0+x] = v
	}
	setI16 := func(x int, v uint16) {
		if im.pix16 == nil {
			im.pix16 = make([]uint16, im.Width*im.Height)
		}
		im.pix16[y*im.Width+x0+x] = v
	}
	setF := func(x int, v float32) {
		if im.pixF == nil {
			im.pixF = make([]float32, im.Width*im.Height)
		}
		im.pixF[y*im.Width+x0+x] = v
	}
	if rawmode == "P;1" {
		if len(r) < (xsize+7)/8 {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			put(x, 0, (r[x>>3]>>(7-uint(x&7)))&1)
		}
		return nil
	}

	if rawmode == "P;1" {
		if len(r) < (xsize+7)/8 {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			put(x, 0, (r[x>>3]>>(7-uint(x&7)))&1)
		}
		return nil
	}
	if strings.HasPrefix(rawmode, "P;2L") || strings.HasPrefix(rawmode, "P;4L") {
		layers := 2
		if strings.HasPrefix(rawmode, "P;4L") {
			layers = 4
		}
		s := (xsize + 7) / 8
		if len(r) < layers*s {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			var v byte
			bit := byte(128 >> uint(x&7))
			for l := 0; l < layers; l++ {
				if r[l*s+x/8]&bit != 0 {
					v |= 1 << uint(l)
				}
			}
			put(x, 0, v)
		}
		return nil
	}
	if strings.HasPrefix(rawmode, "I;16") {
		if len(r) < 2*xsize {
			return truncatedError(0)
		}
		bigEndian := strings.Contains(rawmode, "B")
		signed := strings.HasSuffix(rawmode, "S")
		for x := 0; x < xsize; x++ {
			var u uint16
			if bigEndian {
				u = binary.BigEndian.Uint16(r[2*x:])
			} else {
				u = binary.LittleEndian.Uint16(r[2*x:])
			}
			if im.Mode == "I" {
				if signed {
					setI(x, int32(int16(u)))
				} else {
					setI(x, int32(u))
				}
			} else {
				setI16(x, u)
			}
		}
		return nil
	}
	if strings.HasPrefix(rawmode, "I;8") {
		if len(r) < xsize {
			return truncatedError(0)
		}
		signed := strings.HasSuffix(rawmode, "S")
		for x := 0; x < xsize; x++ {
			if signed {
				setI(x, int32(int8(r[x])))
			} else {
				setI(x, int32(r[x]))
			}
		}
		return nil
	}
	if strings.HasPrefix(rawmode, "I;32") {
		if len(r) < 4*xsize {
			return truncatedError(0)
		}
		bigEndian := strings.Contains(rawmode, "B")
		for x := 0; x < xsize; x++ {
			if bigEndian {
				setI(x, int32(binary.BigEndian.Uint32(r[4*x:])))
			} else {
				setI(x, int32(binary.LittleEndian.Uint32(r[4*x:])))
			}
		}
		return nil
	}
	if strings.HasPrefix(rawmode, "F;64") {
		if len(r) < 8*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			var u uint64
			if strings.HasSuffix(rawmode, "BF") {
				u = binary.BigEndian.Uint64(r[8*x:])
			} else {
				u = binary.LittleEndian.Uint64(r[8*x:])
			}
			setF(x, float32(math.Float64frombits(u)))
		}
		return nil
	}
	if strings.HasPrefix(rawmode, "F;") && !strings.HasSuffix(rawmode, "F") {
		switch {
		case strings.HasPrefix(rawmode, "F;8"):
			if len(r) < xsize {
				return truncatedError(0)
			}
			for x := 0; x < xsize; x++ {
				setF(x, float32(r[x]))
			}
			return nil
		case strings.HasPrefix(rawmode, "F;16"):
			if len(r) < 2*xsize {
				return truncatedError(0)
			}
			for x := 0; x < xsize; x++ {
				var v uint16
				if strings.HasSuffix(rawmode, "B") || strings.HasSuffix(rawmode, "BS") {
					v = binary.BigEndian.Uint16(r[2*x:])
				} else {
					v = binary.LittleEndian.Uint16(r[2*x:])
				}
				setF(x, float32(v))
			}
			return nil
		case strings.HasPrefix(rawmode, "F;32"):
			if len(r) < 4*xsize {
				return truncatedError(0)
			}
			for x := 0; x < xsize; x++ {
				var u uint32
				if strings.HasSuffix(rawmode, "B") || strings.HasSuffix(rawmode, "BS") {
					u = binary.BigEndian.Uint32(r[4*x:])
				} else {
					u = binary.LittleEndian.Uint32(r[4*x:])
				}
				setF(x, float32(u))
			}
			return nil
		}
	}
	if strings.HasPrefix(rawmode, "L;16") {
		if len(r) < 2*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			if rawmode == "L;16B" {
				put(x, 0, r[2*x])
			} else {
				put(x, 0, r[2*x+1])
			}
		}
		return nil
	}
	if rawmode == "RGBa" || rawmode == "RGBaX" || rawmode == "RGBaXX" {
		step := 4
		if rawmode == "RGBaX" {
			step = 5
		} else if rawmode == "RGBaXX" {
			step = 6
		}
		if len(r) < step*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			a := r[step*x+3]
			var rr, gg, bb byte
			if a == 0 {
				rr, gg, bb = 0, 0, 0
			} else if a == 255 {
				rr, gg, bb = r[step*x], r[step*x+1], r[step*x+2]
			} else {
				clip := func(v int) byte {
					if v < 0 {
						return 0
					}
					if v > 255 {
						return 255
					}
					return byte(v)
				}
				rr = clip(int(r[step*x]) * 255 / int(a))
				gg = clip(int(r[step*x+1]) * 255 / int(a))
				bb = clip(int(r[step*x+2]) * 255 / int(a))
			}
			put(x, 0, rr)
			put(x, 1, gg)
			put(x, 2, bb)
			put(x, 3, a)
		}
		return nil
	}
	if strings.HasPrefix(rawmode, "RGB;16") || strings.HasPrefix(rawmode, "RGBA;16") ||
		strings.HasPrefix(rawmode, "RGBX;16") || strings.HasPrefix(rawmode, "RGBa;16") {
		ch := 3
		outCh := 3
		switch {
		case strings.HasPrefix(rawmode, "RGBA"):
			ch, outCh = 4, 4
		case strings.HasPrefix(rawmode, "RGBX"):
			ch, outCh = 4, 3
		case strings.HasPrefix(rawmode, "RGBa"):
			ch, outCh = 4, 4
		}
		if len(r) < 2*ch*xsize {
			return truncatedError(0)
		}
		big := strings.HasSuffix(rawmode, "16B")
		for x := 0; x < xsize; x++ {
			for s := 0; s < outCh; s++ {
				idx := (ch*x + s) * 2
				if big {
					put(x, s, r[idx])
				} else {
					put(x, s, r[idx+1])
				}
			}
		}
		return nil
	}
	if strings.HasPrefix(rawmode, "BGRA;15Z") || strings.HasPrefix(rawmode, "BGR;15") || strings.HasPrefix(rawmode, "BGR;16") {
		if len(r) < 2*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			v := int(binary.LittleEndian.Uint16(r[2*x:]))
			var r5, g6, b5 int
			if rawmode == "BGR;16" {
				r5 = (v >> 11) & 0x1F
				g6 = (v >> 5) & 0x3F
				b5 = v & 0x1F
				put(x, 1, byte(g6*255/63))
			} else {
				r5 = (v >> 10) & 0x1F
				g6 = (v >> 5) & 0x1F
				b5 = v & 0x1F
				put(x, 1, byte(g6*255/31))
			}
			put(x, 0, byte(r5*255/31))
			put(x, 2, byte(b5*255/31))
			if rawmode == "BGRA;15Z" {
				if v&0x8000 != 0 {
					put(x, 3, 0)
				} else {
					put(x, 3, 255)
				}
			}
		}
		return nil
	}
	if rawPlanar(rawmode) {
		planes := rawSamples(rawmode)
		if len(r) < planes*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			for s := 0; s < planes; s++ {
				put(x, s, r[s*xsize+x])
			}
		}
		return nil
	}
	switch base {
	case "1":
		if len(r) < (xsize+7)/8 {
			return truncatedError(0)
		}
		invert := strings.Contains(rawmode, "I")
		rev := strings.HasSuffix(rawmode, "R")
		for x := 0; x < xsize; x++ {
			var bit byte
			if rev {
				bit = (r[x>>3] >> uint(x&7)) & 1
			} else {
				bit = (r[x>>3] >> (7 - uint(x&7))) & 1
			}
			if invert {
				bit ^= 1
			}
			v := byte(0)
			if bit != 0 {
				v = 255
			}
			put(x, 0, v)
		}
		return nil
	case "L":
		rev := strings.HasSuffix(rawmode, "R")
		invert := strings.Contains(rawmode, "I")
		flip := func(v byte) byte {
			if rev {
				return bitReverse(v)
			}
			return v
		}
		if len(r) < xsize && !strings.Contains(rawmode, ";4") && !strings.Contains(rawmode, ";2") {
			return truncatedError(0)
		}
		if strings.Contains(rawmode, ";4") || strings.Contains(rawmode, ";2") {
			bits := 4
			scale := 17
			if strings.Contains(rawmode, ";2") {
				bits, scale = 2, 85
			}
			perByte := 8 / bits
			mask := (1 << bits) - 1
			if len(r) < (xsize+perByte-1)/perByte {
				return truncatedError(0)
			}
			for x := 0; x < xsize; x++ {
				shift := 8 - bits*(x%perByte+1)
				v := int((flip(r[x/perByte]) >> uint(shift)) & byte(mask))
				v *= scale
				if invert {
					v = 255 - v
				}
				put(x, 0, byte(v))
			}
			return nil
		}
		for x := 0; x < xsize; x++ {
			v := flip(r[x])
			if invert {
				v = 255 - v
			}
			put(x, 0, v)
		}
		return nil
	case "P":
		if strings.HasSuffix(rawmode, ";4") || strings.HasSuffix(rawmode, ";2") {
			bits := 4
			if strings.HasSuffix(rawmode, ";2") {
				bits = 2
			}
			perByte := 8 / bits
			mask := (1 << bits) - 1
			if len(r) < (xsize+perByte-1)/perByte {
				return truncatedError(0)
			}
			for x := 0; x < xsize; x++ {
				shift := 8 - bits*(x%perByte+1)
				put(x, 0, (r[x/perByte]>>uint(shift))&byte(mask))
			}
			return nil
		}
		if len(r) < xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			put(x, 0, r[x])
		}
		return nil
	case "R", "G", "B", "A", "C", "M", "Y", "K":
		if len(r) < xsize {
			return truncatedError(0)
		}
		b := band
		if b < 0 {
			switch im.Mode {
			case "LAB":
				switch base {
				case "L":
					b = 0
				case "A":
					b = 1
				case "B":
					b = 2
				default:
					b = 0
				}
			case "CMYK":
				switch base {
				case "M":
					b = 1
				case "Y":
					b = 2
				case "K":
					b = 3
				default:
					b = 0
				}
			default:
				switch base {
				case "G":
					b = 1
				case "B":
					b = 2
				case "A":
					b = 3
				default:
					b = 0
				}
			}
		}
		invert := strings.HasSuffix(rawmode, ";I")
		for x := 0; x < xsize; x++ {
			v := r[x]
			if invert {
				v = 255 - v
			}
			putBand(x, b, v)
		}
		return nil
	case "LA", "PA":
		if len(r) < 2*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			put(x, 0, r[2*x])
			put(x, 1, r[2*x+1])
		}
		return nil
	case "RGB":
		if len(r) < 3*xsize {
			return truncatedError(0)
		}
		rev := strings.HasSuffix(rawmode, "R")
		for x := 0; x < xsize; x++ {
			for s := 0; s < 3; s++ {
				v := r[3*x+s]
				if rev {
					v = bitReverse(v)
				}
				put(x, s, v)
			}
		}
		return nil
	case "BGR":
		if len(r) < 3*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			put(x, 0, r[3*x+2])
			put(x, 1, r[3*x+1])
			put(x, 2, r[3*x])
		}
		return nil
	case "RGBX":
		if len(r) < 4*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			put(x, 0, r[4*x])
			put(x, 1, r[4*x+1])
			put(x, 2, r[4*x+2])
		}
		return nil
	case "RGBA":
		if len(r) < 4*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			put(x, 0, r[4*x])
			put(x, 1, r[4*x+1])
			put(x, 2, r[4*x+2])
			put(x, 3, r[4*x+3])
		}
		return nil
	case "BGRA":
		if len(r) < 4*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			put(x, 0, r[4*x+2])
			put(x, 1, r[4*x+1])
			put(x, 2, r[4*x])
			put(x, 3, r[4*x+3])
		}
		return nil
	case "BGRX":
		if len(r) < 4*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			put(x, 0, r[4*x+2])
			put(x, 1, r[4*x+1])
			put(x, 2, r[4*x])
		}
		return nil
	case "CMYK":
		invert := strings.HasSuffix(rawmode, ";I")
		if len(r) < 4*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			for s := 0; s < 4; s++ {
				v := r[4*x+s]
				if invert {
					v = 255 - v
				}
				put(x, s, v)
			}
		}
		return nil
	case "YCbCr", "LAB":
		if len(r) < 3*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			put(x, 0, r[3*x])
			put(x, 1, r[3*x+1])
			put(x, 2, r[3*x+2])
		}
		return nil
	case "BGR;15", "BGR;16", "BGRA;15Z":
		bytesPer := 2
		if len(r) < bytesPer*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			v := int(binary.LittleEndian.Uint16(r[2*x:]))
			var r5, g6, b5 int
			if base == "BGR;16" {
				r5 = (v >> 11) & 0x1F
				g6 = (v >> 5) & 0x3F
				b5 = v & 0x1F
				put(x, 1, byte(g6*255/63))
			} else {
				r5 = (v >> 10) & 0x1F
				g6 = (v >> 5) & 0x1F
				b5 = v & 0x1F
				put(x, 1, byte(g6*255/31))
			}
			put(x, 0, byte(r5*255/31))
			put(x, 2, byte(b5*255/31))
			if base == "BGRA;15Z" {
				if v&0x8000 != 0 {
					put(x, 3, 0)
				} else {
					put(x, 3, 255)
				}
			}
		}
		return nil
	case "I;16", "I;16B":
		if len(r) < 2*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			var v uint16
			if base == "I;16B" {
				v = binary.BigEndian.Uint16(r[2*x:])
			} else {
				v = binary.LittleEndian.Uint16(r[2*x:])
			}
			setI16(x, v)
		}
		return nil
	case "I", "I;32":
		if len(r) < 4*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			setI(x, int32(binary.LittleEndian.Uint32(r[4*x:])))
		}
		return nil
	case "I;32B":
		if len(r) < 4*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			setI(x, int32(binary.BigEndian.Uint32(r[4*x:])))
		}
		return nil
	case "F", "F;32F", "F;32BF":
		if len(r) < 4*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			var u uint32
			if rawmode == "F;32BF" {
				u = binary.BigEndian.Uint32(r[4*x:])
			} else {
				u = binary.LittleEndian.Uint32(r[4*x:])
			}
			setF(x, math.Float32frombits(u))
		}
		return nil
	case "F;64BF":
		if len(r) < 8*xsize {
			return truncatedError(0)
		}
		for x := 0; x < xsize; x++ {
			u := binary.BigEndian.Uint64(r[8*x:])
			setF(x, float32(math.Float64frombits(u)))
		}
		return nil
	}
	return valueError("unknown raw mode for given image mode")
}

// rawTile decodes a single raw tile over the whole image.
func (im *Image) rawTile(data []byte, offset, stride, orientation int, rawmode string) error {
	if err := im.rawDecode(data, offset, stride, orientation, rawmode, [4]int{0, 0, im.Width, im.Height}, -1); err != nil {
		return err
	}
	im.ensurePix()
	return nil
}

// ensurePix makes sure a byte image has its pixel buffer allocated, so an empty
// decode still yields the zero-filled samples Pillow's core image has.
func (im *Image) ensurePix() {
	if im.pix == nil {
		n := im.Width * im.Height * im.samplesPerPixel()
		im.pix = make([]byte, n)
	}
}

// bitReverse is Unpack.c's BITFLIP table.
func bitReverse(v byte) byte {
	v = v>>4 | v<<4
	v = (v&0xCC)>>2 | (v&0x33)<<2
	v = (v&0xAA)>>1 | (v&0x55)<<1
	return v
}
