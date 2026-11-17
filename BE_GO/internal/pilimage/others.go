package pilimage

import (
	"bytes"
	"encoding/binary"
	"math"
	"regexp"
	"strconv"
	"strings"
)

// This file ports the accept tests and the header checks of the Pillow plugins
// this package identifies but does not decode. Their _open methods are tried in
// Pillow's plugin order; a check that fails raises an exception Pillow skips,
// and data whose header parses is reported with ErrFormatNotPorted.

// shortError is the struct.error Pillow's _binary helpers raise for a short
// buffer; ImageFile.__init__ turns it into SyntaxError, so Open skips the
// plugin.
func shortError() *Error {
	return &Error{Class: "error", Text: "unpack_from requires a larger buffer"}
}

// pyReprStr is Python's repr() for a str (approximate for non-ASCII).
func pyReprStr(s string) string {
	quote := byte('\'')
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		quote = '"'
	}
	var sb strings.Builder
	sb.WriteByte(quote)
	for _, c := range []byte(s) {
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

func be16(b []byte) int  { return int(binary.BigEndian.Uint16(b)) }
func be32(b []byte) int  { return int(binary.BigEndian.Uint32(b)) }
func le16(b []byte) int  { return int(binary.LittleEndian.Uint16(b)) }
func le32(b []byte) int  { return int(binary.LittleEndian.Uint32(b)) }
func le32s(b []byte) int { return int(int32(binary.LittleEndian.Uint32(b))) }

// --- AVIF -----------------------------------------------------------------
//
// Divergence X19 (image codec libraries): AVIF is decoded by the bundled libavif,
// whose output depends on that library's build. This package answers as a
// Pillow built without the AVIF codec: AvifImagePlugin._accept returns a message
// instead of True, so Image.open does not identify the data and raises
// UnidentifiedImageError.

// --- BLP ------------------------------------------------------------------

func acceptBLP(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("BLP1")) || bytes.HasPrefix(prefix, []byte("BLP2"))
}

// --- BUFR, GRIB, HDF5 (stub formats) --------------------------------------

func acceptBUFR(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("BUFR")) || bytes.HasPrefix(prefix, []byte("ZCZC"))
}

func acceptGRIB(prefix []byte) bool {
	return len(prefix) >= 8 && bytes.HasPrefix(prefix, []byte("GRIB")) && prefix[7] == 1
}

func acceptHDF5(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\x89HDF\r\n\x1a\n"))
}

// --- CUR ------------------------------------------------------------------

func acceptCUR(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\x00\x00\x02\x00"))
}

// --- PCX / DCX ------------------------------------------------------------

// --- DDS ------------------------------------------------------------------

func acceptDDS(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("DDS ")) }

// --- EPS ------------------------------------------------------------------

func acceptEPS(prefix []byte) bool {
	if bytes.HasPrefix(prefix, []byte("%!PS")) {
		return true
	}
	return len(prefix) >= 4 && binary.LittleEndian.Uint32(prefix) == 0xC6D3D0C5
}

func openEPS(data []byte) (*Image, error) {
	// EpsImageFile._find_offset: the PostScript section starts at offset 0 for
	// a "%!PS" file or at the offset in a DOS EPS binary header.
	offset := 0
	length := len(data)
	if len(data) >= 4 && bytes.HasPrefix(data, []byte("%!PS")) {
		offset = 0
	} else if len(data) >= 4 && binary.LittleEndian.Uint32(data) == 0xC6D3D0C5 {
		if len(data) < 12 {
			return nil, shortError()
		}
		offset = le32s(data[4:])
		length = le32s(data[8:])
		_ = length
	} else {
		return nil, syntaxError("not an EPS file")
	}
	section := data
	if offset > len(data) {
		section = nil
	} else if offset > 0 {
		section = data[offset:]
	}

	info := map[string]string{}
	var boundingBox []int
	imagedataSize := [2]int{}
	hasImagedata := false
	mode := "RGB"
	readingHeader := true
	readingTrailer := false
	trailerReached := false
	stopped := false

	checkRequired := func() error {
		if _, ok := info["PS-Adobe"]; !ok {
			return syntaxError(`EPS header missing "%!PS-Adobe" comment`)
		}
		if _, ok := info["BoundingBox"]; !ok {
			return syntaxError(`EPS header missing "%%BoundingBox" comment`)
		}
		return nil
	}
	readComment := func(s string) bool {
		if !bytes.HasPrefix([]byte(s), []byte("%%")) {
			return false
		}
		rest := s[2:]
		colon := bytes.IndexByte([]byte(rest), ':')
		if colon < 0 {
			return false
		}
		k := rest[:colon]
		v := rest[colon+1:]
		v = string(bytes.TrimLeft([]byte(v), " \t"))
		v = string(bytes.TrimRight([]byte(v), " \t"))
		info[k] = v
		if k == "BoundingBox" {
			if v == "(atend)" {
				readingTrailer = true
			} else if boundingBox == nil || (trailerReached && readingTrailer) {
				if parsed, ok := parseEPSBBox(v); ok {
					boundingBox = parsed
				}
			}
		}
		return true
	}

	// Split on \r and \n the way the byte loop treats each as a line end.
	lines := epsLines(section)
	for _, line := range lines {
		if stopped {
			break
		}
		if len(line) > 255 && len(line) > 0 && line[0] == '%' {
			return nil, syntaxError("not an EPS file")
		}
		if readingHeader {
			if len(line) == 0 {
				continue
			}
			if line[0] != '%' || bytes.HasPrefix(line, []byte("%%EndComments")) {
				if err := checkRequired(); err != nil {
					return nil, err
				}
				readingHeader = false
				continue
			}
			s := string(line)
			if !readComment(s) {
				if k, ok := epsField(s); ok {
					if strings.HasPrefix(k, "PS-Adobe") {
						if len(k) > 9 {
							info["PS-Adobe"] = k[9:]
						} else {
							info["PS-Adobe"] = ""
						}
					} else {
						info[k] = ""
					}
				} else if s[0] == '%' {
					// Non-DSC comment.
				} else {
					return nil, osError("bad EPS header")
				}
			}
			continue
		}
		if bytes.HasPrefix(line, []byte("%ImageData:")) {
			if hasImagedata {
				continue
			}
			values := bytes.Fields(line[11:])
			if len(values) < 4 {
				return nil, valueError("not enough values to unpack (expected 4, got " + strconv.Itoa(len(values)) + ")")
			}
			nums := [4]int{}
			for i := 0; i < 4; i++ {
				n, err := strconv.Atoi(string(values[i]))
				if err != nil {
					return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes(values[i]))
				}
				nums[i] = n
			}
			if nums[2] == 1 {
				mode = "1"
			} else if nums[2] == 8 {
				switch nums[3] {
				case 1:
					mode = "L"
				case 2:
					mode = "LAB"
				case 3:
					mode = "RGB"
				case 4:
					mode = "CMYK"
				default:
					stopped = true
					continue
				}
			} else {
				stopped = true
				continue
			}
			imagedataSize = [2]int{nums[0], nums[1]}
			hasImagedata = true
			continue
		}
		if bytes.HasPrefix(line, []byte("%%EOF")) {
			break
		}
		if trailerReached && readingTrailer {
			readComment(string(line))
			continue
		}
		if bytes.HasPrefix(line, []byte("%%Trailer")) {
			trailerReached = true
			continue
		}
		if bytes.HasPrefix(line, []byte("%%BeginBinary:")) {
			n, err := strconv.Atoi(string(line[14:]))
			if err == nil && n < 0 {
				return nil, valueError("BeginBinary bytecount cannot be negative")
			}
			continue
		}
	}
	if readingHeader {
		if err := checkRequired(); err != nil {
			return nil, err
		}
	}
	if boundingBox == nil {
		return nil, osError("cannot determine EPS bounding box")
	}
	w, h := imagedataSize[0], imagedataSize[1]
	if !hasImagedata {
		w, h = boundingBox[2]-boundingBox[0], boundingBox[3]-boundingBox[1]
	}
	im := newImage(data, "EPS", mode, w, h)
	im.loadFn = func() error {
		// Ghostscript is not part of the production platform.
		return osError("Unable to locate Ghostscript on paths")
	}
	return im, nil
}

