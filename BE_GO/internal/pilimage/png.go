package pilimage

import (
	"bytes"
	"compress/flate"
	"compress/zlib"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"io"
)

// pngState is the PngStream/PngImageFile state needed after open.
type pngState struct {
	rawmode string
	// idatOffset is where the IDAT/fdAT data the decoder starts with begins,
	// idatLength its length (already excluding the fdAT sequence number).
	idatOffset int
	idatLength int
	haveTile   bool

	interlaced bool
	// bboxValue is the APNG first frame's fcTL bounding box when it is known.
	bboxValue [4]int
	haveBBox  bool
	// interlaceY is the row the Adam7 decoder is filling.
	interlaceY int
}

// pngModes is PngImagePlugin._MODES: (bit depth, colour type) -> mode/rawmode.
var pngModes = map[[2]int][2]string{
	{1, 0}:  {"1", "1"},
	{2, 0}:  {"L", "L;2"},
	{4, 0}:  {"L", "L;4"},
	{8, 0}:  {"L", "L"},
	{16, 0}: {"I;16", "I;16B"},
	{8, 2}:  {"RGB", "RGB"},
	{16, 2}: {"RGB", "RGB;16B"},
	{1, 3}:  {"P", "P;1"},
	{2, 3}:  {"P", "P;2"},
	{4, 3}:  {"P", "P;4"},
	{8, 3}:  {"P", "P"},
	{8, 4}:  {"LA", "LA"},
	{16, 4}: {"RGBA", "LA;16B"},
	{8, 6}:  {"RGBA", "RGBA"},
	{16, 6}: {"RGBA", "RGBA;16B"},
}

var pngMagic = []byte("\x89PNG\r\n\x1a\n")

func acceptPNG(prefix []byte) bool { return bytes.HasPrefix(prefix, pngMagic) }

// chunk is one PNG chunk header plus the file offset of its data.
type chunk struct {
	cid    []byte
	offset int
	length int
}

// readChunk is PngStream.read.
func readChunk(data []byte, pos int) (chunk, int, bool) {
	if pos+8 > len(data) {
		return chunk{}, pos, false
	}
	length := int(binary.BigEndian.Uint32(data[pos:]))
	cid := data[pos+4 : pos+8]
	return chunk{cid: cid, offset: pos + 8, length: length}, pos + 8, true
}

// validCID is PngImagePlugin.is_cid (re \w\w\w\w).
func validCID(cid []byte) bool {
	if len(cid) != 4 {
		return false
	}
	for _, c := range cid {
		if !(c == '_' || c >= '0' && c <= '9' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z') {
			return false
		}
	}
	return true
}

// pyReprBytes is Python's repr() for a bytes object.
func pyReprBytes(b []byte) string {
	quote := byte('\'')
	if bytes.ContainsRune(b, '\'') && !bytes.ContainsRune(b, '"') {
		quote = '"'
	}
	var sb bytes.Buffer
	sb.WriteByte('b')
	sb.WriteByte(quote)
	for _, c := range b {
		switch c {
		case '\\':
			sb.WriteString(`\\`)
		case quote:
			sb.WriteByte('\\')
			sb.WriteByte(c)
		case '\t':
			sb.WriteString(`\t`)
		case '\n':
			sb.WriteString(`\n`)
		case '\r':
			sb.WriteString(`\r`)
		default:
			if c >= 0x20 && c < 0x7f {
				sb.WriteByte(c)
			} else {
				sb.WriteString(`\x`)
				const hex = "0123456789abcdef"
				sb.WriteByte(hex[c>>4])
				sb.WriteByte(hex[c&15])
			}
		}
	}
	sb.WriteByte(quote)
	return sb.String()
}

// pngChunkData reads a chunk's payload, raising OSError("Truncated File Read")
// when the file is shorter than the chunk says (ImageFile._safe_read).
func pngChunkData(data []byte, c chunk) ([]byte, error) {
	if c.length < 0 || c.offset+c.length > len(data) {
		return nil, osError("Truncated File Read")
	}
	return data[c.offset : c.offset+c.length], nil
}

