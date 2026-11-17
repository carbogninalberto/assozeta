package pilimage

import (
	"bytes"
	"compress/zlib"
	"encoding/binary"
	"io"
	"strconv"
)

// This file ports TiffImagePlugin: the IFD tag reader, the mode table, the
// strip/tile layout, and the deterministic decoders (none, PackBits, LZW,
// Deflate). JPEG-in-TIFF follows the PDF writer's JPEG rule and CCITT is not
// decoded here.

// acceptTIFF is TiffImagePlugin._accept.
func acceptTIFF(prefix []byte) bool {
	for _, p := range []string{"MM\x00\x2a", "II\x2a\x00", "MM\x2a\x00", "II\x00\x2a", "MM\x00\x2b", "II\x2b\x00"} {
		if bytes.HasPrefix(prefix, []byte(p)) {
			return true
		}
	}
	return false
}

type tiffIFD struct {
	order     binary.ByteOrder
	prefix    string
	big       bool
	offset    int
	tags      map[int][]int64
	next      int64
	offsets   []int
	truncated bool
}

func tiffTagValueCount(t int) int {
	switch t {
	case 1, 2, 6, 7:
		return 1
	case 3, 8:
		return 2
	case 4, 9, 11, 13:
		return 4
	case 5, 10, 12:
		return 8
	}
	return 0
}

// parseTIFFIFD is ImageFileDirectory_v2.load plus the tag reading of
// TiffImageFile._open.
func parseTIFFIFD(data []byte) (*tiffIFD, error) {
	if len(data) < 8 {
		return nil, &Error{Class: "error", Text: "unpack_from requires a larger buffer"}
	}
	ifh := data[:8]
	big := ifh[2] == 43
	if big {
		if len(data) < 16 {
			return nil, &Error{Class: "error", Text: "unpack_from requires a larger buffer"}
		}
		ifh = data[:16]
	}
	prefix := string(ifh[:2])
	var order binary.ByteOrder
	switch prefix {
	case "II":
		order = binary.LittleEndian
	case "MM":
		order = binary.BigEndian
	default:
		return nil, syntaxError("not a TIFF file (header " + pyReprBytes(ifh) + " not valid)")
	}
	ifd := &tiffIFD{order: order, prefix: prefix, big: big, tags: map[int][]int64{}}
	if big {
		first := int64(binary.BigEndian.Uint64(ifh[8:]))
		if order == binary.LittleEndian {
			first = int64(binary.LittleEndian.Uint64(ifh[8:]))
		}
		ifd.next = first
	} else {
		ifd.next = int64(order.Uint32(ifh[4:]))
	}
	if err := ifd.load(data); err != nil {
		return nil, err
	}
	return ifd, nil
}

