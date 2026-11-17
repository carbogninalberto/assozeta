package pilimage

import (
	"bytes"
	"encoding/binary"
	"math"
	"regexp"
	"strconv"
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

func be16(b []byte) int  { return int(binary.BigEndian.Uint16(b)) }
func be32(b []byte) int  { return int(binary.BigEndian.Uint32(b)) }
func le16(b []byte) int  { return int(binary.LittleEndian.Uint16(b)) }
func le32(b []byte) int  { return int(binary.LittleEndian.Uint32(b)) }
func le32s(b []byte) int { return int(int32(binary.LittleEndian.Uint32(b))) }

// --- AVIF -----------------------------------------------------------------

func acceptAVIF(prefix []byte) bool {
	if len(prefix) < 12 || string(prefix[4:8]) != "ftyp" {
		return false
	}
	switch string(prefix[8:12]) {
	case "avif", "avis", "mif1", "msf1":
		return true
	}
	return false
}

func openAVIF(data []byte) (*Image, error) {
	// The AVIF decoder rejects data without a complete box structure; a file
	// whose boxes all fit is reported as a format this package does not decode.
	pos := 0
	for pos < len(data) {
		if pos+8 > len(data) {
			return nil, syntaxError("Invalid box")
		}
		size := int(binary.BigEndian.Uint32(data[pos:]))
		if !printableBoxType(data[pos+4 : pos+8]) {
			return nil, syntaxError("Invalid box")
		}
		if size == 0 {
			// A zero length means the box extends to the end of the file.
			pos = len(data)
			break
		}
		if size == 1 {
			if pos+16 > len(data) {
				return nil, syntaxError("Invalid box")
			}
			size = int(binary.BigEndian.Uint64(data[pos+8:]))
		}
		if size < 8 || pos+size > len(data) {
			return nil, syntaxError("Invalid box")
		}
		pos += size
	}
	return identified("AVIF")
}

// printableBoxType reports whether b looks like an ISO base media box type.
func printableBoxType(b []byte) bool {
	if len(b) != 4 {
		return false
	}
	for _, c := range b {
		if c < 0x20 || c > 0x7e {
			return false
		}
	}
	return true
}

// --- BLP ------------------------------------------------------------------

func acceptBLP(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("BLP1")) || bytes.HasPrefix(prefix, []byte("BLP2"))
}

