package medicalform

import (
	"bytes"
	"compress/zlib"
	"encoding/binary"
	"image"
	"image/color"
	"image/jpeg"
	"testing"
)

// ─── small file builders ────────────────────────────────────────────────────

func zlibBytes(data []byte) []byte {
	var out bytes.Buffer
	writer := zlib.NewWriter(&out)
	_, _ = writer.Write(data)
	_ = writer.Close()
	return out.Bytes()
}

func pngChunk(kind string, data []byte) []byte {
	var out bytes.Buffer
	_ = binary.Write(&out, binary.BigEndian, uint32(len(data)))
	out.WriteString(kind)
	out.Write(data)
	crc := crc32IEEE(append([]byte(kind), data...))
	_ = binary.Write(&out, binary.BigEndian, crc)
	return out.Bytes()
}

func crc32IEEE(data []byte) uint32 {
	crc := ^uint32(0)
	for _, b := range data {
		crc ^= uint32(b)
		for i := 0; i < 8; i++ {
			if crc&1 != 0 {
				crc = crc>>1 ^ 0xedb88320
			} else {
				crc >>= 1
			}
		}
	}
	return ^crc
}

type pngOptions struct {
	depth     int
	colour    int
	rows      []byte
	interlace int
	noIDAT    bool
	noIEND    bool
	badCRC    bool
	afterIEND []byte
	split     int
	plte      []byte
	trns      []byte
}

func buildPNG(width, height int, options pngOptions) []byte {
	ihdr := make([]byte, 13)
	binary.BigEndian.PutUint32(ihdr[0:], uint32(width))
	binary.BigEndian.PutUint32(ihdr[4:], uint32(height))
	ihdr[8] = byte(options.depth)
	ihdr[9] = byte(options.colour)
	ihdr[12] = byte(options.interlace)
	out := pngSignature()
	header := pngChunk("IHDR", ihdr)
	if options.badCRC {
		header[len(header)-4] ^= 0xff
	}
	out = append(out, header...)
	if options.plte != nil {
		out = append(out, pngChunk("PLTE", options.plte)...)
	}
	if options.trns != nil {
		out = append(out, pngChunk("tRNS", options.trns)...)
	}
	if options.split < 0 {
		out = append(out, pngChunk("IDAT", nil)...)
	}
	if !options.noIDAT {
		payload := zlibBytes(options.rows)
		if options.split > 0 {
			for i := 0; i < len(payload); i += options.split {
				end := i + options.split
				if end > len(payload) {
					end = len(payload)
				}
				out = append(out, pngChunk("IDAT", payload[i:end])...)
			}
		} else {
			out = append(out, pngChunk("IDAT", payload)...)
		}
	}
	if !options.noIEND {
		out = append(out, pngChunk("IEND", nil)...)
	}
	return append(out, options.afterIEND...)
}

func pngSignature() []byte { return []byte("\x89PNG\r\n\x1a\n") }

// pngWithChunks puts extra chunks between IHDR and the image data.
func pngWithChunks(width, height, channels int, extra ...[]byte) []byte {
	out := append(pngSignature(), pngChunk("IHDR", pngIHDR(width, height, 8, channels))...)
	for _, chunk := range extra {
		out = append(out, chunk...)
	}
	out = append(out, pngChunk("IDAT", zlibBytes(pngRows(width, height, channels)))...)
	return append(out, pngChunk("IEND", nil)...)
}

// pngRows builds filter-0 scanlines for 8-bit gray/RGB samples.
func pngRows(width, height, channels int) []byte {
	var out []byte
	for y := 0; y < height; y++ {
		out = append(out, 0)
		for x := 0; x < width; x++ {
			for c := 0; c < channels; c++ {
				out = append(out, byte((x*37+y*11+c*53)%256))
			}
		}
	}
	return out
}

type bmpOptions struct {
	width, height int
	bpp           int
	rows          [][]byte // top-down
	palette       []byte
	compression   uint32
	header        int
	masks         []uint32
	topdown       bool
	stream        []byte // overrides rows (RLE)
	colors        uint32
}

