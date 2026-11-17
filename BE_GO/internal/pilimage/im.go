package pilimage

import "strconv"

// This file ports ImImagePlugin.

type imTile struct {
	rawmode    string
	offset     int
	threeTiles bool // RGB;T / RYB;T
	bit        int  // "bit" decoder bits, 0 when unused
}

// imOpen maps OPEN mode descriptions to (mode, rawmode).
var imOpen = map[string][2]string{
	"0 1 image":       {"1", "1"},
	"L 1 image":       {"1", "1"},
	"Greyscale image": {"L", "L"},
	"Grayscale image": {"L", "L"},
	"RGB image":       {"RGB", "RGB;L"},
	"RLB image":       {"RGB", "RLB"},
	"RYB image":       {"RGB", "RLB"},
	"B1 image":        {"1", "1"},
	"B2 image":        {"P", "P;2"},
	"B4 image":        {"P", "P;4"},
	"X 24 image":      {"RGB", "RGB"},
	"L 32 S image":    {"I", "I;32"},
	"L 32 F image":    {"F", "F;32"},
	"RGB3 image":      {"RGB", "RGB;T"},
	"RYB3 image":      {"RGB", "RYB;T"},
	"LA image":        {"LA", "LA;L"},
	"PA image":        {"LA", "PA;L"},
	"RGBA image":      {"RGBA", "RGBA;L"},
	"RGBX image":      {"RGB", "RGBX;L"},
	"CMYK image":      {"CMYK", "CMYK;L"},
	"YCC image":       {"YCbCr", "YCbCr;L"},
}

func init() {
	for _, i := range []string{"8", "8S", "16", "16S", "32", "32F"} {
		imOpen["L "+i+" image"] = [2]string{"F", "F;" + i}
		imOpen["L*"+i+" image"] = [2]string{"F", "F;" + i}
	}
	for _, i := range []string{"16", "16L", "16B"} {
		imOpen["L "+i+" image"] = [2]string{"I;" + i, "I;" + i}
		imOpen["L*"+i+" image"] = [2]string{"I;" + i, "I;" + i}
	}
	imOpen["L 32S image"] = [2]string{"I", "I;32S"}
	imOpen["L*32S image"] = [2]string{"I", "I;32S"}
	for j := 2; j <= 32; j++ {
		imOpen["L*"+strconv.Itoa(j)+" image"] = [2]string{"F", "F;" + strconv.Itoa(j)}
	}
}

var imTags = map[string]bool{
	"Comment": true, "Date": true, "Digitalization equipment": true,
	"File size (no of images)": true, "Lut": true, "Name": true,
	"Scale (x,y)": true, "Image size (x*y)": true, "Image type": true,
}

// imNumber is ImImagePlugin.number: int() first, then float().
func imNumber(s string) float64 {
	if n, err := strconv.Atoi(s); err == nil {
		return float64(n)
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		_ = err
		return 0
	}
	return f
}

// loadIM is the raw/"bit" tiles ImImageFile._open sets.
func (im *Image) loadIM(t imTile) error {
	if t.bit != 0 {
		return im.loadIMBit(t)
	}
	if t.threeTiles {
		size := im.Width * im.Height
		if err := im.rawDecode(im.data, t.offset, 0, -1, "G", [4]int{0, 0, im.Width, im.Height}, 1); err != nil {
			return err
		}
		if err := im.rawDecode(im.data, t.offset+size, 0, -1, "R", [4]int{0, 0, im.Width, im.Height}, 0); err != nil {
			return err
		}
		if err := im.rawDecode(im.data, t.offset+2*size, 0, -1, "B", [4]int{0, 0, im.Width, im.Height}, 2); err != nil {
			return err
		}
		im.ensurePix()
		return nil
	}
	return im.rawTile(im.data, t.offset, 0, -1, t.rawmode)
}

// loadIMBit is the "bit" decoder for F;N modes with N not in 8/16/32.
func (im *Image) loadIMBit(t imTile) error {
	bits := t.bit
	im.ensurePix()
	pos := t.offset
	data := im.data
	var bitbuf uint32
	var nbits int
	readBits := func(n int) (int, bool) {
		for nbits < n {
			if pos >= len(data) {
				return 0, false
			}
			bitbuf = bitbuf<<8 | uint32(data[pos])
			pos++
			nbits += 8
		}
		v := int(bitbuf>>uint(nbits-n)) & ((1 << uint(n)) - 1)
		nbits -= n
		return v, true
	}
	readSigned := func(n int) (int, bool) {
		v, ok := readBits(n)
		if !ok {
			return 0, false
		}
		if v >= 1<<uint(n-1) {
			v -= 1 << uint(n)
		}
		return v, true
	}
	n := im.Width * im.Height
	for i := 0; i < n; i++ {
		switch bits {
		case 8:
			v, ok := readSigned(8)
			if !ok {
				return truncatedError(0)
			}
			im.pixF[i] = float32(v)
		case 16:
			v, ok := readSigned(16)
			if !ok {
				return truncatedError(0)
			}
			im.pixF[i] = float32(float64(v) * 8 / 255)
		case 24:
			v, ok := readSigned(24)
			if !ok {
				return truncatedError(0)
			}
			im.pixF[i] = float32(float64(v) * 8 / 65535)
		case 32:
			v, ok := readSigned(32)
			if !ok {
				return truncatedError(0)
			}
			im.pixF[i] = float32(float64(v) * 8 / 16777215)
		default:
			v, ok := readSigned(bits)
			if !ok {
				return truncatedError(0)
			}
			im.pixF[i] = float32(float64(v) * 8 / float64((int(1)<<uint(bits))-1))
		}
	}
	return nil
}

