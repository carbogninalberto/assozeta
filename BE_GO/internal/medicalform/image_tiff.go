// TIFF loading with MuPDF's semantics (fz_load_tiff): baseline chunky images,
// 1/2/4/8/16-bit samples, strips or tiles, no compression, LZW, PackBits,
// Deflate (with predictor 2), JPEG, WhiteIsZero, palettes, CMYK and extra
// samples as alpha. JPEG-compressed strips are decoded with the standard
// library; every other path follows load-tiff.c number for number.
package medicalform

import (
	"bytes"
	"compress/zlib"
	"image/jpeg"
	"io"
)

type tiffFile struct {
	data  []byte
	order uint16 // 0x4949 little endian, 0x4d4d big endian

	rowsperstrip uint32
	stripoffsets []uint32
	stripcounts  []uint32
	hasStripOffs bool
	hasStripSize bool
	maxstrips    uint32

	tilelength uint32
	tilewidth  uint32
	tileoffs   []uint32
	tilecounts []uint32
	maxtiles   uint32

	colormap    []uint32
	colormaplen uint32
	maxcolors   uint32

	subfiletype     uint32
	photometric     uint32
	compression     uint32
	imagewidth      uint32
	imagelength     uint32
	samplesperpixel uint32
	bitspersample   uint32
	planar          uint32
	extrasamples    uint32
	xresolution     uint32
	yresolution     uint32
	resolutionunit  uint32
	fillorder       uint32
	g3opts          uint32
	g4opts          uint32
	predictor       uint32
	ycbcrsubsamp    [2]uint32

	jpegtables []byte
	jpegofs    uint32
	jpeglen    uint32

	stride     int
	tilestride int
	samples    []byte
	gray       bool
	cmyk       bool
}

func (t *tiffFile) u16(p int) int {
	if p+2 > len(t.data) {
		return -1
	}
	if t.order == 0x4949 {
		return int(t.data[p]) | int(t.data[p+1])<<8
	}
	return int(t.data[p])<<8 | int(t.data[p+1])
}

func (t *tiffFile) u32(p int) uint32 {
	if p+4 > len(t.data) {
		return 0
	}
	if t.order == 0x4949 {
		return uint32(t.data[p]) | uint32(t.data[p+1])<<8 | uint32(t.data[p+2])<<16 | uint32(t.data[p+3])<<24
	}
	return uint32(t.data[p])<<24 | uint32(t.data[p+1])<<16 | uint32(t.data[p+2])<<8 | uint32(t.data[p+3])
}

func decodeTIFF(file []byte) (imageAsset, bool) {
	t := &tiffFile{data: file}
	if len(file) < 8 {
		return imageAsset{}, false
	}
	t.order = uint16(t.u16(0))
	if t.order != 0x4949 && t.order != 0x4d4d {
		return imageAsset{}, false
	}
	if t.u16(2) != 42 {
		return imageAsset{}, false
	}
	// Tag defaults.
	t.bitspersample = 1
	t.compression = 1
	t.samplesperpixel = 1
	t.resolutionunit = 2
	t.rowsperstrip = 0xffffffff
	t.fillorder = 1
	t.planar = 1
	t.predictor = 1
	t.ycbcrsubsamp = [2]uint32{2, 2}
	offset := t.u32(4)
	if int(offset)+2 > len(file) {
		return imageAsset{}, false
	}
	if !t.readIFD(int(offset)) {
		return imageAsset{}, false
	}
	if !t.decodeIFD() {
		return imageAsset{}, false
	}
	if !t.decodeSamples() {
		return imageAsset{}, false
	}
	return t.pixmap()
}

const (
	tiffByte     = 1
	tiffShort    = 3
	tiffLong     = 4
	tiffRational = 5
)

