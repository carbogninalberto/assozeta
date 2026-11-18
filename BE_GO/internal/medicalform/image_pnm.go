// PNM loading with MuPDF's parser (fz_load_pnm): P1-P6 with one or two bytes
// per sample, P7/PAM with its tuple types and packed-bit workaround, and
// Pf/PF (PFM) through the same tone mapping fz_new_pixmap_from_float_data uses.
package medicalform

import (
	"math"
	"strconv"
)

const (
	pamUnknown = iota
	pamBW
	pamBWA
	pamGray
	pamGrayA
	pamRGB
	pamRGBA
	pamCMYK
	pamCMYKA
)

type pnmReader struct {
	data []byte
	pos  int
}

func (r *pnmReader) left() int { return len(r.data) - r.pos }

func decodePNM(file []byte) (imageAsset, bool) {
	r := &pnmReader{data: file}
	r.skipWhitesAndEols(false)
	start := r.pos
	if !r.readSignature() {
		return imageAsset{}, false
	}
	signature := string(file[start:r.pos])
	if !r.whitesAndEols(true) {
		return imageAsset{}, false
	}
	var asset imageAsset
	var ok bool
	switch signature {
	case "P1", "P2", "P3":
		asset, ok = r.readASCII(signature)
	case "P4", "P5", "P6":
		asset, ok = r.readBinary(signature)
	case "P7":
		asset, ok = r.readPAM()
	case "Pf", "PF":
		asset, ok = r.readPFM(signature == "PF")
	default:
		return imageAsset{}, false
	}
	if !ok || int(asset.width) <= 0 || int(asset.height) <= 0 {
		return imageAsset{}, false
	}
	return asset, true
}

func (r *pnmReader) readSignature() bool {
	if r.left() < 2 {
		return false
	}
	if r.data[r.pos] != 'P' {
		return false
	}
	c := r.data[r.pos+1]
	if !(c >= '1' && c <= '7') && c != 'F' && c != 'f' {
		return false
	}
	r.pos += 2
	return true
}

func (r *pnmReader) untilEOL(acceptCR bool) (int, bool) {
	if r.left() < 1 {
		return r.pos, false
	}
	for r.pos < len(r.data) {
		c := r.data[r.pos]
		if acceptCR {
			if c == '\r' || c == '\n' {
				break
			}
		} else if c == '\n' {
			break
		}
		r.pos++
	}
	return r.pos, true
}

func (r *pnmReader) readEOL(acceptCR bool) bool {
	if r.left() < 1 {
		return false
	}
	c := r.data[r.pos]
	if acceptCR {
		if c != '\r' && c != '\n' {
			return false
		}
		if c == '\r' {
			r.pos++
		}
		if r.pos < len(r.data) && r.data[r.pos] == '\n' {
			r.pos++
		}
		return true
	}
	if c != '\n' {
		return false
	}
	r.pos++
	return true
}

func (r *pnmReader) whites(required bool) bool {
	if required && (r.left() < 1 || (r.data[r.pos] != ' ' && r.data[r.pos] != '\t')) {
		return false
	}
	for r.pos < len(r.data) && (r.data[r.pos] == ' ' || r.data[r.pos] == '\t') {
		r.pos++
	}
	return true
}

func (r *pnmReader) whiteOrEOL() bool {
	if r.left() < 1 {
		return false
	}
	switch r.data[r.pos] {
	case ' ', '\t', '\r', '\n':
		r.pos++
		return true
	}
	return false
}

func (r *pnmReader) skipWhitesAndEols(required bool) bool {
	if required && (r.left() < 1 || !isWhiteEOL(r.data[r.pos])) {
		return false
	}
	for r.pos < len(r.data) && isWhiteEOL(r.data[r.pos]) {
		r.pos++
	}
	return true
}

func (r *pnmReader) whitesAndEols(required bool) bool { return r.skipWhitesAndEols(required) }