// epsLines splits EPS data into the logical lines the byte loop sees: every
// CR and every LF ends a line, and an empty line is skipped by the caller.
func epsLines(data []byte) [][]byte {
	var out [][]byte
	start := 0
	for i := 0; i <= len(data); i++ {
		if i == len(data) {
			if start < len(data) {
				out = append(out, data[start:])
			}
			break
		}
		if data[i] == '\r' || data[i] == '\n' {
			out = append(out, data[start:i])
			start = i + 1
		}
	}
	return out
}

// parseEPSBBox is the BoundingBox value conversion: int(float(i)) per token,
// ignoring a value that cannot be parsed.
func parseEPSBBox(v string) ([]int, bool) {
	fields := strings.Fields(v)
	out := make([]int, 0, len(fields))
	for _, f := range fields {
		x, err := strconv.ParseFloat(f, 64)
		if err != nil {
			if !strings.Contains(err.Error(), "invalid") {
				return nil, false
			}
			return nil, false
		}
		out = append(out, int(x))
	}
	return out, true
}

// epsField is the re.match of EpsImagePlugin's field pattern, returning the
// captured key.
func epsField(s string) (string, bool) {
	if len(s) < 2 || s[0] != '%' {
		return "", false
	}
	c := s[1]
	if c != '%' && c != '!' && !(c == '_' || (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) {
		return "", false
	}
	rest := s[2:]
	if i := strings.IndexByte(rest, ':'); i >= 0 {
		return "", false
	}
	return strings.TrimRight(rest, " \t"), true
}

// --- FITS -----------------------------------------------------------------

func acceptFITS(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("SIMPLE")) }

// --- FLI ------------------------------------------------------------------

func acceptFLI(prefix []byte) bool {
	if len(prefix) < 16 {
		return false
	}
	magic := le16(prefix[4:])
	flags := le16(prefix[14:])
	return (magic == 0xAF11 || magic == 0xAF12) && (flags == 0 || flags == 3)
}

// --- FTEX -----------------------------------------------------------------

func acceptFTEX(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("FTEX")) }

func openFTEX(data []byte) (*Image, error) {
	if len(data) < 4 || !acceptFTEX(data[:4]) {
		return nil, syntaxError("not an FTEX file")
	}
	if len(data) < 32 {
		return nil, shortError()
	}
	width, height := le32s(data[8:]), le32s(data[12:])
	formatCount := le32s(data[20:])
	if formatCount != 1 {
		return nil, valueError("Unsupported number of texture formats: " + strconv.Itoa(formatCount))
	}
	format := le32s(data[24:])
	where := le32s(data[28:])
	if where < 0 {
		return nil, valueError("negative seek value " + strconv.Itoa(where))
	}
	if where+4 > len(data) {
		return nil, shortError()
	}
	mipmapSize := le32s(data[where:])
	mip := where + 4
	if mipmapSize < 0 {
		// BytesIO.read(negative) reads to the end.
		mipmapSize = max(0, len(data)-mip)
	}
	mode := ""
	switch format {
	case 0:
		mode = "RGBA"
	case 1:
		mode = "RGB"
	default:
		return nil, valueError("Invalid texture compression format: " + strconv.Itoa(format))
	}
	if width <= 0 || height <= 0 {
		// FtexImageFile._open closes its stream; the next plugin attempt then
		// raises on the closed file before "not identified" is reported.
		return nil, &Error{Class: "ValueError", Text: "I/O operation on closed file."}
	}
	im := newImage(data, "FTEX", mode, width, height)
	if format == 0 {
		im.loadFn = func() error { return im.loadFTEXDXT1(mip, mipmapSize) }
	} else {
		im.loadFn = func() error { return im.loadFTEXUncompressed(mip, mipmapSize) }
	}
	return im, nil
}

// --- GBR ------------------------------------------------------------------

func acceptGBR(prefix []byte) bool {
	if len(prefix) < 8 {
		return false
	}
	return be32(prefix) >= 20 && (be32(prefix[4:]) == 1 || be32(prefix[4:]) == 2)
}

