package pilimage

// This file reproduces the two libtiff decode paths Pillow's TiffDecode.c uses
// besides its plain strip reader: the JPEG codec (tif_jpeg.c with libjpeg,
// decoded here by Go's image/jpeg under divergence X19) and
// _decodeAsRGBA/TIFFRGBAImageGet for YCbCr images (tif_getimage.c put
// routines), which is deterministic and therefore matched exactly.

// tiffIntsBytes converts a tag value to bytes.
func tiffIntsBytes(v []int64) []byte {
	out := make([]byte, len(v))
	for i, x := range v {
		out[i] = byte(x)
	}
	return out
}

// tiffStripLayout is the strip/tile list the libtiff decoder walks.
type tiffStripLayout struct {
	offsets, counts []int64
	tw, th          int
	tiled           bool
}

// tiffStrips collects STRIPOFFSETS/STRIPBYTECOUNTS or the tile tags.
func tiffStrips(d *tiffIFD, xsize, ysize int) (tiffStripLayout, bool) {
	var l tiffStripLayout
	if v, ok := d.get(273); ok {
		l.offsets = v
		if c, ok := d.get(279); ok {
			l.counts = c
		}
		l.tw = xsize
		l.th = int(d.get1(278, int64(ysize)))
		if l.th <= 0 || l.th > ysize {
			l.th = ysize
		}
		return l, true
	}
	if v, ok := d.get(324); ok {
		l.tiled = true
		l.offsets = v
		if c, ok := d.get(325); ok {
			l.counts = c
		}
		l.tw = int(d.get1(322, 0))
		l.th = int(d.get1(323, 0))
		if l.tw <= 0 || l.th <= 0 {
			return l, false
		}
		return l, true
	}
	return l, false
}

// mergeJPEGTables is libtiff's JPEGTables handling: the abbreviated stream in
// the strip is prefixed with the tables tag's segments (SOI and EOI removed).
func mergeJPEGTables(tables, strip []byte) []byte {
	if len(tables) < 2 || tables[0] != 0xFF || tables[1] != 0xD8 {
		return strip
	}
	end := len(tables)
	if end >= 2 && tables[end-2] == 0xFF && tables[end-1] == 0xD9 {
		end -= 2
	}
	body := strip
	if len(body) >= 2 && body[0] == 0xFF && body[1] == 0xD8 {
		body = body[2:]
	}
	out := make([]byte, 0, 2+end-2+len(body))
	out = append(out, 0xFF, 0xD8)
	out = append(out, tables[2:end]...)
	out = append(out, body...)
	return out
}

// loadTIFFJPEG is the libtiff JPEG codec for TIFF compressions 6 and 7. The
// pixels are Go's JPEG decoder's (X19); a stream it refuses answers libtiff's
// decode failure, which Pillow reports as "decoder error -2".
func (im *Image) loadTIFFJPEG(d *tiffIFD, data []byte, xsize, ysize, compression int) error {
	samples := im.samplesPerPixel()
	im.pix = make([]byte, samples*xsize*ysize)
	var tables []byte
	if v, ok := d.get(347); ok {
		tables = tiffIntsBytes(v)
	}
	if compression == 6 {
		// Old-style JPEG may carry the whole interchange stream in
		// JPEGInterchangeFormat.
		if offv, ok := d.get(513); ok && len(offv) > 0 {
			n := int64(0)
			if v, ok := d.get(514); ok && len(v) > 0 {
				n = v[0]
			}
			off := offv[0]
			if off >= 0 && n > 0 && off+n <= int64(len(data)) {
				stream := data[off : off+n]
				dec, err := decodeJPEGImage(mergeJPEGTables(tables, stream))
				if err != nil {
					dec, err = decodeJPEGImage(stream)
				}
				if err != nil {
					return osError("decoder error -2")
				}
				pix, err := dec.pixelsFor(im.Mode)
				if err != nil {
					return err
				}
				im.copyJPEGRegion(pix, dec.Width, dec.Height, 0, 0, xsize, ysize)
				return nil
			}
		}
	}
	layout, ok := tiffStrips(d, xsize, ysize)
	if !ok {
		return osError("decoder error -2")
	}
	cols := max(1, (xsize+layout.tw-1)/layout.tw)
	for i, off := range layout.offsets {
		var count int64
		if i < len(layout.counts) {
			count = layout.counts[i]
		}
		if off < 0 || count <= 0 || off+count > int64(len(data)) {
			return osError("decoder error -2")
		}
		dec, err := decodeJPEGImage(mergeJPEGTables(tables, data[off:off+count]))
		if err != nil && len(tables) > 0 {
			// A stand-alone strip stream (no abbreviated tables) is valid too.
			dec, err = decodeJPEGImage(data[off : off+count])
		}
		if err != nil {
			return osError("decoder error -2")
		}
		pix, err := dec.pixelsFor(im.Mode)
		if err != nil {
			return err
		}
		tx := (i % cols) * layout.tw
		ty := (i / cols) * layout.th
		im.copyJPEGRegion(pix, dec.Width, dec.Height, tx, ty, xsize, ysize)
	}
	return nil
}