func isWhiteEOL(c byte) bool {
	return c == ' ' || c == '\t' || c == '\r' || c == '\n'
}

func (r *pnmReader) comment(acceptCR bool) bool {
	if r.left() < 1 {
		return false
	}
	if r.data[r.pos] != '#' {
		return true
	}
	_, ok := r.untilEOL(acceptCR)
	return ok
}

func (r *pnmReader) comments(acceptCR bool) bool {
	if r.left() < 1 {
		return false
	}
	for r.pos < len(r.data) && r.data[r.pos] == '#' {
		if !r.comment(acceptCR) {
			return false
		}
		if !r.readEOL(acceptCR) {
			return false
		}
	}
	return true
}

func (r *pnmReader) digit(number *int) bool {
	if r.left() < 1 {
		return false
	}
	c := r.data[r.pos]
	if c < '0' || c > '1' {
		return false
	}
	if number != nil {
		*number = int(c - '0')
	}
	r.pos++
	return true
}

func (r *pnmReader) integer(number *int) bool {
	if r.left() < 1 {
		return false
	}
	c := r.data[r.pos]
	if c < '0' || c > '9' {
		return false
	}
	for r.pos < len(r.data) && r.data[r.pos] >= '0' && r.data[r.pos] <= '9' {
		if number != nil {
			*number = *number*10 + int(r.data[r.pos]-'0')
		}
		r.pos++
	}
	return true
}

func (r *pnmReader) real(number *float32) bool {
	if r.left() < 1 {
		return false
	}
	c := r.data[r.pos]
	if c != '+' && c != '-' && (c < '0' || c > '9') {
		return false
	}
	start := r.pos
	for r.pos < len(r.data) {
		c = r.data[r.pos]
		if c == '+' || c == '-' || c == '.' || (c >= '0' && c <= '9') {
			r.pos++
			continue
		}
		break
	}
	value, err := strconv.ParseFloat(string(r.data[start:r.pos]), 32)
	if err != nil {
		value = 0
	}
	*number = float32(value)
	return true
}

func (r *pnmReader) token(tokens map[string]int, out *int) bool {
	if r.left() < 1 {
		return false
	}
	start := r.pos
	for r.pos < len(r.data) && !isWhiteEOL(r.data[r.pos]) {
		r.pos++
	}
	value, ok := tokens[string(r.data[start:r.pos])]
	if !ok {
		return false
	}
	*out = value
	return true
}

func mapColor(value, inmax, outmax int) int {
	f := float32(value) / float32(inmax)
	return int(f * float32(outmax))
}

func (r *pnmReader) readASCII(signature string) (imageAsset, bool) {
	bitmap := signature == "P1"
	channels := 1
	gray := true
	if signature == "P3" {
		channels, gray = 3, false
	}
	width, height, maxval := 0, 0, 0
	if !r.comments(true) {
		return imageAsset{}, false
	}
	if !r.integer(&width) || !r.whitesAndEols(true) {
		return imageAsset{}, false
	}
	if bitmap {
		if !r.integer(&height) || !r.whitesAndEols(true) {
			return imageAsset{}, false
		}
		maxval = 1
	} else {
		if !r.comments(true) || !r.integer(&height) || !r.whitesAndEols(true) {
			return imageAsset{}, false
		}
		maxval = 0
		if !r.comments(true) || !r.integer(&maxval) || !r.whiteOrEOL() {
			return imageAsset{}, false
		}
	}
	if maxval <= 0 || maxval >= 65536 || width <= 0 || height <= 0 || int64(width)*int64(height) > 1<<28 {
		return imageAsset{}, false
	}
	colour := make([]byte, 0, width*height*channels)
	for y := 0; y < height; y++ {
		for x := 0; x < width; x++ {
			if bitmap {
				r.skipWhitesAndEols(false)
				v := 0
				if !r.digit(&v) {
					return imageAsset{}, false
				}
				r.skipWhitesAndEols(false)
				if v != 0 {
					colour = append(colour, 0x00)
				} else {
					colour = append(colour, 0xff)
				}
			} else {
				for k := 0; k < channels; k++ {
					r.skipWhitesAndEols(false)
					v := 0
					if !r.integer(&v) {
						return imageAsset{}, false
					}
					r.skipWhitesAndEols(false)
					if v > maxval {
						v = maxval
					}
					colour = append(colour, byte(mapColor(v, maxval, 255)))
				}
			}
		}
	}
	return newSampleAsset(width, height, channels, colour, nil, gray, false), true
}