func openBLP(data []byte) (*Image, error) {
	if len(data) < 4 || !acceptBLP(data[:4]) {
		return nil, syntaxError("Bad BLP magic")
	}
	pos := 4
	readI := func() (int, bool) {
		if pos+4 > len(data) {
			return 0, false
		}
		v := le32(data[pos:])
		pos += 4
		return v, true
	}
	if _, ok := readI(); !ok {
		return nil, shortError()
	}
	if string(data[:4]) == "BLP1" {
		if verbose := func() bool {
			_, ok := readI()
			return ok
		}; !verbose() {
			return nil, shortError()
		}
	} else {
		if pos+4 > len(data) {
			return nil, shortError()
		}
		pos += 3
		pos++ // mips
	}
	if pos+8 > len(data) {
		return nil, shortError()
	}
	w, h := 0, 0
	if pos+8 > len(data) {
		return nil, shortError()
	}
	w = int(binary.LittleEndian.Uint32(data[pos:]))
	h = int(binary.LittleEndian.Uint32(data[pos+4:]))
	pos += 8 // size
	if string(data[:4]) == "BLP1" {
		if _, ok := readI(); !ok {
			return nil, shortError()
		}
		pos += 4
	}
	mode := "RGB"
	if len(data) > 8 {
		// The alpha flag was read earlier; BLP1 uses a 32-bit flag.
	}
	if string(data[:4]) == "BLP1" && pos >= 12 && binary.LittleEndian.Uint32(data[8:12]) != 0 {
		mode = "RGBA"
	}
	if string(data[:4]) == "BLP2" && len(data) > 10 && data[10] != 0 {
		mode = "RGBA"
	}
	return &Image{Format: "BLP", Mode: mode, Width: w, Height: h}, nil
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

func openCUR(data []byte) (*Image, error) {
	if len(data) < 6 || !acceptCUR(data[:4]) {
		return nil, syntaxError("not a CUR file")
	}
	count := le16(data[4:])
	pos := 6
	var best []byte
	for i := 0; i < count; i++ {
		if pos+16 > len(data) {
			return nil, shortError()
		}
		s := data[pos : pos+16]
		pos += 16
		if best == nil {
			best = s
		} else if s[0] > best[0] && s[1] > best[1] {
			best = s
		}
	}
	if best == nil {
		return nil, &Error{Class: "TypeError", Text: "No cursors were found"}
	}
	header := le32(best[12:])
	if header < 0 || header >= len(data) {
		return nil, syntaxError("not a CUR file")
	}
	if _, err := bmpBitmap(data, header, 0); err != nil {
		return nil, err
	}
	return identified("CUR")
}

// --- PCX / DCX ------------------------------------------------------------

var acceptPCXcolor = map[byte]bool{0: true, 2: true, 3: true, 5: true}

func acceptPCX(prefix []byte) bool {
	return len(prefix) >= 2 && prefix[0] == 10 && acceptPCXcolor[prefix[1]]
}

func openPCX(data []byte) (*Image, error) {
	if _, err := pcxOpenAt(data, 0, true); err != nil {
		return nil, err
	}
	return identified("PCX")
}

func pcxOpenAt(data []byte, base int, requireAccept bool) (*Image, error) {
	if base+68 > len(data) {
		return nil, shortError()
	}
	s := data[base : base+68]
	if requireAccept && !acceptPCX(s) {
		return nil, syntaxError("not a PCX file")
	}
	bbox := [4]int{le16(s[4:]), le16(s[6:]), le16(s[8:]) + 1, le16(s[10:]) + 1}
	if bbox[2] <= bbox[0] || bbox[3] <= bbox[1] {
		return nil, syntaxError("bad PCX image size")
	}
	version := s[1]
	bits := s[3]
	planes := s[65]
	if bits == 1 && planes == 1 {
	} else if bits == 1 && (planes == 2 || planes == 4) {
	} else if version == 5 && bits == 8 && planes == 1 {
	} else if version == 5 && bits == 8 && planes == 3 {
	} else {
		return nil, osError("unknown PCX mode")
	}
	return identified("PCX")
}

const dcxMagic = 0x3ADE68B1

func acceptDCX(prefix []byte) bool {
	return len(prefix) >= 4 && binary.LittleEndian.Uint32(prefix) == dcxMagic
}

func openDCX(data []byte) (*Image, error) {
	if len(data) < 4 || !acceptDCX(data[:4]) {
		return nil, syntaxError("not a DCX file")
	}
	pos := 4
	var offsets []int
	for i := 0; i < 1024; i++ {
		if pos+4 > len(data) {
			return nil, shortError()
		}
		v := le32(data[pos:])
		pos += 4
		if v == 0 {
			break
		}
		offsets = append(offsets, v)
	}
	if len(offsets) == 0 {
		return nil, &Error{Class: "IndexError", Text: "list index out of range"}
	}
	if offsets[0] < 0 || offsets[0] >= len(data) {
		return nil, shortError()
	}
	if _, err := pcxOpenAt(data, offsets[0], false); err != nil {
		return nil, err
	}
	return identified("DCX")
}

// --- DDS ------------------------------------------------------------------

func acceptDDS(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("DDS ")) }

func openDDS(data []byte) (*Image, error) {
	if len(data) < 4 || !acceptDDS(data[:4]) {
		return nil, syntaxError("not a DDS file")
	}
	if len(data) < 8 {
		return nil, shortError()
	}
	headerSize := int(binary.LittleEndian.Uint32(data[4:]))
	if headerSize != 124 {
		return nil, osError("Unsupported header size " + strconv.Itoa(headerSize))
	}
	if len(data) < 128 {
		return nil, osError("Incomplete header: " + strconv.Itoa(len(data)-8) + " bytes")
	}
	header := data[8:128]
	pfflags := binary.LittleEndian.Uint32(header[72:])
	fourcc := header[76:80]
	bitcount := binary.LittleEndian.Uint32(header[80:])
	const (
		ddpfAlphaPixels = 0x1
		ddpfRGB         = 0x40
		ddpfLuminance   = 0x20000
		ddpfFourCC      = 0x4
	)
	if pfflags&ddpfRGB != 0 {
		return identified("DDS")
	}
	if pfflags&ddpfLuminance != 0 {
		if bitcount == 8 || (bitcount == 16 && pfflags&ddpfAlphaPixels != 0) {
			return identified("DDS")
		}
		return nil, osError("Unsupported bitcount " + strconv.Itoa(int(bitcount)) + " for " + strconv.Itoa(int(pfflags)))
	}
	if pfflags&ddpfFourCC != 0 {
		switch string(fourcc) {
		case "DXT1", "DXT3", "DXT5", "ATI1", "BC5S", "ATI2", "DX10":
			return identified("DDS")
		}
		return nil, &Error{Class: "NotImplementedError", Text: "Unimplemented pixel format " + pyReprBytes(fourcc)}
	}
	return nil, &Error{Class: "NotImplementedError", Text: "Unknown pixel format flags " + strconv.Itoa(int(pfflags))}
}

// --- EPS ------------------------------------------------------------------