func (t *tiffFile) readTagValue(typ uint16, offset int, n int) []uint32 {
	out := make([]uint32, n)
	divisor := 1
	switch typ {
	case tiffShort:
		divisor = 2
	case tiffLong:
		divisor = 4
	case tiffRational:
		divisor = 8
	}
	if offset > len(t.data) {
		offset = len(t.data)
	}
	if avail := (len(t.data) - offset) / divisor; n > avail {
		n = avail
	}
	for i := 0; i < n; i++ {
		switch typ {
		case tiffRational:
			num := t.u32(offset)
			den := t.u32(offset + 4)
			if den != 0 {
				out[i] = num / den
			} else {
				out[i] = ^uint32(0)
			}
			offset += 8
		case tiffByte:
			if offset < len(t.data) {
				out[i] = uint32(t.data[offset])
			}
			offset++
		case tiffShort:
			out[i] = uint32(t.u16(offset))
			offset += 2
		case tiffLong:
			out[i] = t.u32(offset)
			offset += 4
		default:
			offset++
		}
	}
	return out
}

func (t *tiffFile) readTag(offset int) {
	if offset+12 > len(t.data) {
		return
	}
	tag := t.u16(offset)
	typ := uint16(t.u16(offset + 2))
	count := int(t.u32(offset + 4))
	value := offset + 8
	switch {
	case typ == tiffByte && count <= 4,
		typ == tiffShort && count <= 2,
		typ == tiffLong && count <= 1:
	default:
		value = int(t.u32(offset + 8))
	}
	first := func() uint32 {
		values := t.readTagValue(typ, value, 1)
		if len(values) == 0 {
			return 0
		}
		return values[0]
	}
	switch tag {
	case 254:
		t.subfiletype = first()
	case 256:
		t.imagewidth = first()
	case 257:
		t.imagelength = first()
	case 258:
		t.bitspersample = first()
	case 259:
		t.compression = first()
	case 262:
		t.photometric = first()
	case 266:
		t.fillorder = first()
	case 273:
		t.hasStripOffs = true
	case 277:
		t.samplesperpixel = first()
	case 278:
		t.rowsperstrip = first()
	case 279:
		t.hasStripSize = true
	case 282:
		t.xresolution = first()
	case 283:
		t.yresolution = first()
	case 284:
		t.planar = first()
	case 292:
		t.g3opts = first()
	case 293:
		t.g4opts = first()
	case 296:
		t.resolutionunit = first()
	case 317:
		t.predictor = first()
	case 320:
		t.colormaplen = uint32(count)
	case 322:
		t.tilewidth = first()
	case 323:
		t.tilelength = first()
	case 324:
	case 325:
	case 338:
		t.extrasamples = first()
	case 347:
		if value+count <= len(t.data) {
			t.jpegtables = t.data[value : value+count]
		} else if value < len(t.data) {
			t.jpegtables = t.data[value:]
		}
	case 530:
		values := t.readTagValue(typ, value, 2)
		if len(values) > 0 {
			t.ycbcrsubsamp[0] = values[0]
		}
		if len(values) > 1 {
			t.ycbcrsubsamp[1] = values[1]
		}
	case 513:
		t.jpegofs = first()
	case 514:
		t.jpeglen = first()
	}
}

func (t *tiffFile) readTagArray(offset int) bool {
	if offset+12 > len(t.data) {
		return false
	}
	tag := t.u16(offset)
	typ := uint16(t.u16(offset + 2))
	count := int(t.u32(offset + 4))
	value := offset + 8
	switch {
	case typ == tiffByte && count <= 4,
		typ == tiffShort && count <= 2,
		typ == tiffLong && count <= 1:
	default:
		value = int(t.u32(offset + 8))
	}
	switch tag {
	case 273:
		if t.hasStripOffs && t.stripoffsets != nil && count > 0 {
			return false
		}
		if t.rowsperstrip == 0 {
			return false
		}
		if uint32(count) > t.maxstrips {
			count = int(t.maxstrips)
		}
		t.stripoffsets = t.readTagValue(typ, value, count)
		t.hasStripOffs = true
	case 279:
		if t.hasStripSize && t.stripcounts != nil && count > 0 {
			return false
		}
		if t.rowsperstrip == 0 {
			return false
		}
		if uint32(count) > t.maxstrips {
			count = int(t.maxstrips)
		}
		t.stripcounts = t.readTagValue(typ, value, count)
		t.hasStripSize = true
	case 320:
		if t.colormap != nil {
			return false
		}
		if typ != tiffShort {
			return false
		}
		if uint32(count) > t.maxcolors {
			count = int(t.maxcolors)
		}
		t.colormap = t.readTagValue(typ, value, count)
		t.colormaplen = uint32(len(t.colormap))
	case 324:
		if t.tileoffs != nil {
			return false
		}
		if t.tilelength == 0 || t.tilewidth == 0 {
			return false
		}
		if uint32(count) > t.maxtiles {
			count = int(t.maxtiles)
		}
		t.tileoffs = t.readTagValue(typ, value, count)
	case 325:
		if t.tilecounts != nil {
			return false
		}
		if t.tilelength == 0 || t.tilewidth == 0 {
			return false
		}
		if uint32(count) > t.maxtiles {
			count = int(t.maxtiles)
		}
		t.tilecounts = t.readTagValue(typ, value, count)
	}
	return true
}

