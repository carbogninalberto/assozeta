package pilimage

import (
	"encoding/binary"
	"math"
)

// This file ports IcoImagePlugin.IcoFile/frame and CurImagePlugin.

type icoEntry struct {
	width, height int
	nbColor       int
	bpp           int
	size          int
	offset        int
	colorDepth    int
}

// icoEntries is IcoFile.__init__.
func icoEntries(data []byte) ([]icoEntry, error) {
	if len(data) < 6 || !acceptICO(data[:4]) {
		return nil, syntaxError("not an ICO file")
	}
	nb := le16(data[4:])
	var entries []icoEntry
	pos := 6
	for i := 0; i < nb; i++ {
		if pos+16 > len(data) {
			return nil, &Error{Class: "IndexError", Text: "index out of range"}
		}
		s := data[pos : pos+16]
		pos += 16
		w := int(s[0])
		if w == 0 {
			w = 256
		}
		h := int(s[1])
		if h == 0 {
			h = 256
		}
		nbColor := int(s[2])
		bpp := le16(s[6:])
		colorDepth := bpp
		if colorDepth == 0 {
			if nbColor != 0 {
				colorDepth = int(math.Ceil(math.Log2(float64(nbColor))))
			} else {
				colorDepth = 256
			}
		}
		entries = append(entries, icoEntry{
			width: w, height: h, nbColor: nbColor, bpp: bpp,
			size:       int(int32(binary.LittleEndian.Uint32(s[8:]))),
			offset:     int(int32(binary.LittleEndian.Uint32(s[12:]))),
			colorDepth: colorDepth,
		})
	}
	// Stable sorts: by colour depth, then by area (largest first).
	sortStable(entries, func(a, b icoEntry) bool { return a.colorDepth < b.colorDepth })
	sortStable(entries, func(a, b icoEntry) bool { return a.width*a.height > b.width*b.height })
	return entries, nil
}

func sortStable(e []icoEntry, less func(a, b icoEntry) bool) {
	for i := 1; i < len(e); i++ {
		for j := i; j > 0 && less(e[j], e[j-1]); j-- {
			e[j], e[j-1] = e[j-1], e[j]
		}
	}
}