func buildBMP(options bmpOptions) []byte {
	header := options.header
	if header == 0 {
		header = 40
	}
	entry := 4
	if header == 12 {
		entry = 3
	}
	var palette []byte
	for i := 0; i+2 < len(options.palette); i += 3 {
		palette = append(palette, options.palette[i+2], options.palette[i+1], options.palette[i])
		if entry == 4 {
			palette = append(palette, 0)
		}
	}
	stride := (options.width*options.bpp + 31) / 32 * 4
	var pixels []byte
	if options.stream != nil {
		pixels = options.stream
	} else {
		order := options.rows
		if !options.topdown {
			order = make([][]byte, len(options.rows))
			for i := range options.rows {
				order[len(options.rows)-1-i] = options.rows[i]
			}
		}
		for _, row := range order {
			line := append([]byte(nil), row...)
			for len(line) < stride {
				line = append(line, 0)
			}
			pixels = append(pixels, line[:stride]...)
		}
	}
	var info []byte
	if header == 12 {
		info = make([]byte, 12)
		binary.LittleEndian.PutUint32(info[0:], 12)
		binary.LittleEndian.PutUint16(info[4:], uint16(options.width))
		binary.LittleEndian.PutUint16(info[6:], uint16(options.height))
		binary.LittleEndian.PutUint16(info[8:], 1)
		binary.LittleEndian.PutUint16(info[10:], uint16(options.bpp))
	} else {
		info = make([]byte, header)
		binary.LittleEndian.PutUint32(info[0:], uint32(header))
		binary.LittleEndian.PutUint32(info[4:], uint32(options.width))
		height := int32(options.height)
		if options.topdown {
			height = -height
		}
		binary.LittleEndian.PutUint32(info[8:], uint32(height))
		binary.LittleEndian.PutUint16(info[12:], 1)
		binary.LittleEndian.PutUint16(info[14:], uint16(options.bpp))
		binary.LittleEndian.PutUint32(info[16:], options.compression)
		binary.LittleEndian.PutUint32(info[20:], uint32(len(pixels)))
		binary.LittleEndian.PutUint32(info[32:], options.colors)
		for i, mask := range options.masks {
			if 40+i*4+4 <= len(info) {
				binary.LittleEndian.PutUint32(info[40+i*4:], mask)
			}
		}
		// A version 3 header stores bit-field masks after the header itself.
		if header == 40 && (options.compression == 3 || options.compression == 6) {
			for _, mask := range options.masks {
				var b [4]byte
				binary.LittleEndian.PutUint32(b[:], mask)
				info = append(info, b[:]...)
			}
		}
	}
	offset := 14 + len(info) + len(palette)
	out := make([]byte, 14)
	copy(out, "BM")
	binary.LittleEndian.PutUint32(out[2:], uint32(offset+len(pixels)))
	binary.LittleEndian.PutUint32(out[10:], uint32(offset))
	out = append(out, info...)
	out = append(out, palette...)
	return append(out, pixels...)
}

func rgbRows(width, height int) [][]byte {
	rows := make([][]byte, height)
	for y := 0; y < height; y++ {
		row := make([]byte, 0, width*3)
		for x := 0; x < width; x++ {
			row = append(row, byte(x*37%256), byte(y*53%256), byte((x*y*7)%256))
		}
		rows[y] = row
	}
	return rows
}

// ─── TIFF builder ───────────────────────────────────────────────────────────

type tiffTag struct {
	tag    uint16
	typ    uint16
	values []uint32
}

func findTag(tags []tiffTag, tag uint16) int {
	for i, item := range tags {
		if item.tag == tag {
			return i
		}
	}
	return -1
}

func buildTIFF(order binary.ByteOrder, tags []tiffTag, blobs [][]byte) []byte {
	// Layout: header (8), IFD, out-of-line tag values, blobs.
	count := len(tags)
	ifdSize := 2 + count*12 + 4
	dataStart := 8 + ifdSize
	var data []byte
	offsets := make([]uint32, count)
	for i, tag := range tags {
		size := 0
		switch tag.typ {
		case 3:
			size = 2 * len(tag.values)
		case 4:
			size = 4 * len(tag.values)
		}
		if size > 4 {
			offsets[i] = uint32(dataStart + len(data))
			for _, value := range tag.values {
				if tag.typ == 3 {
					var b [2]byte
					order.PutUint16(b[:], uint16(value))
					data = append(data, b[:]...)
				} else {
					var b [4]byte
					order.PutUint32(b[:], value)
					data = append(data, b[:]...)
				}
			}
		}
	}
	blobStart := dataStart + len(data)
	blobOffsets := make([]uint32, len(blobs))
	for i, blob := range blobs {
		blobOffsets[i] = uint32(blobStart)
		blobStart += len(blob)
	}
	out := make([]byte, 8)
	order.PutUint16(out[0:], 0x4949)
	if order == binary.BigEndian {
		order.PutUint16(out[0:], 0x4d4d)
	}
	order.PutUint16(out[2:], 42)
	order.PutUint32(out[4:], 8)
	var index [2]byte
	order.PutUint16(index[:], uint16(count))
	out = append(out, index[:]...)
	values := make([]uint32, len(tags))
	for i, tag := range tags {
		values[i] = tag.values[0]
	}
	if tag := findTag(tags, 273); tag >= 0 && len(blobs) > 0 && values[tag] == 0 {
		values[tag] = blobOffsets[0]
	}
	for i, tag := range tags {
		var entry [12]byte
		order.PutUint16(entry[0:], tag.tag)
		order.PutUint16(entry[2:], tag.typ)
		order.PutUint32(entry[4:], uint32(len(tag.values)))
		switch {
		case tag.typ == 3 && len(tag.values) == 1:
			order.PutUint16(entry[8:], uint16(values[i]))
		case tag.typ == 4 && len(tag.values) == 1:
			order.PutUint32(entry[8:], values[i])
		case offsets[i] != 0:
			order.PutUint32(entry[8:], offsets[i])
		}
		out = append(out, entry[:]...)
	}
	out = append(out, 0, 0, 0, 0) // no next IFD
	out = append(out, data...)
	for _, blob := range blobs {
		out = append(out, blob...)
	}
	return out
}