// openPNG is PngImageFile._open.
func openPNG(data []byte) (*Image, error) {
	if !acceptPNG(data) {
		return nil, syntaxError("not a PNG file")
	}
	im := newImage(data, "PNG", "", 0, 0)
	st := &pngState{}
	im.png = st

	// PngStream state that feeds the attributes copied after the loop.
	var (
		imSize       [2]int
		imMode       string
		rawmode      string
		imPalette    []byte
		havePalette  bool
		imTile       bool
		lastCID      []byte
		lastLength   int
		lastOffset   int
		seqNum       *int
		nFrames      int
		nFramesKnown bool
		bbox         [4]int
		haveBBox     bool
		haveDefault  bool
	)
	info := map[string]any{}

	pos := 8 // after the signature
	for {
		if pos+8 > len(data) {
			// struct.error/IndexError from fp.read(8)+i32 -> SyntaxError.
			return nil, syntaxError("broken PNG file (chunk b'')")
		}
		c, next, ok := readChunk(data, pos)
		if !ok {
			return nil, syntaxError("broken PNG file (chunk b'')")
		}
		pos = next
		if !validCID(c.cid) {
			return nil, syntaxError("broken PNG file (chunk " + pyReprBytes(c.cid) + ")")
		}
		stop := false
		var payload []byte
		switch string(c.cid) {
		case "IHDR":
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
			if c.length < 13 {
				return nil, valueError("Truncated IHDR chunk")
			}
			imSize = [2]int{int(binary.BigEndian.Uint32(s[0:])), int(binary.BigEndian.Uint32(s[4:]))}
			if m, ok := pngModes[[2]int{int(s[8]), int(s[9])}]; ok {
				imMode, rawmode = m[0], m[1]
			}
			if s[12] != 0 {
				info["interlace"] = 1
			}
			if s[11] != 0 {
				return nil, syntaxError("unknown filter category")
			}
		case "IDAT":
			// chunk_IDAT: record the tile and stop reading.
			if haveBBox || nFramesKnown {
				// bbox tile or default image; both decode from this offset.
			}
			if nFramesKnown && !haveBBox {
				haveDefault = true
			}
			imTile = true
			lastCID = c.cid
			lastLength = c.length
			lastOffset = c.offset
			stop = true
		case "IEND":
			lastCID = c.cid
			stop = true
		case "PLTE":
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
			if imMode == "P" {
				imPalette = s
				havePalette = true
			}
		case "tRNS":
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
			switch imMode {
			case "P":
				if simple, idx := pngSimplePalette(s); simple {
					im.transparency = transparency{present: true, isInt: true, index: idx}
				} else {
					im.transparency = transparency{present: true, data: append([]byte(nil), s...)}
				}
			case "1", "L", "I;16", "RGB":
				if c.length < 6 {
					if imMode != "RGB" {
						if c.length < 2 {
							return nil, valueError("Truncated tRNS chunk")
						}
					} else {
						return nil, valueError("Truncated tRNS chunk")
					}
				}
				switch imMode {
				case "1":
					v := 0
					if len(s) >= 2 && binary.BigEndian.Uint16(s) != 0 {
						v = 255
					}
					im.transparency = transparency{present: true, isInt: true, index: v}
				case "L", "I;16":
					im.transparency = transparency{present: true, isInt: true, index: int(binary.BigEndian.Uint16(s))}
				case "RGB":
					im.transparency = transparency{
						present: true,
						isTuple: true,
						tuple:   [3]int{int(binary.BigEndian.Uint16(s)), int(binary.BigEndian.Uint16(s[2:])), int(binary.BigEndian.Uint16(s[4:]))},
					}
				}
			}
		case "gAMA":
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
			if c.length < 4 {
				return nil, valueError("Truncated gAMA chunk")
			}
		case "cHRM":
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
			if c.length < 32 {
				return nil, valueError("Truncated cHRM chunk")
			}
		case "sRGB":
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
			if c.length < 1 {
				return nil, valueError("Truncated sRGB chunk")
			}
		case "pHYs":
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
			if c.length < 9 {
				return nil, valueError("Truncated pHYs chunk")
			}
		case "tEXt", "zTXt", "iTXt", "eXIf":
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
			if c.cid[0] == 'z' || c.cid[0] == 'i' {
				if err := pngTextChunk(c.cid[0] == 'z', s); err != nil {
					return nil, err
				}
			}
		case "acTL":
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
			if c.length < 8 {
				return nil, valueError("APNG contains truncated acTL chunk")
			}
			if nFramesKnown {
				nFramesKnown = false
			} else {
				n := int(binary.BigEndian.Uint32(s))
				if n == 0 || n > 0x80000000 {
					// invalid APNG: default image
				} else {
					nFrames = n
					nFramesKnown = true
					info["loop"] = int(int32(binary.BigEndian.Uint32(s[4:])))
				}
			}
		case "fcTL":
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
			if c.length < 26 {
				return nil, valueError("APNG contains truncated fcTL chunk")
			}
			seq := int(int32(binary.BigEndian.Uint32(s)))
			if (seqNum == nil && seq != 0) || (seqNum != nil && *seqNum != seq-1) {
				return nil, syntaxError("APNG contains frame sequence errors")
			}
			seqNum = &seq
			w, h := int(binary.BigEndian.Uint32(s[4:])), int(binary.BigEndian.Uint32(s[8:]))
			px, py := int(binary.BigEndian.Uint32(s[12:])), int(binary.BigEndian.Uint32(s[16:]))
			if px+w > imSize[0] || py+h > imSize[1] {
				return nil, syntaxError("APNG contains invalid frames")
			}
			bbox = [4]int{px, py, px + w, py + h}
			haveBBox = true
			info["bbox"] = bbox
			dn, dd := int(binary.BigEndian.Uint16(s[20:])), int(binary.BigEndian.Uint16(s[22:]))
			if dd == 0 {
				dd = 100
			}
			info["duration"] = float64(dn) / float64(dd) * 1000
			info["disposal"] = int(s[24])
			info["blend"] = int(s[25])
		case "fdAT":
			if c.length < 4 {
				return nil, valueError("APNG contains truncated fDAT chunk")
			}
			s, err := pngChunkData(data, chunk{cid: c.cid, offset: c.offset, length: 4})
			if err != nil {
				return nil, err
			}
			seq := int(int32(binary.BigEndian.Uint32(s)))
			if seqNum == nil || *seqNum != seq-1 {
				return nil, syntaxError("APNG contains frame sequence errors")
			}
			seqNum = &seq
			// chunk_fdAT then calls chunk_IDAT for the remaining bytes.
			imTile = true
			lastCID = c.cid
			lastLength = c.length - 4
			lastOffset = c.offset + 4
			stop = true
		default:
			s, err := pngChunkData(data, c)
			if err != nil {
				return nil, err
			}
			payload = s
		}
		if stop {
			break
		}
		// PngStream.crc.
		sum := crc32.NewIEEE()
		sum.Write(c.cid)
		sum.Write(payload)
		if c.offset+c.length+4 > len(data) {
			return nil, syntaxError("broken PNG file (incomplete checksum in " + pyReprBytes(c.cid) + ")")
		}
		want := binary.BigEndian.Uint32(data[c.offset+c.length:])
		if sum.Sum32() != want {
			return nil, syntaxError("broken PNG file (bad header checksum in " + pyReprBytes(c.cid) + ")")
		}
		pos = c.offset + c.length + 4
	}

	im.Mode = imMode
	im.Width, im.Height = imSize[0], imSize[1]
	st.rawmode = rawmode
	if havePalette {
		im.setPaletteRGB(imPalette, imPalette)
	} else if imMode == "P" {
		// Image.core.new allocates an all-zero palette.
		im.setPaletteRGB(nil, nil)
	}
	if imMode == "P" && im.transparency.present {
		if im.transparency.isInt {
			im.setPaletteAlpha(im.transparency.index, 0)
		} else {
			im.setPaletteAlphas(im.transparency.data)
		}
	}
	if imTile {
		st.idatOffset = lastOffset
		st.idatLength = lastLength
		st.haveTile = true
	}
	if v, ok := info["interlace"]; ok && v != 0 {
		st.interlaced = true
	}
	if haveBBox {
		st.bboxValue, st.haveBBox = bbox, true
	}
	if haveDefault {
		info["default_image"] = true
	}
	_ = nFrames
	_ = haveBBox
	if !imTile && lastCID == nil {
		// Loop ended without IDAT or IEND only through a short read, which
		// readChunk handles as a SyntaxError.
		return nil, syntaxError("broken PNG file (chunk b'')")
	}
	return im, nil
}

