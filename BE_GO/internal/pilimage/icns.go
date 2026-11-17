package pilimage

import "encoding/binary"

// This file ports IcnsImagePlugin: the size table, the raw/RLE readers and the
// PNG/JPEG 2000 sub-images.

type icnsFmt struct {
	sig    string
	reader int
}

type icnsSize struct {
	w, h, scale int
	fmts        []icnsFmt
}

var icnsSizes = []icnsSize{
	{512, 512, 2, []icnsFmt{{"ic10", 0}}},
	{512, 512, 1, []icnsFmt{{"ic09", 0}}},
	{256, 256, 2, []icnsFmt{{"ic14", 0}}},
	{256, 256, 1, []icnsFmt{{"ic08", 0}}},
	{128, 128, 2, []icnsFmt{{"ic13", 0}}},
	{128, 128, 1, []icnsFmt{{"ic07", 0}, {"it32", 1}, {"t8mk", 2}}},
	{64, 64, 1, []icnsFmt{{"icp6", 0}}},
	{32, 32, 2, []icnsFmt{{"ic12", 0}}},
	{48, 48, 1, []icnsFmt{{"ih32", 3}, {"h8mk", 2}}},
	{32, 32, 1, []icnsFmt{{"icp5", 0}, {"il32", 3}, {"l8mk", 2}}},
	{16, 16, 2, []icnsFmt{{"ic11", 0}}},
	{16, 16, 1, []icnsFmt{{"icp4", 0}, {"is32", 3}, {"s8mk", 2}}},
}

type icnsFile struct {
	data   []byte
	blocks map[string][2]int
}

// newIcnsFile is IcnsFile.__init__.
func newIcnsFile(data []byte) (*icnsFile, error) {
	if len(data) < 8 {
		return nil, shortError()
	}
	if string(data[:4]) != "icns" {
		return nil, syntaxError("not an icns file")
	}
	filesize := int(binary.BigEndian.Uint32(data[4:]))
	f := &icnsFile{data: data, blocks: map[string][2]int{}}
	i := 8
	pos := 8
	for i < filesize {
		if pos+8 > len(data) {
			return nil, shortError()
		}
		sig := string(data[pos : pos+4])
		blockSize := int(int32(binary.BigEndian.Uint32(data[pos+4:])))
		pos += 8
		if blockSize <= 0 {
			return nil, syntaxError("invalid block header")
		}
		i += 8
		blockSize -= 8
		f.blocks[sig] = [2]int{pos, blockSize}
		pos += blockSize
		if pos > len(data) {
			pos = len(data)
		}
		i += blockSize
	}
	return f, nil
}

// itersizes is IcnsFile.itersizes.
func (f *icnsFile) itersizes() []icnsSize {
	var out []icnsSize
	for _, s := range icnsSizes {
		for _, fmt := range s.fmts {
			if _, ok := f.blocks[fmt.sig]; ok {
				out = append(out, s)
				break
			}
		}
	}
	return out
}

// bestsize is IcnsFile.bestsize.
func (f *icnsFile) bestsize() (icnsSize, error) {
	sizes := f.itersizes()
	if len(sizes) == 0 {
		return icnsSize{}, syntaxError("No 32bit icon resources found")
	}
	best := sizes[0]
	for _, s := range sizes[1:] {
		if s.w > best.w || (s.w == best.w && (s.h > best.h || (s.h == best.h && s.scale > best.scale))) {
			best = s
		}
	}
	return best, nil
}

// icnsReadPNGOrJPEG2000 is read_png_or_jpeg2000.
func icnsReadPNGOrJPEG2000(data []byte, start, length int, size [3]int) (*Image, error) {
	if start < 0 || start > len(data) {
		return nil, &Error{Class: "IndexError", Text: "index out of range"}
	}
	sig := data[start:min(start+12, len(data))]
	switch {
	case isPNGMagic(sig):
		im, err := openPNG(data[start:])
		if err != nil {
			return nil, err
		}
		if err := checkDecompressionBomb(im.Width, im.Height); err != nil {
			return nil, err
		}
		return im, nil
	case len(sig) >= 4 && (string(sig[:4]) == "\xff\x4f\xff\x51" || string(sig[:4]) == "\x0d\x0a\x87\x0a") ||
		len(sig) == 12 && string(sig) == "\x00\x00\x00\x0cjP  \x0d\x0a\x87\x0a":
		// Divergence X19: no JPEG 2000 codec in this build.
		return nil, valueError("Unsupported icon subimage format (rebuild PIL with JPEG 2000 support to fix this)")
	default:
		return nil, valueError("Unsupported icon subimage format")
	}
}

