package pilimage

import (
	"encoding/binary"
	"strconv"
)

// This file ports the load paths of the small Pillow plugins that either use
// the raw decoder or a simple Python decoder.

// --- QOI ------------------------------------------------------------------

// loadQOI is QoiImageFile's "qoi" py decoder.
func (im *Image) loadQOI() error {
	bands := 3
	if im.Mode == "RGBA" {
		bands = 4
	}
	dest := im.Width * im.Height * bands
	var out []byte
	px := [4]byte{0, 0, 0, 255}
	var seen [64][4]byte
	var used [64]bool
	pos := 14
	data := im.data
	for len(out) < dest {
		if pos >= len(data) {
			break
		}
		b := data[pos]
		pos++
		var value [4]byte
		switch {
		case b == 0xFE:
			if pos+3 > len(data) {
				pos = len(data)
				goto done
			}
			value = [4]byte{data[pos], data[pos+1], data[pos+2], px[3]}
			pos += 3
		case b == 0xFF:
			if pos+4 > len(data) {
				pos = len(data)
				goto done
			}
			value = [4]byte{data[pos], data[pos+1], data[pos+2], data[pos+3]}
			pos += 4
		default:
			switch b >> 6 {
			case 0:
				idx := int(b & 0x3F)
				if used[idx] {
					value = seen[idx]
				}
			case 1:
				value = [4]byte{
					byte((int(px[0]) + int((b&0x30)>>4) - 2) % 256),
					byte((int(px[1]) + int((b&0x0C)>>2) - 2) % 256),
					byte((int(px[2]) + int(b&0x03) - 2) % 256),
					px[3],
				}
			case 2:
				if pos >= len(data) {
					goto done
				}
				b2 := data[pos]
				pos++
				dg := int(b&0x3F) - 32
				dr := int((b2&0xF0)>>4) - 8
				db := int(b2&0x0F) - 8
				value = [4]byte{
					byte((int(px[0]) + dg + dr) % 256),
					byte((int(px[1]) + dg) % 256),
					byte((int(px[2]) + dg + db) % 256),
					px[3],
				}
			case 3:
				run := int(b&0x3F) + 1
				for i := 0; i < run; i++ {
					if bands == 3 {
						out = append(out, px[0], px[1], px[2])
					} else {
						out = append(out, px[:]...)
					}
				}
				continue
			}
		}
		px = value
		hash := (int(value[0])*3 + int(value[1])*5 + int(value[2])*7 + int(value[3])*11) % 64
		seen[hash] = value
		used[hash] = true
		if bands == 3 {
			out = append(out, value[0], value[1], value[2])
		} else {
			out = append(out, value[:]...)
		}
	}
done:
	if len(out) < dest {
		return valueError("not enough image data")
	}
	im.pix = append([]byte{}, out[:dest]...)
	return nil
}

// --- XBM ------------------------------------------------------------------

// loadXBM is XbmImageFile's "xbm" C decoder: hexadecimal bytes, least
// significant bit first, 1 = white.
func (im *Image) loadXBM(offset int) error {
	xsize, ysize := im.Width, im.Height
	rowBytes := (xsize + 7) / 8
	data := im.data
	pos := offset
	im.pix = make([]byte, xsize*ysize)
	for y := 0; y < ysize; y++ {
		for i := 0; i < rowBytes; i++ {
			// Skip separators (whitespace, commas).
			for pos < len(data) && (data[pos] == ' ' || data[pos] == '\t' ||
				data[pos] == '\r' || data[pos] == '\n' || data[pos] == ',') {
				pos++
			}
			if pos+2 > len(data) || data[pos] != '0' || (data[pos+1] != 'x' && data[pos+1] != 'X') {
				return truncatedError(0)
			}
			pos += 2
			v := 0
			n := 0
			for pos < len(data) && n < 2 {
				c := data[pos]
				var d int
				switch {
				case c >= '0' && c <= '9':
					d = int(c - '0')
				case c >= 'a' && c <= 'f':
					d = int(c-'a') + 10
				case c >= 'A' && c <= 'F':
					d = int(c-'A') + 10
				default:
					d = -1
				}
				if d < 0 {
					break
				}
				v = v<<4 | d
				n++
				pos++
			}
			if n == 0 {
				return truncatedError(0)
			}
			for bit := 0; bit < 8; bit++ {
				x := i*8 + bit
				if x >= xsize {
					break
				}
				if v&(1<<uint(bit)) != 0 {
					im.pix[y*xsize+x] = 255
				}
			}
		}
	}
	return nil
}