// rgb16Predictor is one row of the MuPDF predictor test: raw 16-bit samples
// whose horizontal differences are stored.
func rgb16PredictorSamples() []byte {
	values := []uint16{0, 9001, 18002, 3001, 3001, 3001, 3001, 3001, 3001}
	out := make([]byte, len(values)*2)
	for i, v := range values {
		binary.BigEndian.PutUint16(out[i*2:], v)
	}
	return out
}

// ─── PNM / PSD / GIF builders ───────────────────────────────────────────────

func pamFile(width, height, depth, maxval int, tuple string, body []byte) []byte {
	head := "P7\n"
	head += "WIDTH " + itoa(width) + "\n"
	head += "HEIGHT " + itoa(height) + "\n"
	head += "DEPTH " + itoa(depth) + "\n"
	head += "MAXVAL " + itoa(maxval) + "\n"
	if tuple != "" {
		head += "TUPLTYPE " + tuple + "\n"
	}
	head += "ENDHDR\n"
	return append([]byte(head), body...)
}

func itoa(v int) string {
	if v == 0 {
		return "0"
	}
	var digits []byte
	for v > 0 {
		digits = append([]byte{byte('0' + v%10)}, digits...)
		v /= 10
	}
	return string(digits)
}

func psdFile(width, height, channels, colourType, bpc, compression int, planes [][]byte) []byte {
	var out bytes.Buffer
	out.WriteString("8BPS")
	_ = binary.Write(&out, binary.BigEndian, uint16(1))
	out.Write(make([]byte, 6))
	_ = binary.Write(&out, binary.BigEndian, uint16(channels))
	_ = binary.Write(&out, binary.BigEndian, uint32(height))
	_ = binary.Write(&out, binary.BigEndian, uint32(width))
	_ = binary.Write(&out, binary.BigEndian, uint16(bpc))
	_ = binary.Write(&out, binary.BigEndian, uint16(colourType))
	_ = binary.Write(&out, binary.BigEndian, uint32(0)) // colour mode data
	_ = binary.Write(&out, binary.BigEndian, uint32(0)) // image resources
	_ = binary.Write(&out, binary.BigEndian, uint32(0)) // layer data
	_ = binary.Write(&out, binary.BigEndian, uint16(compression))
	if compression == 0 {
		for _, plane := range planes {
			out.Write(plane)
		}
		return out.Bytes()
	}
	var counts, payload bytes.Buffer
	for _, plane := range planes {
		rowBytes := width * (bpc / 8)
		for y := 0; y < height; y++ {
			row := plane[y*rowBytes : (y+1)*rowBytes]
			encoded := packBitsForTest(row)
			_ = binary.Write(&counts, binary.BigEndian, uint16(len(encoded)))
			payload.Write(encoded)
		}
	}
	out.Write(counts.Bytes())
	out.Write(payload.Bytes())
	return out.Bytes()
}

func packBitsForTest(data []byte) []byte {
	out := []byte{byte(len(data) - 1)}
	return append(out, data...)
}

func gifFile(width, height int, palette []byte, frames [][]int, transparent int) []byte {
	var out bytes.Buffer
	out.WriteString("GIF89a")
	tableBits := 1
	for 1<<tableBits < len(palette)/3 {
		tableBits++
	}
	_ = binary.Write(&out, binary.LittleEndian, uint16(width))
	_ = binary.Write(&out, binary.LittleEndian, uint16(height))
	out.WriteByte(0x80 | 0x70 | byte(tableBits-1))
	out.WriteByte(0)
	out.WriteByte(0)
	table := append([]byte(nil), palette...)
	for len(table) < 3*(1<<tableBits) {
		table = append(table, 0, 0, 0)
	}
	out.Write(table)
	for _, indices := range frames {
		flags := byte(0)
		index := byte(0)
		if transparent >= 0 {
			flags, index = 1, byte(transparent)
		}
		out.Write([]byte{0x21, 0xf9, 0x04, flags, 0, 0, index, 0})
		out.WriteByte(0x2c)
		for _, v := range []int{0, 0, width, height} {
			_ = binary.Write(&out, binary.LittleEndian, uint16(v))
		}
		out.WriteByte(0)
		out.WriteByte(8)
		compressed := gifEncodeForTest(indices)
		for len(compressed) > 0 {
			n := len(compressed)
			if n > 255 {
				n = 255
			}
			out.WriteByte(byte(n))
			out.Write(compressed[:n])
			compressed = compressed[n:]
		}
		out.WriteByte(0)
	}
	out.WriteByte(0x3b)
	return out.Bytes()
}