func acceptEPS(prefix []byte) bool {
	if bytes.HasPrefix(prefix, []byte("%!PS")) {
		return true
	}
	return len(prefix) >= 4 && binary.LittleEndian.Uint32(prefix) == 0xC6D3D0C5
}

func openEPS(data []byte) (*Image, error) {
	offset := 0
	if len(data) >= 4 && binary.LittleEndian.Uint32(data) == 0xC6D3D0C5 {
		// DOS EPS binary header: the PostScript section follows at a fixed
		// offset stored in the header.
		if len(data) < 12 {
			return nil, shortError()
		}
		offset = int(binary.LittleEndian.Uint32(data[4:]))
		if offset < 0 || offset > len(data) {
			return nil, syntaxError("not an EPS file")
		}
	}
	section := data[offset:]
	hasAdobe, hasBBox := false, false
	pos := 0
	for pos < len(section) {
		end := bytes.IndexAny(section[pos:], "\r\n")
		var line []byte
		if end < 0 {
			line = section[pos:]
			pos = len(section)
		} else {
			line = section[pos : pos+end]
			pos += end + 1
			if len(line) > 0 && line[len(line)-1] == '\r' {
				line = line[:len(line)-1]
			}
		}
		if len(line) == 0 {
			continue
		}
		if bytes.HasPrefix(line, []byte("%%EOF")) || bytes.HasPrefix(line, []byte("%%Trailer")) {
			break
		}
		if bytes.HasPrefix(line, []byte("%%BoundingBox:")) {
			hasBBox = true
			continue
		}
		if bytes.HasPrefix(line, []byte("%!PS-Adobe")) {
			hasAdobe = true
			continue
		}
		if bytes.HasPrefix(line, []byte("%ImageData:")) {
			continue
		}
		if line[0] == '%' {
			continue
		}
		break
	}
	if !hasAdobe || !hasBBox {
		return nil, syntaxError("not an EPS file")
	}
	return identified("EPS")
}

// --- FITS -----------------------------------------------------------------

func acceptFITS(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("SIMPLE")) }

func openFITS(data []byte) (*Image, error) {
	pos := 0
	for {
		if pos >= len(data) {
			return nil, osError("Truncated FITS file")
		}
		end := min(pos+80, len(data))
		header := data[pos:end]
		pos = end
		var keyword []byte
		if len(header) >= 8 {
			keyword = bytes.TrimSpace(header[:8])
		} else {
			keyword = bytes.TrimSpace(header)
		}
		value := []byte{}
		if len(header) > 8 {
			value = bytes.TrimSpace(bytes.SplitN(header[8:], []byte("/"), 2)[0])
			value = bytes.TrimPrefix(value, []byte("="))
			value = bytes.TrimSpace(value)
		}
		if bytes.HasPrefix(keyword, []byte("SIMPLE")) || bytes.HasPrefix(keyword, []byte("XTENSION")) {
			if string(value) != "T" {
				return nil, syntaxError("Not a FITS file")
			}
		} else if string(keyword) == "END" {
			dataStart := (pos + 2879) / 2880 * 2880
			if dataStart >= len(data) {
				return nil, valueError("No image data")
			}
			return identified("FITS")
		}
	}
}

// --- FLI ------------------------------------------------------------------

func acceptFLI(prefix []byte) bool {
	if len(prefix) < 16 {
		return false
	}
	magic := le16(prefix[4:])
	flags := le16(prefix[14:])
	return (magic == 0xAF11 || magic == 0xAF12) && (flags == 0 || flags == 3)
}

func openFLI(data []byte) (*Image, error) {
	if len(data) < 128 || !acceptFLI(data[:16]) || data[20] != 0 || data[21] != 0 {
		return nil, syntaxError("not an FLI/FLC file")
	}
	for _, c := range data[42:80] {
		if c != 0 {
			return nil, syntaxError("not an FLI/FLC file")
		}
	}
	for _, c := range data[88:] {
		if c != 0 {
			return nil, syntaxError("not an FLI/FLC file")
		}
	}
	if len(data) < 144 {
		return nil, shortError()
	}
	return identified("FLI")
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
	formatCount := le32s(data[20:])
	if formatCount != 1 {
		return nil, valueError("Unsupported number of texture formats: " + strconv.Itoa(formatCount))
	}
	format := le32s(data[24:])
	if format != 1 && format != 2 {
		return nil, valueError("Invalid texture compression format: " + strconv.Itoa(format))
	}
	return identified("FTEX")
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
	if version == 2 {
		if len(data) < 28 {
			return nil, shortError()
		}
		if string(data[20:24]) != "GIMP" {
			return nil, syntaxError("not a GIMP brush, bad magic number")
		}
	}
	if err := checkDecompressionBomb(width, height); err != nil {
		return nil, err
	}
	return identified("GBR")
}

// --- JPEG 2000 ------------------------------------------------------------

