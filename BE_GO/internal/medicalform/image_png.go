// PNG loading with MuPDF's leniency: chunk CRC values are never checked, an
// unknown chunk is skipped (critical or not), a missing IEND is a warning, data
// after IEND is ignored, and a file whose image data decodes short is filled
// with 0xff. A chunk that runs past the end of the file, a zero-length IDAT
// while the zlib stream is open and bytes the stream leaves unconsumed are
// refused, as png_read_image refuses them.
package medicalform

import (
	"bytes"
	"compress/zlib"
	"errors"
	"io"
)

type pngInfo struct {
	width, height int
	depth, n      int
	gray, indexed bool
	interlace     int
	samples       []byte
	palette       [256 * 4]byte
	transparency  bool
	trns          [4]int
}

func be32(p []byte) int {
	return int(p[0])<<24 | int(p[1])<<16 | int(p[2])<<8 | int(p[3])
}

func decodePNG(file []byte) (imageAsset, bool) {
	info, ok := pngRead(file)
	if !ok {
		return imageAsset{}, false
	}
	if info.indexed {
		// fz_unpack_tile first expands the packed indices (scale 1 keeps the
		// index value), then png_expand_palette looks each one up.
		stride := (info.width*info.depth + 7) / 8
		colour := make([]byte, 0, info.width*info.height*3)
		var alpha []byte
		if info.transparency {
			alpha = make([]byte, 0, info.width*info.height)
		}
		for y := 0; y < info.height; y++ {
			line := info.samples[y*stride:]
			for x := 0; x < info.width; x++ {
				index := pngComponent(line, x, info.depth) * 4
				colour = append(colour, info.palette[index], info.palette[index+1], info.palette[index+2])
				if alpha != nil {
					alpha = append(alpha, info.palette[index+3])
				}
			}
		}
		return newSampleAsset(info.width, info.height, 3, colour, alpha, false, false), true
	}
	return info.asset(), true
}

// asset unpacks the samples the way fz_unpack_tile does for fz_load_png.
func (info *pngInfo) asset() imageAsset {
	gray := info.gray
	channels := 3
	if gray {
		channels = 1
	}
	colour := make([]byte, 0, info.width*info.height*channels)
	var alpha []byte
	sourceChannels := info.n
	// fz_unpack_tile pads an image whose destination has an alpha channel but
	// whose source has none (a tRNS colour key) with 255.
	haveAlpha := info.n == 2 || info.n == 4 || info.transparency
	if haveAlpha {
		alpha = make([]byte, 0, info.width*info.height)
	}
	stride := (info.width*info.n*info.depth + 7) / 8
	for y := 0; y < info.height; y++ {
		line := info.samples[y*stride:]
		for x := 0; x < info.width; x++ {
			values := make([]int, sourceChannels)
			for k := 0; k < sourceChannels; k++ {
				values[k] = pngComponent(line, x*sourceChannels+k, info.depth)
			}
			switch info.depth {
			case 1:
				for k := range values {
					values[k] *= 255
				}
			case 2:
				for k := range values {
					values[k] *= 85
				}
			case 4:
				for k := range values {
					values[k] *= 17
				}
			case 16:
				for k := range values {
					values[k] >>= 8
				}
			}
			switch sourceChannels {
			case 1:
				colour = append(colour, byte(values[0]))
				if haveAlpha {
					alpha = append(alpha, 255)
				}
			case 2:
				colour = append(colour, byte(values[0]))
				alpha = append(alpha, byte(values[1]))
			case 3:
				colour = append(colour, byte(values[0]), byte(values[1]), byte(values[2]))
				if haveAlpha {
					alpha = append(alpha, 255)
				}
			case 4:
				colour = append(colour, byte(values[0]), byte(values[1]), byte(values[2]))
				alpha = append(alpha, byte(values[3]))
			}
			// png_mask_transparency zeroes the alpha of a colour-key match.
			if info.transparency && !info.indexed && pngKeyMatch(line, x, info) {
				alpha[len(alpha)-1] = 0
			}
		}
	}
	return newSampleAsset(info.width, info.height, channels, colour, alpha, gray, false)
}

func pngKeyMatch(line []byte, x int, info *pngInfo) bool {
	for k := 0; k < info.n; k++ {
		if pngComponent(line, x*info.n+k, info.depth) != info.trns[k] {
			return false
		}
	}
	return true
}

