// Image file decoding with MuPDF 1.25.5's semantics: fz_recognize_image_format
// picks the loader from the first bytes, and each loader decides whether the
// file opens and which 8-bit samples it produces. The overlay needs both, so
// that the forms it draws match Django's PyMuPDF output.
package medicalform

import "errors"

// errImage is the refusal of a file MuPDF cannot open.
var errImage = errors.New("medicalform: unknown image file format")

// decodeImage is fz_new_image_from_buffer followed by the decode pdf_add_image
// performs: a file MuPDF opens has an asset, anything else is refused.
func decodeImage(file []byte) (imageAsset, bool) {
	// fz_recognize_image_format reads eight bytes.
	if len(file) < 8 {
		return imageAsset{}, false
	}
	switch {
	case file[0] == 'P' && (file[1] >= '1' && file[1] <= '7' || file[1] == 'F' || file[1] == 'f'):
		return decodePNM(file)
	case file[0] == 0xff && file[1] == 0xd8:
		return decodeJPEG(file)
	case string(file[:8]) == "\x89PNG\r\n\x1a\n":
		return decodePNG(file)
	case string(file[:4]) == "II*\x00" || string(file[:4]) == "MM\x00*":
		return decodeTIFF(file)
	case string(file[:3]) == "GIF":
		return decodeGIF(file)
	case file[0] == 'B' && (file[1] == 'M' || file[1] == 'A'):
		return decodeBMP(file)
	case string(file[:4]) == "8BPS":
		return decodePSD(file)
	}
	return imageAsset{}, false
}

// newSampleAsset converts straight (non-premultiplied) 8-bit samples to what
// pdf_add_image writes: the colour samples after MuPDF's premultiply-then-
// divide round trip (fz_mul255, fz_div255) and the alpha channel as a mask,
// packed to one bit when every sample is 0 or 255.
func newSampleAsset(width, height, channels int, colour, alpha []byte, gray, cmyk bool) imageAsset {
	asset := imageAsset{width: width, height: height, gray: gray, cmyk: cmyk}
	if alpha == nil {
		asset.color = colour
		return asset
	}
	binary := true
	for _, a := range alpha {
		if a != 0 && a != 0xff {
			binary = false
			break
		}
	}
	out := append([]byte(nil), colour...)
	for i := 0; i < width*height; i++ {
		a := int(alpha[i])
		for c := 0; c < channels; c++ {
			at := i*channels + c
			out[at] = byte(fzDiv255(fzMul255(int(out[at]), a), a))
		}
	}
	asset.color = out
	asset.alpha = true
	if binary {
		asset.mask, asset.maskBits = packBits(alpha, width, height), 1
		return asset
	}
	asset.mask, asset.maskBits = alpha, 8
	return asset
}