// pixelsFor converts a decoded JPEG to the sample layout of mode.
func (im *Image) pixelsFor(mode string) ([]byte, error) {
	switch mode {
	case "RGB":
		if im.Mode == "RGB" {
			return im.pix, nil
		}
		return im.convertRGB()
	case "L":
		if im.Mode == "L" {
			return im.pix, nil
		}
		return im.convertGray()
	case "CMYK":
		if im.Mode == "CMYK" {
			return im.pix, nil
		}
	}
	return nil, osError("decoder error -2")
}

// copyJPEGRegion places the decoded strip/tile samples into the image.
func (im *Image) copyJPEGRegion(pix []byte, w, h, tx, ty, xsize, ysize int) {
	n := im.samplesPerPixel()
	for y := 0; y < h && ty+y < ysize; y++ {
		for x := 0; x < w && tx+x < xsize; x++ {
			dst := ((ty+y)*xsize + tx + x) * n
			src := (y*w + x) * n
			if src+n > len(pix) || dst+n > len(im.pix) {
				continue
			}
			copy(im.pix[dst:dst+n], pix[src:src+n])
		}
	}
}

// ycbcrTables is TIFFYCbCrToRGB: the fixed-point conversion tables
// TIFFYCbCrToRGBInit builds from YCbCrCoefficients and ReferenceBlackWhite.
type ycbcrTables struct {
	yTab, crRTab, cbBTab, crGTab, cbGTab [256]int32
}

// floatEq is TIFF_FLOAT_EQ for the comparisons tif_color.c makes.
func floatEq(a, b float32) bool { return a == b }

// clampF32 is the CLAMP macro of tif_color.c.
func clampF32(f, lo, hi float32) float32 {
	if !(f >= lo) {
		return lo
	}
	if f > hi {
		return hi
	}
	return f
}

// fixF32 is FIX(x): the double multiply and truncation.
func fixF32(x float32) int32 { return int32(float64(x)*(1<<16) + 0.5) }

// code2V is the Code2V macro of tif_color.c, evaluated in float32.
func code2V(c int, rb, rw, cr float32) float32 {
	num := (float32(c) - float32(int32(rb))) * cr
	den := float32(1)
	if !floatEq(rw, rb) {
		den = rw - rb
	}
	return num / den
}

// clampW is the CLAMPw helper of tif_color.c.
func clampW(v, lo, hi float32) float32 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// newYCbCrTables is TIFFYCbCrToRGBInit.
func newYCbCrTables(luma, refBlackWhite [6]float32) *ycbcrTables {
	t := &ycbcrTables{}
	f1 := 2 - 2*luma[0]
	d1 := fixF32(clampF32(f1, 0, 2))
	f2 := luma[0] * f1 / luma[1]
	d2 := -fixF32(clampF32(f2, 0, 2))
	f3 := 2 - 2*luma[2]
	d3 := fixF32(clampF32(f3, 0, 2))
	f4 := luma[2] * f3 / luma[1]
	d4 := -fixF32(clampF32(f4, 0, 2))
	for i, x := 0, -128; i < 256; i, x = i+1, x+1 {
		cr := int32(clampW(code2V(x, refBlackWhite[4]-128, refBlackWhite[5]-128, 127), -128*32, 128*32))
		cb := int32(clampW(code2V(x, refBlackWhite[2]-128, refBlackWhite[3]-128, 127), -128*32, 128*32))
		t.crRTab[i] = (d1*cr + 1<<15) >> 16
		t.cbBTab[i] = (d3*cb + 1<<15) >> 16
		t.crGTab[i] = d2 * cr
		t.cbGTab[i] = d4*cb + 1<<15
		t.yTab[i] = int32(clampW(code2V(x+128, refBlackWhite[0], refBlackWhite[1], 255), -128*32, 128*32))
	}
	return t
}

// toRGB is TIFFYCbCrtoRGB.
func (t *ycbcrTables) toRGB(y, cb, cr int) (byte, byte, byte) {
	if y > 255 {
		y = 255
	}
	if cb < 0 {
		cb = 0
	} else if cb > 255 {
		cb = 255
	}
	if cr < 0 {
		cr = 0
	} else if cr > 255 {
		cr = 255
	}
	r := t.yTab[y] + t.crRTab[cr]
	g := t.yTab[y] + ((t.cbGTab[cb] + t.crGTab[cr]) >> 16)
	b := t.yTab[y] + t.cbBTab[cb]
	return clipI32(r), clipI32(g), clipI32(b)
}