func (d *tiffIFD) load(data []byte) error {
	off := d.next
	if off <= 0 || off >= int64(len(data)) {
		return &Error{Class: "error", Text: "unpack_from requires a larger buffer"}
	}
	d.offset = int(off)
	pos := int(off)
	var count int64
	if d.big {
		if pos+8 > len(data) {
			return nil
		}
		count = int64(d.order.Uint64(data[pos:]))
		pos += 8
	} else {
		if pos+2 > len(data) {
			return nil
		}
		count = int64(d.order.Uint16(data[pos:]))
		pos += 2
	}
	entrySize := 12
	if d.big {
		entrySize = 20
	}
	truncated := false
	for i := int64(0); i < count; i++ {
		if pos+entrySize > len(data) {
			truncated = true
			break
		}
		var tag, typ int
		var n int64
		var raw []byte
		if d.big {
			tag = int(d.order.Uint16(data[pos:]))
			typ = int(d.order.Uint16(data[pos+2:]))
			n = int64(d.order.Uint64(data[pos+4:]))
			raw = data[pos+12 : pos+20]
			pos += 20
		} else {
			tag = int(d.order.Uint16(data[pos:]))
			typ = int(d.order.Uint16(data[pos+2:]))
			n = int64(d.order.Uint32(data[pos+4:]))
			raw = data[pos+8 : pos+12]
			pos += 12
		}
		size := tiffTagValueCount(typ)
		if size == 0 || n < 0 {
			continue
		}
		total := n * int64(size)
		var payload []byte
		if total <= int64(len(raw)) {
			payload = raw[:total]
		} else {
			start := int64(0)
			if d.big {
				start = int64(d.order.Uint64(raw))
			} else {
				start = int64(d.order.Uint32(raw))
			}
			if start < 0 || start+total > int64(len(data)) {
				// ImageFile._safe_read raises OSError: the IFD load aborts and
				// keeps the tags read so far.
				d.truncated = true
				d.next = 0
				return nil
			}
			payload = data[start : start+total]
		}
		values := make([]int64, 0, n)
		switch typ {
		case 1, 2, 6, 7:
			for i := int64(0); i < n && int(i) < len(payload); i++ {
				values = append(values, int64(payload[i]))
			}
		case 3, 8:
			for i := int64(0); i < n && int(i)*2+2 <= len(payload); i++ {
				values = append(values, int64(d.order.Uint16(payload[i*2:])))
			}
		case 4, 9, 13:
			for i := int64(0); i < n && int(i)*4+4 <= len(payload); i++ {
				values = append(values, int64(d.order.Uint32(payload[i*4:])))
			}
		case 5, 10:
			for i := int64(0); i < n && int(i)*8+8 <= len(payload); i++ {
				values = append(values, int64(d.order.Uint32(payload[i*8:])), int64(d.order.Uint32(payload[i*8+4:])))
			}
		case 11:
			for i := int64(0); i < n && int(i)*4+4 <= len(payload); i++ {
				values = append(values, int64(d.order.Uint32(payload[i*4:])))
			}
		case 12:
			for i := int64(0); i < n && int(i)*8+8 <= len(payload); i++ {
				values = append(values, int64(d.order.Uint64(payload[i*8:])))
			}
		}
		d.tags[tag] = values
	}
	if truncated {
		d.next = 0
		d.truncated = true
		return nil
	}
	nextPos := pos
	if d.big {
		if nextPos+8 <= len(data) {
			d.next = int64(d.order.Uint64(data[nextPos:]))
		}
	} else if nextPos+4 <= len(data) {
		d.next = int64(d.order.Uint32(data[nextPos:]))
	}
	return nil
}

func (d *tiffIFD) get(tag int) ([]int64, bool) {
	v, ok := d.tags[tag]
	return v, ok
}

func (d *tiffIFD) get1(tag int, def int64) int64 {
	if v, ok := d.tags[tag]; ok && len(v) > 0 {
		return v[0]
	}
	return def
}

func tiffJoin(v []int64) string {
	var sb bytes.Buffer
	for i, x := range v {
		if i > 0 {
			sb.WriteByte(',')
		}
		sb.WriteString(strconv.FormatInt(x, 10))
	}
	return sb.String()
}

func tiffOpenInfoKey(prefix string, photo int64, sf []int64, fill int64, bps []int64, extra []int64) string {
	return prefix + "|" + strconv.FormatInt(photo, 10) + "|" + tiffJoin(sf) + "|" +
		strconv.FormatInt(fill, 10) + "|" + tiffJoin(bps) + "|" + tiffJoin(extra)
}