func (r *pnmReader) readBinary(signature string) (imageAsset, bool) {
	bitmap := signature == "P4"
	channels := 1
	gray := true
	if signature == "P6" {
		channels, gray = 3, false
	}
	width, height, maxval := 0, 0, 0
	if !r.comments(true) {
		return imageAsset{}, false
	}
	if !r.integer(&width) || !r.whitesAndEols(true) {
		return imageAsset{}, false
	}
	if bitmap {
		if !r.integer(&height) || !r.whitesAndEols(true) {
			return imageAsset{}, false
		}
		maxval = 1
	} else {
		if !r.comments(true) || !r.integer(&height) || !r.whitesAndEols(true) {
			return imageAsset{}, false
		}
		if !r.comments(true) || !r.integer(&maxval) || !r.whiteOrEOL() {
			return imageAsset{}, false
		}
	}
	if maxval <= 0 || maxval >= 65536 || width <= 0 || height <= 0 || int64(width)*int64(height) > 1<<28 {
		return imageAsset{}, false
	}
	bitdepth := 0
	for v := maxval; v != 0; v >>= 1 {
		bitdepth++
	}
	var span int
	if bitdepth == 1 {
		span = (channels*width + 7) / 8
	} else {
		span = width * channels * ((bitdepth-1)/8 + 1)
	}
	if span <= 0 || r.left() < span*height {
		return imageAsset{}, false
	}
	if !bitmap && maxval < 255 && bitdepth == 1 && r.left() < width*height*channels {
		// MuPDF's span check is the packed width here, but the decoder reads
		// one byte per sample; keep that read inside the file.
		return imageAsset{}, false
	}
	p := r.pos
	colour := make([]byte, 0, width*height*channels)
	if maxval == 255 {
		colour = append(colour, r.data[p:p+width*height*channels]...)
		p += width * height * channels
	} else if bitmap {
		for y := 0; y < height; y++ {
			for x := 0; x < width; x++ {
				if r.data[p]&(1<<uint(7-x&7)) != 0 {
					colour = append(colour, 0x00)
				} else {
					colour = append(colour, 0xff)
				}
				if x&7 == 7 {
					p++
				}
			}
			if width&7 != 0 {
				p++
			}
		}
	} else if maxval < 255 {
		for i := 0; i < width*height*channels; i++ {
			colour = append(colour, byte(mapColor(int(r.data[p]), maxval, 255)))
			p++
		}
	} else {
		for i := 0; i < width*height*channels; i++ {
			value := int(r.data[p])<<8 | int(r.data[p+1])
			p += 2
			colour = append(colour, byte(mapColor(value, maxval, 255)))
		}
	}
	r.pos = p
	return newSampleAsset(width, height, channels, colour, nil, gray, false), true
}

