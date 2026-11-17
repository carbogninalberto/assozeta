package pilimage

import (
	"bytes"
	"compress/gzip"
	"io"
	"strconv"
	"strings"
)

// This file ports FitsImagePlugin: the header walk, the BITPIX mode choice and
// the fits_gzip py decoder.

type fitsTile struct {
	gzip   bool
	offset int
	bits   int
	xsize  int
	ysize  int
}

func fitsCard(header []byte) (keyword, value []byte) {
	if len(header) > 8 {
		keyword = bytes.TrimSpace(header[:8])
		value = bytes.TrimSpace(bytes.SplitN(header[8:], []byte("/"), 2)[0])
		value = bytes.TrimSpace(bytes.TrimPrefix(value, []byte("=")))
		return keyword, value
	}
	return bytes.TrimSpace(header), nil
}

func fitsInt(v []byte) (int, error) {
	n, err := strconv.Atoi(string(v))
	if err != nil {
		return 0, valueError("invalid literal for int() with base 10: " + pyReprBytes(v))
	}
	return n, nil
}

// loadFITS is the raw or fits_gzip tile.
func (im *Image) loadFITS(t fitsTile) error {
	if !t.gzip {
		return im.loadFITSRaw(t)
	}
	return im.loadFITSGzip(t)
}

func (im *Image) loadFITSRaw(t fitsTile) error {
	return im.rawTile(im.data, t.offset, 0, -1, im.fitsRawmode(t.bits))
}

// fitsRawmode is the rawmode _parse_headers selects for BITPIX.
func (im *Image) fitsRawmode(bits int) string {
	switch bits {
	case 8:
		return "L"
	case 16:
		return "I;16B"
	case 32:
		return "I;32B"
	case -32:
		return "F;32BF"
	case -64:
		return "F;64BF"
	}
	return "L"
}

// loadFITSGzip is FitsGzipDecoder.decode.
func (im *Image) loadFITSGzip(t fitsTile) error {
	if t.offset < 0 || t.offset > len(im.data) {
		return valueError("not enough image data")
	}
	tail := im.data[t.offset:]
	if len(tail) == 0 {
		return valueError("not enough image data")
	}
	zr, err := gzip.NewReader(bytes.NewReader(tail))
	if err != nil {
		if len(tail) < 2 || tail[0] != 0x1f || tail[1] != 0x8b {
			n := min(2, len(tail))
			return &Error{Class: "BadGzipFile", Text: "Not a gzipped file (" + pyReprBytes(tail[:n]) + ")"}
		}
		return &Error{Class: "EOFError", Text: "Compressed file ended before the end-of-stream marker was reached"}
	}
	want := t.xsize * t.ysize * 4
	value := make([]byte, 0, want)
	buf := make([]byte, 4096)
	for len(value) < want {
		n, err := zr.Read(buf)
		value = append(value, buf[:n]...)
		if err != nil {
			if err == io.EOF {
				break
			}
			if err == io.ErrUnexpectedEOF {
				return &Error{Class: "EOFError", Text: "Compressed file ended before the end-of-stream marker was reached"}
			}
			return &Error{Class: "BadGzipFile", Text: "CRC check failed"}
		}
	}
	if len(value) > want {
		value = value[:want]
	}
	nob := t.bits
	if nob < 0 {
		nob = -nob
	}
	nob /= 8
	out := make([]byte, 0, want)
	offset := 0
	for y := 0; y < t.ysize; y++ {
		row := make([]byte, 0, t.xsize*nob)
		for x := 0; x < t.xsize; x++ {
			start := offset + 3
			stop := offset + (4 - nob) - 1
			for i := start; i > stop && i >= 0 && i < len(value); i-- {
				row = append(row, value[i])
			}
			offset += 4
		}
		out = append(row, out...)
	}
	need := im.Width * im.Height * im.samplesPerPixel()
	if len(out) < need {
		return valueError("not enough image data")
	}
	im.pix = out[:need]
	return nil
}