// --- XPM ------------------------------------------------------------------

// loadXPM is XpmImageFile's "xpm" py decoder.
func (im *Image) loadXPM(offset, bpp int, palette map[string][]byte, keys []string, mode string) error {
	dest := im.Width * im.Height
	if mode == "RGB" || mode == "RGBA" {
		dest *= len(mode)
	}
	var out []byte
	pos := offset
	data := im.data
	pixelHeader := false
	readLine := func() ([]byte, bool) {
		if pos >= len(data) {
			return nil, false
		}
		end := pos
		for end < len(data) && data[end] != '\n' {
			end++
		}
		line := data[pos:end]
		pos = end
		if pos < len(data) {
			pos++
		}
		return line, true
	}
	for len(out) < dest {
		line, ok := readLine()
		if !ok {
			break
		}
		if !pixelHeader && string(bytesTrimRightWhitespace(line)) == "/* pixels */" {
			pixelHeader = true
			continue
		}
		// b'"'.join(line.split(b'"')[1:-1])
		parts := splitByte(line, '"')
		if len(parts) >= 2 {
			parts = parts[1 : len(parts)-1]
		} else {
			parts = nil
		}
		line = joinByte(parts, '"')
		for i := 0; i < len(line); i += bpp {
			if i+bpp > len(line) {
				break
			}
			key := string(line[i : i+bpp])
			if mode == "RGB" || mode == "RGBA" {
				col, ok := palette[key]
				if !ok {
					return valueError("unknown color")
				}
				out = append(out, col...)
			} else {
				idx := -1
				for j, k := range keys {
					if k == key {
						idx = j
						break
					}
				}
				if idx < 0 {
					return valueError("unknown color")
				}
				out = append(out, byte(idx))
			}
		}
	}
	if len(out) < dest {
		return valueError("not enough image data")
	}
	im.pix = append([]byte{}, out[:dest]...)
	return nil
}

func bytesTrimRightWhitespace(b []byte) []byte {
	end := len(b)
	for end > 0 {
		switch b[end-1] {
		case ' ', '\t', '\r', '\n', '\v', '\f':
			end--
		default:
			return b[:end]
		}
	}
	return b[:end]
}

func splitByte(b []byte, sep byte) [][]byte {
	var out [][]byte
	start := 0
	for i := 0; i < len(b); i++ {
		if b[i] == sep {
			out = append(out, b[start:i])
			start = i + 1
		}
	}
	out = append(out, b[start:])
	return out
}

func joinByte(parts [][]byte, sep byte) []byte {
	var out []byte
	for i, p := range parts {
		if i > 0 {
			out = append(out, sep)
		}
		out = append(out, p...)
	}
	return out
}

// --- GBR ------------------------------------------------------------------

// loadGBR is GbrImageFile.load: an uncompressed block of w*h*bands bytes at
// the current position.
func (im *Image) loadGBR(offset int) error {
	n := im.Width * im.Height * im.samplesPerPixel()
	if offset+n > len(im.data) {
		return valueError("not enough image data")
	}
	im.pix = append([]byte{}, im.data[offset:offset+n]...)
	return nil
}

// --- FTEX -----------------------------------------------------------------

// loadFTEXUncompressed is FtexImageFile's raw "RGB" tile over its mipmap data.
func (im *Image) loadFTEXUncompressed(offset, size int) error {
	return im.rawTile(im.ftmip(offset, size), 0, 0, 1, "RGB")
}