func openGBR(data []byte) (*Image, error) {
	if len(data) < 20 {
		return nil, shortError()
	}
	headerSize := be32(data)
	if headerSize < 20 {
		return nil, syntaxError("not a GIMP brush")
	}
	version := be32(data[4:])
	if version != 1 && version != 2 {
		return nil, syntaxError("Unsupported GIMP brush version: " + strconv.Itoa(version))
	}
	width, height := be32(data[8:]), be32(data[12:])
	if width == 0 || height == 0 {
		return nil, syntaxError("not a GIMP brush")
	}
	depth := be32(data[16:])
	if depth != 1 && depth != 4 {
		return nil, syntaxError("Unsupported GIMP brush color depth: " + strconv.Itoa(depth))
	}
	pos := 20
	commentLength := headerSize - 20
	if version == 2 {
		if len(data) < 28 {
			return nil, shortError()
		}
		pos = 24
		if string(data[20:24]) != "GIMP" {
			return nil, syntaxError("not a GIMP brush, bad magic number")
		}
		pos = 28
		commentLength = headerSize - 28
	}
	if commentLength < 0 {
		commentLength = 0
	}
	pos += commentLength
	if pos > len(data) {
		pos = len(data)
	}
	if err := checkDecompressionBomb(width, height); err != nil {
		return nil, err
	}
	mode := "L"
	if depth == 4 {
		mode = "RGBA"
	}
	im := newImage(data, "GBR", mode, width, height)
	im.loadFn = func() error { return im.loadGBR(pos) }
	return im, nil
}

// --- JPEG 2000 ------------------------------------------------------------

func acceptJPEG2000(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\xff\x4f\xff\x51")) ||
		bytes.HasPrefix(prefix, []byte("\x00\x00\x00\x0cjP  \x0d\x0a\x87\x0a"))
}

// jp2BoxReader is Jpeg2KImagePlugin.BoxReader: a cursor over data with an
// optional enclosing box length.
type jp2BoxReader struct {
	data      []byte
	pos       int
	remaining int
	hasLength bool
	length    int
}

func (r *jp2BoxReader) canRead(n int) bool {
	if r.hasLength && r.pos+n > r.length {
		return false
	}
	if r.remaining >= 0 {
		return n <= r.remaining
	}
	return true
}

func (r *jp2BoxReader) readBytes(n int) ([]byte, error) {
	if !r.canRead(n) {
		return nil, syntaxError("Not enough data in header")
	}
	if uint64(r.pos)+uint64(n) >= 1<<63 {
		return nil, syntaxError("Box length too large")
	}
	if r.pos+n > len(r.data) {
		got := max(0, len(r.data)-r.pos)
		return nil, osError("Expected to read " + strconv.Itoa(n) + " bytes but only got " + strconv.Itoa(got) + ".")
	}
	out := r.data[r.pos : r.pos+n]
	r.pos += n
	if r.remaining > 0 {
		r.remaining -= n
	}
	return out, nil
}

// rawRead is a plain file read: it returns fewer than n bytes at the end of the
// data without raising.
func (r *jp2BoxReader) rawRead(n int) []byte {
	start := min(r.pos, len(r.data))
	end := min(start+n, len(r.data))
	r.pos = end
	return r.data[start:end]
}

func (r *jp2BoxReader) hasNextBox() bool {
	if r.hasLength {
		return r.pos+r.remaining < r.length
	}
	return true
}

func (r *jp2BoxReader) nextBoxType() ([]byte, error) {
	if r.remaining > 0 {
		if uint64(r.pos)+uint64(r.remaining) >= 1<<63 {
			return nil, syntaxError("Box length too large")
		}
		r.pos += r.remaining
	}
	r.remaining = -1
	hdr, err := r.readBytes(8)
	if err != nil {
		return nil, err
	}
	lbox := int(binary.BigEndian.Uint32(hdr))
	tbox := hdr[4:8]
	hlen := 8
	if lbox == 1 {
		b, err := r.readBytes(8)
		if err != nil {
			return nil, err
		}
		lbox = int(binary.BigEndian.Uint64(b))
		hlen = 16
	}
	if lbox < hlen || !r.canRead(lbox-hlen) {
		return nil, syntaxError("Invalid header length")
	}
	r.remaining = lbox - hlen
	return tbox, nil
}

// readBoxes is BoxReader.read_boxes.
func (r *jp2BoxReader) readBoxes() (*jp2BoxReader, error) {
	size := r.remaining
	data, err := r.readBytes(size)
	if err != nil {
		return nil, err
	}
	return &jp2BoxReader{data: data, remaining: -1, hasLength: true, length: size}, nil
}

// parseJPCodestream is Jpeg2KImagePlugin._parse_codestream.
func parseJPCodestream(r *jp2BoxReader) (int, int, string, error) {
	hdr := r.rawRead(2)
	if len(hdr) < 2 {
		return 0, 0, "", shortError()
	}
	lsiz := be16(hdr)
	if lsiz < 38 {
		return 0, 0, "", valueError("SIZ marker length must be at least 38")
	}
	if lsiz-2 < 0 {
		return 0, 0, "", shortError()
	}
	rest := r.rawRead(lsiz - 2)
	siz := append(append([]byte{}, hdr...), rest...)
	if len(siz) < 38 {
		return 0, 0, "", shortError()
	}
	// >HHIIIIIIIIH: length, rsiz, xsiz, ysiz, xosiz, yosiz, ...
	xsiz := int(binary.BigEndian.Uint32(siz[4:]))
	ysiz := int(binary.BigEndian.Uint32(siz[8:]))
	xosiz := int(binary.BigEndian.Uint32(siz[12:]))
	yosiz := int(binary.BigEndian.Uint32(siz[16:]))
	csiz := be16(siz[36:])
	var mode string
	switch csiz {
	case 1:
		if len(siz) < 39 {
			return 0, 0, "", shortError()
		}
		if int(siz[38]&0x7F)+1 > 8 {
			mode = "I;16"
		} else {
			mode = "L"
		}
	case 2:
		mode = "LA"
	case 3:
		mode = "RGB"
	case 4:
		mode = "RGBA"
	default:
		return 0, 0, "", syntaxError("unable to determine J2K image mode")
	}
	return xsiz - xosiz, ysiz - yosiz, mode, nil
}

// parseJPComment is Jpeg2KImagePlugin._parse_comment.
func parseJPComment(r *jp2BoxReader) error {
	for {
		marker := r.rawRead(2)
		if len(marker) == 0 {
			return nil
		}
		if len(marker) < 2 {
			return &Error{Class: "IndexError", Text: "index out of range"}
		}
		typ := marker[1]
		if typ == 0x90 || typ == 0xD9 {
			return nil
		}
		hdr := r.rawRead(2)
		if len(hdr) < 2 {
			return shortError()
		}
		length := be16(hdr)
		if length < 2 {
			return valueError("Marker length too small")
		}
		if typ == 0x64 {
			r.rawRead(length - 2)
			return nil
		}
		r.pos += length - 2
	}
}