// icnsRead32 is read_32/read_32t: an RGB band resource, uncompressed or RLE.
func icnsRead32(data []byte, start, length int, size [3]int, t bool) (*Image, error) {
	if t {
		if start+4 > len(data) || string(data[start:start+4]) != "\x00\x00\x00\x00" {
			return nil, syntaxError("Unknown signature, expecting 0x00000000")
		}
		start += 4
		length -= 4
	}
	pixelW, pixelH := size[0]*size[2], size[1]*size[2]
	sizesq := pixelW * pixelH
	im := &Image{Format: "ICNS", Mode: "RGB", Width: pixelW, Height: pixelH}
	if length == sizesq*3 {
		if start+sizesq*3 > len(data) {
			return nil, valueError("buffer is not large enough")
		}
		im.pix = append([]byte{}, data[start:start+sizesq*3]...)
		return im, nil
	}
	im.pix = make([]byte, sizesq*3)
	pos := start
	for band := 0; band < 3; band++ {
		var bandData []byte
		bytesleft := sizesq
		for bytesleft > 0 {
			if pos >= len(data) {
				break
			}
			b := data[pos]
			pos++
			if b&0x80 != 0 {
				blocksize := int(b) - 125
				if pos >= len(data) {
					break
				}
				v := data[pos]
				pos++
				for i := 0; i < blocksize; i++ {
					bandData = append(bandData, v)
				}
				bytesleft -= blocksize
				if bytesleft <= 0 {
					break
				}
			} else {
				blocksize := int(b) + 1
				if pos+blocksize > len(data) {
					bandData = append(bandData, data[pos:]...)
					pos = len(data)
				} else {
					bandData = append(bandData, data[pos:pos+blocksize]...)
					pos += blocksize
				}
				bytesleft -= blocksize
				if bytesleft <= 0 {
					break
				}
			}
		}
		if bytesleft != 0 {
			return nil, syntaxError("Error reading channel [" + pyReprInt(bytesleft) + " left]")
		}
		if len(bandData) < sizesq {
			return nil, valueError("buffer is not large enough")
		}
		for i := 0; i < sizesq; i++ {
			im.pix[3*i+band] = bandData[i]
		}
	}
	return im, nil
}

// icnsReadMK is read_mk: the uncompressed alpha band.
func icnsReadMK(data []byte, start int, size [3]int) (*Image, error) {
	pixelW, pixelH := size[0]*size[2], size[1]*size[2]
	n := pixelW * pixelH
	im := &Image{Format: "ICNS", Mode: "L", Width: pixelW, Height: pixelH}
	if start < 0 || start+n > len(data) {
		return nil, valueError("buffer is not large enough")
	}
	im.pix = append([]byte{}, data[start:start+n]...)
	return im, nil
}

func pyReprInt(v int) string { return itoa(v) }

// openICNS is IcnsImageFile._open.
func openICNS(data []byte) (*Image, error) {
	f, err := newIcnsFile(data)
	if err != nil {
		return nil, err
	}
	best, err := f.bestsize()
	if err != nil {
		return nil, err
	}
	im := &Image{Format: "ICNS", Mode: "RGBA", Width: best.w * best.scale, Height: best.h * best.scale}
	im.loadFn = func() error { return im.loadICNS(f, best) }
	return im, nil
}

// loadICNS is IcnsImageFile.load: decode the best size's channels and return
// the RGB(A) image getimage builds.
func (im *Image) loadICNS(f *icnsFile, size icnsSize) error {
	var rgb *Image
	var alpha *Image
	var rgba *Image
	for _, fmt := range size.fmts {
		desc, ok := f.blocks[fmt.sig]
		if !ok {
			continue
		}
		switch fmt.reader {
		case 0:
			img, err := icnsReadPNGOrJPEG2000(f.data, desc[0], desc[1], [3]int{size.w, size.h, size.scale})
			if err != nil {
				return err
			}
			if err := img.load(); err != nil {
				return err
			}
			rgba = img
		case 1:
			img, err := icnsRead32(f.data, desc[0], desc[1], [3]int{size.w, size.h, size.scale}, true)
			if err != nil {
				return err
			}
			rgb = img
		case 2:
			img, err := icnsReadMK(f.data, desc[0], [3]int{size.w, size.h, size.scale})
			if err != nil {
				return err
			}
			alpha = img
		case 3:
			img, err := icnsRead32(f.data, desc[0], desc[1], [3]int{size.w, size.h, size.scale}, false)
			if err != nil {
				return err
			}
			rgb = img
		}
	}
	result := rgba
	if result == nil {
		if rgb == nil {
			return &Error{Class: "KeyError", Text: "'RGB'"}
		}
		result = rgb
		if alpha != nil {
			if alpha.Width != rgb.Width || alpha.Height != rgb.Height {
				return valueError("images do not match")
			}
			out := &Image{Format: "ICNS", Mode: "RGBA", Width: rgb.Width, Height: rgb.Height}
			out.pix = make([]byte, rgb.Width*rgb.Height*4)
			for i := 0; i < rgb.Width*rgb.Height; i++ {
				out.pix[4*i] = rgb.pix[3*i]
				out.pix[4*i+1] = rgb.pix[3*i+1]
				out.pix[4*i+2] = rgb.pix[3*i+2]
				out.pix[4*i+3] = alpha.pix[i]
			}
			result = out
		}
	}
	// IcnsImageFile.load copies the decoded image into itself.
	im.Mode = result.Mode
	im.Width, im.Height = result.Width, result.Height
	im.pix = result.pix
	im.pix16 = result.pix16
	im.pixI = result.pixI
	im.pixF = result.pixF
	im.palette = result.palette
	im.palRGB = result.palRGB
	im.palAlpha = result.palAlpha
	im.hasPal = result.hasPal
	im.transparency = result.transparency
	return nil
}
