package pilimage

// This file reproduces the conversions reportlab's ImageReader.getRGBData
// asks Pillow for: convert("RGB"), convert("L") for LA images, and
// convert("RGBA") for palette PNGs with transparency. The per-mode rules are
// those of Pillow's C ImagingConvert (src/libImaging/Convert.c).

// convertRGB is Image.convert("RGB") for the modes decoded formats produce.
func (im *Image) convertRGB() ([]byte, error) {
	n := im.Width * im.Height
	out := make([]byte, 3*n)
	switch im.Mode {
	case "1":
		for i := 0; i < n; i++ {
			v := byte(0)
			if im.pix[i] != 0 {
				v = 255
			}
			out[3*i], out[3*i+1], out[3*i+2] = v, v, v
		}
	case "L":
		for i := 0; i < n; i++ {
			v := im.pix[i]
			out[3*i], out[3*i+1], out[3*i+2] = v, v, v
		}
	case "LA":
		for i := 0; i < n; i++ {
			v := im.pix[2*i]
			out[3*i], out[3*i+1], out[3*i+2] = v, v, v
		}
	case "RGB":
		copy(out, im.pix[:3*n])
	case "RGBA":
		for i := 0; i < n; i++ {
			out[3*i], out[3*i+1], out[3*i+2] = im.pix[4*i], im.pix[4*i+1], im.pix[4*i+2]
		}
	case "P", "PA":
		step := 1
		if im.Mode == "PA" {
			step = 4
		}
		for i := 0; i < n; i++ {
			idx := int(im.pix[step*i]) & 255
			out[3*i], out[3*i+1], out[3*i+2] = im.palRGB[3*idx], im.palRGB[3*idx+1], im.palRGB[3*idx+2]
		}
	case "I":
		for i := 0; i < n; i++ {
			out[3*i], out[3*i+1], out[3*i+2] = clipI8(im.pixI[i]), clipI8(im.pixI[i]), clipI8(im.pixI[i])
		}
	case "I;16", "I;16B", "I;16L", "I;16N":
		// I16_RGB: the high byte, if any, becomes 255.
		for i := 0; i < n; i++ {
			v := im.pix16[i]
			var b byte
			if v>>8 != 0 {
				b = 255
			} else {
				b = byte(v)
			}
			out[3*i], out[3*i+1], out[3*i+2] = b, b, b
		}
	case "YCbCr":
		for i := 0; i < n; i++ {
			y, cb, cr := int(im.pix[3*i]), int(im.pix[3*i+1]), int(im.pix[3*i+2])
			r := y + int(yccR_Cr[cr])>>6
			g := y + (int(yccG_Cb[cb])+int(yccG_Cr[cr]))>>6
			b := y + int(yccB_Cb[cb])>>6
			out[3*i], out[3*i+1], out[3*i+2] = clipByte(r), clipByte(g), clipByte(b)
		}
	case "F":
		// Image.convert falls back to the mode base ("L") for F -> RGB.
		for i := 0; i < n; i++ {
			b := clipF8(im.pixF[i])
			out[3*i], out[3*i+1], out[3*i+2] = b, b, b
		}
	default:
		if !knownMode(im.Mode) {
			return nil, valueError("unrecognized image mode")
		}
		return nil, valueError("conversion from " + im.Mode + " to RGB not supported")
	}
	return out, nil
}

// rgbaFromP is Image.convert("RGBA") for a palette image: p2rgba copies the
// palette entry's RGBA.
func (im *Image) rgbaFromP() []byte {
	n := im.Width * im.Height
	out := make([]byte, 4*n)
	alphaFromImage := im.Mode == "PA"
	for i := 0; i < n; i++ {
		var idx int
		if alphaFromImage {
			idx = int(im.pix[4*i])
		} else {
			idx = int(im.pix[i])
		}
		out[4*i] = im.palRGB[3*idx]
		out[4*i+1] = im.palRGB[3*idx+1]
		out[4*i+2] = im.palRGB[3*idx+2]
		if alphaFromImage {
			out[4*i+3] = im.pix[4*i+3]
		} else {
			out[4*i+3] = im.palAlpha[idx]
		}
	}
	return out
}

// rgbFromRGBA is Image.convert("RGB") for mode RGBA (Pillow's rgb2rgba:
// RGB values unchanged, alpha dropped).
func (im *Image) rgbFromRGBA() []byte {
	n := im.Width * im.Height
	out := make([]byte, 3*n)
	for i := 0; i < n; i++ {
		out[3*i], out[3*i+1], out[3*i+2] = im.pix[4*i], im.pix[4*i+1], im.pix[4*i+2]
	}
	return out
}

// clipI8 is the i2l/i2rgb saturation.
func clipI8(v int32) byte {
	if v <= 0 {
		return 0
	}
	if v >= 255 {
		return 255
	}
	return byte(v)
}

// clipF8 is f2l: cast to UINT8 with C truncation after clamping.
func clipF8(v float32) byte {
	if v <= 0 {
		return 0
	}
	if v >= 255 {
		return 255
	}
	return byte(v)
}

// convertGray is Image.convert("L") for the modes a LA image can load as:
// Pillow's rgb2l uses L24 with rounding.
func (im *Image) convertGray() ([]byte, error) {
	rgb, err := im.convertRGB()
	if err != nil {
		return nil, err
	}
	n := im.Width * im.Height
	out := make([]byte, n)
	for i := 0; i < n; i++ {
		r, g, b := int(rgb[3*i]), int(rgb[3*i+1]), int(rgb[3*i+2])
		out[i] = byte((r*19595 + g*38470 + b*7471 + 0x8000) >> 16)
	}
	return out, nil
}

// knownMode lists the modes Image._getmodebase/convert recognise.
func knownMode(mode string) bool {
	switch mode {
	case "1", "L", "LA", "La", "P", "PA", "RGB", "RGBA", "RGBX", "RGBa",
		"CMYK", "YCbCr", "LAB", "HSV", "I", "I;16", "I;16B", "I;16L", "I;16N",
		"F", "RGB;15", "BGR;15", "BGR;16", "BGR;24":
		return true
	}
	return false
}

// clipByte is the clamp ConvertYCbCr.c applies.
func clipByte(v int) byte {
	if v <= 0 {
		return 0
	}
	if v >= 255 {
		return 255
	}
	return byte(v)
}