// parseJP2Header is Jpeg2KImagePlugin._parse_jp2_header: size and mode, plus
// the header consumption its box walk performs.
func parseJP2Header(r *jp2BoxReader) (int, int, string, error) {
	var header *jp2BoxReader
	for r.hasNextBox() {
		tbox, err := r.nextBoxType()
		if err != nil {
			return 0, 0, "", err
		}
		if string(tbox) == "jp2h" {
			header, err = r.readBoxes()
			if err != nil {
				return 0, 0, "", err
			}
			break
		}
		if string(tbox) == "ftyp" {
			if _, err := r.readBytes(4); err != nil {
				return 0, 0, "", err
			}
		}
	}
	if header == nil {
		return 0, 0, "", &Error{Class: "AssertionError", Text: ""}
	}
	width, height, mode := 0, 0, ""
	nc := 0
	colr := ""
	haveSize := false
	for header.hasNextBox() {
		tbox, err := header.nextBoxType()
		if err != nil {
			return 0, 0, "", err
		}
		switch string(tbox) {
		case "ihdr":
			b, err := header.readBytes(11)
			if err != nil {
				return 0, 0, "", err
			}
			h := int(binary.BigEndian.Uint32(b[0:]))
			w := int(binary.BigEndian.Uint32(b[4:]))
			nc = be16(b[8:])
			bpc := int(b[10])
			height, width = h, w
			haveSize = true
			switch {
			case nc == 1 && bpc&0x7F > 8:
				mode = "I;16"
			case nc == 1:
				mode = "L"
			case nc == 2:
				mode = "LA"
			case nc == 3:
				mode = "RGB"
			case nc == 4:
				mode = "RGBA"
			}
		case "colr":
			b, err := header.readBytes(7)
			if err != nil {
				return 0, 0, "", err
			}
			meth := int(b[0])
			enumcs := int(binary.BigEndian.Uint32(b[3:]))
			if meth == 1 {
				switch enumcs {
				case 0, 15:
					colr = "1"
				case 12:
					colr = "CMYK"
					if nc == 4 {
						mode = "CMYK"
					}
				case 17:
					colr = "L"
				}
			}
		case "pclr":
			if (mode == "L" || mode == "LA") && colr != "1" && colr != "L" {
				b, err := header.readBytes(3)
				if err != nil {
					return 0, 0, "", err
				}
				ne := be16(b)
				npc := int(b[2])
				bitdepths, err := header.readBytes(npc)
				if err != nil {
					return 0, 0, "", err
				}
				maxBitdepth := 0
				for _, v := range bitdepths {
					maxBitdepth = max(maxBitdepth, int(v))
				}
				if maxBitdepth <= 8 {
					for i := 0; i < ne; i++ {
						if _, err := header.readBytes(npc); err != nil {
							return 0, 0, "", err
						}
					}
					if mode == "L" {
						mode = "P"
					} else {
						mode = "PA"
					}
				}
			}
		case "res ":
			res, err := header.readBoxes()
			if err != nil {
				return 0, 0, "", err
			}
			for res.hasNextBox() {
				tres, err := res.nextBoxType()
				if err != nil {
					return 0, 0, "", err
				}
				if string(tres) == "resc" {
					if _, err := res.readBytes(10); err != nil {
						return 0, 0, "", err
					}
					break
				}
			}
		}
	}
	if !haveSize || mode == "" {
		return 0, 0, "", syntaxError("Malformed JP2 header")
	}
	return width, height, mode, nil
}

func openJPEG2000(data []byte) (*Image, error) {
	r := &jp2BoxReader{data: data, remaining: -1, hasLength: false}
	var w, h int
	var mode string
	if len(data) >= 4 && bytes.HasPrefix(data, []byte("\xff\x4f\xff\x51")) {
		var err error
		w, h, mode, err = parseJPCodestream(r)
		if err != nil {
			return nil, err
		}
		if err := parseJPComment(r); err != nil {
			return nil, err
		}
	} else if len(data) >= 12 && bytes.HasPrefix(data, []byte("\x00\x00\x00\x0cjP  \x0d\x0a\x87\x0a")) {
		r.pos = 12
		var err error
		w, h, mode, err = parseJP2Header(r)
		if err != nil {
			return nil, err
		}
		// The codestream header follows a jp2c box header.
		tail := r.rawRead(12)
		if bytes.HasSuffix(tail, []byte("jp2c\xff\x4f\xff\x51")) {
			if _, _, _, err := parseJPCodestream(r); err != nil {
				return nil, err
			}
			if err := parseJPComment(r); err != nil {
				return nil, err
			}
		}
	} else {
		return nil, syntaxError("not a JPEG 2000 file")
	}
	im := newImage(data, "JPEG2000", mode, w, h)
	im.loadFn = func() error {
		// X19: the JPEG 2000 codec library is not part of this build.
		return osError("decoder JPEG2000 not available")
	}
	return im, nil
}

// --- ICNS -----------------------------------------------------------------

func acceptICNS(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("icns")) }

// --- ICO ------------------------------------------------------------------

func acceptICO(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("\x00\x00\x01\x00")) }

// --- IM / IMT / IPTC ------------------------------------------------------

var imSplit = regexp.MustCompile(`^([A-Za-z0-9 ]+):[ \t]*(.*)$`)

func openIMT(data []byte) (*Image, error) {
	if !bytes.Contains(data[:min(100, len(data))], []byte("\n")) {
		return nil, syntaxError("not an IM file")
	}
	buffer := append([]byte{}, data[:min(100, len(data))]...)
	pos := len(buffer)
	xsize, ysize := 0, 0
	mode := ""
	var tileOffset int
	haveTile := false
	readMore := func(n int) []byte {
		start := pos
		end := min(pos+n, len(data))
		pos = end
		return data[start:end]
	}
	for {
		var s []byte
		if len(buffer) > 0 {
			s = buffer[:1]
			buffer = buffer[1:]
		} else {
			s = readMore(1)
		}
		if len(s) == 0 {
			break
		}
		if s[0] == 0x0c {
			tileOffset = pos - len(buffer)
			haveTile = true
			break
		}
		if !bytes.Contains(buffer, []byte("\n")) {
			buffer = append(buffer, readMore(100)...)
		}
		lines := bytes.SplitN(buffer, []byte("\n"), 2)
		s = append(s, lines[0]...)
		if len(lines) > 1 {
			buffer = lines[1]
		} else {
			buffer = nil
		}
		if len(s) == 1 || len(s) > 100 {
			break
		}
		if s[0] == '*' {
			continue
		}
		line := string(s)
		i := 0
		for i < len(line) && line[i] >= 'a' && line[i] <= 'z' {
			i++
		}
		if i >= len(line) || line[i] != ' ' {
			break
		}
		key := line[:i]
		rest := line[i+1:]
		end := len(rest)
		for j := 0; j < len(rest); j++ {
			if rest[j] == ' ' || rest[j] == '\r' || rest[j] == '\n' {
				end = j
				break
			}
		}
		v := rest[:end]
		switch key {
		case "width":
			n, err := strconv.Atoi(v)
			if err != nil {
				return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes([]byte(v)))
			}
			xsize = n
		case "height":
			n, err := strconv.Atoi(v)
			if err != nil {
				return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes([]byte(v)))
			}
			ysize = n
		case "pixel":
			if v == "n8" {
				mode = "L"
			}
		}
	}
	im := newImage(data, "IMT", mode, xsize, ysize)
	if haveTile {
		im.loadFn = func() error { return im.loadIMT(tileOffset) }
	}
	return im, nil
}

