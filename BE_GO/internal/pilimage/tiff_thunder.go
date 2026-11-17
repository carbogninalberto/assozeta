package pilimage

// This file ports libtiff's ThunderScan 4-bit decoder (tif_thunder.c:
// ThunderDecode and ThunderDecodeRow), TIFF compression 32809.

const (
	thunderCode       = 0xc0
	thunderRun        = 0x00
	thunder2BitDeltas = 0x40
	delta2Skip        = 2
	thunder3BitDeltas = 0x80
	delta3Skip        = 4
	thunderRaw        = 0xc0
)

var thunder2Delta = [4]int{0, 1, 0, -1}
var thunder3Delta = [8]int{0, 1, 2, 3, 0, -3, -2, -1}

// thunderDecode is ThunderDecodeRow: one strip of rows, each decoded to
// ceil(width/2) packed nibble bytes. A row that does not produce exactly
// width pixels is a decode failure.
func thunderDecode(src []byte, width, rows int) ([]byte, bool) {
	rowbytes := (width + 1) / 2
	out := make([]byte, rowbytes*rows)
	bp := 0
	for r := 0; r < rows; r++ {
		row := out[r*rowbytes : (r+1)*rowbytes]
		var lastpixel, npixels, op int
		setPixel := func(v int) {
			lastpixel = v & 0xf
			if npixels < width {
				if npixels&1 != 0 {
					row[op] |= byte(lastpixel)
					op++
				} else {
					row[op] = byte(lastpixel << 4)
				}
				npixels++
			}
		}
		for bp < len(src) && npixels < width {
			n := int(src[bp])
			bp++
			switch n & thunderCode {
			case thunderRun:
				if n == 0 {
					break
				}
				if npixels&1 != 0 {
					row[op] |= byte(lastpixel)
					lastpixel = int(row[op])
					op++
					npixels++
					n--
				} else {
					lastpixel |= lastpixel << 4
				}
				npixels += n
				if npixels > width {
					break
				}
				for ; n > 0; n -= 2 {
					row[op] = byte(lastpixel)
					op++
				}
				if n == -1 {
					op--
					row[op] &= 0xf0
				}
				lastpixel &= 0xf
			case thunder2BitDeltas:
				delta := (n >> 4) & 3
				if delta != delta2Skip {
					setPixel(lastpixel + thunder2Delta[delta])
				}
				delta = (n >> 2) & 3
				if delta != delta2Skip {
					setPixel(lastpixel + thunder2Delta[delta])
				}
				delta = n & 3
				if delta != delta2Skip {
					setPixel(lastpixel + thunder2Delta[delta])
				}
			case thunder3BitDeltas:
				delta := (n >> 3) & 7
				if delta != delta3Skip {
					setPixel(lastpixel + thunder3Delta[delta])
				}
				delta = n & 7
				if delta != delta3Skip {
					setPixel(lastpixel + thunder3Delta[delta])
				}
			case thunderRaw:
				setPixel(n)
			}
		}
		if npixels != width {
			// libtiff zeroes the rest of the row and fails the strip.
			for i := op; i < rowbytes; i++ {
				row[i] = 0
			}
			return nil, false
		}
	}
	return out, true
}