func (t *tiffFile) readIFD(offset int) bool {
	p := offset
	count := t.u16(p)
	if count < 0 {
		return false
	}
	p += 2
	if count*12 > len(t.data)-p {
		return false
	}
	for i := 0; i < count; i++ {
		t.readTag(p + i*12)
	}
	if t.bitspersample > 16 {
		t.bitspersample = 16
	}
	t.maxcolors = t.colormaplen
	if t.maxcolors != 0 {
		comps := 3
		switch t.photometric {
		case 0, 1, 4, 32844:
			comps = 1
		case 2, 3, 6, 8, 9, 32845:
			comps = 3
		case 5:
			comps = 4
		}
		limit := uint32(comps) * (1 << t.bitspersample)
		if t.maxcolors > limit {
			t.maxcolors = limit
		}
	}
	t.maxtiles = 0
	if t.tilelength != 0 && t.tilewidth != 0 {
		down := (t.imagelength + t.tilelength - 1) / t.tilelength
		across := (t.imagewidth + t.tilewidth - 1) / t.tilewidth
		per := across * down
		if t.planar == 1 {
			t.maxtiles = per
		} else {
			t.maxtiles = t.samplesperpixel * per
		}
	}
	t.maxstrips = t.imagelength
	if t.rowsperstrip != 0 && t.rowsperstrip != 0xffffffff {
		if t.rowsperstrip < t.imagelength {
			t.maxstrips = (t.imagelength + t.rowsperstrip - 1) / t.rowsperstrip
		}
	}
	if t.tilelength != 0 && t.tilewidth != 0 {
		t.maxstrips = t.maxtiles
	}
	// The arrays read the same entries again.
	original := p
	for i := 0; i < count; i++ {
		if !t.readTagArray(original + i*12) {
			return false
		}
	}
	return true
}