// gifEncodeForTest is a minimal GIF LZW encoder (LSB first, no early change).
func gifEncodeForTest(indices []int) []byte {
	const clear, end = 256, 257
	codeSize := 9
	next := end + 1
	table := map[string]int{}
	for i := 0; i < 256; i++ {
		table[string([]byte{byte(i)})] = i
	}
	var bits []int
	write := func(code int) {
		for i := 0; i < codeSize; i++ {
			bits = append(bits, code>>uint(i)&1)
		}
	}
	write(clear)
	current := []byte{byte(indices[0])}
	for _, value := range indices[1:] {
		candidate := append(append([]byte(nil), current...), byte(value))
		if _, ok := table[string(candidate)]; ok {
			current = candidate
			continue
		}
		write(table[string(current)])
		table[string(candidate)] = next
		next++
		if next == 1<<codeSize && codeSize < 12 {
			codeSize++
		}
		current = []byte{byte(value)}
	}
	write(table[string(current)])
	write(end)
	var out []byte
	var acc, n int
	for _, bit := range bits {
		acc |= bit << uint(n)
		n++
		if n == 8 {
			out = append(out, byte(acc))
			acc, n = 0, 0
		}
	}
	if n > 0 {
		out = append(out, byte(acc))
	}
	return out
}

// ─── the decision table ─────────────────────────────────────────────────────