// pngSimplePalette is PngImagePlugin._simple_palette: ^\xff*\x00\xff*$.
func pngSimplePalette(s []byte) (bool, int) {
	i := 0
	for i < len(s) && s[i] == 0xff {
		i++
	}
	if i >= len(s) || s[i] != 0 {
		return false, 0
	}
	zero := i
	i++
	for i < len(s) && s[i] == 0xff {
		i++
	}
	// Python's $ also matches just before one trailing newline.
	if i == len(s) || (i == len(s)-1 && s[i] == '\n') {
		return true, zero
	}
	return false, 0
}

// pngTextChunk reproduces the decompression side effects of chunk_zTXt and
// chunk_iTXt that can raise.
func pngTextChunk(z bool, s []byte) error {
	if z {
		_, v, ok := bytes.Cut(s, []byte{0})
		if !ok {
			v = nil
		}
		comp := byte(0)
		if len(v) > 0 {
			comp = v[0]
		}
		if comp != 0 {
			return syntaxError("Unknown compression method " + itoa(int(comp)) + " in zTXt chunk")
		}
		if len(v) > 1 {
			if _, err := safeZlibDecompress(v[1:]); err != nil {
				return err
			}
		}
		return nil
	}
	_, r, ok := bytes.Cut(s, []byte{0})
	if !ok {
		return nil
	}
	if len(r) < 2 {
		return nil
	}
	cf, cm, r := r[0], r[1], r[2:]
	_, r, ok = bytes.Cut(r, []byte{0})
	if !ok {
		return nil
	}
	_, v, ok := bytes.Cut(r, []byte{0})
	if !ok {
		return nil
	}
	if cf != 0 {
		if cm == 0 {
			if _, err := safeZlibDecompress(v); err != nil {
				return err
			}
		}
	}
	return nil
}