func validIPTCtag(v int) bool {
	return v == 1 || v == 2 || (v >= 3 && v <= 9) || v == 240
}

// --- MCIDAS / MPEG / MSP --------------------------------------------------

func acceptMCIDAS(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\x00\x00\x00\x00\x00\x00\x00\x04"))
}

func openMCIDAS(data []byte) (*Image, error) {
	if len(data) < 256 || !acceptMCIDAS(data[:8]) {
		return nil, syntaxError("not an McIdas area file")
	}
	w := make([]int, 65)
	for i := 0; i < 64; i++ {
		w[i+1] = int(int32(binary.BigEndian.Uint32(data[4*i:])))
	}
	var mode, rawmode string
	switch w[11] {
	case 1:
		mode, rawmode = "L", "L"
	case 2:
		mode, rawmode = "I;16B", "I;16B"
	case 4:
		mode, rawmode = "I", "I;32B"
	default:
		return nil, syntaxError("unsupported McIdas format")
	}
	im := newImage(data, "MCIDAS", mode, w[10], w[9])
	offset := w[34] + w[15]
	stride := w[15] + w[10]*w[11]*w[14]
	im.loadFn = func() error { return im.loadMCIDAS(offset, stride, rawmode) }
	return im, nil
}

func acceptMPEG(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\x00\x00\x01\xb3"))
}

func openMPEG(data []byte) (*Image, error) {
	if len(data) < 7 {
		return nil, shortError()
	}
	// BitStream(fd).read(32) == 0x1B3 then two 12-bit fields.
	bits := func(off, n int) int {
		v := 0
		for i := 0; i < n; i++ {
			bit := (int(data[(off+i)/8]) >> (7 - uint((off+i)%8))) & 1
			v = v<<1 | bit
		}
		return v
	}
	w := bits(32, 12)
	h := bits(44, 12)
	im := &Image{Format: "MPEG", Mode: "RGB", Width: w, Height: h}
	im.loadFn = func() error {
		// The plugin has no tile and no decoder.
		return osError("cannot load this image")
	}
	return im, nil
}

func acceptMSP(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("DanM")) || bytes.HasPrefix(prefix, []byte("LinS"))
}

func openMSP(data []byte) (*Image, error) {
	if len(data) < 32 || !acceptMSP(data[:4]) {
		return nil, syntaxError("not an MSP file")
	}
	checksum := 0
	for i := 0; i < 32; i += 2 {
		checksum ^= le16(data[i:])
	}
	if checksum != 0 {
		return nil, syntaxError("bad MSP checksum")
	}
	im := newImage(data, "MSP", "1", le16(data[4:]), le16(data[6:]))
	if bytes.HasPrefix(data, []byte("DanM")) {
		im.loadFn = func() error { return im.loadMSPRaw(32) }
	} else {
		im.loadFn = func() error { return im.loadMSP(32) }
	}
	return im, nil
}

// --- PCD / PIXAR / PSD / QOI ---------------------------------------------

func acceptPIXAR(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\x80\xe8\x00\x00"))
}

func openPIXAR(data []byte) (*Image, error) {
	if len(data) < 4 || !acceptPIXAR(data[:4]) {
		return nil, syntaxError("not a PIXAR file")
	}
	if len(data) < 512 {
		if len(data) < 428 {
			return nil, shortError()
		}
	}
	width, height := le16(data[418:]), le16(data[416:])
	mode := [2]int{le16(data[424:]), le16(data[426:])}
	im := newImage(data, "PIXAR", "", width, height)
	if mode == [2]int{14, 2} {
		im.Mode = "RGB"
		im.loadFn = func() error { return im.loadPIXAR() }
	}
	return im, nil
}

func acceptPSD(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("8BPS")) }

func acceptQOI(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("qoif")) }