func TestImageFormatDecisions(t *testing.T) {
	grayScan := pngRows(7, 5, 1)
	rgbScan := pngRows(7, 5, 3)
	good := buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgbScan})
	goodGray := buildPNG(7, 5, pngOptions{depth: 8, colour: 0, rows: grayScan})
	// A JPEG from the standard library for the cut cases.
	jpegBytes := func() []byte {
		picture := image.NewRGBA(image.Rect(0, 0, 8, 8))
		for y := 0; y < 8; y++ {
			for x := 0; x < 8; x++ {
				picture.Set(x, y, color.RGBA{uint8(x * 30), uint8(y * 30), 90, 255})
			}
		}
		var buffer bytes.Buffer
		if err := jpeg.Encode(&buffer, picture, nil); err != nil {
			t.Fatal(err)
		}
		return buffer.Bytes()
	}()
	sos := bytes.Index(jpegBytes, []byte{0xff, 0xda})
	sosLength := int(jpegBytes[sos+2])<<8 | int(jpegBytes[sos+3])
	badDHT := append([]byte(nil), jpegBytes...)
	if dht := bytes.Index(badDHT, []byte{0xff, 0xc4}); dht >= 0 {
		badDHT[dht+5] = 0xff // 255 codes of length one
	}
	zeroComponents := append([]byte(nil), jpegBytes...)
	if sof := bytes.Index(zeroComponents, []byte{0xff, 0xc0}); sof >= 0 {
		zeroComponents[sof+9] = 0
	}
	gif := gifFile(4, 3, []byte{255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255},
		[][]int{{0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3}}, -1)
	gifNoTrailer := gif[:len(gif)-1]
	gifCutBlocks := append([]byte(nil), gif...)
	// Cut the first data sub-block in half (the chain no longer terminates).
	at := bytes.Index(gifCutBlocks, []byte{0x2c})
	at += 11
	gifCutBlocks = append(gifCutBlocks[:at], append([]byte{200, 1, 2}, 0x3b)...)
	gifBadCode := append([]byte(nil), gif...)
	descriptor := bytes.Index(gifBadCode, []byte{0x2c})
	gifBadCode[descriptor+12] = 0xff
	// A properly framed but short LZW stream: MuPDF warns, pads the rest of
	// the image with zeros and keeps the file open.
	gifShort := append([]byte(nil), gif[:descriptor+11]...)
	gifShort = append(gifShort, 3, gif[descriptor+12], gif[descriptor+13], gif[descriptor+14], 0, 0x3b)

	tiffGray := buildTIFF(binary.LittleEndian, []tiffTag{
		{256, 4, []uint32{2}}, {257, 4, []uint32{2}}, {258, 3, []uint32{8}},
		{259, 3, []uint32{1}}, {262, 3, []uint32{1}}, {273, 4, []uint32{0}},
		{277, 3, []uint32{1}}, {278, 4, []uint32{2}}, {279, 4, []uint32{4}},
	}, [][]byte{{0, 64, 128, 255}})
	tiffTruncated := buildTIFF(binary.LittleEndian, []tiffTag{
		{256, 4, []uint32{2}}, {257, 4, []uint32{2}}, {258, 3, []uint32{8}},
		{259, 3, []uint32{1}}, {262, 3, []uint32{1}}, {273, 4, []uint32{0}},
		{277, 3, []uint32{1}}, {278, 4, []uint32{2}}, {279, 4, []uint32{10000}},
	}, [][]byte{{0, 64, 128, 255}})
	tiffBadPhoto := buildTIFF(binary.LittleEndian, []tiffTag{
		{256, 4, []uint32{2}}, {257, 4, []uint32{2}}, {258, 3, []uint32{8}},
		{259, 3, []uint32{1}}, {262, 3, []uint32{7}}, {273, 4, []uint32{0}},
		{277, 3, []uint32{1}}, {278, 4, []uint32{2}}, {279, 4, []uint32{4}},
	}, [][]byte{{0, 1, 2, 3}})

	psdRGB := psdFile(2, 1, 3, 3, 8, 0, [][]byte{{10, 20}, {30, 40}, {50, 60}})
	psdDeflate := append([]byte(nil), psdRGB...)
	// The compression word follows the 38-byte header.
	binary.BigEndian.PutUint16(psdDeflate[38:], 2)

	cases := []struct {
		name string
		file []byte
		open bool
	}{
		{"png valid rgb", good, true},
		{"png valid gray", goodGray, true},
		{"png no image data", buildPNG(7, 5, pngOptions{depth: 8, colour: 2, noIDAT: true}), true},
		{"png no image data no iend", buildPNG(7, 5, pngOptions{depth: 8, colour: 2, noIDAT: true, noIEND: true}), true},
		{"png bad crc", buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgbScan, badCRC: true}), true},
		{"png missing iend", buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgbScan, noIEND: true}), true},
		{"png after iend", buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgbScan, afterIEND: []byte("garbage")}), true},
		{"png unknown critical", func() []byte {
			out := append(pngSignature(), pngChunk("IHDR", pngIHDR(7, 5, 8, 2))...)
			out = append(out, pngChunk("ZZZZ", []byte("x"))...)
			out = append(out, pngChunk("IDAT", zlibBytes(rgbScan))...)
			return append(out, pngChunk("IEND", nil)...)
		}(), true},
		{"png split idat", buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgbScan, split: 5}), true},
		{"png gama", pngWithChunks(7, 5, 2, pngChunk("gAMA", []byte{0, 0, 0xb1, 0x8f})), true},
		{"png srgb", pngWithChunks(7, 5, 2, pngChunk("sRGB", []byte{0})), true},
		{"png iccp", pngWithChunks(7, 5, 2,
			pngChunk("iCCP", append([]byte("sRGB\x00\x00"), zlibBytes([]byte("not a profile"))...))), true},
		{"png iccp broken", pngWithChunks(7, 5, 2, pngChunk("iCCP", []byte("sRGB\x00\x00nope"))), true},
		{"png interlaced 1x1", buildPNG(1, 1, pngOptions{depth: 8, colour: 2, interlace: 1, rows: []byte{0, 1, 2, 3}}), true},
		{"png interlaced adam7", buildPNG(5, 3, pngOptions{depth: 8, colour: 2, interlace: 1, rows: pngInterlacedRows(5, 3)}), true},
		{"png zero length idat", buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgbScan, split: -1}), false},
		{"png bad depth", buildPNG(7, 5, pngOptions{depth: 3, colour: 0, rows: grayScan}), false},
		{"png too short", []byte("\x89PNG\r\n\x1a\nIHDR"), false},
		{"jpeg valid", jpegBytes, true},
		{"jpeg after sos header", jpegBytes[:sos+2+sosLength], true},
		{"jpeg cut in sos header", jpegBytes[:sos+4], false},
		{"jpeg bad huffman counts", badDHT, false},
		{"jpeg zero components", zeroComponents, false},
		{"jpeg only soi", []byte{0xff, 0xd8}, false},
		{"jpeg trailing", append(append([]byte(nil), jpegBytes...), "trailing"...), true},
		{"jpeg garbage scan", append(append([]byte(nil), jpegBytes[:sos+2+sosLength]...), 1, 2, 3, 4), true},
		{"gif valid", gif, true},
		{"gif no trailer", gifNoTrailer, false},
		{"gif truncated subblock", gifCutBlocks, false},
		{"gif invalid lzw code", gifBadCode, true},
		{"gif short lzw stream", gifShort, true},
		{"bmp 24", buildBMP(bmpOptions{width: 4, height: 3, bpp: 24, rows: rgbRows(4, 3)}), true},
		{"bmp 24 topdown", buildBMP(bmpOptions{width: 4, height: 3, bpp: 24, rows: rgbRows(4, 3), topdown: true}), true},
		{"bmp 32 bgrx", buildBMP(bmpOptions{width: 4, height: 3, bpp: 32,
			rows: [][]byte{{1, 2, 3, 0, 4, 5, 6, 0, 7, 8, 9, 0, 10, 11, 12, 0}}}), true},
		{"bmp 32 bitfields alpha", buildBMP(bmpOptions{width: 2, height: 1, bpp: 32, compression: 6,
			masks: []uint32{0xff0000, 0xff00, 0xff, 0xff000000},
			rows:  [][]byte{{3, 2, 1, 128, 6, 5, 4, 255}}}), true},
		{"bmp 16 555", buildBMP(bmpOptions{width: 2, height: 1, bpp: 16,
			rows: [][]byte{{0x1f, 0x7c, 0xe0, 0x03}}}), true},
		{"bmp 16 565 bitfields", buildBMP(bmpOptions{width: 2, height: 1, bpp: 16, compression: 3,
			masks: []uint32{0xf800, 0x07e0, 0x001f}, rows: [][]byte{{0x1f, 0xf8, 0xe0, 0x07}}}), true},
		{"bmp 8 palette", buildBMP(bmpOptions{width: 4, height: 2, bpp: 8, colors: 4,
			palette: []byte{0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255},
			rows:    [][]byte{{0, 1, 2, 3}, {3, 2, 1, 0}}}), true},
		{"bmp 4", buildBMP(bmpOptions{width: 4, height: 1, bpp: 4, colors: 16,
			palette: bytes.Repeat([]byte{0, 0, 0}, 16), rows: [][]byte{{0x01, 0x23}}}), true},
		{"bmp 1 os2", buildBMP(bmpOptions{width: 8, height: 1, bpp: 1, header: 12,
			palette: []byte{0, 0, 0, 255, 255, 255}, rows: [][]byte{{0x0f}}}), true},
		{"bmp 64", buildBMP(bmpOptions{width: 1, height: 1, bpp: 64,
			rows: [][]byte{{0xff, 0xff, 0, 0, 0, 0, 0x80, 0x80}}}), true},
		{"bmp rle8", buildBMP(bmpOptions{width: 3, height: 2, bpp: 8, compression: 1, colors: 4,
			palette: []byte{0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255},
			stream:  []byte{3, 1, 3, 2, 0, 0, 3, 3, 0, 1}}), true},
		{"bmp rle4", buildBMP(bmpOptions{width: 4, height: 1, bpp: 4, compression: 2, colors: 16,
			palette: bytes.Repeat([]byte{0, 0, 0}, 16), stream: []byte{4, 0x12, 0, 1}}), true},
		{"bmp bad compression", buildBMP(bmpOptions{width: 2, height: 1, bpp: 8, compression: 9,
			palette: bytes.Repeat([]byte{0, 0, 0}, 256), rows: [][]byte{{0, 1}}}), false},
		{"tiff gray raw", tiffGray, true},
		{"tiff truncated strip", tiffTruncated, false},
		{"tiff bad photometric", tiffBadPhoto, false},
		{"pnm p1", []byte("P1\n2 1\n1 0\n"), true},
		{"pnm p2", []byte("P2\n2 1\n255\n0 255\n"), true},
		{"pnm p3", []byte("P3\n1 1\n255\n1 2 3\n"), true},
		{"pnm p4", []byte("P4\n8 1\n\x0f"), true},
		{"pnm p5 255", []byte("P5\n2 1\n255\n\x00\xff"), true},
		{"pnm p5 65535", append([]byte("P5\n1 1\n65535\n"), 0xff, 0x00), true},
		{"pnm p6", append([]byte("P6\n1 1\n255\n"), 1, 2, 3), true},
		{"pnm truncated", []byte("P6\n2 2\n255\n\x01\x02\x03"), false},
		{"pnm bad maxval", []byte("P5\n1 1\n0\n\x00"), false},
		{"pam rgb", pamFile(1, 1, 3, 255, "RGB", []byte{1, 2, 3}), true},
		{"pam rgba", pamFile(1, 1, 4, 255, "RGB_ALPHA", []byte{1, 2, 3, 4}), true},
		{"pam gray", pamFile(1, 1, 1, 255, "GRAYSCALE", []byte{7}), true},
		{"pam bw", pamFile(2, 1, 1, 1, "BLACKANDWHITE", []byte{1, 0}), true},
		{"pam cmyk", pamFile(1, 1, 4, 255, "CMYK", []byte{1, 2, 3, 4}), true},
		{"pam guessed depth", pamFile(1, 1, 3, 255, "", []byte{1, 2, 3}), true},
		{"pam bad depth", pamFile(1, 1, 2, 255, "RGB", []byte{1, 2}), false},
		{"pfm gray", append([]byte("Pf\n2 1\n-1.0\n"), 0, 0, 0x80, 0x3f, 0, 0, 0, 0x40), true},
		{"psd rgb", psdRGB, true},
		{"psd deflate", psdDeflate, false},
		{"psd truncated", psdRGB[:len(psdRGB)-2], false},
	}
	for _, item := range cases {
		asset, ok := decodeImage(item.file)
		if ok != item.open {
			t.Errorf("%s: open=%v, want %v", item.name, ok, item.open)
			continue
		}
		if ok && (asset.width <= 0 || asset.height <= 0) {
			t.Errorf("%s: %dx%d", item.name, asset.width, asset.height)
		}
	}
}