func (t *tiffFile) decodeIFD() bool {
	if t.imagelength == 0 || t.imagewidth == 0 {
		return false
	}
	if t.bitspersample > 16 || t.bitspersample == 0 || t.bitspersample&(t.bitspersample-1) != 0 {
		return false
	}
	if t.samplesperpixel == 0 || t.samplesperpixel >= 32 {
		return false
	}
	if t.samplesperpixel < t.extrasamples {
		return false
	}
	if t.imagewidth > (^uint32(0)-7)/t.samplesperpixel/t.bitspersample {
		return false
	}
	if t.planar != 1 {
		return false
	}
	if t.tilewidth&0xf != 0 || t.tilelength&0xf != 0 {
		return false
	}
	if t.photometric == 6 {
		if t.samplesperpixel != 3 || t.bitspersample != 8 {
			return false
		}
		if t.ycbcrsubsamp[0] != 1 && t.ycbcrsubsamp[0] != 2 && t.ycbcrsubsamp[0] != 4 {
			return false
		}
		if t.ycbcrsubsamp[1] != 1 && t.ycbcrsubsamp[1] != 2 && t.ycbcrsubsamp[1] != 4 {
			return false
		}
	}
	comps := t.samplesperpixel - uint32(boolInt(t.extrasamples != 0))
	switch t.photometric {
	case 0, 1:
		if comps < 1 {
			return false
		}
		t.gray = true
	case 2:
		if comps < 3 {
			return false
		}
	case 3:
		if comps < 1 {
			return false
		}
		if t.colormap == nil {
			return false
		}
	case 4:
		if comps < 1 {
			return false
		}
		t.gray = true
	case 5:
		if comps < 4 {
			return false
		}
		t.cmyk = true
	case 6:
		if comps < 3 {
			return false
		}
	case 8, 9, 32844, 32845:
		// Lab and the SGI log encodings are not part of the forms' corpus.
		return false
	default:
		return false
	}
	t.stride = int((t.imagewidth*t.samplesperpixel*t.bitspersample + 7) / 8)
	t.tilestride = int((t.tilewidth*t.samplesperpixel*t.bitspersample + 7) / 8)
	if t.stride <= 0 {
		return false
	}
	if t.resolutionunit == 3 {
		t.xresolution = t.xresolution * 254 / 100
		t.yresolution = t.yresolution * 254 / 100
	} else if t.resolutionunit != 2 {
		t.xresolution, t.yresolution = 96, 96
	}
	if t.xresolution == 0 || t.yresolution == 0 {
		t.xresolution, t.yresolution = 96, 96
	}
	if t.rowsperstrip > t.imagelength {
		t.rowsperstrip = t.imagelength
	}
	if t.compression == 1 {
		if t.rowsperstrip == 0 {
			return false
		}
		if t.tilelength == 0 && t.tilewidth == 0 && len(t.stripcounts) == 0 {
			strips := int((t.imagelength + t.rowsperstrip - 1) / t.rowsperstrip)
			t.stripcounts = make([]uint32, strips)
			for i := range t.stripcounts {
				t.stripcounts[i] = t.rowsperstrip * uint32(t.stride)
			}
			t.hasStripSize = true
		}
		if t.tilelength != 0 && t.tilewidth != 0 && len(t.tilecounts) == 0 {
			down := (t.imagelength + t.tilelength - 1) / t.tilelength
			across := (t.imagewidth + t.tilewidth - 1) / t.tilewidth
			t.tilecounts = make([]uint32, across*down)
			for i := range t.tilecounts {
				t.tilecounts[i] = t.tilelength * uint32(t.tilestride)
			}
		}
	}
	if t.tilelength != 0 && t.tilewidth != 0 {
		if len(t.tileoffs) == 0 && len(t.tilecounts) == 0 && len(t.stripoffsets) > 0 {
			t.tileoffs = t.stripoffsets
			t.stripoffsets = nil
		}
		if len(t.tilecounts) == 0 && len(t.stripcounts) > 0 {
			t.tilecounts = t.stripcounts
			t.stripcounts = nil
		}
	}
	return true
}

func boolInt(v bool) int {
	if v {
		return 1
	}
	return 0
}

func (t *tiffFile) decodeSamples() bool {
	if int64(t.imagelength)*int64(t.stride) > 1<<30 {
		return false
	}
	t.samples = make([]byte, int(t.imagelength)*t.stride)
	switch {
	case t.tilelength != 0 && t.tilewidth != 0 && len(t.tileoffs) > 0 && len(t.tilecounts) > 0:
		return t.decodeTiles()
	case t.rowsperstrip != 0 && len(t.stripoffsets) > 0 && len(t.stripcounts) > 0:
		return t.decodeStrips()
	case t.jpegofs != 0 && t.jpeglen != 0:
		return t.decodeWholeJPEG()
	}
	return false
}

func (t *tiffFile) stripOK(offset, count uint32) bool {
	if int64(offset) > int64(len(t.data)) {
		return false
	}
	if int64(offset)+int64(count) > int64(len(t.data)) {
		return false
	}
	return count != 0
}

func (t *tiffFile) decodeStrips() bool {
	strips := int((t.imagelength + t.rowsperstrip - 1) / t.rowsperstrip)
	if len(t.stripoffsets) < strips || len(t.stripcounts) < strips {
		return false
	}
	data := t.samples
	for y := 0; y < int(t.imagelength); y += int(t.rowsperstrip) {
		strip := y / int(t.rowsperstrip)
		offset := t.stripoffsets[strip]
		count := t.stripcounts[strip]
		if !t.stripOK(offset, count) {
			return false
		}
		wlen := t.stride * int(t.rowsperstrip)
		if y+int(t.rowsperstrip) >= int(t.imagelength) {
			wlen = t.stride * (int(t.imagelength) - y)
		}
		decoded, ok := t.decodeData(t.data[offset:offset+count], wlen)
		if !ok {
			return false
		}
		if len(decoded) < wlen {
			return true
		}
		copy(data, decoded)
		data = data[wlen:]
	}
	return true
}