func (r *pnmReader) readPAM() (imageAsset, bool) {
	width, height, depth, maxval, tupletype := 0, 0, 0, 0, pamUnknown
	seen := [6]bool{}
	tokens := map[string]int{
		"WIDTH": 1, "HEIGHT": 2, "DEPTH": 3, "MAXVAL": 4, "TUPLTYPE": 5, "ENDHDR": 6,
	}
	tupleTypes := map[string]int{
		"BLACKANDWHITE": pamBW, "BLACKANDWHITE_ALPHA": pamBWA,
		"GRAYSCALE": pamGray, "GRAYSCALE_ALPHA": pamGrayA,
		"RGB": pamRGB, "RGB_ALPHA": pamRGBA, "CMYK": pamCMYK, "CMYK_ALPHA": pamCMYKA,
	}
	for r.pos < len(r.data) {
		lineStart := r.pos
		eol, ok := r.untilEOL(false)
		if !ok {
			return imageAsset{}, false
		}
		r.pos = lineStart
		r.whites(false)
		token := 0
		if r.pos < eol && r.data[r.pos] != '#' {
			tokenNameStart := r.pos
			for r.pos < eol && !isWhiteEOL(r.data[r.pos]) {
				r.pos++
			}
			token = tokens[string(r.data[tokenNameStart:r.pos])]
			if token == 0 {
				return imageAsset{}, false
			}
			if seen[token-1] {
				return imageAsset{}, false
			}
			seen[token-1] = true
			if token != 6 {
				if !r.whites(true) {
					return imageAsset{}, false
				}
				switch token {
				case 1:
					width = 0
					if !r.integer(&width) {
						return imageAsset{}, false
					}
				case 2:
					height = 0
					if !r.integer(&height) {
						return imageAsset{}, false
					}
				case 3:
					depth = 0
					if !r.integer(&depth) {
						return imageAsset{}, false
					}
				case 4:
					maxval = 0
					if !r.integer(&maxval) {
						return imageAsset{}, false
					}
				case 5:
					start := r.pos
					for r.pos < eol && !isWhiteEOL(r.data[r.pos]) {
						r.pos++
					}
					value, ok := tupleTypes[string(r.data[start:r.pos])]
					if !ok {
						return imageAsset{}, false
					}
					tupletype = value
				}
			}
			r.whites(false)
		}
		if r.pos < eol && r.data[r.pos] == '#' {
			if !r.comment(false) {
				return imageAsset{}, false
			}
		}
		if r.pos > eol {
			r.pos = eol
		}
		if !r.readEOL(false) {
			return imageAsset{}, false
		}
		if token == 6 {
			break
		}
	}
	if tupletype == pamUnknown {
		switch depth {
		case 1:
			tupletype = pamGray
			if maxval == 1 {
				tupletype = pamBW
			}
		case 2:
			tupletype = pamGrayA
			if maxval == 1 {
				tupletype = pamBWA
			}
		case 3:
			tupletype = pamRGB
		case 4:
			tupletype = pamCMYK
		case 5:
			tupletype = pamCMYKA
		default:
			return imageAsset{}, false
		}
	}
	if tupletype == pamBW && maxval > 1 {
		tupletype = pamGray
	} else if tupletype == pamGray && maxval == 1 {
		tupletype = pamBW
	} else if tupletype == pamBWA && maxval > 1 {
		tupletype = pamGrayA
	} else if tupletype == pamGrayA && maxval == 1 {
		tupletype = pamBWA
	}
	channels := 3
	gray, cmyk, alpha, bitmap := false, false, false, false
	minval, maxvalLimit := 1, 65535
	switch tupletype {
	case pamBWA:
		alpha = true
		fallthrough
	case pamBW:
		channels, gray, maxvalLimit, bitmap = 1, true, 1, true
	case pamGrayA:
		alpha = true
		fallthrough
	case pamGray:
		channels, gray, minval = 1, true, 2
	case pamRGBA:
		alpha = true
		fallthrough
	case pamRGB:
	case pamCMYKA:
		alpha = true
		fallthrough
	case pamCMYK:
		channels, cmyk = 4, true
	default:
		return imageAsset{}, false
	}
	if depth != channels+boolInt(alpha) || maxval < minval || maxval > maxvalLimit ||
		width <= 0 || height <= 0 || int64(width)*int64(height) > 1<<28 {
		return imageAsset{}, false
	}
	size := width * height * (channels + boolInt(alpha))
	p := r.pos
	packed := false
	if maxval == 1 {
		ePacked := p + size/8
		if ePacked < len(r.data)-1 && r.data[ePacked] == 'P' && r.data[ePacked+1] >= '0' && r.data[ePacked+1] <= '7' {
			r.data = r.data[:ePacked]
		}
		if len(r.data)-p < size {
			packed = true
		}
	}
	if packed && len(r.data)-p < size/8 {
		return imageAsset{}, false
	}
	if !packed {
		bytesPerSample := 2
		if maxval < 256 {
			bytesPerSample = 1
		}
		if len(r.data)-p < size*bytesPerSample {
			return imageAsset{}, false
		}
	}
	colour := make([]byte, 0, width*height*channels)
	var alphaPlane []byte
	if alpha {
		alphaPlane = make([]byte, 0, width*height)
	}
	if maxval == 255 {
		all := r.data[p : p+size]
		for i := 0; i < width*height; i++ {
			colour = append(colour, all[i*(channels+boolInt(alpha)):i*(channels+boolInt(alpha))+channels]...)
			if alpha {
				alphaPlane = append(alphaPlane, all[i*(channels+1)+channels])
			}
		}
		p += size
	} else if bitmap && packed {
		for y := 0; y < height; y++ {
			for x := 0; x < width; x++ {
				for k := 0; k < channels+boolInt(alpha); k++ {
					bit := byte(0)
					if p < len(r.data) {
						bit = r.data[p]
					}
					if bit&(1<<uint(7-x&7)) != 0 {
						if k < channels {
							colour = append(colour, 0x00)
						} else {
							alphaPlane = append(alphaPlane, 0x00)
						}
					} else {
						if k < channels {
							colour = append(colour, 0xff)
						} else {
							alphaPlane = append(alphaPlane, 0xff)
						}
					}
					if x&7 == 7 {
						p++
					}
				}
				if width&7 != 0 {
					p++
				}
			}
		}
	} else if bitmap {
		for i := 0; i < size; i++ {
			v := byte(0)
			if r.data[p] != 0 {
				v = 0xff
			}
			p++
			if alpha && i%(channels+1) == channels {
				alphaPlane = append(alphaPlane, v)
			} else {
				colour = append(colour, v)
			}
		}
	} else if maxval < 255 {
		for i := 0; i < size; i++ {
			v := byte(mapColor(int(r.data[p]), maxval, 255))
			p++
			if alpha && i%(channels+1) == channels {
				alphaPlane = append(alphaPlane, v)
			} else {
				colour = append(colour, v)
			}
		}
	} else {
		for i := 0; i < size; i++ {
			v := byte(mapColor(int(r.data[p])<<8|int(r.data[p+1]), maxval, 255))
			p += 2
			if alpha && i%(channels+1) == channels {
				alphaPlane = append(alphaPlane, v)
			} else {
				colour = append(colour, v)
			}
		}
	}
	r.pos = p
	return newSampleAsset(width, height, channels, colour, alphaPlane, gray, cmyk), true
}