func pngIHDR(width, height, depth, colour int) []byte {
	p := make([]byte, 13)
	binary.BigEndian.PutUint32(p[0:], uint32(width))
	binary.BigEndian.PutUint32(p[4:], uint32(height))
	p[8], p[9] = byte(depth), byte(colour)
	return p
}

// ─── samples ────────────────────────────────────────────────────────────────

// pngInterlacedRows builds the seven Adam7 passes (filter 0) of an RGB image.
func pngInterlacedRows(width, height int) []byte {
	passes := [7][4]int{{0, 8, 0, 8}, {4, 8, 0, 8}, {0, 4, 4, 8}, {2, 4, 0, 4},
		{0, 2, 2, 4}, {1, 2, 0, 2}, {0, 1, 1, 2}}
	var out []byte
	for _, pass := range passes {
		w := (width + pass[1] - pass[0] - 1) / pass[1]
		h := (height + pass[3] - pass[2] - 1) / pass[3]
		if w <= 0 || h <= 0 {
			continue
		}
		for y := 0; y < h; y++ {
			out = append(out, 0)
			for x := 0; x < w; x++ {
				px, py := x*pass[1]+pass[0], y*pass[3]+pass[2]
				out = append(out, byte(px*37%256), byte(py*53%256), byte((px*py*7)%256))
			}
		}
	}
	return out
}