// openTIFF is TiffImageFile._open (first frame).
func openTIFF(data []byte) (*Image, error) {
	ifd, err := parseTIFFIFD(data)
	if err != nil {
		return nil, err
	}
	if _, ok := ifd.get(0xBC01); ok {
		return nil, osError("Windows Media Photo files not yet supported")
	}
	compression := int(ifd.get1(259, 1))
	planar := int(ifd.get1(284, 1))
	photoVal, hasPhoto := ifd.get(262)
	photo := int64(0)
	if hasPhoto && len(photoVal) > 0 {
		photo = photoVal[0]
	}
	_ = photoVal
	if compression == 6 {
		photo = 6
	}
	fill := ifd.get1(266, 1)
	wv, ok := ifd.get(256)
	if !ok {
		return nil, &Error{Class: "TypeError", Text: "Missing dimensions"}
	}
	hv, ok := ifd.get(257)
	if !ok {
		return nil, &Error{Class: "TypeError", Text: "Missing dimensions"}
	}
	if len(wv) == 0 || len(hv) == 0 {
		return nil, valueError("Invalid dimensions")
	}
	xsize, ysize := int(wv[0]), int(hv[0])
	orientation := ifd.get1(274, 0)
	w, h := xsize, ysize
	if orientation >= 5 && orientation <= 8 {
		w, h = ysize, xsize
	}
	sampleFormat := []int64{1}
	if v, ok := ifd.get(339); ok && len(v) > 0 {
		sampleFormat = v
	}
	if len(sampleFormat) > 1 {
		mx, mn := sampleFormat[0], sampleFormat[0]
		for _, v := range sampleFormat {
			if v > mx {
				mx = v
			}
			if v < mn {
				mn = v
			}
		}
		if mx == mn {
			sampleFormat = sampleFormat[:1]
		}
	}
	bps := []int64{1}
	if v, ok := ifd.get(258); ok && len(v) > 0 {
		bps = v
	}
	extra := []int64{}
	if v, ok := ifd.get(338); ok {
		extra = v
	}
	spp := ifd.get1(277, 1)
	if compression == 6 && (photo == 2 || photo == 6) {
		spp = 3
	}
	var bpsCount int
	switch photo {
	case 2, 6, 8:
		bpsCount = 3
	case 5:
		bpsCount = 4
	default:
		bpsCount = 1
	}
	if planar == 2 && len(extra) > 0 && maxInt64(extra) == 0 {
		bps = bps[:len(bps)-len(extra)]
		spp -= int64(len(extra))
		extra = []int64{}
	}
	bpsCount += len(extra)
	if spp > 6 {
		return nil, syntaxError("Invalid value for samples per pixel")
	}
	if int(spp) < len(bps) {
		bps = bps[:spp]
	} else if int(spp) > len(bps) && len(bps) == 1 {
		rep := make([]int64, spp)
		for i := range rep {
			rep[i] = bps[0]
		}
		bps = rep
	}
	if int64(len(bps)) != spp {
		return nil, syntaxError("unknown data organization")
	}
	key := tiffOpenInfoKey(ifd.prefix, photo, sampleFormat, fill, bps, extra)
	info, ok := tiffOpenInfo[key]
	if !ok {
		return nil, syntaxError("unknown pixel mode")
	}
	mode, rawmode := info[0], info[1]
	useLibtiff := compression != 1
	if useLibtiff {
		if fill == 2 {
			key = tiffOpenInfoKey(ifd.prefix, photo, sampleFormat, 1, bps, extra)
			info, ok = tiffOpenInfo[key]
			if !ok {
				return nil, syntaxError("unknown pixel mode")
			}
			mode, rawmode = info[0], info[1]
		}
		if photo == 6 {
			mode = "RGB"
			im := newImage(data, "TIFF", mode, w, h)
			if compression == 7 || compression == 6 {
				// JPEG-in-TIFF: the PDF writer's JPEG rule.
				im.loadFn = func() error { return ErrFormatNotPorted }
				return im, nil
			}
			// Pillow's libtiff strip decoder rejects the 24-to-32-bit row
			// mismatch for YCbCr data.
			im.loadFn = func() error { return osError("decoder error -2") }
			return im, nil
		}
		if rawmode == "I;16" {
			rawmode = "I;16N"
		} else if len(rawmode) > 5 && (rawmode[len(rawmode)-4:] == ";16B" || rawmode[len(rawmode)-4:] == ";16L") {
			rawmode = rawmode[:len(rawmode)-1] + "N"
		}
	}
	im := newImage(data, "TIFF", mode, w, h)
	if mode == "P" || mode == "PA" {
		colormap, _ := ifd.get(320)
		bits := int64(1)
		if len(bps) > 0 {
			bits = bps[0]
		}
		raw := make([]byte, 0, 3*(int64(1)<<uint(bits)))
		for _, v := range colormap {
			raw = append(raw, byte(v/256))
		}
		im.setPaletteRGB(raw, realizePalette("RGB;L", raw))
	}
	if compression == 6 || compression == 7 {
		// JPEG-in-TIFF: the PDF writer's JPEG rule.
		im.loadFn = func() error { return ErrFormatNotPorted }
		return im, nil
	}
	if useLibtiff {
		if ifd.truncated {
			im.loadFn = func() error { return osError("decoder error -2") }
			return im, nil
		}
		if !hasPhoto {
			im.loadFn = func() error { return osError("decoder error -2") }
			return im, nil
		}
		dec, err := tiffDecodedData(ifd, data, compression, xsize, ysize, photo, bps, planar, spp)
		if err != nil {
			im.loadFn = func() error { return err }
			return im, nil
		}
		im.loadFn = func() error {
			scratch := &Image{Format: "TIFF", Mode: im.Mode, Width: xsize, Height: ysize,
				palette: im.palette, palRGB: im.palRGB, palAlpha: im.palAlpha, hasPal: im.hasPal}
			if err := scratch.loadTIFFCompressed(dec, rawmode, xsize, ysize); err != nil {
				return err
			}
			return im.adoptTransposed(scratch, int(orientation))
		}
		return im, nil
	}
	tiles, err := tiffRawTiles(ifd, data, xsize, ysize, rawmode, bps, planar, int(spp))
	if err != nil {
		return nil, err
	}
	im.loadFn = func() error {
		scratch := &Image{Format: "TIFF", Mode: im.Mode, Width: xsize, Height: ysize, palette: im.palette,
			palRGB: im.palRGB, palAlpha: im.palAlpha, hasPal: im.hasPal}
		for _, t := range tiles {
			if err := scratch.rawDecode(im.data, t.offset, t.stride, 1, t.rawmode, t.bbox, -1); err != nil {
				return err
			}
		}
		scratch.ensurePix()
		return im.adoptTransposed(scratch, int(orientation))
	}
	return im, nil
}