func acceptJPEG2000(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\xff\x4f\xff\x51")) ||
		bytes.HasPrefix(prefix, []byte("\x00\x00\x00\x0cjP  \x0d\x0a\x87\x0a"))
}

// jp2BoxReader is Jpeg2KImagePlugin.BoxReader.
type jp2BoxReader struct {
	data      []byte
	pos       int
	remaining int
	length    int // -1 when the box length is unknown
}

func (r *jp2BoxReader) readBytes(n int) ([]byte, error) {
	if r.length >= 0 && r.pos+n > r.length {
		return nil, syntaxError("Not enough data in header")
	}
	if r.remaining >= 0 && n > r.remaining {
		return nil, syntaxError("Not enough data in header")
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

func (r *jp2BoxReader) nextBoxType() ([]byte, error) {
	if r.remaining > 0 {
		if int64(r.pos)+int64(r.remaining) >= 1<<62 {
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
	if lbox < hlen {
		return nil, syntaxError("Invalid header length")
	}
	r.remaining = lbox - hlen
	return tbox, nil
}

func openJPEG2000(data []byte) (*Image, error) {
	if bytes.HasPrefix(data, []byte("\xff\x4f\xff\x51")) {
		// Codestream: parse the SIZ marker.
		if len(data) < 4 {
			return nil, shortError()
		}
		lsiz := be16(data[2:])
		if lsiz < 38 {
			return nil, valueError("SIZ marker length must be at least 38")
		}
		if len(data) < 2+lsiz {
			return nil, shortError()
		}
		csiz := be16(data[2+36:])
		if csiz < 1 || csiz > 4 {
			return nil, syntaxError("unable to determine J2K image mode")
		}
		return identified("JPEG2000")
	}
	if len(data) < 12 || string(data[4:12]) != "jP  \x0d\x0a\x87\x0a" {
		return nil, syntaxError("not a JPEG 2000 file")
	}
	r := &jp2BoxReader{data: data, pos: 12, remaining: -1, length: -1}
	var header *jp2BoxReader
	for {
		tbox, err := r.nextBoxType()
		if err != nil {
			return nil, err
		}
		if string(tbox) == "jp2h" {
			size := r.remaining
			if size < 0 || r.pos+size > len(data) {
				return nil, osError("Expected to read " + strconv.Itoa(size) + " bytes but only got 0.")
			}
			header = &jp2BoxReader{data: data[r.pos : r.pos+size], remaining: -1, length: size}
			break
		}
		if string(tbox) == "ftyp" {
			if _, err := r.readBytes(4); err != nil {
				return nil, err
			}
		}
		if r.remaining == 0 && r.pos >= len(data) {
			return nil, syntaxError("Invalid header length")
		}
	}
	// The JP2 header must contain an ihdr box.
	for header.pos < len(header.data) {
		tbox, err := header.nextBoxType()
		if err != nil {
			return nil, err
		}
		if string(tbox) == "ihdr" {
			return identified("JPEG2000")
		}
	}
	return nil, syntaxError("Invalid header length")
}

// --- ICNS -----------------------------------------------------------------

func acceptICNS(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("icns")) }

func openICNS(data []byte) (*Image, error) {
	if len(data) < 8 {
		return nil, shortError()
	}
	if string(data[:4]) != "icns" {
		return nil, syntaxError("not an icns file")
	}
	filesize := int(binary.BigEndian.Uint32(data[4:]))
	known := 0
	pos := 8
	for pos < filesize {
		if pos+8 > len(data) {
			return nil, shortError()
		}
		blockSize := int(binary.BigEndian.Uint32(data[pos+4:]))
		if blockSize <= 0 {
			return nil, syntaxError("invalid block header")
		}
		pos += 8
		blockSize -= 8
		switch string(data[pos-8 : pos-4]) {
		case "ic10", "ic09", "ic14", "ic08", "ic13", "ic07", "it32", "t8mk",
			"icp6", "ic12", "ih32", "h8mk", "icp5", "il32", "l8mk", "ic11",
			"icp4", "is32", "s8mk":
			known++
		}
		pos += blockSize
	}
	if known == 0 {
		return nil, syntaxError("No 32bit icon resources found")
	}
	return identified("ICNS")
}

// --- ICO ------------------------------------------------------------------

func acceptICO(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("\x00\x00\x01\x00")) }

func openICO(data []byte) (*Image, error) {
	if len(data) < 6 || !acceptICO(data[:4]) {
		return nil, syntaxError("not an ICO file")
	}
	count := le16(data[4:])
	if count == 0 {
		return nil, &Error{Class: "IndexError", Text: "list index out of range"}
	}
	if 6+16*count > len(data) {
		return nil, shortError()
	}
	// IcoImageFile._open loads the first entry; malformed entries fail there.
	entry := data[6:22]
	if entry[0] == 0 || entry[1] == 0 {
		return identified("ICO")
	}
	return identified("ICO")
}

// --- IM / IMT / IPTC ------------------------------------------------------

var imSplit = regexp.MustCompile(`^([A-Za-z0-9 ]+):[ \t]*(.*)$`)

func openIM(data []byte) (*Image, error) {
	if !bytes.Contains(data[:min(100, len(data))], []byte("\n")) {
		return nil, syntaxError("not an IM file")
	}
	pos := 0
	sawTag := false
	for {
		if pos >= len(data) {
			break
		}
		c := data[pos]
		if c == 0 || c == 0x1a {
			break
		}
		if c == '\r' {
			pos++
			continue
		}
		lineEnd := bytes.IndexByte(data[pos:], '\n')
		var line []byte
		if lineEnd < 0 {
			line = data[pos:]
			pos = len(data)
		} else {
			line = data[pos : pos+lineEnd+1]
			pos += lineEnd + 1
		}
		line = bytes.TrimSuffix(line, []byte("\n"))
		line = bytes.TrimSuffix(line, []byte("\r"))
		if len(line) > 100 {
			return nil, syntaxError("not an IM file")
		}
		m := imSplit.FindSubmatch(line)
		if m == nil {
			return nil, syntaxError("Syntax error in IM header: " + string(line))
		}
		k := string(m[1])
		switch k {
		case "Frames", "Scale", "Size", "Mode", "LUT", "Comment":
			sawTag = true
		}
	}
	if !sawTag {
		return nil, syntaxError("Not an IM file")
	}
	return identified("IM")
}

func openIMT(data []byte) (*Image, error) {
	if !bytes.Contains(data[:min(100, len(data))], []byte("\n")) {
		return nil, syntaxError("not an IM file")
	}
	mode, w, h := "", 0, 0
	pos := 0
	line := func() ([]byte, bool) {
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
	for {
		s, ok := line()
		if !ok {
			break
		}
		if len(s) == 1 && s[0] == 0x0c {
			break
		}
		if len(s) == 0 || len(s) > 100 {
			break
		}
		if s[0] == '*' {
			continue
		}
		// field = re.compile(rb"([a-z]*) ([^ \r\n]*)")
		i := 0
		for i < len(s) && s[i] >= 'a' && s[i] <= 'z' {
			i++
		}
		if i == 0 || i >= len(s) || s[i] != ' ' {
			break
		}
		key := s[:i]
		rest := s[i+1:]
		end := len(rest)
		for j, c := range rest {
			if c == ' ' || c == '\r' || c == '\n' {
				end = j
				break
			}
		}
		v := rest[:end]
		switch string(key) {
		case "width":
			n, err := strconv.Atoi(string(v))
			if err != nil {
				return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes(v))
			}
			w = n
		case "height":
			n, err := strconv.Atoi(string(v))
			if err != nil {
				return nil, valueError("invalid literal for int() with base 10: " + pyReprBytes(v))
			}
			h = n
		case "pixel":
			if string(v) == "n8" {
				mode = "L"
			}
		}
	}
	if mode == "" || w <= 0 || h <= 0 {
		return nil, syntaxError("not identified by this driver")
	}
	return identified("IMT")
}

// openIPTC ports IptcImageFile._open: parse 0x1C field headers until the
// image data field, then require the image attribute fields.
func openIPTC(data []byte) (*Image, error) {
	pos := 0
	info := map[[2]int][]byte{}
	for {
		if pos+5 > len(data) {
			return nil, shortError()
		}
		s := data[pos : pos+5]
		pos += 5
		if len(bytes.Trim(s, "\x00")) == 0 {
			break
		}
		tag := [2]int{int(s[1]), int(s[2])}
		if s[0] != 0x1c || !validIPTCtag(tag[0]) {
			return nil, syntaxError("invalid IPTC/NAA file")
		}
		size := int(s[3])
		if size > 132 {
			return nil, osError("illegal field length in IPTC/NAA file")
		} else if size == 128 {
			size = 0
		} else if size > 128 {
			if pos+size-128 > len(data) {
				return nil, shortError()
			}
			size = int(binary.BigEndian.Uint32(data[pos+size-128-4:]))
		} else {
			size = be16(s[3:])
		}
		var tagdata []byte
		if size > 0 {
			if pos+size > len(data) {
				return nil, shortError()
			}
			tagdata = data[pos : pos+size]
			pos += size
		}
		info[tag] = tagdata
		if tag == [2]int{8, 10} {
			break
		}
	}
	mode := info[[2]int{3, 60}]
	if len(mode) < 2 {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	return identified("IPTC")
}

func validIPTCtag(v int) bool {
	return v == 1 || v == 2 || (v >= 3 && v <= 9) || v == 240
}

// --- MCIDAS / MPEG / MSP --------------------------------------------------

func acceptMCIDAS(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\x00\x00\x00\x00\x00\x00\x00\x04"))
}

func openMCIDAS(data []byte) (*Image, error) {
	if len(data) < 256 {
		return nil, syntaxError("not an McIdas area file")
	}
	w := make([]int, 65)
	for i := 0; i < 64; i++ {
		w[i+1] = be32(data[4*i:])
	}
	switch w[11] {
	case 1, 2, 4:
	default:
		return nil, syntaxError("unsupported McIdas format")
	}
	return identified("MCIDAS")
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
	return &Image{Format: "MPEG", Mode: "RGB", Width: w, Height: h}, nil
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
	return identified("MSP")
}

// --- PCD / PIXAR / PSD / QOI ---------------------------------------------

func openPCD(data []byte) (*Image, error) {
	if len(data) < 2048+1539 {
		return nil, syntaxError("not a PCD file")
	}
	if string(data[2048:2052]) != "PCD_" {
		return nil, syntaxError("not a PCD file")
	}
	return identified("PCD")
}

func acceptPIXAR(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\x80\xe8\x00\x00"))
}

func openPIXAR(data []byte) (*Image, error) {
	if len(data) < 428 {
		return nil, shortError()
	}
	width, height := le16(data[418:]), le16(data[416:])
	mode := [2]int{le16(data[424:]), le16(data[426:])}
	if mode != [2]int{14, 2} {
		return nil, syntaxError("not identified by this driver")
	}
	return &Image{Format: "PIXAR", Mode: "RGB", Width: width, Height: height}, nil
}

func acceptPSD(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("8BPS")) }

func openPSD(data []byte) (*Image, error) {
	if len(data) < 26 || !acceptPSD(data[:4]) {
		return nil, syntaxError("not a PSD file")
	}
	if be16(data[4:]) != 1 {
		return nil, syntaxError("not a PSD file")
	}
	bits := be16(data[22:])
	channels := be16(data[12:])
	modeID := be16(data[24:])
	type psdMode struct {
		mode     string
		channels int
	}
	modes := map[[2]int]psdMode{
		{0, 1}: {"1", 1},
		{0, 8}: {"L", 1},
		{1, 8}: {"L", 1},
		{2, 8}: {"P", 1},
		{3, 8}: {"RGB", 3},
		{4, 8}: {"CMYK", 4},
		{7, 8}: {"L", 1},
		{8, 8}: {"L", 1},
		{9, 8}: {"LAB", 3},
	}
	m, ok := modes[[2]int{modeID, bits}]
	if !ok {
		return nil, &Error{Class: "KeyError", Text: "unsupported mode"}
	}
	if m.channels > channels {
		return nil, osError("not enough channels")
	}
	if m.mode == "RGB" && channels == 4 {
		m.mode = "RGBA"
	}
	w := be32(data[18:])
	h := be32(data[14:])
	return &Image{Format: "PSD", Mode: m.mode, Width: w, Height: h}, nil
}

func acceptQOI(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("qoif")) }

func openQOI(data []byte) (*Image, error) {
	if len(data) < 14 || !acceptQOI(data[:4]) {
		return nil, syntaxError("not a QOI file")
	}
	w := be32(data[4:])
	h := be32(data[8:])
	mode := "RGBA"
	if data[12] == 3 {
		mode = "RGB"
	}
	return &Image{Format: "QOI", Mode: mode, Width: w, Height: h}, nil
}

// --- SGI / SPIDER / SUN ---------------------------------------------------

func acceptSGI(prefix []byte) bool { return len(prefix) >= 2 && be16(prefix) == 474 }

func openSGI(data []byte) (*Image, error) {
	if len(data) < 2 || !acceptSGI(data[:2]) {
		return nil, valueError("Not an SGI image file")
	}
	if len(data) < 12 {
		return nil, shortError()
	}
	bpc := data[3]
	dimension := be16(data[4:])
	zsize := be16(data[10:])
	switch [3]int{int(bpc), dimension, zsize} {
	case [3]int{1, 1, 1}, [3]int{1, 2, 1}, [3]int{2, 1, 1}, [3]int{2, 2, 1},
		[3]int{1, 3, 3}, [3]int{2, 3, 3}, [3]int{1, 3, 4}, [3]int{2, 3, 4}:
	default:
		return nil, valueError("Unsupported SGI image mode")
	}
	return identified("SGI")
}

// openSPIDER ports SpiderImagePlugin._open's header validation.
func openSPIDER(data []byte) (*Image, error) {
	if len(data) < 27*4 {
		return nil, syntaxError("not a valid Spider file")
	}
	big := true
	hdrlen := spiderHeader(data, true)
	if hdrlen == 0 {
		big = false
		hdrlen = spiderHeader(data, false)
	}
	if hdrlen == 0 {
		return nil, syntaxError("not a valid Spider file")
	}
	var vals [27]float64
	for i := 0; i < 27; i++ {
		var u uint32
		if big {
			u = binary.BigEndian.Uint32(data[4*i:])
		} else {
			u = binary.LittleEndian.Uint32(data[4*i:])
		}
		vals[i] = float64(math.Float32frombits(u))
	}
	if int(vals[4]) != 1 {
		return nil, syntaxError("not a Spider 2D image")
	}
	istack := int(vals[23])
	imgnumber := int(vals[26])
	if !(istack == 0 && imgnumber == 0) && !(istack > 0 && imgnumber == 0) && !(istack == 0 && imgnumber > 0) {
		return nil, syntaxError("inconsistent stack header values")
	}
	return identified("SPIDER")
}

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
	depth := be32(data[12:])
	fileType := be32(data[20:])
	paletteType := be32(data[24:])
	paletteLength := be32(data[28:])
	switch depth {
	case 1, 4, 8, 24, 32:
	default:
		return nil, syntaxError("Unsupported Mode/Bit Depth")
	}
	if paletteLength != 0 {
		if paletteLength > 1024 {
			return nil, syntaxError("Unsupported Color Palette Length")
		}
		if paletteType != 1 {
			return nil, syntaxError("Unsupported Palette Type")
		}
	}
	if !(fileType == 0 || fileType == 1 || fileType == 2 || fileType == 3 || fileType == 4 || fileType == 5) {
		return nil, syntaxError("Unsupported Sun Raster file type")
	}
	return identified("SUN")
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
	switch imageType {
	case 3, 11:
	case 1, 9:
	case 2, 10:
	default:
		return nil, syntaxError("unknown TGA mode")
	}
	orientation := flags & 0x30
	if orientation != 0x20 && orientation != 0x30 && orientation != 0 && orientation != 0x10 {
		return nil, syntaxError("unknown TGA orientation")
	}
	pos := 18
	if idLen > 0 {
		pos += idLen
	}
	if colormapType != 0 {
		if len(s) < 8 {
			return nil, shortError()
		}
		size := le16(s[5:])
		mapDepth := s[7]
		bytesPer := 0
		switch mapDepth {
		case 16:
			bytesPer = 2
		case 24:
			bytesPer = 3
		case 32:
			bytesPer = 4
		default:
			return nil, syntaxError("unknown TGA map depth")
		}
		pos += bytesPer * size
	}
	if pos > len(data) {
		return nil, shortError()
	}
	return identified("TGA")
}