// loadFTEXDXT1 is FtexImageFile's "bcn" tile with DXT1 (n = 1).
func (im *Image) loadFTEXDXT1(offset, size int) error {
	return im.loadBCNData(im.ftmip(offset, size), 0, 1, im.Width, im.Height)
}

// ftmip is the BytesIO(mipmap data) FtexImageFile seeks to.
func (im *Image) ftmip(offset, size int) []byte {
	if offset > len(im.data) {
		return nil
	}
	end := offset + size
	if end > len(im.data) {
		end = len(im.data)
	}
	return im.data[offset:end]
}

// --- MSP ------------------------------------------------------------------

// loadMSPRaw is MspImageFile's raw "1" tile for version 1 files.
func (im *Image) loadMSPRaw(offset int) error {
	return im.rawTile(im.data, offset, 0, 1, "1")
}

// loadMSP is MspImageFile's "MSP" py decoder for version 2 RLE files.
func (im *Image) loadMSP(offset int) error {
	xsize, ysize := im.Width, im.Height
	data := im.data
	if offset+ysize*2 > len(data) {
		return osError("Truncated MSP file in row map")
	}
	rowmap := make([]int, ysize)
	for y := 0; y < ysize; y++ {
		rowmap[y] = le16(data[offset+2*y:])
	}
	pos := offset + ysize*2
	blankLine := make([]byte, (xsize+7)/8)
	for i := range blankLine {
		blankLine[i] = 0xFF
	}
	var out []byte
	for y, rowlen := range rowmap {
		if rowlen == 0 {
			out = append(out, blankLine...)
			continue
		}
		if pos+rowlen > len(data) {
			return osError("Truncated MSP file, expected " + strconv.Itoa(rowlen) + " bytes on row " + strconv.Itoa(y))
		}
		row := data[pos : pos+rowlen]
		pos += rowlen
		idx := 0
		var rowData []byte
		for idx < rowlen {
			runtype := row[idx]
			idx++
			if runtype == 0 {
				if idx+2 > len(row) {
					return osError("Corrupted MSP file in row " + strconv.Itoa(y))
				}
				runcount := int(row[idx])
				runval := row[idx+1]
				idx += 2
				if remain := xsize - len(rowData); runcount > remain {
					runcount = remain
				}
				for i := 0; i < runcount; i++ {
					rowData = append(rowData, runval)
				}
			} else {
				runcount := int(runtype)
				if remain := xsize - len(rowData); runcount > remain {
					runcount = remain
				}
				end := idx + runcount
				if end > len(row) {
					rowData = append(rowData, row[idx:]...)
				} else {
					rowData = append(rowData, row[idx:end]...)
				}
				idx += runcount
			}
		}
		out = append(out, rowData...)
	}
	need := ((xsize + 7) / 8) * ysize
	if len(out) < need {
		return valueError("not enough image data")
	}
	// set_as_raw with mode "1" unpacks the bit rows.
	im.pix = make([]byte, xsize*ysize)
	for y := 0; y < ysize; y++ {
		row := out[y*((xsize+7)/8):]
		for x := 0; x < xsize; x++ {
			if row[x>>3]&(1<<uint(7-x&7)) != 0 {
				im.pix[y*xsize+x] = 255
			}
		}
	}
	return nil
}

// --- MCIDAS ---------------------------------------------------------------

// loadMCIDAS is McIdasImageFile's raw tile.
func (im *Image) loadMCIDAS(offset, stride int, rawmode string) error {
	return im.rawTile(im.data, offset, stride, 1, rawmode)
}

// --- PIXAR ----------------------------------------------------------------

// loadPIXAR is PixarImageFile's raw "RGB" tile at offset 1024.
func (im *Image) loadPIXAR() error {
	return im.rawTile(im.data, 1024, 0, 1, "RGB")
}

// --- IMT ------------------------------------------------------------------

// loadIMT is ImtImageFile's raw tile at the recorded offset.
func (im *Image) loadIMT(offset int) error {
	return im.rawTile(im.data, offset, 0, 1, "L")
}

// le16 helper is in others.go; keep the import of encoding/binary used by
// future loaders in this file.
var _ = binary.LittleEndian