// safeZlibDecompress is PIL.PngImagePlugin._safe_zlib_decompress.
func safeZlibDecompress(data []byte) ([]byte, error) {
	const maxTextChunk = 1024 * 1024
	out, err := inflateZlib(data, maxTextChunk)
	if err != nil {
		// zlib.error is swallowed by the callers; ValueError is not.
		var zerr *zlibDataError
		if errors.As(err, &zerr) {
			return nil, nil
		}
		return nil, err
	}
	return out, nil
}

// itoa avoids pulling strconv into the hot path.
func itoa(v int) string {
	if v == 0 {
		return "0"
	}
	var buf [20]byte
	i := len(buf)
	for v > 0 {
		i--
		buf[i] = byte('0' + v%10)
		v /= 10
	}
	return string(buf[i:])
}

// zlibDataError marks a zlib stream error (zlib.error in Python).
type zlibDataError struct{ msg string }

func (e *zlibDataError) Error() string { return e.msg }

// inflateZlib inflates a complete zlib stream, returning an error for a bad
// header or corrupt data. A short stream is not an error for the PNG decoder.
func inflateZlib(data []byte, limit int) ([]byte, error) {
	if len(data) < 2 {
		return nil, &zlibDataError{"incomplete zlib stream"}
	}
	cmf, flg := data[0], data[1]
	if cmf&0x0f != 8 || cmf>>4 > 7 || (uint16(cmf)<<8|uint16(flg))%31 != 0 {
		return nil, &zlibDataError{"invalid zlib header"}
	}
	if flg&0x20 != 0 {
		return nil, &zlibDataError{"zlib dictionary required"}
	}
	fr := flate.NewReader(bytes.NewReader(data[2:]))
	defer fr.Close()
	var out bytes.Buffer
	buf := make([]byte, 4096)
	for {
		n, err := fr.Read(buf)
		if n > 0 {
			out.Write(buf[:n])
			if out.Len() > limit {
				return nil, valueError("Decompressed data too large")
			}
		}
		if err == io.EOF {
			return out.Bytes(), nil
		}
		if err != nil {
			if errors.Is(err, io.ErrUnexpectedEOF) {
				return out.Bytes(), nil
			}
			return nil, &zlibDataError{err.Error()}
		}
	}
}