func (t *tiffFile) decodeTiles() bool {
	down := int((t.imagelength + t.tilelength - 1) / t.tilelength)
	across := int((t.imagewidth + t.tilewidth - 1) / t.tilewidth)
	tiles := across * down
	if len(t.tileoffs) < tiles || len(t.tilecounts) < tiles {
		return false
	}
	if t.photometric == 6 && t.compression != 6 && t.compression != 7 {
		return false
	}
	index := 0
	for y := 0; y < int(t.imagelength); y += int(t.tilelength) {
		for x := 0; x < int(t.imagewidth); x += int(t.tilewidth) {
			offset := t.tileoffs[index]
			count := t.tilecounts[index]
			if !t.stripOK(offset, count) {
				return false
			}
			want := t.tilelength * uint32(t.tilestride)
			decoded, ok := t.decodeData(t.data[offset:offset+count], int(want))
			if !ok {
				return false
			}
			if len(decoded) != int(want) {
				return false
			}
			t.pasteTile(decoded, y, x)
			index++
		}
	}
	return true
}

func (t *tiffFile) pasteTile(tile []byte, row, col int) {
	spp := int(t.samplesperpixel)
	bits := int(t.bitspersample)
	for y := 0; y < int(t.tilelength) && row+y < int(t.imagelength); y++ {
		for x := 0; x < int(t.tilewidth) && col+x < int(t.imagewidth); x++ {
			for k := 0; k < spp; k++ {
				dst := (row+y)*t.stride + (((col+x)*spp+k)*bits)/8
				src := y*t.tilestride + ((x*spp+k)*bits)/8
				switch bits {
				case 1:
					shift := 8 - (((col+x)*spp+k)*bits)%8 - bits
					t.samples[dst] |= (tile[src] >> uint(7-((col+x)%8))) & 1 << uint(shift)
				case 2:
					shift := 8 - (((col+x)*spp+k)*bits)%8 - bits
					t.samples[dst] |= (tile[src] >> uint(6-2*((col+x)%4))) & 3 << uint(shift)
				case 4:
					shift := 8 - (((col+x)*spp+k)*bits)%8 - bits
					t.samples[dst] |= (tile[src] >> uint(4-4*((col+x)%2))) & 15 << uint(shift)
				case 8:
					t.samples[dst] = tile[src]
				default:
					copy(t.samples[dst:dst+bits/8], tile[src:src+bits/8])
				}
			}
		}
	}
}

// decodeData applies the strip or tile compression and returns the decoded
// bytes, which can be shorter than want.
func (t *tiffFile) decodeData(source []byte, want int) ([]byte, bool) {
	if t.fillorder == 2 {
		reversed := make([]byte, len(source))
		for i, b := range source {
			reversed[i] = bitReverse[b]
		}
		source = reversed
	}
	switch t.compression {
	case 1:
		n := want
		if n > len(source) {
			n = len(source)
		}
		return source[:n], true
	case 5:
		old := len(source) >= 2 && source[0] == 0 && source[1]&1 != 0
		return tiffLZW(source, !old, want), true
	case 32773:
		return tiffPackBits(source, want), true
	case 8, 32946:
		return tiffInflate(source, want)
	case 6, 7:
		return t.decodeJPEGStrip(tiffJPEGStream(t.jpegtables, source), want)
	}
	return nil, false
}

// bitReverse is the reversed byte FillOrder 2 asks for.
var bitReverse = func() (table [256]byte) {
	for i := range table {
		v := byte(i)
		var r byte
		for b := 0; b < 8; b++ {
			r = r<<1 | v&1
			v >>= 1
		}
		table[i] = r
	}
	return
}()

func tiffInflate(source []byte, want int) ([]byte, bool) {
	reader, err := zlib.NewReader(bytes.NewReader(source))
	if err != nil {
		return nil, false
	}
	out := make([]byte, want)
	n, err := io.ReadFull(reader, out)
	if err != nil && err != io.ErrUnexpectedEOF && err != io.EOF {
		return nil, false
	}
	return out[:n], true
}

