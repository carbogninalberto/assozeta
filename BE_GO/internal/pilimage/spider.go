package pilimage

import "math"

// This file ports SpiderImagePlugin: the header detection and the raw float
// tile.

// loadSPIDER is SpiderImageFile's raw "F;32BF"/"F;32F" tile.
func (im *Image) loadSPIDER(offset int, rawmode string) error {
	return im.rawTile(im.data, offset, 0, 1, rawmode)
}

// openSPIDER is SpiderImageFile._open.
func openSPIDER(data []byte) (*Image, error) {
	if len(data) < 27*4 {
		return nil, syntaxError("not a valid Spider file")
	}
	big := true
	hdrlen := spiderHeader(data, true)
	if hdrlen == 0 {
		big = false
		hdrlen = spiderHeader(data, false)
	}
	if hdrlen == 0 {
		return nil, syntaxError("not a valid Spider file")
	}
	var vals [27]float64
	for i := 0; i < 27; i++ {
		var u uint32
		if big {
			u = uint32(data[4*i])<<24 | uint32(data[4*i+1])<<16 | uint32(data[4*i+2])<<8 | uint32(data[4*i+3])
		} else {
			u = uint32(data[4*i]) | uint32(data[4*i+1])<<8 | uint32(data[4*i+2])<<16 | uint32(data[4*i+3])<<24
		}
		vals[i] = float64(math.Float32frombits(u))
	}
	h := func(i int) float64 { return vals[i-1] }
	if int(h(5)) != 1 {
		return nil, syntaxError("not a Spider 2D image")
	}
	w := int(h(12))
	height := int(h(2))
	istack := int(h(24))
	imgnumber := int(h(27))
	var offset int
	if istack == 0 && imgnumber == 0 {
		offset = hdrlen
	} else if istack > 0 && imgnumber == 0 {
		offset = hdrlen * 2
	} else if istack == 0 && imgnumber > 0 {
		return nil, &Error{Class: "AttributeError", Text: "'SpiderImageFile' object has no attribute 'stkoffset'"}
	} else {
		return nil, syntaxError("inconsistent stack header values")
	}
	rawmode := "F;32F"
	if big {
		rawmode = "F;32BF"
	}
	im := newImage(data, "SPIDER", "F", w, height)
	im.loadFn = func() error { return im.loadSPIDER(offset, rawmode) }
	return im, nil
}