// icoFrame is IcoFile.frame.
func icoFrame(data []byte, e icoEntry) (*Image, error) {
	if e.offset < 0 || e.offset > len(data) {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	if e.offset+8 <= len(data) && isPNGMagic(data[e.offset:]) {
		im, err := openPNG(data[e.offset:])
		if err != nil {
			return nil, err
		}
		if err := im.load(); err != nil {
			return nil, err
		}
		return im, nil
	}
	// XOR + AND mask DIB frame.
	im, err := bmpBitmap(data, e.offset, 0)
	if err != nil {
		return nil, err
	}
	if err := checkDecompressionBomb(im.Width, im.Height); err != nil {
		return nil, err
	}
	realHeight := im.Height / 2
	im.Height = realHeight
	// The plugin builds the mask before converting (loading) the DIB, so a
	// short mask is reported before any truncated bitmap data.
	var alpha []byte
	if e.bpp == 32 {
		o := im.bmp.dataOffset
		need := im.Width * im.Height * 4
		end := o + need
		if end > len(data) {
			end = len(data)
		}
		alphaBytes := make([]byte, 0, im.Width*im.Height)
		for i := o + 3; i < end; i += 4 {
			alphaBytes = append(alphaBytes, data[i])
		}
		if len(alphaBytes) < im.Width*im.Height {
			// Image.frombuffer("L", ...) goes through core.map_buffer.
			return nil, valueError("buffer is not large enough")
		}
		// frombuffer("L", size, alpha_bytes, "raw", ("L", 0, -1))
		mask := &Image{Format: "ICO", Mode: "L", Width: im.Width, Height: im.Height}
		if err := mask.rawTile(alphaBytes, 0, 0, -1, "L"); err != nil {
			return nil, err
		}
		alpha = mask.pix
	} else {
		w := im.Width
		if w%32 > 0 {
			w += 32 - (im.Width % 32)
		}
		total := w * im.Height / 8
		andOffset := e.offset + e.size - total
		var maskData []byte
		if andOffset >= 0 && andOffset < len(data) {
			end := andOffset + total
			if end > len(data) {
				end = len(data)
			}
			maskData = data[andOffset:end]
		}
		mask := &Image{Format: "ICO", Mode: "1", Width: im.Width, Height: im.Height}
		if err := mask.rawTile(maskData, 0, w/8, -1, "1;I"); err != nil {
			return nil, valueError("not enough image data")
		}
		alpha = append(alpha, mask.pix...)

	}
	if err := im.load(); err != nil {
		return nil, err
	}
	rgba, err := im.rgbaWithAlpha(alpha)
	if err != nil {
		return nil, err
	}
	if err := rgba.load(); err != nil {
		return nil, err
	}
	return rgba, nil
}

// rgbaWithAlpha is im.convert("RGBA") followed by putalpha(alpha).
func (im *Image) rgbaWithAlpha(alpha []byte) (*Image, error) {
	out := &Image{Format: im.Format, Mode: "RGBA", Width: im.Width, Height: im.Height}
	out.palette = im.palette
	out.palRGB = im.palRGB
	out.palAlpha = im.palAlpha
	out.hasPal = im.hasPal
	out.pix = make([]byte, im.Width*im.Height*4)
	for i := 0; i < im.Width*im.Height; i++ {
		switch im.Mode {
		case "RGB", "RGBA", "CMYK":
			var r, g, b byte
			if im.Mode == "CMYK" {
				c, m, y, k := im.pix[4*i], im.pix[4*i+1], im.pix[4*i+2], im.pix[4*i+3]
				r = byte((255 - c) * (255 - k) / 255)
				g = byte((255 - m) * (255 - k) / 255)
				b = byte((255 - y) * (255 - k) / 255)
			} else {
				r, g, b = im.pix[3*i], im.pix[3*i+1], im.pix[3*i+2]
			}
			out.pix[4*i], out.pix[4*i+1], out.pix[4*i+2] = r, g, b
		case "P", "PA":
			idx := int(im.pix[i]) & 255
			out.pix[4*i] = im.palRGB[3*idx]
			out.pix[4*i+1] = im.palRGB[3*idx+1]
			out.pix[4*i+2] = im.palRGB[3*idx+2]
			out.pix[4*i+3] = im.palAlpha[idx]
		case "1":
			v := byte(0)
			if im.pix[i] != 0 {
				v = 255
			}
			out.pix[4*i], out.pix[4*i+1], out.pix[4*i+2] = v, v, v
		case "L":
			v := im.pix[i]
			out.pix[4*i], out.pix[4*i+1], out.pix[4*i+2] = v, v, v
		case "LA":
			v := im.pix[2*i]
			out.pix[4*i], out.pix[4*i+1], out.pix[4*i+2] = v, v, v
			out.pix[4*i+3] = im.pix[2*i+1]
		default:
			return nil, valueError("conversion from " + im.Mode + " to RGBA not supported")
		}
		if im.Mode == "LA" && im.pix[2*i+1] != 0 {
			// putalpha replaces the alpha band entirely below.
		}
	}
	for i := 0; i < im.Width*im.Height && i < len(alpha); i++ {
		out.pix[4*i+3] = alpha[i]
	}
	return out, nil
}

func isPNGMagic(b []byte) bool {
	return len(b) >= 8 && string(b[:8]) == "\x89PNG\r\n\x1a\n"
}

// openICO is IcoImageFile._open, which loads the largest frame immediately.
func openICO(data []byte) (*Image, error) {
	entries, err := icoEntries(data)
	if err != nil {
		return nil, err
	}
	if len(entries) == 0 {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	target := [2]int{entries[0].width, entries[0].height}
	idx := 0
	for i, e := range entries {
		if e.width == target[0] && e.height == target[1] {
			idx = i
			break
		}
	}
	im, err := icoFrame(data, entries[idx])
	if err != nil {
		return nil, err
	}
	im.Format = "ICO"
	return im, nil
}

// openCUR is CurImageFile._open.
func openCUR(data []byte) (*Image, error) {
	if len(data) < 6 || !acceptCUR(data[:4]) {
		return nil, syntaxError("not a CUR file")
	}
	var best *icoEntry
	pos := 6
	for i := 0; i < le16(data[4:]); i++ {
		if pos+16 > len(data) {
			return nil, &Error{Class: "IndexError", Text: "index out of range"}
		}
		s := data[pos : pos+16]
		pos += 16
		e := &icoEntry{width: int(s[0]), height: int(s[1]), offset: int(int32(binary.LittleEndian.Uint32(s[12:])))}
		if best == nil {
			best = e
		} else if e.width > best.width && e.height > best.height {
			best = e
		}
	}
	if best == nil {
		return nil, &Error{Class: "TypeError", Text: "No cursors were found"}
	}
	im, err := bmpBitmap(data, best.offset, 0)
	if err != nil {
		return nil, err
	}
	// BmpImagePlugin._bitmap: a 32-bit RAW bitmap opened at header offset 22
	// (a .cur) is read as BGRA/RGBA.
	if im.Mode == "RGB" && im.bmp != nil && im.bmp.rawmode == "BGRX" && best.offset == 22 {
		im.Mode = "RGBA"
		im.bmp.rawmode = "BGRA"
	}
	masked := im.Mode == "1" || im.Mode == "L"
	rawMode := im.Mode
	realHeight := im.Height / 2
	im.Height = realHeight
	im.Format = "CUR"
	if masked {
		im.Mode = "LA"
		im.loadFn = func() error { return im.loadCURMasked(rawMode) }
	} else {
		im.loadFn = func() error { return im.loadCURPlain() }
	}
	return im, nil
}

// loadCURPlain decodes the non-masked (colour) cursor frame, height halved.
func (im *Image) loadCURPlain() error {
	if err := im.rawTile(im.data, im.bmp.dataOffset, im.bmp.stride, im.bmp.direction, im.bmp.rawmode); err != nil {
		return err
	}
	return nil
}

// loadCURMasked is CurImageFile.load_prepare/load_end for 1-bit and 8-bit
// cursors: XOR over AND with the mask inverted into the alpha band.
func (im *Image) loadCURMasked(rawmode string) error {
	full := &Image{Format: "CUR", Mode: rawmode, Width: im.Width, Height: im.Height * 2}
	if err := full.rawTile(im.data, im.bmp.dataOffset, im.bmp.stride, im.bmp.direction, im.bmp.rawmode); err != nil {
		return err
	}
	n := im.Width * im.Height
	im.pix = make([]byte, 2*n)
	for y := 0; y < im.Height; y++ {
		andRow := (y) * im.Width
		xorRow := (im.Height + y) * im.Width
		for x := 0; x < im.Width; x++ {
			var l, alpha byte
			if full.Mode == "1" {
				if full.pix[xorRow+x] != 0 {
					l = 255
				}
				if full.pix[andRow+x] == 0 {
					alpha = 255
				}
			} else {
				l = full.pix[xorRow+x]
				if full.pix[andRow+x] == 0 {
					alpha = 255
				}
			}
			im.pix[2*(y*im.Width+x)] = l
			im.pix[2*(y*im.Width+x)+1] = alpha
		}
	}
	im.Mode = "LA"
	return nil
}