func tiffPackBits(source []byte, want int) []byte {
	out := make([]byte, 0, want)
	for i := 0; i < len(source) && len(out) < want; {
		n := int(source[i])
		i++
		switch {
		case n < 128:
			count := n + 1
			if i+count > len(source) {
				count = len(source) - i
			}
			out = append(out, source[i:i+count]...)
			i += count
		case n > 128:
			if i >= len(source) {
				return out
			}
			count := 257 - n
			for j := 0; j < count && len(out) < want; j++ {
				out = append(out, source[i])
			}
			i++
		}
	}
	return out
}

// tiffLZW decodes TIFF's LZW variant. earlyChange widens the code one entry
// before the table is full, which MuPDF selects from the first two bytes.
func tiffLZW(source []byte, earlyChange bool, want int) []byte {
	out := make([]byte, 0, want)
	const (
		clearCode = 256
		endCode   = 257
		maxCodes  = 4096
	)
	prefix := make([]int, maxCodes)
	suffix := make([]byte, maxCodes)
	for i := range prefix {
		prefix[i] = -1
	}
	for i := 0; i < 256; i++ {
		suffix[i] = byte(i)
	}
	next := endCode + 1
	codeSize := 9
	previous := -1
	bitPos := 0
	readCode := func() int {
		value := 0
		for i := 0; i < codeSize; i++ {
			value <<= 1
			at := bitPos >> 3
			if at < len(source) {
				value |= int(source[at]>>uint(7-bitPos&7)) & 1
			}
			bitPos++
		}
		return value
	}
	emit := func(code int) {
		var stack [maxCodes]byte
		top := 0
		for code >= 0 && top < maxCodes {
			stack[top] = suffix[code]
			top++
			code = prefix[code]
		}
		for top > 0 && len(out) < want {
			top--
			out = append(out, stack[top])
		}
	}
	first := func(code int) byte {
		for prefix[code] >= 0 {
			code = prefix[code]
		}
		return suffix[code]
	}
	for bitPos+codeSize <= len(source)*8 {
		code := readCode()
		if code == clearCode {
			next = endCode + 1
			codeSize = 9
			previous = -1
			continue
		}
		if code == endCode {
			break
		}
		if previous < 0 {
			if code >= 256 {
				return out
			}
			emit(code)
			previous = code
			continue
		}
		if code < next {
			if code >= 256 && prefix[code] < 0 {
				return out
			}
			emit(code)
			if next < maxCodes {
				prefix[next] = previous
				suffix[next] = first(code)
				next++
			}
		} else if code == next {
			if next < maxCodes {
				prefix[next] = previous
				suffix[next] = first(previous)
				next++
			}
			emit(code)
		} else {
			return out
		}
		previous = code
		limit := 1 << uint(codeSize)
		if earlyChange {
			limit--
		}
		if next == limit && codeSize < 12 {
			codeSize++
		}
	}
	return out
}

// tiffJPEGStream merges the JPEGTables tag's tables with a strip that carries
// only the frame and the scan, as libjpeg does for fz_open_dctd.
func tiffJPEGStream(tables, source []byte) []byte {
	if len(tables) < 4 || tables[0] != 0xff || tables[1] != 0xd8 {
		return source
	}
	tables = tables[2:]
	if len(tables) >= 2 && tables[len(tables)-2] == 0xff && tables[len(tables)-1] == 0xd9 {
		tables = tables[:len(tables)-2]
	}
	image := source
	if len(image) >= 2 && image[0] == 0xff && image[1] == 0xd8 {
		image = image[2:]
	}
	combined := make([]byte, 0, 2+len(tables)+len(image))
	combined = append(combined, 0xff, 0xd8)
	combined = append(combined, tables...)
	combined = append(combined, image...)
	return combined
}

func (t *tiffFile) decodeJPEGStrip(source []byte, want int) ([]byte, bool) {
	decoded, err := jpeg.Decode(bytes.NewReader(source))
	if err != nil {
		return nil, false
	}
	bounds := decoded.Bounds()
	out := make([]byte, want)
	at := 0
	for y := bounds.Min.Y; y < bounds.Max.Y && at+t.stride <= len(out); y++ {
		line := out[at : at+t.stride]
		x := 0
		for px := bounds.Min.X; px < bounds.Max.X; px++ {
			r, g, b, _ := decoded.At(px, y).RGBA()
			if x+3 <= len(line) {
				line[x], line[x+1], line[x+2] = byte(r>>8), byte(g>>8), byte(b>>8)
			}
			x += 3
		}
		at += t.stride
	}
	return out, true
}