// pngIDATStream concatenates the consecutive IDAT/DDAT/fdAT chunks that
// PngImageFile.load_read feeds to the decoder, starting at st.idatOffset. The
// returned error is what load_read raises when the decoder asks for more data
// after the last complete chunk.
func (im *Image) pngIDATStream() ([]byte, error) {
	st := im.png
	var out []byte
	offset, length := st.idatOffset, st.idatLength
	for {
		if length > 0 {
			end := min(offset+length, len(im.data))
			out = append(out, im.data[offset:end]...)
			if offset+length > len(im.data) {
				// The chunk itself is short. If no byte of it exists, the read
				// returns b"" and ImageFile.load reports the leftover count;
				// otherwise __idat still reaches zero and the following chunk
				// header read fails with the plain "image file is truncated".
				if end == offset {
					return out, truncatedError(0)
				}
				return out, osError("image file is truncated")
			}
		}
		pos := offset + length
		// load_read skips the CRC field, then reads the next chunk header.
		pos += 4
		avail := len(im.data) - pos
		if avail < 4 {
			return out, osError("image file is truncated")
		}
		if avail < 8 {
			return out, syntaxError("broken PNG file (chunk " + pyReprBytes(im.data[pos+4:]) + ")")
		}
		c, _, ok := readChunk(im.data, pos)
		if !ok {
			return out, osError("image file is truncated")
		}
		if !validCID(c.cid) {
			return out, syntaxError("broken PNG file (chunk " + pyReprBytes(c.cid) + ")")
		}
		if string(c.cid) != "IDAT" && string(c.cid) != "DDAT" && string(c.cid) != "fdAT" {
			return out, nil
		}
		if string(c.cid) == "fdAT" {
			c.offset += 4
			c.length -= 4
			if c.length < 0 {
				c.length = 0
			}
		}
		offset, length = c.offset, c.length
	}
}

// pngRowBytes is the non-interlaced state->bytes / get_row_len.
func pngRowBytes(width, bits int, interlaced bool, pass int) int {
	if !interlaced {
		return (width*bits + 7) / 8
	}
	if pass < 0 || pass >= 7 {
		return 0
	}
	rowLen := (width + pngOffset[pass]) / pngColIncrement[pass]
	return (rowLen*bits + 7) / 8
}

var (
	pngOffset       = [7]int{7, 3, 3, 1, 1, 0, 0}
	pngStartingCol  = [7]int{0, 4, 0, 2, 0, 1, 0}
	pngStartingRow  = [7]int{0, 0, 4, 0, 2, 0, 1}
	pngColIncrement = [7]int{8, 8, 4, 4, 2, 2, 1}
	pngRowIncrement = [7]int{8, 8, 8, 4, 4, 2, 2}
)

