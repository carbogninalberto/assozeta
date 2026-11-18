package pilimage

import "strings"

// This file ports SunImagePlugin and its "sun_rle" decoder.

// sunPalette is ImagePalette.raw: it records the raw palette bytes and the RGB
// triples they realize (the rawmode is applied as putpalette does).
func (im *Image) sunPalette(raw []byte, rawmode string) {
	im.hasPal = true
	im.palette = raw
	im.palRGB = [768]byte{}
	im.palAlpha = [256]byte{}
	for i := range im.palAlpha {
		im.palAlpha[i] = 255
	}
	rgb := realizePalette(rawmode, raw)
	if len(rgb) > len(im.palRGB) {
		rgb = rgb[:len(im.palRGB)]
	}
	copy(im.palRGB[:], rgb)
	im.palInvalid = len(raw)/3 > 256
}

// loadSUNRaw is SunImageFile's raw tile.
func (im *Image) loadSUNRaw(offset, stride int, rawmode string) error {
	return im.rawTile(im.data, offset, stride, 1, rawmode)
}

// loadSUNRLE is SunImageFile's "sun_rle" decoder.
func (im *Image) loadSUNRLE(offset int, rawmode string) error {
	rowBytes := rawRowBytes(rawmode, im.Width)
	data := im.data
	pos := offset
	raw := make([]byte, 0, rowBytes*im.Height)
	for len(raw) < rowBytes*im.Height {
		if pos >= len(data) {
			break
		}
		b := data[pos]
		pos++
		if b == 0x80 {
			if pos+2 > len(data) {
				pos = len(data)
				break
			}
			count := int(data[pos]) + 1
			v := data[pos+1]
			pos += 2
			for i := 0; i < count; i++ {
				raw = append(raw, v)
			}
		} else {
			// A non-0x80 byte is a single literal byte.
			raw = append(raw, b)
		}
	}
	if len(raw) < rowBytes*im.Height {
		return truncatedError(0)
	}
	im.ensurePix()
	for row := 0; row < im.Height; row++ {
		start := row * rowBytes
		if err := im.rawRow(raw[start:start+rowBytes], 0, row, im.Width, rawmode, baseRawmode(rawmode), im.samplesPerPixel(), -1); err != nil {
			return err
		}
	}
	return nil
}

func baseRawmode(rawmode string) string {
	if i := strings.IndexByte(rawmode, ';'); i >= 0 {
		return rawmode[:i]
	}
	return rawmode
}