func openQOI(data []byte) (*Image, error) {
	if len(data) < 4 || !acceptQOI(data[:4]) {
		return nil, syntaxError("not a QOI file")
	}
	if len(data) < 12 {
		return nil, shortError()
	}
	w := be32(data[4:])
	h := be32(data[8:])
	if len(data) < 13 {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	mode := "RGBA"
	if data[12] == 3 {
		mode = "RGB"
	}
	im := newImage(data, "QOI", mode, w, h)
	im.loadFn = func() error { return im.loadQOI() }
	return im, nil
}

// --- SGI / SPIDER / SUN ---------------------------------------------------

func acceptSGI(prefix []byte) bool { return len(prefix) >= 2 && be16(prefix) == 474 }

func spiderHeader(data []byte, big bool) int {
	isInt := func(f float64) bool { return f == math.Trunc(f) }
	get := func(i int) float64 {
		var u uint32
		if big {
			u = binary.BigEndian.Uint32(data[4*(i-1):])
		} else {
			u = binary.LittleEndian.Uint32(data[4*(i-1):])
		}
		return float64(math.Float32frombits(u))
	}
	for _, i := range []int{1, 2, 5, 12, 13, 22, 23} {
		if !isInt(get(i)) {
			return 0
		}
	}
	iform := int(get(5))
	if iform != 1 && iform != 3 && iform != -11 && iform != -12 && iform != -21 && iform != -22 {
		return 0
	}
	labrec := int(get(13))
	labbyt := int(get(22))
	lenbyt := int(get(23))
	if labbyt != labrec*lenbyt {
		return 0
	}
	return labbyt
}

func acceptSUN(prefix []byte) bool {
	return len(prefix) >= 4 && binary.BigEndian.Uint32(prefix) == 0x59A66A95
}

func openSUN(data []byte) (*Image, error) {
	if len(data) < 32 || !acceptSUN(data[:4]) {
		return nil, syntaxError("not an SUN raster file")
	}
	w, h := be32(data[4:]), be32(data[8:])
	depth := be32(data[12:])
	fileType := be32(data[20:])
	paletteType := be32(data[24:])
	paletteLength := be32(data[28:])
	offset := 32
	var mode, rawmode string
	switch depth {
	case 1:
		mode, rawmode = "1", "1;I"
	case 4:
		mode, rawmode = "L", "L;4"
	case 8:
		mode, rawmode = "L", "L"
	case 24:
		mode = "RGB"
		if fileType == 3 {
			rawmode = "RGB"
		} else {
			rawmode = "BGR"
		}
	case 32:
		mode = "RGB"
		if fileType == 3 {
			rawmode = "RGBX"
		} else {
			rawmode = "BGRX"
		}
	default:
		return nil, syntaxError("Unsupported Mode/Bit Depth")
	}
	var palette []byte
	var paletteMode string
	if paletteLength != 0 {
		if paletteLength > 1024 {
			return nil, syntaxError("Unsupported Color Palette Length")
		}
		if paletteType != 1 {
			return nil, syntaxError("Unsupported Palette Type")
		}
		offset += paletteLength
		if 32+paletteLength > len(data) {
			palette = append([]byte{}, data[32:]...)
		} else {
			palette = append([]byte{}, data[32:32+paletteLength]...)
		}
		paletteMode = "RGB;L"
		if mode == "L" {
			mode = "P"
			rawmode = strings.Replace(rawmode, "L", "P", 1)
		}
	}
	stride := ((w*depth + 15) / 16) * 2
	im := newImage(data, "SUN", mode, w, h)
	if palette != nil {
		im.sunPalette(palette, paletteMode)
	}
	switch {
	case fileType == 0 || fileType == 1 || fileType == 3 || fileType == 4 || fileType == 5:
		im.loadFn = func() error { return im.loadSUNRaw(offset, stride, rawmode) }
	case fileType == 2:
		im.loadFn = func() error { return im.loadSUNRLE(offset, rawmode) }
	default:
		return nil, syntaxError("Unsupported Sun Raster file type")
	}
	return im, nil
}

// --- TGA ------------------------------------------------------------------

func openTGA(data []byte) (*Image, error) {
	if len(data) < 18 {
		return nil, shortError()
	}
	s := data[:18]
	idLen := int(s[0])
	colormapType := s[1]
	imageType := s[2]
	depth := s[16]
	flags := s[17]
	w, h := le16(s[12:]), le16(s[14:])
	if (colormapType != 0 && colormapType != 1) || w <= 0 || h <= 0 ||
		!(depth == 1 || depth == 8 || depth == 16 || depth == 24 || depth == 32) {
		return nil, syntaxError("not a TGA file")
	}
	var mode string
	switch {
	case imageType == 3 || imageType == 11:
		mode = "L"
		if depth == 1 {
			mode = "1"
		} else if depth == 16 {
			mode = "LA"
		}
	case imageType == 1 || imageType == 9:
		if colormapType != 0 {
			mode = "P"
		} else {
			mode = "L"
		}
	case imageType == 2 || imageType == 10:
		if depth == 24 {
			mode = "RGB"
		} else {
			mode = "RGBA"
		}
	default:
		return nil, syntaxError("unknown TGA mode")
	}
	orientation := flags & 0x30
	flip := orientation == 0x10 || orientation == 0x30
	var orient int
	switch orientation {
	case 0x20, 0x30:
		orient = 1
	case 0, 0x10:
		orient = -1
	default:
		return nil, syntaxError("unknown TGA orientation")
	}
	pos := 18
	if idLen > 0 {
		pos += idLen
		if pos > len(data) {
			pos = len(data)
		}
	}
	var palette []byte
	var palRawmode string
	if colormapType != 0 {
		start, size, mapDepth := le16(s[3:]), le16(s[5:]), s[7]
		count := 0
		switch mapDepth {
		case 16:
			palRawmode = "BGRA;15Z"
			count = 2
		case 24:
			palRawmode = "BGR"
			count = 3
		case 32:
			palRawmode = "BGRA"
			count = 4
		default:
			return nil, syntaxError("unknown TGA map depth")
		}
		head := make([]byte, count*start)
		avail := len(data) - pos
		if avail < 0 {
			avail = 0
		}
		n := count * size
		if n > avail {
			n = avail
		}
		palette = append(head, data[pos:pos+n]...)
		pos += n
		if pos > len(data) {
			pos = len(data)
		}
	}
	rawmode, hasMode := tgaModes[[2]int{int(imageType & 7), int(depth)}]
	im := newImage(data, "TGA", mode, w, h)
	if palette != nil {
		im.sunPalette(palette, palRawmode)
	}
	t := &tgaLoad{offset: pos, rawmode: rawmode, orientation: orient, depth: int(depth) / 8, flip: flip}
	if hasMode {
		t.rle = imageType&8 != 0
		im.loadFn = func() error { return im.loadTGA(t) }
	} else {
		im.loadFn = func() error { return osError("cannot load this image") }
	}
	return im, nil
}

// --- WEBP ----------------------------------------------------------------
//
// Divergence X19 (image codec libraries): WebP is decoded by the bundled libwebp.
// VP8L lossless bitstreams are deterministic and are decoded below; the lossy
// VP8/VP8X path depends on libwebp's YUV conversion, so this package answers as
// a Pillow built without the WEBP codec for those streams: WebPImagePlugin._accept
// returns a message instead of True and Image.open raises UnidentifiedImageError.

// acceptWEBPLossless is WebPImagePlugin._accept for a simple VP8L file.
func acceptWEBPLossless(prefix []byte) bool {
	return len(prefix) >= 16 && bytes.HasPrefix(prefix, []byte("RIFF")) &&
		string(prefix[8:12]) == "WEBP" && string(prefix[12:16]) == "VP8L"
}

// openWEBPLossless is WebPImageFile._open for a simple lossless file.
func openWEBPLossless(data []byte) (*Image, error) {
	if len(data) < 20 || !acceptWEBPLossless(data[:16]) {
		return nil, syntaxError("not a WEBP file")
	}
	if len(data) < 25 || data[20] != 0x2F {
		return nil, osError("could not create decoder object")
	}
	hdr, ok := vp8lParseHeader(data[20:])
	if !ok {
		return nil, osError("could not create decoder object")
	}
	// _get_size then _decompression_bomb_check(error_only=True).
	if int64(hdr.width)*int64(hdr.height) > 2*maxImagePixels {
		return nil, &Error{Class: "DecompressionBombError",
			Text: "Image size (" + itoa(hdr.width*hdr.height) + " pixels) exceeds limit of " +
				itoa(2*maxImagePixels) + " pixels, could be decompression bomb DOS attack."}
	}
	chunkSize := int(binary.LittleEndian.Uint32(data[16:]))
	payloadEnd := 20 + chunkSize
	if payloadEnd > len(data) || payloadEnd < 20 {
		return nil, osError("could not create decoder object")
	}
	riffSize := int(binary.LittleEndian.Uint32(data[4:]))
	riffEnd := riffSize + 8
	if riffEnd < 20 || riffEnd > len(data) || payloadEnd > riffEnd {
		return nil, osError("could not create decoder object")
	}
	mode := "RGB"
	if hdr.alpha {
		mode = "RGBA"
	}
	im := newImage(data, "WEBP", mode, hdr.width, hdr.height)
	// WebPAnimDecoder validates the stream when it is created, so decode now.
	argb, w, h, alpha, err := decodeVP8L(data[20:payloadEnd])
	if err != nil || w != hdr.width || h != hdr.height {
		return nil, osError("could not create decoder object")
	}
	n := w * h
	if alpha {
		im.pix = make([]byte, n*4)
		for i := 0; i < n; i++ {
			im.pix[4*i] = argb[4*i+2]
			im.pix[4*i+1] = argb[4*i+1]
			im.pix[4*i+2] = argb[4*i]
			im.pix[4*i+3] = argb[4*i+3]
		}
	} else {
		im.pix = make([]byte, n*3)
		for i := 0; i < n; i++ {
			im.pix[3*i] = argb[4*i+2]
			im.pix[3*i+1] = argb[4*i+1]
			im.pix[3*i+2] = argb[4*i]
		}
	}
	return im, nil
}

func acceptWMF(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\xd7\xcd\xc6\x9a\x00\x00")) ||
		bytes.HasPrefix(prefix, []byte("\x01\x00\x00\x00"))
}

func openWMF(data []byte) (*Image, error) {
	// WmfStubImageFile._open.
	s := data[:min(44, len(data))]
	if len(s) < 6 {
		return nil, shortError()
	}
	var w, h int
	if bytes.HasPrefix(s, []byte("\xd7\xcd\xc6\x9a\x00\x00")) {
		if len(s) < 16 {
			return nil, shortError()
		}
		inch := le16(s[14:])
		if inch == 0 {
			return nil, valueError("Invalid inch")
		}
		x0, y0 := int(i16s(le16(s[6:]))), int(i16s(le16(s[8:])))
		x1, y1 := int(i16s(le16(s[10:]))), int(i16s(le16(s[12:])))
		w = (x1 - x0) * 72 / inch
		h = (y1 - y0) * 72 / inch
		var head []byte
		if len(s) >= 22 {
			head = s[22:min(26, len(s))]
		}
		if !bytes.Equal(head, []byte("\x01\x00\x09\x00")) {
			return nil, syntaxError("Unsupported WMF file format")
		}
	} else if bytes.HasPrefix(s, []byte("\x01\x00\x00\x00")) && len(s) >= 44 && bytes.Equal(s[40:44], []byte(" EMF")) {
		x0, y0 := le32s(s[8:]), le32s(s[12:])
		x1, y1 := le32s(s[16:]), le32s(s[20:])
		f0, f1 := le32s(s[24:]), le32s(s[28:])
		f2, f3 := le32s(s[32:]), le32s(s[36:])
		w, h = x1-x0, y1-y0
		denX, denY := f2-f0, f3-f1
		if denX == 0 || denY == 0 {
			return nil, &Error{Class: "ZeroDivisionError", Text: "float division by zero"}
		}
	} else {
		return nil, syntaxError("Unsupported file format")
	}
	im := &Image{Format: "WMF", Mode: "RGB", Width: w, Height: h}
	im.loadFn = func() error {
		// No WMF/EMF handler is registered on this platform.
		return osError("cannot find loader for this WMF file")
	}
	return im, nil
}

func i16s(v int) int16 { return int16(uint16(v)) }

// --- XBM / XPM / XVTHUMB --------------------------------------------------

func acceptXBM(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("#define")) ||
		bytes.HasPrefix(prefix, []byte("\n#define")) ||
		bytes.HasPrefix(prefix, []byte("/*"))
}