// A PNG with IHDR but no image data opens with 0xff samples, as
// png_read_image warns and fills the rest of the buffer.
func TestPNGNoImageDataIsWhite(t *testing.T) {
	file := buildPNG(3, 2, pngOptions{depth: 8, colour: 2, noIDAT: true})
	asset, ok := decodeImage(file)
	if !ok {
		t.Fatal("refused")
	}
	if len(asset.color) != 3*2*3 {
		t.Fatalf("%d samples", len(asset.color))
	}
	for _, sample := range asset.color {
		if sample != 0xff {
			t.Fatalf("sample %d, want 255", sample)
		}
	}
}

// The Adam7 de-interlacer puts every pass's pixels at its position.
func TestPNGInterlaced(t *testing.T) {
	for _, size := range [][2]int{{1, 1}, {5, 3}, {7, 5}} {
		file := buildPNG(size[0], size[1], pngOptions{depth: 8, colour: 2, interlace: 1,
			rows: pngInterlacedRows(size[0], size[1])})
		asset, ok := decodeImage(file)
		if !ok {
			t.Fatalf("%dx%d refused", size[0], size[1])
		}
		want := make([]byte, 0, size[0]*size[1]*3)
		for y := 0; y < size[1]; y++ {
			for x := 0; x < size[0]; x++ {
				want = append(want, byte(x*37%256), byte(y*53%256), byte((x*y*7)%256))
			}
		}
		if !bytes.Equal(asset.color, want) {
			t.Errorf("%dx%d: samples differ", size[0], size[1])
		}
	}
}

// The lenient PNG variants decode to the same samples as the strict file.
func TestPNGLeniencyKeepsSamples(t *testing.T) {
	rgb := pngRows(7, 5, 3)
	strict, ok := decodeImage(buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgb}))
	if !ok {
		t.Fatal("strict refused")
	}
	for name, file := range map[string][]byte{
		"bad crc":    buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgb, badCRC: true}),
		"no iend":    buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgb, noIEND: true}),
		"after iend": buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgb, afterIEND: []byte("x")}),
		"split idat": buildPNG(7, 5, pngOptions{depth: 8, colour: 2, rows: rgb, split: 3}),
	} {
		asset, ok := decodeImage(file)
		if !ok || !bytes.Equal(asset.color, strict.color) {
			t.Errorf("%s: differs from the strict file", name)
		}
	}
}