func maxInt64(v []int64) int64 {
	m := v[0]
	for _, x := range v {
		if x > m {
			m = x
		}
	}
	return m
}

type tiffRawTile struct {
	rawmode string
	offset  int
	stride  int
	bbox    [4]int
}

// tiffRawTiles builds the plugin's raw tiles.
func tiffRawTiles(d *tiffIFD, data []byte, xsize, ysize int, rawmode string, bps []int64, planar, spp int) ([]tiffRawTile, error) {
	var offsets []int
	var tw, th int
	if v, ok := d.get(273); ok {
		offsets = intsOf(v)
		th = int(d.get1(278, int64(ysize)))
		if th <= 0 || th > ysize {
			th = ysize
		}
		tw = xsize
	} else if v, ok := d.get(324); ok {
		offsets = intsOf(v)
		tw = int(d.get1(322, 0))
		th = int(d.get1(323, 0))
		if tw <= 0 || th <= 0 {
			return nil, valueError("Invalid tile dimensions")
		}
	} else {
		return nil, syntaxError("unknown data organization")
	}
	if tw == xsize && th == ysize && planar != 2 && len(offsets) > 0 {
		offsets = offsets[len(offsets)-1:]
	}
	bpsSum := int64(0)
	for _, b := range bps {
		bpsSum += b
	}
	var tiles []tiffRawTile
	x, y, layer := 0, 0, 0
	for _, offset := range offsets {
		stride := 0
		if x+tw > xsize {
			stride = int(float64(int64(tw)*bpsSum) / 8)
		}
		tileRaw := rawmode
		if planar == 2 {
			if layer < len(rawmode) {
				tileRaw = string(rawmode[layer])
			}
			stride /= len(bps)
		}
		bbox := [4]int{x, y, min(x+tw, xsize), min(y+th, ysize)}
		tiles = append(tiles, tiffRawTile{rawmode: tileRaw, offset: offset, stride: stride, bbox: bbox})
		x += tw
		if x >= xsize {
			x, y = 0, y+th
			if y >= ysize {
				y = 0
				layer++
			}
		}
	}
	return tiles, nil
}