func openXBM(data []byte) (*Image, error) {
	// XbmImageFile._open: scan #define lines, then note where the bit data
	// starts.
	width, height := -1, -1
	offset := -1
	pos := 0
	starting := true
	inBlockComment := false
	readLine := func() []byte {
		if pos >= len(data) {
			return nil
		}
		end := pos
		limit := pos + 75
		if limit > len(data) {
			limit = len(data)
		}
		for end < limit && data[end] != '\n' {
			end++
		}
		line := data[pos:end]
		pos = end
		if pos < len(data) && data[pos] == '\n' {
			pos++
		}
		return line
	}
	for {
		line := readLine()
		if line == nil {
			break
		}
		line = bytes.TrimRight(line, " \t\r\n\v\f")
		if inBlockComment {
			if bytes.HasSuffix(line, []byte("*/")) {
				inBlockComment = false
			}
			continue
		}
		if bytes.HasPrefix(line, []byte("/*")) {
			inBlockComment = !bytes.HasSuffix(line, []byte("*/"))
			continue
		}
		if starting && len(line) == 0 {
			starting = false
			continue
		}
		if !bytes.HasPrefix(line, []byte("#define ")) {
			if bytes.HasSuffix(line, []byte("_bits[] = {")) {
				offset = pos
			}
			break
		}
		fields := bytes.Fields(line)
		if len(fields) == 0 {
			break
		}
		v, err := strconv.Atoi(string(fields[len(fields)-1]))
		if err != nil {
			return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes(fields[len(fields)-1]))
		}
		switch {
		case bytes.Contains(line, []byte("width ")):
			width = v
		case bytes.Contains(line, []byte("height ")):
			height = v
		}
	}
	if width < 0 || height < 0 || offset < 0 {
		return nil, syntaxError("not a XBM file")
	}
	im := newImage(data, "XBM", "1", width, height)
	im.loadFn = func() error { return im.loadXBM(offset) }
	return im, nil
}

