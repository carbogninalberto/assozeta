package pilimage

import (
	"encoding/binary"
	"math"
	"strconv"
)

// ppmState records the decoder the PPM plugin chose.
type ppmState struct {
	decoder   string // "raw", "ppm", "ppm_plain"
	rawmode   string
	maxval    int
	dataStart int
	plain     bool // P1, P2, P3
	flip      bool // Pf reads bottom-up
}

var ppmWhitespace = []byte(" \t\n\v\f\r")

func isPPMWhitespace(c byte) bool {
	switch c {
	case ' ', '\t', '\n', '\v', '\f', '\r':
		return true
	}
	return false
}

// acceptPPM is PpmImagePlugin._accept.
func acceptPPM(prefix []byte) bool {
	if len(prefix) < 2 || prefix[0] != 'P' {
		return false
	}
	switch prefix[1] {
	case '0', '1', '2', '3', '4', '5', '6', 'f', 'y':
		return true
	}
	return false
}

// ppmMagic reads the magic number: up to six bytes, stopping at whitespace.
func ppmMagic(data []byte) (string, int) {
	pos := 0
	start := 0
	for i := 0; i < 6; i++ {
		if pos >= len(data) {
			break
		}
		c := data[pos]
		if isPPMWhitespace(c) {
			magic := string(data[start:pos])
			pos++
			return magic, pos
		}
		pos++
	}
	return string(data[start:pos]), pos
}

// ppmToken is PpmImageFile._read_token.
func ppmToken(data []byte, pos *int) ([]byte, error) {
	var token []byte
	for len(token) <= 10 {
		if *pos >= len(data) {
			break
		}
		c := data[*pos]
		*pos++
		if isPPMWhitespace(c) {
			if len(token) == 0 {
				continue
			}
			break
		}
		if c == '#' {
			for {
				if *pos >= len(data) {
					break
				}
				d := data[*pos]
				*pos++
				if d == '\r' || d == '\n' {
					break
				}
			}
			continue
		}
		token = append(token, c)
	}
	if len(token) == 0 {
		return nil, valueError("Reached EOF while reading header")
	}
	if len(token) > 10 {
		return nil, valueError(pyReprBytes(append([]byte("Token too long in file header: "), token...)))
	}
	return token, nil
}

// pyInt is int(token) for a bytes token.
func pyInt(token []byte) (int, error) {
	s := string(token)
	v, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		// Python also accepts a single underscore between digits.
		if clean, ok := pyUnderscores(s); ok {
			if v2, err2 := strconv.ParseInt(clean, 10, 64); err2 == nil {
				return int(v2), nil
			}
		}
		return 0, valueError("invalid literal for int() with base 10: " + pyReprBytes(token))
	}
	return int(v), nil
}

func pyUnderscores(s string) (string, bool) {
	out := make([]byte, 0, len(s))
	for i := 0; i < len(s); i++ {
		if s[i] == '_' {
			if i == 0 || i == len(s)-1 || s[i-1] == '_' || !isDigit(s[i-1]) || !isDigit(s[i+1]) {
				return "", false
			}
			continue
		}
		out = append(out, s[i])
	}
	return string(out), true
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }

// openPPM is PpmImageFile._open.
func openPPM(data []byte) (*Image, error) {
	magic, pos := ppmMagic(data)
	var mode string
	switch magic {
	case "P1", "P4":
		mode = "1"
	case "P2", "P5":
		mode = "L"
	case "P3", "P6":
		mode = "RGB"
	case "P0CMYK":
		mode = "CMYK"
	case "Pf":
		mode = "F"
	case "PyP":
		mode = "P"
	case "PyRGBA":
		mode = "RGBA"
	case "PyCMYK":
		mode = "CMYK"
	default:
		return nil, syntaxError("not a PPM file")
	}
	wt, err := ppmToken(data, &pos)
	if err != nil {
		return nil, err
	}
	w, err := pyInt(wt)
	if err != nil {
		return nil, err
	}
	ht, err := ppmToken(data, &pos)
	if err != nil {
		return nil, err
	}
	h, err := pyInt(ht)
	if err != nil {
		return nil, err
	}
	im := newImage(data, "PPM", mode, w, h)
	st := &ppmState{}
	decoderName := "raw"
	if magic == "P1" || magic == "P2" || magic == "P3" {
		decoderName = "ppm_plain"
		st.plain = true
	}
	switch {
	case mode == "1":
		st.decoder = decoderName
		st.rawmode = "1;I"
	case mode == "F":
		tok, err := ppmToken(data, &pos)
		if err != nil {
			return nil, err
		}
		scale, err := strconv.ParseFloat(string(tok), 64)
		if err != nil {
			return nil, valueError("could not convert string to float: " + pyReprBytes(tok))
		}
		if scale == 0 || math.IsNaN(scale) || math.IsInf(scale, 0) {
			return nil, valueError("scale must be finite and non-zero")
		}
		st.decoder = "raw"
		if scale < 0 {
			st.rawmode = "F;32F"
		} else {
			st.rawmode = "F;32BF"
		}
		st.flip = true
	default:
		tok, err := ppmToken(data, &pos)
		if err != nil {
			return nil, err
		}
		maxval, err := pyInt(tok)
		if err != nil {
			return nil, err
		}
		if !(maxval > 0 && maxval < 65536) {
			return nil, valueError("maxval must be greater than 0 and less than 65536")
		}
		if maxval > 255 && mode == "L" {
			im.Mode = "I"
		}
		st.maxval = maxval
		st.rawmode = mode
		if decoderName != "ppm_plain" {
			if maxval == 65535 && mode == "L" {
				st.rawmode = "I;16B"
			} else if maxval != 255 {
				st.decoder = "ppm"
			}
		}
		if st.decoder == "" {
			st.decoder = decoderName
		}
	}
	if st.decoder == "" {
		st.decoder = decoderName
	}
	st.dataStart = pos
	im.ppm = st
	return im, nil
}