func (r *pnmReader) readPFM(rgb bool) (imageAsset, bool) {
	width, height := 0, 0
	var scale float32
	if !r.integer(&width) || !r.whitesAndEols(true) {
		return imageAsset{}, false
	}
	if !r.integer(&height) || !r.whitesAndEols(true) {
		return imageAsset{}, false
	}
	if !r.real(&scale) || !r.whiteOrEOL() {
		return imageAsset{}, false
	}
	little := scale < 0
	if little {
		scale = -scale
	}
	if width <= 0 || height <= 0 || int64(width)*int64(height)*3 > 1<<28 {
		return imageAsset{}, false
	}
	channels := 1
	gray := true
	if rgb {
		channels, gray = 3, false
	}
	count := width * height * channels
	if r.left() < count*4 {
		return imageAsset{}, false
	}
	samples := make([]float32, count)
	for i := 0; i < count; i++ {
		p := r.pos
		var bits uint32
		if little {
			bits = uint32(r.data[p]) | uint32(r.data[p+1])<<8 | uint32(r.data[p+2])<<16 | uint32(r.data[p+3])<<24
		} else {
			bits = uint32(r.data[p+3]) | uint32(r.data[p+2])<<8 | uint32(r.data[p+1])<<16 | uint32(r.data[p])<<24
		}
		r.pos += 4
		samples[i] = math.Float32frombits(bits) / scale
	}
	colour := floatToneMap(samples, width, height, channels)
	return newSampleAsset(width, height, channels, colour, nil, gray, false), true
}