// --- WEBP / WMF -----------------------------------------------------------

func acceptWEBP(prefix []byte) bool {
	if len(prefix) < 16 || !bytes.HasPrefix(prefix, []byte("RIFF")) || string(prefix[8:12]) != "WEBP" {
		return false
	}
	switch string(prefix[12:16]) {
	case "VP8 ", "VP8X", "VP8L":
		return true
	}
	return false
}

func openWEBP(data []byte) (*Image, error) {
	// The VP8 decoder rejects an incomplete container; a RIFF file whose
	// declared size fits is a WebP image this package does not decode.
	if len(data) < 20 {
		return nil, osError("could not create decoder object")
	}
	riffSize := int(binary.LittleEndian.Uint32(data[4:])) + 8
	if riffSize < 20 || riffSize > len(data) {
		return nil, osError("could not create decoder object")
	}
	return identified("WEBP")
}

func acceptWMF(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("\xd7\xcd\xc6\x9a\x00\x00")) ||
		bytes.HasPrefix(prefix, []byte("\x01\x00\x00\x00"))
}

func openWMF(data []byte) (*Image, error) {
	if len(data) < 6 {
		return nil, shortError()
	}
	s := data[:min(44, len(data))]
	if bytes.HasPrefix(s, []byte("\xd7\xcd\xc6\x9a\x00\x00")) {
		if len(s) < 16 {
			return nil, shortError()
		}
		inch := le16(s[14:])
		if inch == 0 {
			return nil, valueError("Invalid inch")
		}
		if len(s) < 26 {
			return nil, shortError()
		}
		if !bytes.Equal(s[22:26], []byte("\x01\x00\x09\x00")) {
			return nil, syntaxError("Unsupported WMF file format")
		}
		return identified("WMF")
	}
	if bytes.HasPrefix(s, []byte("\x01\x00\x00\x00")) && len(s) >= 44 && bytes.Equal(s[40:44], []byte(" EMF")) {
		return identified("WMF")
	}
	return nil, syntaxError("Unsupported file format")
}