// loadPPM runs the decoder the plugin selected.
func (im *Image) loadPPM() error {
	st := im.ppm
	if st == nil {
		return osError("cannot load this image")
	}
	switch im.Mode {
	case "1", "L", "P":
		im.pix = make([]byte, im.Width*im.Height)
	case "RGB":
		im.pix = make([]byte, 3*im.Width*im.Height)
	case "RGBA", "CMYK":
		im.pix = make([]byte, 4*im.Width*im.Height)
	case "I":
		im.pixI = make([]int32, im.Width*im.Height)
	case "F":
		im.pixF = make([]float32, im.Width*im.Height)
	}
	if st.plain {
		return im.loadPPMPlain(st)
	}
	if st.decoder == "ppm" {
		return im.loadPPMBinaryScaled(st)
	}
	return im.loadPPMRaw(st)
}

// ppmRawBits is the rawmode's bits per pixel.
func ppmRawBits(rawmode string) int {
	switch rawmode {
	case "1;I":
		return 1
	case "L", "P":
		return 8
	case "I;16B":
		return 16
	case "RGB":
		return 24
	case "RGBA", "CMYK", "F;32F", "F;32BF":
		return 32
	}
	return 8
}

// loadPPMRaw simulates ImagingRawDecode for the uncompressed PPM data.
func (im *Image) loadPPMRaw(st *ppmState) error {
	data := im.data[st.dataStart:]
	rowBytes := (im.Width*ppmRawBits(st.rawmode) + 7) / 8
	p := 0
	for rowIndex := 0; rowIndex < im.Height; rowIndex++ {
		if len(data)-p < rowBytes {
			return truncatedError(len(data) - p)
		}
		row := data[p : p+rowBytes]
		y := rowIndex
		if st.flip {
			y = im.Height - 1 - rowIndex
		}
		base := y * im.Width
		switch st.rawmode {
		case "1;I":
			for x := 0; x < im.Width; x++ {
				if (row[x/8]>>(7-uint(x%8)))&1 != 0 {
					im.pix[base+x] = 0
				} else {
					im.pix[base+x] = 255
				}
			}
		case "L", "P":
			copy(im.pix[base:base+im.Width], row[:im.Width])
		case "I;16B":
			for x := 0; x < im.Width; x++ {
				im.pixI[base+x] = int32(binary.BigEndian.Uint16(row[2*x:]))
			}
		case "RGB":
			copy(im.pix[3*base:3*(base+im.Width)], row[:3*im.Width])
		case "RGBA", "CMYK":
			copy(im.pix[4*base:4*(base+im.Width)], row[:4*im.Width])
		case "F;32F":
			for x := 0; x < im.Width; x++ {
				im.pixF[base+x] = math.Float32frombits(binary.LittleEndian.Uint32(row[4*x:]))
			}
		case "F;32BF":
			for x := 0; x < im.Width; x++ {
				im.pixF[base+x] = math.Float32frombits(binary.BigEndian.Uint32(row[4*x:]))
			}
		}
		p += rowBytes
	}
	return nil
}