func pngComponent(line []byte, x, depth int) int {
	switch depth {
	case 1:
		return int(line[x>>3]>>(7-uint(x&7))) & 1
	case 2:
		return int(line[x>>2]>>(2*(3-uint(x&3)))) & 3
	case 4:
		return int(line[x>>1]>>(4*(1-uint(x&1)))) & 15
	case 8:
		return int(line[x])
	case 16:
		return int(line[x<<1])<<8 | int(line[(x<<1)+1])
	}
	return 0
}

func pngRead(file []byte) (pngInfo, bool) {
	var info pngInfo
	for i := range info.palette {
		info.palette[i] = 0xff
	}
	total := len(file)
	if total < 20 || !bytes.HasPrefix(file, []byte("\x89PNG\r\n\x1a\n")) {
		return info, false
	}
	p := 8
	total -= 8
	size := be32(file[p:])
	if size > total-12 {
		return info, false
	}
	if string(file[p+4:p+8]) != "IHDR" || !pngReadIHDR(&info, file[p+8:p+8+size]) {
		return info, false
	}
	p += size + 12
	total -= size + 12

	var payload []byte
	type idatChunk struct {
		empty bool
	}
	var chunks []idatChunk
	idatSeen := false

	var stride int
	if info.interlace == 0 {
		stride = (info.width*info.n*info.depth + 7) / 8
		info.samples = make([]byte, info.height*(1+stride))
	} else {
		passW, passH, passOfs := pngPasses(&info)
		info.samples = make([]byte, passOfs[7])
		_ = passW
		_ = passH
	}

	done := false
	for total > 8 && !done {
		size = be32(file[p:])
		if total < 12 || size > total-12 {
			return pngInfo{}, false
		}
		kind := string(file[p+4 : p+8])
		body := file[p+8 : p+8+size]
		switch kind {
		case "PLTE":
			n := size / 3
			if n > 256 {
				n = 256
			}
			for i := 0; i < n; i++ {
				info.palette[i*4] = body[i*3]
				info.palette[i*4+1] = body[i*3+1]
				info.palette[i*4+2] = body[i*3+2]
			}
			for i := n; i < 256; i++ {
				info.palette[i*4], info.palette[i*4+1], info.palette[i*4+2] = 0, 0, 0
			}
		case "tRNS":
			info.transparency = true
			if info.indexed {
				n := size
				if n > 256 {
					n = 256
				}
				for i := 0; i < n; i++ {
					info.palette[i*4+3] = body[i]
				}
				for i := n; i < 256; i++ {
					info.palette[i*4+3] = 0xff
				}
			} else {
				if size != info.n*2 {
					return pngInfo{}, false
				}
				for i := 0; i < info.n; i++ {
					info.trns[i] = (int(body[i*2])<<8 | int(body[i*2+1])) & ((1 << uint(info.depth)) - 1)
				}
			}
		case "pHYs":
			if size != 9 {
				return pngInfo{}, false
			}
		case "IDAT":
			chunks = append(chunks, idatChunk{empty: size == 0})
			payload = append(payload, body...)
			idatSeen = true
		case "IEND":
			done = true
		}
		p += size + 12
		total -= size + 12
	}
	// A zero-length IDAT before the end of the zlib stream makes inflate
	// return Z_BUF_ERROR, which png_read_idat refuses.
	for i, chunk := range chunks {
		if chunk.empty && (i == 0 || i < len(chunks)-1) {
			return pngInfo{}, false
		}
	}

	if idatSeen {
		if !pngInflate(&info, payload) {
			return pngInfo{}, false
		}
	} else {
		for i := range info.samples {
			info.samples[i] = 0xff
		}
	}

	if info.interlace == 0 {
		stride = (info.width*info.n*info.depth + 7) / 8
		pngPredict(info.samples, info.width, info.height, info.n, info.depth)
	} else {
		if !pngDeinterlace(&info) {
			return pngInfo{}, false
		}
	}
	return info, true
}

