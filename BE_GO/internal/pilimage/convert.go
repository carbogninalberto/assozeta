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
	case "I;16":
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
	case "F":
		// Image.convert falls back to the mode base ("L") for F -> RGB.
		for i := 0; i < n; i++ {
			b := clipF8(im.pixF[i])
			out[3*i], out[3*i+1], out[3*i+2] = b, b, b
		}
	default:
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