func intsOf(v []int64) []int {
	out := make([]int, len(v))
	for i, x := range v {
		out[i] = int(x)
	}
	return out
}

// tiffDecodedData decodes the strips/tiles of an image whose compression is
// not "raw", producing the sample bytes in row order.
func tiffDecodedData(d *tiffIFD, data []byte, compression, xsize, ysize int, photo int64, bps []int64, planar int, spp int64) ([]byte, error) {
	if compression == 2 || compression == 3 || compression == 4 {
		return nil, ErrFormatNotPorted
	}
	if compression == 32771 || compression == 32809 || compression == 34676 || compression == 34677 ||
		compression == 34925 || compression == 50000 || compression == 50001 {
		return nil, ErrFormatNotPorted
	}
	if planar == 2 {
		return nil, ErrFormatNotPorted
	}
	bitsSum := int64(0)
	for _, b := range bps {
		bitsSum += b
	}
	rowBytes := int(int64(xsize)*bitsSum) / 8
	if bitsSum%8 != 0 {
		rowBytes = (int(int64(xsize)*bitsSum) + 7) / 8
	}
	out := make([]byte, rowBytes*ysize)
	tileset := false
	if v, ok := d.get(324); ok {
		tileset = true
		_ = v
	}
	var offsets, counts []int64
	var tw, th int
	if tileset {
		o, _ := d.get(324)
		c, _ := d.get(325)
		offsets, counts = o, c
		tw = int(d.get1(322, 0))
		th = int(d.get1(323, 0))
	} else {
		o, _ := d.get(273)
		c, _ := d.get(279)
		offsets, counts = o, c
		tw = xsize
		th = int(d.get1(278, int64(ysize)))
		if th <= 0 {
			th = ysize
		}
	}
	if len(offsets) == 0 {
		return nil, osError("decoder error -2")
	}
	for i, off := range offsets {
		var count int64
		if i < len(counts) {
			count = counts[i]
		}
		if count <= 0 || off < 0 || off+count > int64(len(data)) {
			continue
		}
		chunk, err := tiffDecompress(compression, data[off:off+count])
		if err != nil {
			return nil, err
		}
		tx := (i % max(1, (xsize+tw-1)/tw)) * tw
		ty := (i / max(1, (xsize+tw-1)/tw)) * th
		bytesPerLine := int(int64(tw)*bitsSum) / 8
		if bitsSum%8 != 0 {
			bytesPerLine = (int(int64(tw)*bitsSum) + 7) / 8
		}
		rows := min(th, ysize-ty)
		if len(chunk) < rows*bytesPerLine {
			return nil, osError("decoder error -2")
		}
		for r := 0; r < rows; r++ {
			src := chunk[r*bytesPerLine : (r+1)*bytesPerLine]
			dstY := ty + r
			line := bytesPerLine
			need := (int64(min(tw, xsize-tx)) * bitsSum) / 8
			if bitsSum%8 != 0 {
				need = (int64(min(tw, xsize-tx))*bitsSum + 7) / 8
			}
			outStart := dstY*rowBytes + int(int64(tx)*bitsSum)/8
			n := int(min(int64(line), need))
			if outStart+n > len(out) {
				n = max(0, len(out)-outStart)
			}
			copy(out[outStart:outStart+n], src[:n])
		}
	}
	if d.get1(317, 1) == 2 {
		tiffUndoPredictor(out, xsize, ysize, bps, int(spp), rowBytes)
	}
	return out, nil
}