func acceptXPM(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("/* XPM */")) }

var xpmHead = regexp.MustCompile(`^\s*"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*"`)

func openXPM(data []byte) (*Image, error) {
	if len(data) < 9 || !acceptXPM(data[:9]) {
		return nil, syntaxError("not an XPM file")
	}
	pos := 9
	readLine := func() ([]byte, bool) {
		if pos >= len(data) {
			return nil, false
		}
		end := bytes.IndexByte(data[pos:], '\n')
		if end < 0 {
			s := data[pos:]
			pos = len(data)
			return s, true
		}
		s := data[pos : pos+end]
		pos += end + 1
		return s, true
	}
	var m [][]byte
	for {
		line, ok := readLine()
		if !ok {
			return nil, syntaxError("broken XPM file")
		}
		m = xpmHead.FindSubmatch(line)
		if m != nil {
			break
		}
	}
	w, err := strconv.Atoi(string(m[1]))
	if err != nil {
		return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes(m[1]))
	}
	h, err := strconv.Atoi(string(m[2]))
	if err != nil {
		return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes(m[2]))
	}
	paletteLength, err := strconv.Atoi(string(m[3]))
	if err != nil {
		return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes(m[3]))
	}
	bpp, err := strconv.Atoi(string(m[4]))
	if err != nil {
		return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes(m[4]))
	}

	// Load the palette description; keys keep their first insertion order.
	palette := map[string][]byte{}
	var keys []string
	var transparentKey string
	hasTransparent := false
	for i := 0; i < paletteLength; i++ {
		line, ok := readLine()
		if !ok {
			return nil, &Error{Class: "IndexError", Text: "index out of range"}
		}
		line = bytes.TrimRight(line, " \t\r\n\v\f")
		var (
			key    string
			fields [][]byte
		)
		if len(line) >= 1 && bpp+1 <= len(line) {
			key = string(line[1 : bpp+1])
		} else if len(line) >= 1 {
			key = string(line[1:])
		}
		if bpp+1 <= len(line) {
			rest := line[bpp+1:]
			if len(rest) > 2 {
				rest = rest[:len(rest)-2]
			} else {
				rest = nil
			}
			fields = bytes.Fields(rest)
		}
		found := false
		for j := 0; j+1 < len(fields); j += 2 {
			if string(fields[j]) == "c" {
				found = true
				rgb := fields[j+1]
				if _, seen := palette[key]; !seen {
					keys = append(keys, key)
				}
				switch {
				case string(rgb) == "None":
					transparentKey = key
					hasTransparent = true
					palette[key] = []byte{0, 0, 0}
				case bytes.HasPrefix(rgb, []byte("#")):
					v, err := strconv.ParseInt(string(rgb[1:]), 16, 64)
					if err != nil {
						return nil, valueError("invalid literal for int() with base 16: " + pyReprBytes(rgb[1:]))
					}
					palette[key] = []byte{byte(v >> 16 & 255), byte(v >> 8 & 255), byte(v & 255)}
				default:
					return nil, valueError("cannot read this XPM file")
				}
				break
			}
		}
		if !found {
			return nil, valueError("cannot read this XPM file")
		}
	}

	im := newImage(data, "XPM", "P", w, h)
	if paletteLength > 256 {
		mode := "RGB"
		if hasTransparent {
			mode = "RGBA"
		}
		rgbapal := map[string][]byte{}
		for k, v := range palette {
			if hasTransparent {
				a := byte(0xFF)
				if k == transparentKey {
					a = 0x00
				}
				rgbapal[k] = []byte{v[0], v[1], v[2], a}
			} else {
				rgbapal[k] = v
			}
		}
		im.Mode = mode
		im.loadFn = func() error { return im.loadXPM(pos, bpp, rgbapal, keys, mode) }
		return im, nil
	}
	raw := []byte{}
	for _, k := range keys {
		raw = append(raw, palette[k]...)
	}
	im.setPaletteRGB(raw, raw)
	if hasTransparent {
		for i, k := range keys {
			if k == transparentKey {
				im.transparency = transparency{present: true, isInt: true, index: i}
				break
			}
		}
	}
	im.loadFn = func() error { return im.loadXPM(pos, bpp, palette, keys, "P") }
	return im, nil
}

func acceptXVTHUMB(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("P7 332")) }

var xvPalette = func() []byte {
	out := make([]byte, 0, 768)
	for r := 0; r < 8; r++ {
		for g := 0; g < 8; g++ {
			for b := 0; b < 4; b++ {
				out = append(out, byte(r*255/7), byte(g*255/7), byte(b*255/3))
			}
		}
	}
	return out
}()

func openXVTHUMB(data []byte) (*Image, error) {
	if len(data) < 6 || !acceptXVTHUMB(data[:6]) {
		return nil, syntaxError("not an XV thumbnail file")
	}
	pos := 6
	readLine := func() ([]byte, bool) {
		if pos >= len(data) {
			return nil, false
		}
		end := bytes.IndexByte(data[pos:], '\n')
		if end < 0 {
			s := data[pos:]
			pos = len(data)
			return s, true
		}
		s := data[pos : pos+end+1]
		pos += len(s)
		return s, true
	}
	if _, ok := readLine(); !ok {
		// The first readline at EOF returns b"" without raising.
		return nil, syntaxError("Unexpected EOF reading XV thumbnail file")
	}
	var line []byte
	for {
		l, ok := readLine()
		if !ok {
			return nil, syntaxError("Unexpected EOF reading XV thumbnail file")
		}
		if len(l) > 0 && l[0] != '#' {
			line = l
			break
		}
	}
	fields := strings.Fields(string(bytes.TrimSpace(line)))
	if len(fields) < 2 {
		return nil, &Error{Class: "ValueError", Text: "not enough values to unpack (expected 2, got " + strconv.Itoa(len(fields)) + ")"}
	}
	w, err := strconv.Atoi(fields[0])
	if err != nil {
		return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes([]byte(fields[0])))
	}
	h, err := strconv.Atoi(fields[1])
	if err != nil {
		return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes([]byte(fields[1])))
	}
	im := newImage(data, "XVThumb", "P", w, h)
	im.setPaletteRGB(xvPalette, xvPalette)
	offset := pos
	im.loadFn = func() error { return im.rawTile(data, offset, 0, 1, "P") }
	return im, nil
}