// --- XBM / XPM / XVTHUMB --------------------------------------------------

func acceptXBM(prefix []byte) bool {
	return bytes.HasPrefix(prefix, []byte("#define")) ||
		bytes.HasPrefix(prefix, []byte("\n#define")) ||
		bytes.HasPrefix(prefix, []byte("/*"))
}

func openXBM(data []byte) (*Image, error) {
	width, height := -1, -1
	offset := -1
	pos := 0
	for {
		if pos >= len(data) {
			break
		}
		end := bytes.IndexByte(data[pos:], '\n')
		var line []byte
		if end < 0 {
			line = data[pos:]
			pos = len(data)
		} else {
			line = data[pos : pos+end+1]
			pos += end + 1
		}
		line = bytes.TrimRight(line, " \t\r\n")
		if len(line) == 0 {
			continue
		}
		if bytes.HasPrefix(line, []byte("/*")) {
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
	return identified("XBM")
}

func acceptXPM(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("/* XPM */")) }

var xpmHead = regexp.MustCompile(`^\s*"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*"`)

func openXPM(data []byte) (*Image, error) {
	if !acceptXPM(data[:min(9, len(data))]) {
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
	paletteLength, err := strconv.Atoi(string(m[3]))
	if err != nil {
		return nil, valueError("invalid literal for int()")
	}
	bpp, err := strconv.Atoi(string(m[4]))
	if err != nil {
		return nil, valueError("invalid literal for int()")
	}
	for i := 0; i < paletteLength; i++ {
		line, ok := readLine()
		if !ok {
			return nil, syntaxError("cannot read this XPM file")
		}
		line = bytes.TrimRight(line, " \t\r")
		if len(line) < 2 || len(line) < bpp+1 {
			return nil, valueError("cannot read this XPM file")
		}
		rest := line[bpp+1:]
		if len(rest) >= 2 {
			rest = rest[:len(rest)-2]
		}
		fields := bytes.Fields(rest)
		found := false
		for j := 0; j+1 < len(fields); j += 2 {
			if string(fields[j]) == "c" {
				found = true
				break
			}
		}
		if !found {
			return nil, valueError("cannot read this XPM file")
		}
	}
	return identified("XPM")
}

func acceptXVTHUMB(prefix []byte) bool { return bytes.HasPrefix(prefix, []byte("P7 332")) }

func openXVTHUMB(data []byte) (*Image, error) {
	if !acceptXVTHUMB(data[:min(6, len(data))]) {
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
		pos += end + 1
		return s, true
	}
	if _, ok := readLine(); !ok {
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
	fields := bytes.Fields(line)
	if len(fields) < 2 {
		return nil, &Error{Class: "ValueError", Text: "not enough values to unpack"}
	}
	return identified("XVTHUMB")
}

// --- TIFF -----------------------------------------------------------------

func acceptTIFF(prefix []byte) bool {
	for _, p := range []string{"MM\x00\x2a", "II\x2a\x00", "MM\x2a\x00", "II\x00\x2a", "MM\x00\x2b", "II\x2b\x00"} {
		if bytes.HasPrefix(prefix, []byte(p)) {
			return true
		}
	}
	return false
}

func openTIFF(data []byte) (*Image, error) {
	if len(data) < 8 {
		return nil, shortError()
	}
	var order binary.ByteOrder
	big := false
	switch {
	case data[0] == 'M' && data[1] == 'M':
		order, big = binary.BigEndian, true
	case data[0] == 'I' && data[1] == 'I':
		order = binary.LittleEndian
	default:
		return nil, syntaxError("Invalid TIFF header")
	}
	var version uint16
	if data[2] == 43 {
		version = 43
	} else if big {
		version = order.Uint16(data[2:])
	} else {
		version = order.Uint16(data[2:])
	}
	if version != 42 && version != 43 {
		return nil, syntaxError("Invalid TIFF header")
	}
	headerLen := 8
	if version == 43 {
		headerLen = 16
	}
	if len(data) < headerLen {
		return nil, shortError()
	}
	var first int64
	if version == 43 {
		first = int64(order.Uint64(data[8:]))
	} else {
		first = int64(order.Uint32(data[4:]))
	}
	if first <= 0 || first+2 > int64(len(data)) {
		return nil, syntaxError("Invalid TIFF header")
	}
	countSize := 2
	if version == 43 {
		countSize = 8
	}
	if int64(len(data)) < first+int64(countSize) {
		return nil, shortError()
	}
	var count uint64
	if version == 43 {
		count = order.Uint64(data[first:])
	} else {
		count = uint64(order.Uint16(data[first:]))
	}
	entrySize := 12
	if version == 43 {
		entrySize = 20
	}
	end := first + int64(countSize) + int64(count)*int64(entrySize)
	if count == 0 || end > int64(len(data)) {
		return nil, syntaxError("Invalid TIFF header")
	}
	return identified("TIFF")
}