func pngReadIHDR(info *pngInfo, p []byte) bool {
	if len(p) != 13 {
		return false
	}
	info.width = be32(p)
	info.height = be32(p[4:])
	info.depth = int(p[8])
	colour := int(p[9])
	compression := int(p[10])
	filter := int(p[11])
	info.interlace = int(p[12])
	if info.width <= 0 || info.height <= 0 {
		return false
	}
	if info.depth != 1 && info.depth != 2 && info.depth != 4 && info.depth != 8 && info.depth != 16 {
		return false
	}
	if colour == 2 && info.depth < 8 || colour == 3 && info.depth > 8 ||
		colour == 4 && info.depth < 8 || colour == 6 && info.depth < 8 {
		return false
	}
	info.indexed = false
	switch colour {
	case 0:
		info.n, info.gray = 1, true
	case 2:
		info.n = 3
	case 4:
		info.n, info.gray = 2, true
	case 6:
		info.n = 4
	case 3:
		info.n, info.indexed = 1, true
	default:
		return false
	}
	if compression != 0 || filter != 0 || info.interlace > 1 {
		return false
	}
	// Keep an allocation a hostile header cannot blow up (MuPDF bounds it by
	// UINT_MAX; Go needs the same order of magnitude).
	if int64(info.height)*int64(1+(info.width*info.n*info.depth+7)/8) > 1<<30 {
		return false
	}
	return true
}

// pngInflate is png_read_idat plus the avail_out bookkeeping of
// png_read_image. It fills the tail of info.samples with 0xff when the stream
// ends short, as MuPDF warns and does.
func pngInflate(info *pngInfo, payload []byte) bool {
	if len(payload) < 2 {
		// inflate needs more input for the zlib header but reports Z_OK, so
		// the output stays the 0xff fill.
		for i := range info.samples {
			info.samples[i] = 0xff
		}
		return true
	}
	source := &pngSource{data: payload}
	reader, err := zlib.NewReader(source)
	if err != nil {
		return false
	}
	written, err := io.ReadFull(reader, info.samples)
	if err != nil && err != io.ErrUnexpectedEOF && err != io.EOF {
		return false
	}
	if err == nil {
		// The output buffer is full: png_read_idat refuses when the stream
		// still carries output (the "ran out of output before input" case).
		var probe [1]byte
		if n, _ := reader.Read(probe[:]); n > 0 {
			return false
		}
	}
	// Drive the stream to its end so that the checksum and any bytes the
	// stream did not consume are seen.
	_, err = io.Copy(io.Discard, reader)
	switch {
	case err == nil:
		// A clean end must consume the whole payload: bytes after the zlib
		// trailer are the "inflate did not consume buffer" refusal.
		if source.pos != len(payload) {
			return false
		}
	case errors.Is(err, io.ErrUnexpectedEOF):
		// The input ends inside the stream: zlib decodes every symbol whose
		// bits are there, compress/flate stops short of the last one. Keep
		// the samples zlib reports.
		exact, ok := inflateZlib(payload, len(info.samples))
		if !ok {
			return false
		}
		if len(exact) > written {
			copy(info.samples, exact)
			written = len(exact)
		}
	default:
		return false
	}
	for i := written; i < len(info.samples); i++ {
		info.samples[i] = 0xff
	}
	return true
}

type pngSource struct {
	data []byte
	pos  int
}

func (r *pngSource) Read(p []byte) (int, error) {
	if r.pos >= len(r.data) {
		return 0, io.EOF
	}
	n := copy(p, r.data[r.pos:])
	r.pos += n
	return n, nil
}

func (r *pngSource) ReadByte() (byte, error) {
	if r.pos >= len(r.data) {
		return 0, io.EOF
	}
	b := r.data[r.pos]
	r.pos++
	return b, nil
}