// openIM is ImImageFile._open.
func openIM(data []byte) (*Image, error) {
	if !containsByte(data[:min(100, len(data))], '\n') {
		return nil, syntaxError("not an IM file")
	}
	pos := 0
	mode := "L"
	rawmode := "L"
	width, height := 512, 512
	n := 0
	hasLUT := false
	paletteBytes := []byte(nil)
	readLine := func() []byte {
		if pos >= len(data) {
			return nil
		}
		end := pos
		for end < len(data) && data[end] != '\n' {
			end++
		}
		line := data[pos:end]
		pos = end
		if pos < len(data) {
			pos++
		}
		return line
	}
	for {
		if pos >= len(data) {
			break
		}
		c := data[pos]
		if c == '\r' {
			pos++
			continue
		}
		if c == 0 || c == 0x1a {
			break
		}
		pos++
		line := append([]byte{c}, readLine()...)
		if len(line) > 100 {
			return nil, syntaxError("not an IM file")
		}
		line = bytesTrimRightWhitespace(line)
		s := string(line)
		if len(s) == 0 || !((s[0] >= 'A' && s[0] <= 'Z') || (s[0] >= 'a' && s[0] <= 'z')) {
			return nil, syntaxError("Syntax error in IM header: " + asciiReplace(s))
		}
		colon := -1
		for i := 1; i < len(s); i++ {
			if s[i] == ':' {
				colon = i
				break
			}
		}
		if colon < 0 {
			return nil, syntaxError("Syntax error in IM header: " + asciiReplace(s))
		}
		k := s[:colon]
		raw := string(bytesTrimLeftWhitespace(bytesTrimRightWhitespace([]byte(s[colon+1:]))))
		switch k {
		case "File size (no of images)", "Scale (x,y)", "Image size (x*y)":
			parts := splitCommaOrStar(raw)
			if k == "Image size (x*y)" {
				if len(parts) >= 2 {
					width = int(imNumber(parts[0]))
					height = int(imNumber(parts[1]))
				} else {
					return nil, valueError("not enough values to unpack (expected 2, got " + itoa(len(parts)) + ")")
				}
			}
		case "Image type":
			if m, ok := imOpen[raw]; ok {
				mode = m[0]
				rawmode = m[1]
			} else {
				mode = latin1Decode(raw)
			}
		case "Lut":
			hasLUT = true
		}
		if imTags[k] {
			n++
		}
	}
	if n == 0 {
		return nil, syntaxError("Not an IM file")
	}
	// Skip forward to the image data.
	for pos < len(data) && data[pos] != 0x1a {
		pos++
	}
	if pos >= len(data) {
		return nil, syntaxError("File truncated")
	}
	pos++ // the 0x1a marker
	if hasLUT {
		if pos+768 > len(data) {
			paletteBytes = append([]byte{}, data[pos:]...)
		} else {
			paletteBytes = append([]byte{}, data[pos:pos+768]...)
		}
		if len(paletteBytes) < 768 {
			paletteBytes = append(paletteBytes, make([]byte, 768-len(paletteBytes))...)
		}
		pos += 768
		if mode == "L" || mode == "LA" || mode == "P" || mode == "PA" {
			if !greyscaleOf(paletteBytes) {
				if mode == "L" || mode == "P" {
					mode = "P"
					rawmode = "P"
				} else {
					mode = "PA"
					rawmode = "PA;L"
				}
			}
		}
	}
	im := newImage(data, "IM", mode, width, height)
	if hasLUT && !greyscaleOf(paletteBytes) && (mode == "P" || mode == "PA") {
		im.sunPalette(paletteBytes, "RGB;L")
	}
	t := imTile{rawmode: rawmode, offset: pos}
	if len(rawmode) > 2 && rawmode[0] == 'F' && rawmode[1] == ';' {
		bits, err := strconv.Atoi(rawmode[2:])
		if err == nil && bits != 8 && bits != 16 && bits != 32 {
			t.bit = bits
		}
	}
	if rawmode == "RGB;T" || rawmode == "RYB;T" {
		t.threeTiles = true
	}
	im.loadFn = func() error { return im.loadIM(t) }
	return im, nil
}

func greyscaleOf(pal []byte) bool {
	for i := 0; i < 256; i++ {
		if pal[i] != pal[i+256] || pal[i] != pal[i+512] {
			return false
		}
	}
	return true
}

func splitCommaOrStar(s string) []string {
	var out []string
	cur := ""
	for i := 0; i < len(s); i++ {
		if s[i] == ',' || s[i] == '*' {
			out = append(out, cur)
			cur = ""
			continue
		}
		cur += string(s[i])
	}
	if cur != "" || len(out) > 0 {
		out = append(out, cur)
	}
	return out
}

func containsByte(b []byte, c byte) bool {
	for _, x := range b {
		if x == c {
			return true
		}
	}
	return false
}

func bytesTrimLeftWhitespace(b []byte) []byte {
	i := 0
	for i < len(b) {
		switch b[i] {
		case ' ', '\t', '\r', '\n', '\v', '\f':
			i++
			continue
		}
		break
	}
	return b[i:]
}

func latin1Decode(s string) string {
	out := make([]rune, 0, len(s))
	for i := 0; i < len(s); i++ {
		out = append(out, rune(s[i]))
	}
	return string(out)
}

func asciiReplace(s string) string {
	out := make([]byte, 0, len(s))
	for i := 0; i < len(s); i++ {
		if s[i] < 0x80 {
			out = append(out, s[i])
		} else {
			out = append(out, 0x3f)
		}
	}
	return string(out)
}