func (t *tiffFile) decodeWholeJPEG() bool {
	if int64(t.jpegofs) > int64(len(t.data)) {
		return false
	}
	end := int64(t.jpegofs) + int64(t.jpeglen)
	if end > int64(len(t.data)) {
		end = int64(len(t.data))
	}
	want := int(t.imagelength) * t.stride
	decoded, ok := t.decodeJPEGStrip(t.data[t.jpegofs:end], want)
	if !ok {
		return false
	}
	copy(t.samples, decoded)
	return true
}

func (t *tiffFile) pixmap() (imageAsset, bool) {
	// Predictor 2 (horizontal differencing) applies to LZW and Deflate.
	if (t.compression == 5 || t.compression == 8 || t.compression == 32946) && t.predictor == 2 {
		for y := 0; y < int(t.imagelength); y++ {
			tiffUnpredict(t.samples[y*t.stride:], int(t.imagewidth), int(t.samplesperpixel), int(t.bitspersample))
		}
	}
	if t.photometric == 6 && t.compression != 6 && t.compression != 7 {
		t.yccToRGB()
	}
	if t.photometric == 3 && t.colormap != nil {
		if !t.expandColormap() {
			return imageAsset{}, false
		}
	}
	if t.photometric == 0 {
		for y := 0; y < int(t.imagelength); y++ {
			tiffInvert(t.samples[y*t.stride:], int(t.imagewidth), int(t.samplesperpixel), int(t.bitspersample), t.extrasamples)
		}
	}
	if t.bitspersample == 16 && t.order == 0x4949 && t.colormap == nil {
		for i := 0; i+1 < len(t.samples); i += 2 {
			t.samples[i], t.samples[i+1] = t.samples[i+1], t.samples[i]
		}
	}
	return t.unpack()
}

func tiffUnpredict(line []byte, width, comps, bits int) {
	// MuPDF keeps the previous sample in an unsigned char, so a 16-bit
	// predictor carries only the low byte of the last value.
	left := make([]byte, comps)
	for i := 0; i < width; i++ {
		for k := 0; k < comps; k++ {
			v := (tiffGetComp(line, i*comps+k, bits) + int(left[k])) % (1 << uint(bits))
			tiffPutComp(line, i*comps+k, bits, v)
			left[k] = byte(v)
		}
	}
}

func tiffInvert(line []byte, width, comps, bits int, alpha uint32) {
	m := (1 << uint(bits)) - 1
	for i := 0; i < width; i++ {
		for k := 0; k < comps; k++ {
			if alpha == 0 || k < comps-1 {
				v := m - tiffGetComp(line, i*comps+k, bits)
				tiffPutComp(line, i*comps+k, bits, v)
			}
		}
	}
}

func tiffGetComp(line []byte, x, bits int) int {
	switch bits {
	case 1:
		return int(line[x>>3]>>(7-uint(x&7))) & 1
	case 2:
		return int(line[x>>2]>>(2*(3-uint(x&3)))) & 3
	case 4:
		return int(line[x>>1]>>(4*(1-uint(x&1)))) & 15
	case 8:
		if x < len(line) {
			return int(line[x])
		}
	case 16:
		if x<<1+1 < len(line) {
			return int(line[x<<1])<<8 | int(line[(x<<1)+1])
		}
	}
	return 0
}

