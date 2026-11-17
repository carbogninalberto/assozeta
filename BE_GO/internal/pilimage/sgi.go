package pilimage

// This file ports SgiImagePlugin, the SGI16 py decoder and the sgi_rle C
// decoder.

type sgiLoad struct {
	rawmode     string // MODES value
	bpc         int
	compression int
	width       int
	height      int
}

func sgiBands(mode string) int {
	switch mode {
	case "RGB":
		return 3
	case "RGBA":
		return 4
	}
	return 1
}

// loadSGI decodes the tile SgiImageFile._open sets up.
func (im *Image) loadSGI(s *sgiLoad) error {
	switch s.compression {
	case 0:
		if s.bpc == 2 {
			return im.loadSGI16()
		}
		pagesize := im.Width * im.Height * s.bpc
		for i, layer := range im.Mode {
			if err := im.rawDecode(im.data, 512+i*pagesize, 0, -1, string(layer),
				[4]int{0, 0, im.Width, im.Height}, i); err != nil {
				return err
			}
		}
		im.ensurePix()
		return nil
	case 1:
		return im.loadSGIRLE(s)
	}
	return osError("cannot load this image")
}

// loadSGI16 is SGI16Decoder.
func (im *Image) loadSGI16() error {
	pagesize := im.Width * im.Height
	bands := len(im.Mode)
	im.ensurePix()
	pos := 512
	for band := 0; band < bands; band++ {
		n := 2 * pagesize
		if pos+n > len(im.data) {
			return valueError("not enough image data")
		}
		row := im.data[pos : pos+n]
		pos += n
		for y := 0; y < im.Height; y++ {
			src := row[y*im.Width*2:]
			outY := im.Height - 1 - y
			for x := 0; x < im.Width; x++ {
				im.pix[(outY*im.Width+x)*len(im.Mode)+band] = src[2*x]
			}
		}
	}
	return nil
}

// loadSGIRLE is ImagingSgiRleDecode.
func (im *Image) loadSGIRLE(s *sgiLoad) error {
	bands := len(im.Mode)
	if len(im.data) < 512 {
		return decoderError(-1)
	}
	bufsize := len(im.data) - 512
	tablen := bands * im.Height
	if bufsize < 8*tablen {
		return decoderError(-1)
	}
	ptr := im.data[512:]
	starttab := make([]uint32, tablen)
	lentab := make([]uint32, tablen)
	for i := 0; i < tablen; i++ {
		starttab[i] = binaryBE32(ptr[4*i:])
		lentab[i] = binaryBE32(ptr[tablen*4+4*i:])
	}
	im.ensurePix()
	atom := 1
	if s.bpc == 2 {
		atom = 2
	}
	rowBytes := im.Width * bands * atom
	buffer := make([]byte, rowBytes)
	for rowno := 0; rowno < im.Height; rowno++ {
		y := rowno
		if s.rawmode != "" && s.compression == 1 {
			// orientation -1 for every SGI file
			y = im.Height - 1 - rowno
		}
		for chanIdx := 0; chanIdx < bands; chanIdx++ {
			off := starttab[rowno+chanIdx*im.Height]
			length := lentab[rowno+chanIdx*im.Height]
			if off < 512 {
				return decoderError(-1)
			}
			idx := int(off) - 512
			if idx > len(ptr) {
				return decoderError(-1)
			}
			status := expandSgiRow(buffer[chanIdx*atom:], ptr[idx:], int(length), bands, im.Width, len(ptr), atom)
			if status == -1 {
				return decoderError(-1)
			}
			if status == 1 {
				return nil
			}
		}
		// Unpack the interleaved row.
		if err := im.rawRow(buffer, 0, y, im.Width, s.rawmode, baseRawmode(s.rawmode), im.samplesPerPixel(), -1); err != nil {
			return err
		}
	}
	return nil
}