// tiffDecompress decodes one strip/tile for the supported compressions.
func tiffDecompress(compression int, src []byte) ([]byte, error) {
	switch compression {
	case 32773:
		return packbitsDecodeAll(src), nil
	case 5:
		out, ok := tiffLZWDecode(src)
		if !ok {
			return nil, osError("decoder error -2")
		}
		return out, nil
	case 8, 32946:
		zr, err := zlib.NewReader(bytes.NewReader(src))
		if err != nil {
			return nil, osError("decoder error 0 when reading image file")
		}
		out, err := io.ReadAll(zr)
		if err != nil {
			return nil, osError("decoder error 0 when reading image file")
		}
		return out, nil
	}
	return nil, ErrFormatNotPorted
}

func packbitsDecodeAll(src []byte) []byte {
	var out []byte
	i := 0
	for i < len(src) {
		b := int(int8(src[i]))
		i++
		if b >= 0 {
			n := b + 1
			end := min(i+n, len(src))
			out = append(out, src[i:end]...)
			i = end
		} else if b != -128 {
			n := 1 - b
			if i >= len(src) {
				break
			}
			for j := 0; j < n; j++ {
				out = append(out, src[i])
			}
			i++
		}
	}
	return out
}

// tiffLZWDecode is libtiff's LZW (MSB-first codes, early change). ok is false
// when the stream ends without an EOI code, which libtiff reports as a
// decoding failure.
func tiffLZWDecode(src []byte) ([]byte, bool) {
	var out []byte
	table := make([][]byte, 4096)
	for i := 0; i < 256; i++ {
		table[i] = []byte{byte(i)}
	}
	next := 258
	width := 9
	var prev []byte
	bitpos := 0
	readCode := func() (int, bool) {
		if bitpos+width > len(src)*8 {
			return 0, false
		}
		v := 0
		for i := 0; i < width; i++ {
			byteIdx := (bitpos + i) >> 3
			bitIdx := 7 - uint((bitpos+i)&7)
			v = v<<1 | int((src[byteIdx]>>bitIdx)&1)
		}
		bitpos += width
		return v, true
	}
	for {
		code, ok := readCode()
		if !ok {
			return out, false
		}
		if code == 256 {
			for i := 0; i < 256; i++ {
				table[i] = []byte{byte(i)}
			}
			next = 258
			width = 9
			prev = nil
			continue
		}
		if code == 257 {
			return out, true
		}
		var entry []byte
		if code < next && code < len(table) && table[code] != nil {
			entry = table[code]
		} else if prev != nil {
			entry = append(append([]byte{}, prev...), prev[0])
		} else {
			return out, false
		}
		out = append(out, entry...)
		if prev != nil && next < 4096 {
			table[next] = append(append([]byte{}, prev...), entry[0])
			next++
			if next == (1<<uint(width))-1 && width < 12 {
				width++
			}
		}
		prev = entry
	}
}

// tiffUndoPredictor reverses the horizontal differencing predictor.
func tiffUndoPredictor(data []byte, xsize, ysize int, bps []int64, spp, rowBytes int) {
	bitsSum := int64(0)
	for _, b := range bps {
		bitsSum += b
	}
	if bitsSum%8 != 0 || len(bps) == 0 {
		return
	}
	bytesPerSample := int(bps[0] / 8)
	if bytesPerSample == 0 {
		return
	}
	for y := 0; y < ysize; y++ {
		row := data[y*rowBytes : (y+1)*rowBytes]
		if bytesPerSample == 1 {
			for x := spp; x < len(row); x++ {
				row[x] += row[x-spp]
			}
		} else {
			samples := len(row) / bytesPerSample
			for x := spp; x < samples; x++ {
				cur := row[x*bytesPerSample:]
				prev := row[(x-spp)*bytesPerSample:]
				switch bytesPerSample {
				case 2:
					v := binary.LittleEndian.Uint16(cur) + binary.LittleEndian.Uint16(prev)
					binary.LittleEndian.PutUint16(cur, v)
				case 4:
					v := binary.LittleEndian.Uint32(cur) + binary.LittleEndian.Uint32(prev)
					binary.LittleEndian.PutUint32(cur, v)
				}
			}
		}
	}
}

