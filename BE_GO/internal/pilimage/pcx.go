package pilimage

import "encoding/binary"

// This file ports PcxImagePlugin, its "pcx" decoder and DcxImagePlugin.

var acceptPCXcolor = map[byte]bool{0: true, 2: true, 3: true, 5: true}

// acceptPCX is PcxImagePlugin._accept.
func acceptPCX(prefix []byte) bool {
	return len(prefix) >= 2 && prefix[0] == 10 && acceptPCXcolor[prefix[1]]
}

const dcxMagic = 0x3ADE68B1

// acceptDCX is DcxImagePlugin._accept.
func acceptDCX(prefix []byte) bool {
	return len(prefix) >= 4 && binary.LittleEndian.Uint32(prefix) == dcxMagic
}

// loadPCX is ImagingPcxDecode.
func (im *Image) loadPCX(offset, planesStride int, rawmode string) error {
	data := im.data
	bytesRow := planesStride
	bits := rawPCXBits(rawmode)
	if (im.Width*bits+7)/8 > bytesRow {
		return decoderError(-1)
	}
	im.ensurePix()
	buf := make([]byte, bytesRow)
	buf2 := make([]byte, bytesRow)
	x := 0
	row := 0
	pos := offset
	overrun := false
	for row < im.Height {
		if pos >= len(data) {
			return truncatedError(0)
		}
		if data[pos]&0xC0 == 0xC0 {
			if pos+1 >= len(data) {
				return truncatedError(0)
			}
			n := int(data[pos] & 0x3F)
			v := data[pos+1]
			pos += 2
			for n > 0 {
				if x >= bytesRow {
					overrun = true
					break
				}
				buf[x] = v
				x++
				n--
			}
		} else {
			buf[x] = data[pos]
			x++
			pos++
		}
		if x >= bytesRow {
			// Compact padded planes, then unpack the row.
			rowBytes := bytesRow
			if bits == 2 || bits == 4 {
				rowBytes = (im.Width + 7) / 8
			} else {
				rowBytes = im.Width
			}
			bands := 1
			if bits == 2 || bits == 4 {
				bands = bits
			} else {
				bands = im.samplesPerPixel()
			}
			stride := bytesRow / bands
			copy(buf2, buf)
			if stride > rowBytes {
				for b := 1; b < bands; b++ {
					copy(buf2[b*rowBytes:], buf[b*stride:b*stride+rowBytes])
				}
			}
			if err := im.rawRow(buf2, 0, row, im.Width, rawmode, baseRawmode(rawmode), im.samplesPerPixel(), -1); err != nil {
				return err
			}
			x = 0
			row++
		}
	}
	if overrun {
		return decoderError(-1)
	}
	return nil
}

// rawPCXBits is the state->bits value Pillow derives from the PCX rawmode.
func rawPCXBits(rawmode string) int {
	switch rawmode {
	case "1":
		return 1
	case "P;2L":
		return 2
	case "P;4L":
		return 4
	case "RGB;L":
		return 24
	}
	return 8
}

// openPCXFile is PcxImageFile._open.
func openPCXFile(data []byte, base int, requireAccept bool) (*Image, error) {
	if base+68 > len(data) {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	s := data[base : base+68]
	if !acceptPCX(s) {
		return nil, syntaxError("not a PCX file")
	}
	bbox := [4]int{le16(s[4:]), le16(s[6:]), le16(s[8:]) + 1, le16(s[10:]) + 1}
	if bbox[2] <= bbox[0] || bbox[3] <= bbox[1] {
		return nil, syntaxError("bad PCX image size")
	}
	offset := base + 68 + 60
	version := s[1]
	bits := int(s[3])
	planes := int(s[65])
	providedStride := le16(s[66:68])
	w, h := bbox[2]-bbox[0], bbox[3]-bbox[1]
	var mode, rawmode string
	var palette []byte
	switch {
	case bits == 1 && planes == 1:
		mode, rawmode = "1", "1"
	case bits == 1 && (planes == 2 || planes == 4):
		mode = "P"
		rawmode = "P;" + itoa(int(planes)) + "L"
		palette = append([]byte{}, s[16:64]...)
	case version == 5 && bits == 8 && planes == 1:
		mode, rawmode = "L", "L"
		start := len(data) - 769
		if start < 0 {
			start = 0
		}
		end := start + 769
		if end > len(data) {
			end = len(data)
		}
		tail := data[start:end]
		if len(tail) == 769 && tail[0] == 12 {
			linear := true
			for i := 0; i < 256; i++ {
				if tail[i*3+1] != byte(i) || tail[i*3+2] != byte(i) || tail[i*3+3] != byte(i) {
					linear = false
					break
				}
			}
			if !linear {
				mode, rawmode = "P", "P"
				palette = append([]byte{}, tail[1:]...)
			}
		}
	case version == 5 && bits == 8 && planes == 3:
		mode, rawmode = "RGB", "RGB;L"
	default:
		return nil, osError("unknown PCX mode")
	}
	stride := (w*bits + 7) / 8
	if providedStride != stride {
		stride += stride % 2
	}
	im := newImage(data, "PCX", mode, w, h)
	if palette != nil {
		im.sunPalette(palette, "RGB")
	}
	im.loadFn = func() error { return im.loadPCX(offset, planes*stride, rawmode) }
	return im, nil
}

func openPCX(data []byte) (*Image, error) {
	return openPCXFile(data, 0, true)
}

func openDCX(data []byte) (*Image, error) {
	if len(data) < 4 || !acceptDCX(data[:4]) {
		return nil, syntaxError("not a DCX file")
	}
	pos := 4
	var offsets []int
	for i := 0; i < 1024; i++ {
		if pos+4 > len(data) {
			return nil, &Error{Class: "IndexError", Text: "index out of range"}
		}
		v := int(int32(binary.LittleEndian.Uint32(data[pos:])))
		pos += 4
		if v == 0 {
			break
		}
		offsets = append(offsets, v)
	}
	if len(offsets) == 0 || offsets[0] < 0 || offsets[0] >= len(data) {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	im, err := openPCXFile(data, offsets[0], false)
	if err != nil {
		return nil, err
	}
	im.Format = "DCX"
	return im, nil
}