// pngBits is the number of bits per pixel for a rawmode.
func pngBits(rawmode string) int {
	switch rawmode {
	case "1", "P;1":
		return 1
	case "L;2", "P;2":
		return 2
	case "L;4", "P;4":
		return 4
	case "L", "P":
		return 8
	case "I;16B", "LA":
		return 16
	case "RGB":
		return 24
	case "RGB;16B":
		return 48
	case "RGBA", "LA;16B":
		return 32
	case "RGBA;16B":
		return 64
	}
	return 0
}

// loadPNG decodes the image data with Pillow's zip decoder
// (src/libImaging/ZipDecode.c).
func (im *Image) loadPNG() error {
	st := im.png
	if !st.haveTile {
		return osError("cannot load this image")
	}
	stream, pending := im.pngIDATStream()

	xsize, ysize := im.Width, im.Height
	xoff, yoff := 0, 0
	if b, ok := im.png.bbox(); ok {
		xoff, yoff = b[0], b[1]
		xsize, ysize = b[2]-b[0], b[3]-b[1]
	}

	bits := pngBits(st.rawmode)
	interlaced := im.pngInterlaced()
	rowBytes := pngRowBytes(xsize, bits, false, 0)

	// Data destination, allocated like Image.core.new.
	switch im.Mode {
	case "I;16":
		im.pix16 = make([]uint16, im.Width*im.Height)
	default:
		if im.Mode == "RGBA" || im.Mode == "CMYK" {
			im.pix = make([]byte, 4*im.Width*im.Height)
		} else if im.Mode == "LA" {
			im.pix = make([]byte, 2*im.Width*im.Height)
		} else if im.Mode == "RGB" {
			im.pix = make([]byte, 3*im.Width*im.Height)
		} else {
			im.pix = make([]byte, im.Width*im.Height)
		}
	}

	// Degenerate image: the C decoder needs a positive row length.
	if rowBytes <= 0 {
		return osError("cannot load this image")
	}

	// zlib inflateInit + inflate. compress/zlib checks the header and the
	// Adler-32 of a finished stream, like the zlib Pillow links.
	if len(stream) < 2 {
		return truncatedError(0)
	}
	fr, zerr := zlib.NewReader(bytes.NewReader(stream))
	if zerr != nil {
		return decoderError(-2)
	}
	defer fr.Close()

	prev := make([]byte, rowBytes+1)
	buf := make([]byte, rowBytes+1)
	// readFull reads up to n bytes of inflated data. It reports whether the
	// stream ended normally, ended early, or failed.
	readFull := func(n int) (int, int) { // (got, status) status: 0 ok, 1 eof, 2 data error
		got := 0
		for got < n {
			m, err := fr.Read(buf[got:n])
			got += m
			if err != nil {
				if errors.Is(err, io.EOF) {
					return got, 1
				}
				if errors.Is(err, io.ErrUnexpectedEOF) {
					return got, 1
				}
				return got, 2
			}
			if m == 0 {
				return got, 1
			}
		}
		return got, 0
	}

	pass := 0
	y := 0
	rowLen := rowBytes
	if interlaced {
		y = pngStartingRow[0]
		rowLen = pngRowBytes(xsize, bits, true, 0)
	}
	for {
		if rowLen > 0 {
			got, status := readFull(rowLen + 1)
			if status == 2 {
				return decoderError(-2)
			}
			if got < rowLen+1 {
				if pending != nil {
					return pending
				}
				return truncatedError(0)
			}
			// Apply the predictor.
			prefix := buf[0]
			row := buf[1 : rowLen+1]
			switch prefix {
			case 0:
			case 1:
				bpp := (bits + 7) / 8
				for i := bpp; i < rowLen; i++ {
					row[i] += row[i-bpp]
				}
			case 2:
				for i := 0; i < rowLen; i++ {
					row[i] += prev[i+1]
				}
			case 3:
				bpp := (bits + 7) / 8
				for i := 0; i < bpp && i < rowLen; i++ {
					row[i] += prev[i+1] / 2
				}
				for i := bpp; i < rowLen; i++ {
					row[i] += byte((int(row[i-bpp]) + int(prev[i+1])) / 2)
				}
			case 4:
				bpp := (bits + 7) / 8
				for i := 0; i < bpp && i < rowLen; i++ {
					row[i] += prev[i+1]
				}
				for i := bpp; i < rowLen; i++ {
					a := int(row[i-bpp])
					b := int(prev[i+1])
					c := int(prev[i-bpp+1])
					pa, pb, pc := abs(b-c), abs(a-c), abs(a+b-2*c)
					var v int
					switch {
					case pa <= pb && pa <= pc:
						v = a
					case pb <= pc:
						v = b
					default:
						v = c
					}
					row[i] += byte(v)
				}
			default:
				return decoderError(-3)
			}
			if interlaced {
				st.interlaceY = y
				im.placePNGRowInterlaced(pass, row, xsize, bits, st.rawmode)
			} else {
				if y+yoff < 0 || y+yoff >= im.Height {
					return decoderError(-3)
				}
				im.placePNGRow(row, y+yoff, xoff, xsize, st.rawmode)
			}
			// Swap the line buffers.
			prev, buf = buf, prev
		}

		if interlaced {
			y += pngRowIncrement[pass]
			for y >= ysize || rowLen <= 0 {
				pass++
				if pass == 7 {
					y = ysize
					break
				}
				y = pngStartingRow[pass]
				rowLen = pngRowBytes(xsize, bits, true, pass)
				clear(buf)
			}
			if y >= ysize {
				return nil
			}
		} else {
			y++
			if y >= ysize {
				return nil
			}
			rowLen = rowBytes
		}
	}
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

// placePNGRow writes one filtered row of pixels at (xoff, y).
func (im *Image) placePNGRow(row []byte, y, xoff, pixels int, rawmode string) {
	base := y*im.Width + xoff
	switch rawmode {
	case "1":
		for x := 0; x < pixels; x++ {
			im.pix[base+x] = (row[x/8] >> (7 - uint(x%8))) & 1
		}
	case "L;2":
		for x := 0; x < pixels; x++ {
			shift := 6 - 2*uint(x%4)
			im.pix[base+x] = ((row[x/4] >> shift) & 3) * 0x55
		}
	case "L;4":
		for x := 0; x < pixels; x++ {
			shift := 4 - 4*uint(x%2)
			im.pix[base+x] = ((row[x/2] >> shift) & 15) * 0x11
		}
	case "L":
		copy(im.pix[base:base+pixels], row[:pixels])
	case "P;1":
		for x := 0; x < pixels; x++ {
			im.pix[base+x] = (row[x/8] >> (7 - uint(x%8))) & 1
		}
	case "P;2":
		for x := 0; x < pixels; x++ {
			shift := 6 - 2*uint(x%4)
			im.pix[base+x] = (row[x/4] >> shift) & 3
		}
	case "P;4":
		for x := 0; x < pixels; x++ {
			shift := 4 - 4*uint(x%2)
			im.pix[base+x] = (row[x/2] >> shift) & 15
		}
	case "P":
		copy(im.pix[base:base+pixels], row[:pixels])
	case "I;16B":
		for x := 0; x < pixels; x++ {
			im.pix16[base+x] = binary.BigEndian.Uint16(row[2*x:])
		}
	case "RGB":
		copy(im.pix[3*base:3*(base+pixels)], row[:3*pixels])
	case "RGB;16B":
		for x := 0; x < pixels; x++ {
			im.pix[3*(base+x)] = row[6*x]
			im.pix[3*(base+x)+1] = row[6*x+2]
			im.pix[3*(base+x)+2] = row[6*x+4]
		}
	case "LA":
		copy(im.pix[2*base:2*(base+pixels)], row[:2*pixels])
	case "LA;16B":
		for x := 0; x < pixels; x++ {
			v := row[4*x]
			im.pix[4*(base+x)] = v
			im.pix[4*(base+x)+1] = v
			im.pix[4*(base+x)+2] = v
			im.pix[4*(base+x)+3] = row[4*x+2]
		}
	case "RGBA":
		copy(im.pix[4*base:4*(base+pixels)], row[:4*pixels])
	case "RGBA;16B":
		for x := 0; x < pixels; x++ {
			im.pix[4*(base+x)] = row[8*x]
			im.pix[4*(base+x)+1] = row[8*x+2]
			im.pix[4*(base+x)+2] = row[8*x+4]
			im.pix[4*(base+x)+3] = row[8*x+6]
		}
	}
}

// placePNGRowInterlaced writes one Adam7 row. The C decoder addresses the
// destination rows by the pass-relative y, without the tile offset.
func (im *Image) placePNGRowInterlaced(pass int, row []byte, xsize, bits int, rawmode string) {
	y := im.png.interlaceY
	pixels := (xsize + pngOffset[pass]) / pngColIncrement[pass]
	cols := make([]int, pixels)
	for i := range cols {
		cols[i] = pngStartingCol[pass] + i*pngColIncrement[pass]
	}
	// Reuse the row placement with a synthetic full-width row would be
	// wasteful; place pixel by pixel.
	for i, col := range cols {
		if col >= im.Width || y < 0 || y >= im.Height {
			continue
		}
		im.placePNGPixel(row, i, col, y, bits, rawmode)
	}
}

// placePNGPixel unpacks pixel i of a row into (col, y).
func (im *Image) placePNGPixel(row []byte, i, col, y int, bits int, rawmode string) {
	base := y*im.Width + col
	switch rawmode {
	case "1":
		im.pix[base] = (row[i/8] >> (7 - uint(i%8))) & 1
	case "L;2":
		im.pix[base] = ((row[i/4] >> (6 - 2*uint(i%4))) & 3) * 0x55
	case "L;4":
		im.pix[base] = ((row[i/2] >> (4 - 4*uint(i%2))) & 15) * 0x11
	case "L":
		im.pix[base] = row[i]
	case "P;1":
		im.pix[base] = (row[i/8] >> (7 - uint(i%8))) & 1
	case "P;2":
		im.pix[base] = (row[i/4] >> (6 - 2*uint(i%4))) & 3
	case "P;4":
		im.pix[base] = (row[i/2] >> (4 - 4*uint(i%2))) & 15
	case "P":
		im.pix[base] = row[i]
	case "I;16B":
		im.pix16[base] = binary.BigEndian.Uint16(row[2*i:])
	case "RGB":
		copy(im.pix[3*base:3*base+3], row[3*i:3*i+3])
	case "RGB;16B":
		im.pix[3*base] = row[6*i]
		im.pix[3*base+1] = row[6*i+2]
		im.pix[3*base+2] = row[6*i+4]
	case "LA":
		im.pix[2*base] = row[2*i]
		im.pix[2*base+1] = row[2*i+1]
	case "LA;16B":
		v := row[4*i]
		im.pix[4*base] = v
		im.pix[4*base+1] = v
		im.pix[4*base+2] = v
		im.pix[4*base+3] = row[4*i+2]
	case "RGBA":
		copy(im.pix[4*base:4*base+4], row[4*i:4*i+4])
	case "RGBA;16B":
		im.pix[4*base] = row[8*i]
		im.pix[4*base+1] = row[8*i+2]
		im.pix[4*base+2] = row[8*i+4]
		im.pix[4*base+3] = row[8*i+6]
	}
}

// pngInterlaceY is the current pass row, tracked per image during decoding.
// It lives on pngState so placePNGRowInterlaced can read it.

func (st *pngState) bbox() ([4]int, bool) {
	if st == nil {
		return [4]int{}, false
	}
	return st.bboxValue, st.haveBBox
}

// pngInterlaced reports IHDR's interlace flag.
func (im *Image) pngInterlaced() bool { return im.png != nil && im.png.interlaced }