// MuPDF's TIFF predictor stores the previous sample in an unsigned char, so a
// 16-bit predictor only carries the low byte. The expected bytes are what
// load-tiff.c produces for the raw samples 0,9001,18002,3001,...
func TestTIFFPredictorTruncatesToByte(t *testing.T) {
	pixels := rgb16PredictorSamples()
	file := buildTIFF(binary.BigEndian, []tiffTag{
		{256, 4, []uint32{3}}, {257, 4, []uint32{1}}, {258, 3, []uint32{16, 16, 16}},
		{259, 3, []uint32{8}}, {262, 3, []uint32{2}}, {273, 4, []uint32{0}},
		{277, 3, []uint32{3}}, {278, 4, []uint32{1}}, {279, 4, []uint32{uint32(len(pixels))}},
		{317, 3, []uint32{2}},
	}, [][]byte{zlibBytes(pixels)})
	asset, ok := decodeImage(file)
	if !ok {
		t.Fatal("refused")
	}
	want := []byte{0, 35, 70, 11, 11, 12, 12, 12, 11}
	if !bytes.Equal(asset.color, want) {
		t.Fatalf("samples %v, want %v", asset.color, want)
	}
}

// An animated GIF paints every frame onto one canvas.
func TestGIFCompositesFrames(t *testing.T) {
	palette := []byte{255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255}
	// Frame 1: 2x2 all green. Frame 2: 2x2 all blue.
	file := gifFile(2, 2, palette, [][]int{{1, 1, 1, 1}, {2, 2, 2, 2}}, -1)
	asset, ok := decodeImage(file)
	if !ok {
		t.Fatal("refused")
	}
	for i := 0; i < 4; i++ {
		want := []byte{0, 0, 255}
		if !bytes.Equal(asset.color[i*3:i*3+3], want) {
			t.Fatalf("pixel %d = %v, want blue", i, asset.color[i*3:i*3+3])
		}
	}
}

// A transparent pixel of a later frame leaves the earlier frame's colour.
func TestGIFTransparencyKeepsEarlierFrame(t *testing.T) {
	palette := []byte{255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255}
	file := gifFile(2, 1, palette, [][]int{{1, 1}, {2, 3}}, 3)
	asset, ok := decodeImage(file)
	if !ok {
		t.Fatal("refused")
	}
	if !bytes.Equal(asset.color[0:3], []byte{0, 0, 255}) {
		t.Errorf("opaque pixel = %v, want blue", asset.color[0:3])
	}
	if !bytes.Equal(asset.color[3:6], []byte{0, 255, 0}) {
		t.Errorf("transparent pixel = %v, want the earlier green", asset.color[3:6])
	}
}

// A 32-bit BMP with an alpha mask keeps alpha and the image is premultiplied
// then divided again, as pdf_add_image writes it.
func TestBMPBitfieldsAlpha(t *testing.T) {
	// BI_ALPHABITS (6) is the version 3 header form whose mask header has the
	// alpha mask; BI_BITFIELDS reads only the three colour masks.
	file := buildBMP(bmpOptions{width: 2, height: 1, bpp: 32, compression: 6,
		masks: []uint32{0xff0000, 0xff00, 0xff, 0xff000000},
		rows:  [][]byte{{3, 2, 1, 128, 6, 5, 4, 255}}})
	asset, ok := decodeImage(file)
	if !ok {
		t.Fatal("refused")
	}
	if !asset.alpha || asset.maskBits != 8 {
		t.Fatalf("alpha=%v bits=%d", asset.alpha, asset.maskBits)
	}
	if !bytes.Equal(asset.mask, []byte{128, 255}) {
		t.Errorf("mask %v", asset.mask)
	}
	// fz_mul255/fz_div255 round: pixel 0 (1,2,3 at alpha 128) comes back
	// (1,1,3); the opaque pixel is unchanged.
	if !bytes.Equal(asset.color, []byte{1, 1, 3, 4, 5, 6}) {
		t.Errorf("colour %v", asset.color)
	}
}

// PSD channels are stored as planes and interleaved for the asset.
func TestPSDPlaneOrder(t *testing.T) {
	file := psdFile(2, 1, 3, 3, 8, 0, [][]byte{{10, 20}, {30, 40}, {50, 60}})
	asset, ok := decodeImage(file)
	if !ok {
		t.Fatal("refused")
	}
	if !bytes.Equal(asset.color, []byte{10, 30, 50, 20, 40, 60}) {
		t.Fatalf("colour %v", asset.color)
	}
}

// The ASCII/16-bit PNM scaling is map_color: float32 division, truncated.
func TestPNMScaling(t *testing.T) {
	file := append([]byte("P5\n2 1\n1000\n"), 0, 0, 0x03, 0xe7) // 0 and 999
	asset, ok := decodeImage(file)
	if !ok {
		t.Fatal("refused")
	}
	if asset.color[0] != 0 || asset.color[1] != 254 {
		t.Fatalf("samples %v, want [0 254]", asset.color)
	}
}