// floatToneMap is fz_new_pixmap_from_float_data: Kim-Kautz tone mapping with
// 1% and 99% percentiles, then a vertical flip (the first sample row is the
// bottom of the pixmap).
func floatToneMap(samples []float32, width, height, channels int) []byte {
	nsamples := len(samples)
	mu := float32(0)
	minsample, maxsample := float32(math.MaxFloat32), -float32(math.MaxFloat32)
	const minLog = float32(0.3)
	const maxLog = float32(300)
	for _, v := range samples {
		logv := float32(math.Log(float64(nonZero(v))))
		mu += logv
		if logv < minsample {
			minsample = logv
		}
		if logv > maxsample {
			maxsample = logv
		}
	}
	mu /= float32(nsamples)
	d0 := maxsample - minsample
	k1 := (float32(math.Log(float64(maxLog))) - float32(math.Log(float64(minLog)))) / d0
	sigma := d0 / 3
	sigmasq2 := sigma * sigma * 2
	for k, v := range samples {
		samplemu := v - mu
		fw := float32(math.Exp(float64(-samplemu * samplemu / sigmasq2)))
		k2 := (1-k1)*fw + k1
		samples[k] = float32(math.Exp(float64(0.5*k2*(float32(math.Log(float64(nonZero(v))))-mu) + mu)))
	}
	minprct, maxprct := percentiles(samples)
	span := maxprct - minprct
	out := make([]byte, 0, nsamples)
	rowLen := width * channels
	for y := height - 1; y >= 0; y-- {
		for _, v := range samples[y*rowLen : (y+1)*rowLen] {
			if v < minprct {
				v = minprct
			}
			if v > maxprct {
				v = maxprct
			}
			out = append(out, byte(255*(v-minprct)/span))
		}
	}
	return out
}

func nonZero(v float32) float32 {
	if v == 0 {
		// FLT_MIN is the smallest normal float, not the smallest denormal.
		return math.Float32frombits(0x00800000)
	}
	return v
}

func percentiles(samples []float32) (float32, float32) {
	minval, maxval := samples[0], samples[0]
	for _, v := range samples[1:] {
		if v < minval {
			minval = v
		}
		if v > maxval {
			maxval = v
		}
	}
	if minval-maxval == 0 {
		return minval, minval
	}
	size := len(samples)
	if size > 65535 {
		size = 65535
	}
	scale := float32(size-1) / (maxval - minval)
	hist := make([]int, size)
	for _, v := range samples {
		index := int(uint16(scale * (v - minval)))
		if index >= size {
			index = size - 1
		}
		hist[index]++
	}
	return calcPercentile(hist, 0.01*float32(len(samples)), scale, minval, maxval),
		calcPercentile(hist, 0.99*float32(len(samples)), scale, minval, maxval)
}

func calcPercentile(hist []int, threshold, scale, minval, maxval float32) float32 {
	count := 0
	k := 0
	for float32(count) < threshold && k < len(hist) {
		count += hist[k]
		k++
	}
	var prct float32
	if k <= 0 {
		prct = float32(k)
	} else {
		c0 := float32(count) - threshold
		c1 := threshold - float32(count-hist[k-1])
		prct = (c1*float32(k) + c0*float32(k-1)) / (c0 + c1)
	}
	prct /= scale
	prct += minval
	if prct < minval {
		return minval
	}
	if prct > maxval {
		return maxval
	}
	return prct
}