// loadTIFFCompressed converts the decoded sample bytes to pixels.
func (im *Image) loadTIFFCompressed(dec []byte, rawmode string, w, h int) error {
	rowBytes := len(dec) / h
	if rowBytes*h != len(dec) {
		rowBytes = len(dec) / h
	}
	im.ensurePix()
	im.Width, im.Height = w, h
	for y := 0; y < h; y++ {
		start := y * rowBytes
		end := start + rowBytes
		if end > len(dec) {
			end = len(dec)
		}
		if err := im.rawRow(dec[start:end], 0, y, w, rawmode, baseRawmode(rawmode), im.samplesPerPixel(), -1); err != nil {
			return err
		}
	}
	return nil
}

// adoptTransposed copies scratch into im through ImageOps.exif_transpose.
func (im *Image) adoptTransposed(scratch *Image, orientation int) error {
	pix, ow, oh := transposePixels(scratch, orientation)
	im.pix, im.pix16, im.pixI, im.pixF = pix, scratch.pix16, scratch.pixI, scratch.pixF
	im.Width, im.Height = ow, oh
	return nil
}

// transposePixels is ImageOps.exif_transpose for the eight orientations.
func transposePixels(src *Image, orientation int) ([]byte, int, int) {
	w, h := src.Width, src.Height
	n := src.samplesPerPixel()
	if orientation <= 1 || orientation > 8 || src.pix == nil {
		return src.pix, w, h
	}
	ow, oh := w, h
	if orientation >= 5 {
		ow, oh = h, w
	}
	out := make([]byte, ow*oh*n)
	for y := 0; y < oh; y++ {
		for x := 0; x < ow; x++ {
			var sx, sy int
			switch orientation {
			case 2:
				sx, sy = w-1-x, y
			case 3:
				sx, sy = w-1-x, h-1-y
			case 4:
				sx, sy = x, h-1-y
			case 5:
				sx, sy = y, x
			case 6:
				sx, sy = y, h-1-x
			case 7:
				sx, sy = w-1-y, h-1-x
			case 8:
				sx, sy = w-1-y, x
			}
			copy(out[(y*ow+x)*n:], src.pix[(sy*w+sx)*n:(sy*w+sx)*n+n])
		}
	}
	return out, ow, oh
}

// exifTranspose is ImageOps.exif_transpose for the eight orientation values.
func (im *Image) exifTranspose(orientation int) error {
	if orientation == 1 {
		return nil
	}
	n := im.samplesPerPixel()
	w, h := im.Width, im.Height
	var ow, oh int
	transpose := false
	switch orientation {
	case 2, 3, 4:
		ow, oh = w, h
	case 5, 6, 7, 8:
		ow, oh = h, w
		transpose = true
	}
	_ = transpose
	if im.pix == nil {
		return nil
	}
	out := make([]byte, ow*oh*n)
	src := func(x, y int) []byte { return im.pix[(y*w+x)*n : (y*w+x)*n+n] }
	for y := 0; y < oh; y++ {
		for x := 0; x < ow; x++ {
			var sx, sy int
			switch orientation {
			case 2:
				sx, sy = w-1-x, y
			case 3:
				sx, sy = w-1-x, h-1-y
			case 4:
				sx, sy = x, h-1-y
			case 5:
				sx, sy = y, x
			case 6:
				sx, sy = y, h-1-x
			case 7:
				sx, sy = w-1-y, h-1-x
			case 8:
				sx, sy = w-1-y, x
			}
			copy(out[(y*ow+x)*n:], src(sx, sy))
		}
	}
	im.pix = out
	im.Width, im.Height = ow, oh
	return nil
}