// expandSgiRow is SgiRleDecode.c expandrow/expandrow2.
func expandSgiRow(dest []byte, src []byte, n, z, xsize, endOfBuffer, atom int) int {
	x := 0
	if atom == 1 {
		pos := 0
		for ; n > 0; n-- {
			if pos > len(src)-1 && pos >= endOfBuffer {
				return -1
			}
			if pos >= len(src) {
				return -1
			}
			pixel := src[pos]
			pos++
			if n == 1 && pixel != 0 {
				return int(n)
			}
			count := int(pixel & 0x7f)
			if count == 0 {
				return 0
			}
			if x+count > xsize {
				return -1
			}
			x += count
			if pixel&0x80 != 0 {
				if pos+count > len(src) || pos+count > endOfBuffer {
					return -1
				}
				for i := 0; i < count; i++ {
					dest[i*z] = src[pos+i]
				}
				pos += count
			} else {
				if pos >= len(src) {
					return -1
				}
				v := src[pos]
				pos++
				for i := 0; i < count; i++ {
					dest[i*z] = v
				}
			}
			// dest advances count pixels; emulate with a rolling base.
			dest = dest[count*z:]
		}
		return 0
	}
	pos := 0
	for ; n > 0; n-- {
		if pos+1 >= len(src) {
			return -1
		}
		pixel := src[pos+1]
		pos += 2
		if n == 1 && pixel != 0 {
			return int(n)
		}
		count := int(pixel & 0x7f)
		if count == 0 {
			return 0
		}
		if x+count > xsize {
			return -1
		}
		x += count
		if pixel&0x80 != 0 {
			if pos+2*count > len(src) {
				return -1
			}
			for i := 0; i < count; i++ {
				dest[i*z*2] = src[pos+2*i]
				dest[i*z*2+1] = src[pos+2*i+1]
			}
			pos += 2 * count
		} else {
			if pos+2 > len(src) {
				return -1
			}
			for i := 0; i < count; i++ {
				dest[i*z*2] = src[pos]
				dest[i*z*2+1] = src[pos+1]
			}
			pos += 2
		}
		dest = dest[count*z*2:]
	}
	return 0
}

func binaryBE32(b []byte) uint32 {
	return uint32(b[0])<<24 | uint32(b[1])<<16 | uint32(b[2])<<8 | uint32(b[3])
}

// openSGI is SgiImageFile._open.
func openSGI(data []byte) (*Image, error) {
	if len(data) < 2 || !acceptSGI(data[:2]) {
		return nil, valueError("Not an SGI image file")
	}
	if len(data) < 12 {
		return nil, shortError()
	}
	s := data[:min(512, len(data))]
	compression := s[2]
	bpc := s[3]
	dimension := be16(s[4:])
	xsize := be16(s[6:])
	ysize := be16(s[8:])
	zsize := be16(s[10:])
	var rawmode string
	switch [3]int{int(bpc), dimension, zsize} {
	case [3]int{1, 1, 1}, [3]int{1, 2, 1}:
		rawmode = "L"
	case [3]int{2, 1, 1}, [3]int{2, 2, 1}:
		rawmode = "L;16B"
	case [3]int{1, 3, 3}:
		rawmode = "RGB"
	case [3]int{2, 3, 3}:
		rawmode = "RGB;16B"
	case [3]int{1, 3, 4}:
		rawmode = "RGBA"
	case [3]int{2, 3, 4}:
		rawmode = "RGBA;16B"
	default:
		return nil, valueError("Unsupported SGI image mode")
	}
	mode := rawmode
	if i := indexByte(rawmode, ';'); i >= 0 {
		mode = rawmode[:i]
	}
	im := newImage(data, "SGI", mode, xsize, ysize)
	im.loadFn = func() error {
		return im.loadSGI(&sgiLoad{rawmode: rawmode, bpc: int(bpc), compression: int(compression), width: xsize, height: ysize})
	}
	return im, nil
}

func indexByte(s string, c byte) int {
	for i := 0; i < len(s); i++ {
		if s[i] == c {
			return i
		}
	}
	return -1
}