// loadPPMBinaryScaled is PpmImagePlugin.PpmDecoder.decode.
func (im *Image) loadPPMBinaryScaled(st *ppmState) error {
	data := im.data[st.dataStart:]
	maxval := st.maxval
	inBytes := 1
	if maxval < 256 {
		inBytes = 1
	} else {
		inBytes = 2
	}
	bands := 1
	switch im.Mode {
	case "RGB":
		bands = 3
	case "RGBA", "CMYK":
		bands = 4
	}
	outMax := 255
	if im.Mode == "I" {
		outMax = 65535
	}
	destLength := im.Width * im.Height * bands
	if im.Mode == "I" {
		destLength *= 4
	}
	var out []byte
	p := 0
	for len(out) < destLength {
		if p+inBytes*bands > len(data) {
			break
		}
		for b := 0; b < bands; b++ {
			var value int
			if inBytes == 1 {
				value = int(data[p+b])
			} else {
				value = int(binary.BigEndian.Uint16(data[p+b*2:]))
			}
			scaled := int(math.Min(float64(outMax), pyRound(float64(value)/float64(maxval)*float64(outMax))))
			if im.Mode == "I" {
				var buf [4]byte
				binary.LittleEndian.PutUint32(buf[:], uint32(scaled))
				out = append(out, buf[:]...)
			} else {
				out = append(out, byte(scaled))
			}
		}
		p += inBytes * bands
	}
	return im.ppmSetAsRaw(out, st)
}

// ppmSetAsRaw is PyDecoder.set_as_raw for a PPM decoder output buffer.
func (im *Image) ppmSetAsRaw(out []byte, st *ppmState) error {
	bands := im.samplesPerPixel()
	need := im.Width * im.Height * bands
	if im.Mode == "I" {
		need = 4 * im.Width * im.Height
	}
	if len(out) < need {
		return valueError("not enough image data")
	}
	switch im.Mode {
	case "I":
		for i := 0; i < im.Width*im.Height; i++ {
			im.pixI[i] = int32(binary.LittleEndian.Uint32(out[4*i:]))
		}
	case "F":
		// Not produced by a Python decoder.
	case "1":
		for i := 0; i < im.Width*im.Height; i++ {
			if out[i] != 0 {
				im.pix[i] = 255
			}
		}
	default:
		copy(im.pix, out[:need])
	}
	return nil
}

// loadPPMPlain is PpmImagePlugin.PpmPlainDecoder.decode.
func (im *Image) loadPPMPlain(st *ppmState) error {
	data := im.data[st.dataStart:]
	if im.Mode == "1" {
		total := im.Width * im.Height
		out := make([]byte, 0, total)
		p := 0
		// PpmPlainDecoder validates every token of the block it reads, then
		// truncates the collected data to the image size.
		for p < len(data) {
			c := data[p]
			p++
			if c == '#' {
				for p < len(data) && data[p] != '\n' && data[p] != '\r' {
					p++
				}
				if p < len(data) {
					p++
				}
				continue
			}
			if isPPMWhitespace(c) {
				continue
			}
			if c != '0' && c != '1' {
				return valueError(pyReprBytes([]byte("Invalid token for this mode: " + string([]byte{c}))))
			}
			if len(out) < total {
				if c == '0' {
					out = append(out, 0xff)
				} else {
					out = append(out, 0x00)
				}
			}
		}
		// set_as_raw(data, "1;8"): any non-zero byte is white.
		if len(out) < total {
			return valueError("not enough image data")
		}
		for i := 0; i < total; i++ {
			if out[i] != 0 {
				im.pix[i] = 255
			}
		}
		return nil
	}

	maxval := st.maxval
	outMax := 255
	if im.Mode == "I" {
		outMax = 65535
	}
	bands := im.samplesPerPixel()
	totalBytes := im.Width * im.Height * bands
	if im.Mode == "I" {
		totalBytes *= 4
	}
	var out []byte
	p := 0
	nextToken := func() ([]byte, bool) {
		var token []byte
		for p < len(data) {
			c := data[p]
			p++
			if c == '#' {
				for p < len(data) && data[p] != '\n' && data[p] != '\r' {
					p++
				}
				if p < len(data) {
					p++
				}
				continue
			}
			if isPPMWhitespace(c) {
				if len(token) == 0 {
					continue
				}
				return token, true
			}
			token = append(token, c)
			if len(token) > 10 {
				return token, true
			}
		}
		if len(token) > 0 {
			return token, true
		}
		return nil, false
	}
	for len(out) < totalBytes {
		token, ok := nextToken()
		if !ok {
			break
		}
		if len(token) > 10 {
			return valueError(pyReprBytes(append([]byte("Token too long found in data: "), token...)))
		}
		value, err := pyInt(token)
		if err != nil {
			return err
		}
		if value < 0 {
			return valueError("Channel value is negative: " + itoa(value))
		}
		if value > maxval {
			return valueError("Channel value too large for this mode: " + itoa(value))
		}
		scaled := int(math.Min(float64(outMax), pyRound(float64(value)/float64(maxval)*float64(outMax))))
		if im.Mode == "I" {
			var buf [4]byte
			binary.LittleEndian.PutUint32(buf[:], uint32(scaled))
			out = append(out, buf[:]...)
		} else {
			out = append(out, byte(scaled))
		}
	}
	return im.ppmSetAsRaw(out, st)
}

// pyRound is Python's round(float) without ndigits: round half to even.
func pyRound(v float64) float64 { return math.RoundToEven(v) }