func clipI32(v int32) byte {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return byte(v)
}

// tiffRationals reads a TIFF RATIONAL tag as the float values libtiff's
// TIFFGetField returns (our IFD reader stores numerator, denominator pairs).
func tiffRationals(d *tiffIFD, tag int, def [6]float32) [6]float32 {
	v, ok := d.get(tag)
	if !ok || len(v) < 2 {
		return def
	}
	out := def
	for i := 0; i < 3 && 2*i+1 < len(v); i++ {
		if v[2*i+1] == 0 {
			out[i] = 0
		} else {
			out[i] = float32(float64(v[2*i]) / float64(v[2*i+1]))
		}
	}
	return out
}

// tiffRefBlackWhite reads ReferenceBlackWhite (six rationals), which starts at
// index 0 of the array the converter uses.
func tiffRefBlackWhite(d *tiffIFD, def [6]float32) [6]float32 {
	v, ok := d.get(532)
	if !ok || len(v) < 2 {
		return def
	}
	out := def
	for i := 0; i < 6 && 2*i+1 < len(v); i++ {
		if v[2*i+1] == 0 {
			out[i] = 0
		} else {
			out[i] = float32(float64(v[2*i]) / float64(v[2*i+1]))
		}
	}
	return out
}

// tiffYCbCrTables builds libtiff's conversion for the image's tags.
func tiffYCbCrTables(d *tiffIFD) *ycbcrTables {
	luma := tiffRationals(d, 529, [6]float32{0.299, 0.587, 0.114})
	ref := tiffRefBlackWhite(d, [6]float32{0, 255, 128, 255, 128, 255})
	return newYCbCrTables(luma, ref)
}

// tiffYCbCrDecoded is TiffDecode.c's _decodeAsRGBA for a YCbCr image: the
// packed strips are decoded by libtiff's codec, TIFFRGBAImageGet converts them
// with the putcontig8bitYCbCr* routines, and the RGBX raw mode keeps the three
// colour bytes of each pixel.
func (im *Image) tiffYCbCrDecoded(d *tiffIFD, data []byte, xsize, ysize int, compression, t4options, fillorder int64) error {
	bpsV, hasBPS := d.get(258)
	bps := bpsV
	if !hasBPS || len(bps) == 0 {
		bps = []int64{1}
	}
	spp := d.get1(277, 1)
	// TIFFRGBAImageOK/PickContigCase: only 8-bit three-sample YCbCr converts.
	if len(bps) != 3 || bps[0] != 8 || bps[1] != 8 || bps[2] != 8 || spp != 3 {
		return osError("decoder error -2")
	}
	hsub, vsub := 2, 2
	if v, ok := d.get(530); ok && len(v) >= 2 {
		hsub, vsub = int(v[0]), int(v[1])
	}
	switch (hsub << 4) | vsub {
	case 0x11, 0x12, 0x21, 0x22, 0x41, 0x42, 0x44:
	default:
		return osError("decoder error -2")
	}
	if xsize < hsub || ysize < vsub {
		return osError("decoder error -2")
	}
	if d.get1(284, 1) == 2 {
		return osError("decoder error -2")
	}
	tables := tiffYCbCrTables(d)
	layout, ok := tiffStrips(d, xsize, ysize)
	if !ok {
		return osError("decoder error -2")
	}
	im.pix = make([]byte, 3*xsize*ysize)
	cols := max(1, (xsize+layout.tw-1)/layout.tw)
	clump := hsub*vsub + 2
	// gtStripContig allocates one strip buffer and keeps it across strips: a
	// decode failure leaves whatever the previous strip or the failed codec
	// wrote in it.
	var stripBuf []byte
	readError := false
	for i, off := range layout.offsets {
		var count int64
		if i < len(layout.counts) {
			count = layout.counts[i]
		}
		if off < 0 || count <= 0 || off+count > int64(len(data)) {
			return osError("decoder error -2")
		}
		tx := (i % cols) * layout.tw
		ty := (i / cols) * layout.th
		w := min(layout.tw, xsize-tx)
		rows := min(layout.th, ysize-ty)
		blocks := (w + hsub - 1) / hsub
		imageBlocks := (xsize + hsub - 1) / hsub
		groups := (rows + vsub - 1) / vsub
		full := blocks * groups * clump
		if len(stripBuf) < full {
			stripBuf = append(stripBuf, make([]byte, full-len(stripBuf))...)
		}
		var chunk []byte
		if readError {
			// LZWDecode and ZIPDecode refuse further strips after an error and
			// zero the buffer.
			for k := 0; k < full; k++ {
				stripBuf[k] = 0
			}
		} else {
			chunk, written, derr := tiffDecompressStrip(int(compression), data[off:off+count], layout.tw, rows, full, t4options, fillorder)
			if derr != nil {
				// gtStripContig decodes with stoponerr=0: a strip whose codec
				// fails after its buffer was allocated keeps the bytes the
				// codec produced (its error path zeroes the rest), and the
				// image still decodes.
				if written > len(stripBuf) {
					written = len(stripBuf)
				}
				copy(stripBuf, chunk[:written])
				switch compression {
				case 5, 8, 32946:
					readError = true
				}
			} else {
				copy(stripBuf, chunk)
			}
		}
		chunk = stripBuf[:full]
		// libtiff's gtStripContig asks the codec for nrowsub *
		// TIFFScanlineSize bytes; for 4x4 subsampling that is two bytes short
		// of the last clump of each horizontal block, and the calloc'd buffer
		// leaves the rest zero. The put routines then read those zeros.
		scanline := imageBlocks * clump / vsub
		read := groups * vsub * scanline
		if read < full {
			padded := make([]byte, full)
			copy(padded, chunk[:read])
			chunk = padded
		}
		for y := 0; y < rows; y++ {
			group := y / vsub
			for x := 0; x < w; x++ {
				c := (group*blocks + x/hsub) * clump
				yidx := c + (y%vsub)*hsub + (x % hsub)
				if yidx+2 >= len(chunk) {
					return osError("decoder error -2")
				}
				yy := int(chunk[yidx])
				cb := int(chunk[c+hsub*vsub])
				cr := int(chunk[c+hsub*vsub+1])
				r, g, b := tables.toRGB(yy, cb, cr)
				dst := ((ty+y)*xsize + tx + x) * 3
				im.pix[dst], im.pix[dst+1], im.pix[dst+2] = r, g, b
			}
		}
	}
	return nil
}