// openFITS is FitsImageFile._open.
func openFITS(data []byte) (*Image, error) {
	if !bytes.HasPrefix(data, []byte("SIMPLE")) {
		return nil, syntaxError("Not a FITS file")
	}
	headers := map[string][]byte{}
	headerInProgress := false
	decoderName := ""
	offset := 0
	var args fitsTile
	var size [2]int
	pos := 0
	for {
		if pos >= len(data) {
			return nil, osError("Truncated FITS file")
		}
		end := min(pos+80, len(data))
		header := data[pos:end]
		pos = end
		keyword, value := fitsCard(header)
		k := string(keyword)
		switch {
		case k == "SIMPLE" || k == "XTENSION":
			headerInProgress = true
		case len(headers) > 0 && !headerInProgress:
			goto dataUnit
		case k == "END":
			pos = (pos + 2879) / 2880 * 2880
			if pos > len(data) {
				pos = len(data)
			}
			if decoderName == "" {
				var err error
				decoderName, offset, args, size, err = fitsParseHeaders(headers)
				if err != nil {
					return nil, err
				}
			}
			headerInProgress = false
			continue
		}
		if decoderName != "" {
			continue
		}
		if len(headers) == 0 && (!strings.HasPrefix(k, "SIMPLE") || string(value) != "T") {
			return nil, syntaxError("Not a FITS file")
		}
		headers[k] = value
	}
dataUnit:
	if decoderName == "" {
		return nil, valueError("No image data")
	}
	offset += pos - 80
	args.offset = offset
	args.xsize = size[0]
	args.ysize = size[1]
	im := newImage(data, "FITS", "", size[0], size[1])
	switch decoderName {
	case "raw", "fits_gzip":
		mode := ""
		switch args.bits {
		case 8:
			mode = "L"
		case 16:
			mode = "I;16"
		case 32:
			mode = "I"
		case -32:
			mode = "F"
		case -64:
			mode = "F"
		}
		im.Mode = mode
	default:
		return nil, valueError("No image data")
	}
	im.loadFn = func() error { return im.loadFITS(args) }
	return im, nil
}

// fitsParseHeaders is FitsImageFile._parse_headers.
func fitsParseHeaders(headers map[string][]byte) (string, int, fitsTile, [2]int, error) {
	prefix := ""
	decoderName := "raw"
	offset := 0
	var t fitsTile
	if string(headers["XTENSION"]) == "'BINTABLE'" && string(headers["ZIMAGE"]) == "T" &&
		string(headers["ZCMPTYPE"]) == "'GZIP_1  '" {
		noPrefixSize, err := fitsGetSize(headers, "")
		if err != nil {
			return "", 0, t, [2]int{}, err
		}
		if noPrefixSize == nil {
			noPrefixSize = &[2]int{0, 0}
		}
		numberOfBits, err := fitsInt(headers["BITPIX"])
		if err != nil {
			return "", 0, t, [2]int{}, err
		}
		offset = noPrefixSize[0] * noPrefixSize[1] * (numberOfBits / 8)
		prefix = "Z"
		decoderName = "fits_gzip"
	}
	size, err := fitsGetSize(headers, prefix)
	if err != nil {
		return "", 0, t, [2]int{}, err
	}
	if size == nil {
		return "", 0, t, [2]int{}, nil
	}
	numberOfBits, err := fitsInt(headers[prefix+"BITPIX"])
	if err != nil {
		return "", 0, t, [2]int{}, err
	}
	switch numberOfBits {
	case 8, 16, 32, -32, -64:
	default:
		return "", 0, t, [2]int{}, osError("Unsupported number of bits")
	}
	t = fitsTile{gzip: decoderName == "fits_gzip", bits: numberOfBits}
	return decoderName, offset, t, *size, nil
}

// fitsGetSize is FitsImageFile._get_size.
func fitsGetSize(headers map[string][]byte, prefix string) (*[2]int, error) {
	raw, ok := headers[prefix+"NAXIS"]
	if !ok {
		return nil, &Error{Class: "KeyError", Text: pyReprStr(prefix + "NAXIS")}
	}
	naxis, err := fitsInt(raw)
	if err != nil {
		return nil, err
	}
	if naxis == 0 {
		return nil, nil
	}
	if naxis == 1 {
		n1, err := fitsInt(headers[prefix+"NAXIS1"])
		if err != nil {
			return nil, err
		}
		return &[2]int{1, n1}, nil
	}
	n1, err := fitsInt(headers[prefix+"NAXIS1"])
	if err != nil {
		return nil, err
	}
	n2, err := fitsInt(headers[prefix+"NAXIS2"])
	if err != nil {
		return nil, err
	}
	return &[2]int{n1, n2}, nil
}