// pngPredict is png_predict of load-png.c.
func pngPredict(samples []byte, width, height, n, depth int) {
	stride := (width*n*depth + 7) / 8
	bpp := (n*depth + 7) / 8
	if bpp == 0 {
		bpp = 1
	}
	for row := 0; row < height; row++ {
		src := samples[(stride+1)*row:]
		dst := samples[stride*row:]
		var prev []byte
		if row > 0 {
			prev = samples[stride*(row-1):]
		}
		filter := src[0]
		src = src[1:]
		switch filter {
		default:
			copy(dst[:stride], src[:stride])
		case 1:
			for i := 0; i < stride; i++ {
				left := byte(0)
				if i >= bpp {
					left = dst[i-bpp]
				}
				dst[i] = src[i] + left
			}
		case 2:
			for i := 0; i < stride; i++ {
				if row == 0 {
					dst[i] = src[i]
				} else {
					dst[i] = src[i] + prev[i]
				}
			}
		case 3:
			for i := 0; i < stride; i++ {
				if row == 0 {
					if i < bpp {
						dst[i] = src[i]
					} else {
						dst[i] = src[i] + dst[i-bpp]>>1
					}
				} else if i < bpp {
					dst[i] = src[i] + prev[i]>>1
				} else {
					dst[i] = src[i] + byte((int(prev[i])+int(dst[i-bpp]))>>1)
				}
			}
		case 4:
			for i := 0; i < stride; i++ {
				var left, up, upperLeft byte
				if i >= bpp {
					left = dst[i-bpp]
				}
				if row > 0 {
					up = prev[i]
				}
				if row > 0 && i >= bpp {
					upperLeft = prev[i-bpp]
				}
				dst[i] = src[i] + paeth(left, up, upperLeft)
			}
		}
	}
}

func paeth(a, b, c byte) byte {
	ac, bc, abcc := int(b)-int(c), int(a)-int(c), int(b)-int(c)+int(a)-int(c)
	pa, pb, pc := abs(ac), abs(bc), abs(abcc)
	if pa <= pb && pa <= pc {
		return a
	}
	if pb <= pc {
		return b
	}
	return c
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

var adam7 = [7][4]int{
	{0, 8, 0, 8}, {4, 8, 0, 8}, {0, 4, 4, 8}, {2, 4, 0, 4},
	{0, 2, 2, 4}, {1, 2, 0, 2}, {0, 1, 1, 2},
}

func pngPasses(info *pngInfo) (w, h, ofs [8]int) {
	bpp := info.depth * info.n
	for p := 0; p < 7; p++ {
		w[p] = (info.width + adam7[p][1] - adam7[p][0] - 1) / adam7[p][1]
		h[p] = (info.height + adam7[p][3] - adam7[p][2] - 1) / adam7[p][3]
		if w[p] == 0 {
			h[p] = 0
		}
		if h[p] == 0 {
			w[p] = 0
		}
		if w[p] > 0 && h[p] > 0 {
			ofs[p+1] = ofs[p] + h[p]*(1+(w[p]*bpp+7)/8)
		} else {
			ofs[p+1] = ofs[p]
		}
	}
	return
}

func pngDeinterlace(info *pngInfo) bool {
	n, depth := info.n, info.depth
	stride := (info.width*n*depth + 7) / 8
	if int64(info.height)*int64(stride) > 1<<30 {
		return false
	}
	output := make([]byte, info.height*stride)
	passW, passH, passOfs := pngPasses(info)
	for p := 0; p < 7; p++ {
		w, h := passW[p], passH[p]
		if w == 0 || h == 0 {
			continue
		}
		sp := info.samples[passOfs[p]:]
		pngPredict(sp, w, h, n, depth)
		rowBytes := (w*depth*n + 7) / 8
		for y := 0; y < h; y++ {
			for x := 0; x < w; x++ {
				outX := x*adam7[p][1] + adam7[p][0]
				outY := y*adam7[p][3] + adam7[p][2]
				dp := output[outY*stride:]
				if outX >= info.width {
					continue
				}
				for k := 0; k < n; k++ {
					v := pngComponent(sp[y*rowBytes:], x*n+k, depth)
					pngPutComponent(dp, outX*n+k, depth, v)
				}
			}
		}
	}
	info.samples = output
	return true
}

func pngPutComponent(line []byte, x, depth, value int) {
	switch depth {
	case 1:
		line[x>>3] &= ^byte(1 << (7 - uint(x&7)))
		line[x>>3] |= byte(value) << (7 - uint(x&7))
	case 2:
		shift := 2 * (3 - uint(x&3))
		line[x>>2] &= ^byte(3 << shift)
		line[x>>2] |= byte(value) << shift
	case 4:
		shift := 4 * (1 - uint(x&1))
		line[x>>1] &= ^byte(15 << shift)
		line[x>>1] |= byte(value) << shift
	case 8:
		line[x] = byte(value)
	case 16:
		line[x<<1] = byte(value >> 8)
		line[(x<<1)+1] = byte(value)
	}
}