// tiffPlanarDecoded is TiffDecode.c's _decodeStrip/_decodeTile for
// PLANARCONFIG_SEPARATE: _pickUnpackers reads each band from its own plane and
// writes it with Pillow's 4-byte pixel stride. For 8-bit samples the band
// bytes are the samples; for 16-bit samples the band takes the native-order
// high byte, which is what the R_16N unpackers store.
func (im *Image) tiffPlanarDecoded(d *tiffIFD, data []byte, xsize, ysize int, compression, t4options, fillorder int64) error {
	bpsV, hasBPS := d.get(258)
	bps := int64(0)
	if hasBPS && len(bpsV) > 0 {
		bps = bpsV[0]
	} else {
		return osError("decoder error -2")
	}
	if bps != 8 && bps != 16 {
		return osError("decoder error -2")
	}
	bands := im.samplesPerPixel()
	im.pix = make([]byte, xsize*ysize*bands)
	layout, ok := tiffStrips(d, xsize, ysize)
	if !ok {
		return osError("decoder error -2")
	}
	cols := max(1, (xsize+layout.tw-1)/layout.tw)
	rowsPerPlane := max(1, (ysize+layout.th-1)/layout.th)
	perPlane := rowsPerPlane * cols
	rowBytes := int(int64(layout.tw)*bps) / 8
	for plane := 0; plane < bands; plane++ {
		for j := 0; j < perPlane; j++ {
			i := plane*perPlane + j
			if i >= len(layout.offsets) {
				// libtiff cannot read the plane's strip/tile.
				return osError("decoder error -2")
			}
			off := layout.offsets[i]
			var count int64
			if i < len(layout.counts) {
				count = layout.counts[i]
			}
			if off < 0 || count <= 0 || off+count > int64(len(data)) {
				return osError("decoder error -2")
			}
			tx := (j % cols) * layout.tw
			ty := (j / cols) * layout.th
			rows := min(layout.th, ysize-ty)
			need := rows * rowBytes
			chunk, _, err := tiffDecompressStrip(int(compression), data[off:off+count], layout.tw, rows, need, t4options, fillorder)
			if err != nil || len(chunk) < need {
				return osError("decoder error -2")
			}
			w := min(layout.tw, xsize-tx)
			for y := 0; y < rows; y++ {
				for x := 0; x < w; x++ {
					var v byte
					if bps == 8 {
						v = chunk[y*rowBytes+x]
					} else {
						v = chunk[y*rowBytes+2*x+1] // native-order high byte
					}
					im.pix[((ty+y)*xsize+tx+x)*bands+plane] = v
				}
			}
		}
	}
	return nil
}