func tiffPutComp(line []byte, x, bits, value int) {
	switch bits {
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

func (t *tiffFile) yccToRGB() {
	spp := int(t.samplesperpixel)
	for y := 0; y < int(t.imagelength); y++ {
		row := t.samples[y*t.stride:]
		for x := 0; x < int(t.imagewidth); x++ {
			c := row[x*spp:]
			yy := float64(c[0])
			cb := float64(int(c[1]) - 128)
			cr := float64(int(c[2]) - 128)
			c[0] = byte(clampFloat(yy + 1.402*cr))
			c[1] = byte(clampFloat(yy - 0.34413*cb - 0.71414*cr))
			c[2] = byte(clampFloat(yy + 1.772*cb))
		}
	}
}

func clampFloat(v float64) int {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return int(v)
}

func (t *tiffFile) expandColormap() bool {
	spp := int(t.samplesperpixel)
	if !(spp == 1 && t.extrasamples == 0) && !(spp == 2 && t.extrasamples != 0) {
		return false
	}
	bits := int(t.bitspersample)
	if bits != 1 && bits != 2 && bits != 4 && bits != 8 && bits != 16 {
		return false
	}
	maxval := 1 << uint(bits)
	if len(t.colormap) < maxval*3 {
		return false
	}
	srcstride := (int(t.imagewidth)*(1+int(t.extrasamples))*bits + 7) / 8
	if t.stride < 0 || srcstride > t.stride {
		return false
	}
	outChannels := 3
	if t.extrasamples != 0 {
		outChannels = 4
	}
	stride := int(t.imagewidth) * outChannels * 2
	samples := make([]byte, stride*int(t.imagelength))
	for y := 0; y < int(t.imagelength); y++ {
		src := t.samples[y*t.stride:]
		dst := samples[y*stride:]
		s := 0
		for x := 0; x < int(t.imagewidth); x++ {
			c := tiffGetComp(src, s, bits)
			s++
			if c >= maxval {
				c = maxval - 1
			}
			dst[0] = byte(t.colormap[c] >> 8)
			dst[1] = byte(t.colormap[c])
			dst[2] = byte(t.colormap[c+maxval] >> 8)
			dst[3] = byte(t.colormap[c+maxval])
			dst[4] = byte(t.colormap[c+maxval*2] >> 8)
			dst[5] = byte(t.colormap[c+maxval*2])
			dst = dst[6:]
			if t.extrasamples != 0 {
				a := tiffGetComp(src, s, bits)
				s++
				if bits <= 16 {
					a <<= uint(16 - bits)
				}
				dst[0] = byte(a >> 8)
				dst[1] = byte(a)
				dst = dst[2:]
				s += int(t.extrasamples) - 1
			}
		}
	}
	t.samplesperpixel += 2
	t.bitspersample = 16
	t.stride = stride
	t.samples = samples
	return true
}

func (t *tiffFile) unpack() (imageAsset, bool) {
	csChannels := 3
	if t.gray {
		csChannels = 1
	} else if t.cmyk {
		csChannels = 4
	}
	alpha := t.extrasamples != 0
	dstChannels := csChannels
	if alpha {
		dstChannels++
	}
	srcChannels := int(t.samplesperpixel)
	width, height := int(t.imagewidth), int(t.imagelength)
	bits := int(t.bitspersample)
	n := srcChannels
	skip := 0
	if dstChannels < srcChannels {
		skip = srcChannels - dstChannels
		n = dstChannels
	}
	colour := make([]byte, 0, width*height*csChannels)
	var alphaPlane []byte
	if alpha {
		alphaPlane = make([]byte, 0, width*height)
	}
	for y := 0; y < height; y++ {
		line := t.samples[y*t.stride:]
		b := 0
		for x := 0; x < width; x++ {
			values := make([]int, dstChannels)
			for k := 0; k < n; k++ {
				switch bits {
				case 1:
					values[k] = tiffGetComp(line, b, 1) * 255
				case 2:
					values[k] = tiffGetComp(line, b, 2) * 85
				case 4:
					values[k] = tiffGetComp(line, b, 4) * 17
				case 8:
					values[k] = tiffGetComp(line, b, 8)
				case 16:
					values[k] = tiffGetComp(line, b, 16) >> 8
				}
				b++
			}
			b += skip
			for k := n; k < dstChannels; k++ {
				values[k] = 255
			}
			colour = append(colour, byte(values[0]))
			if csChannels >= 3 {
				colour = append(colour, byte(values[1]), byte(values[2]))
			}
			if csChannels == 4 {
				colour = append(colour, byte(values[3]))
			}
			if alpha {
				alphaPlane = append(alphaPlane, byte(values[csChannels]))
			}
		}
	}
	return newSampleAsset(width, height, csChannels, colour, alphaPlane, t.gray, t.cmyk), true
}
