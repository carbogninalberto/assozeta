// GIF loading with MuPDF's semantics: every image block is painted onto one
// canvas (no disposal handling), a truncated LZW stream is padded with zeros,
// while a truncated data sub-block or a missing trailer is refused.
package medicalform

func decodeGIF(file []byte) (imageAsset, bool) {
	if len(file) < 6 {
		return imageAsset{}, false
	}
	if string(file[:3]) != "GIF" {
		return imageAsset{}, false
	}
	if string(file[3:6]) != "87a" && string(file[3:6]) != "89a" {
		return imageAsset{}, false
	}
	gif89a := string(file[:6]) == "GIF89a"
	p := 6
	if len(file)-p < 7 {
		return imageAsset{}, false
	}
	width := int(file[p]) | int(file[p+1])<<8
	height := int(file[p+2]) | int(file[p+3])<<8
	if width <= 0 || height <= 0 {
		return imageAsset{}, false
	}
	hasGCT := file[p+4]&0x80 != 0
	gctEntries := 0
	background := 0
	if hasGCT {
		gctEntries = 1 << ((file[p+4] & 7) + 1)
		background = int(file[p+5])
		if background >= gctEntries {
			background = gctEntries - 1
		}
	}
	p += 7

	colour := make([]byte, width*height*3)
	alpha := make([]byte, width*height)
	mask := make([]byte, width*height)
	var gct []byte
	if hasGCT {
		if len(file)-p < gctEntries*3 {
			return imageAsset{}, false
		}
		gct = file[p : p+gctEntries*3]
		p += gctEntries * 3
		base := background * 3
		for i := 0; i < width*height; i++ {
			copy(colour[i*3:i*3+3], gct[base:base+3])
			alpha[i] = 255
			mask[i] = 1
		}
	}

	transparent := 0
	hasTransparency := false
	var lct []byte
	lctEntries := 0

	for {
		if p >= len(file) {
			return imageAsset{}, false
		}
		switch file[p] {
		case 0x3b:
			return finishGIF(width, height, colour, alpha, mask), true
		case 0x21:
			if len(file)-p < 2 {
				return imageAsset{}, false
			}
			label := file[p+1]
			switch {
			case label == 0x01 && gif89a:
				if len(file)-p < 15 {
					return imageAsset{}, false
				}
				if file[p+2] != 0x0c {
					return imageAsset{}, false
				}
				var ok bool
				if p, ok = gifSubBlocks(file, p+15); !ok {
					return imageAsset{}, false
				}
				transparent, hasTransparency = 0, false
			case label == 0xf9 && gif89a:
				if len(file)-p < 8 {
					return imageAsset{}, false
				}
				if file[p+2] != 0x04 {
					return imageAsset{}, false
				}
				hasTransparency = file[p+3]&1 != 0
				if hasTransparency {
					transparent = int(file[p+6])
				}
				p += 8
			case label == 0xfe && gif89a:
				var ok bool
				if p, ok = gifSubBlocks(file, p+2); !ok {
					return imageAsset{}, false
				}
			case label == 0xff && gif89a:
				if len(file)-p < 14 {
					return imageAsset{}, false
				}
				if file[p+2] != 0x0b {
					return imageAsset{}, false
				}
				var ok bool
				if p, ok = gifSubBlocks(file, p+14); !ok {
					return imageAsset{}, false
				}
			default:
				var ok bool
				if p, ok = gifSubBlocks(file, p+2); !ok {
					return imageAsset{}, false
				}
			}
		case 0x2c:
			if len(file)-p < 11 {
				return imageAsset{}, false
			}
			left := int(file[p+2])<<8 | int(file[p+1])
			top := int(file[p+4])<<8 | int(file[p+3])
			imageWidth := int(file[p+6])<<8 | int(file[p+5])
			imageHeight := int(file[p+8])<<8 | int(file[p+7])
			flags := file[p+9]
			interlaced := flags&0x40 != 0
			p += 10
			lct = nil
			if flags&0x80 != 0 {
				lctEntries = 1 << ((flags & 7) + 1)
				if len(file)-p < lctEntries*3 {
					return imageAsset{}, false
				}
				lct = file[p : p+lctEntries*3]
				p += lctEntries * 3
			}
			minCodeSize := int(file[p])
			compressed, next, ok := gifSubBlocksBytes(file, p+1)
			if !ok {
				return imageAsset{}, false
			}
			p = next
			// An image that does not overlap the canvas is only consumed.
			if top < height && left < width && imageWidth > 0 && imageHeight > 0 {
				table, entries := gct, gctEntries
				if lct != nil {
					table, entries = lct, lctEntries
				}
				if table == nil {
					table, entries = gifDefaultTable[:], 256
				}
				// An out-of-range LZW code abandons this frame's painting but
				// not the file: gif_read_tbid's error is reported, the canvas
				// keeps what earlier frames drew.
				if decoded, ok := gifLZW(compressed, minCodeSize, imageWidth*imageHeight); ok {
					gifPaint(colour, alpha, mask, width, height, left, top, imageWidth, imageHeight,
						interlaced, table, entries, decoded, hasTransparency, transparent)
				}
			}
			transparent, hasTransparency = 0, false
		default:
			return imageAsset{}, false
		}
	}
}

func finishGIF(width, height int, colour, alpha, mask []byte) imageAsset {
	for i := range alpha {
		if mask[i] == 0 {
			alpha[i] = 0
		}
	}
	return newSampleAsset(width, height, 3, colour, alpha, false, false)
}

// gifSubBlocks consumes data sub-blocks, returning the position after the
// terminator.
func gifSubBlocks(file []byte, p int) (int, bool) {
	for {
		if p >= len(file) {
			return p, false
		}
		n := int(file[p])
		p++
		if n == 0 {
			return p, true
		}
		if len(file)-p < n {
			return p, false
		}
		p += n
	}
}

func gifSubBlocksBytes(file []byte, p int) ([]byte, int, bool) {
	var out []byte
	for {
		if p >= len(file) {
			return nil, p, false
		}
		n := int(file[p])
		p++
		if n == 0 {
			return out, p, true
		}
		if len(file)-p < n {
			return nil, p, false
		}
		out = append(out, file[p:p+n]...)
		p += n
	}
}

// gifLZW decodes a table-based image data stream with MuPDF's tolerance: the
// codes are read until the input ends (a short stream leaves the rest of the
// image zero, as gif_read_tbid pads it), while an invalid code refuses the
// file.
func gifLZW(data []byte, minCodeSize, size int) ([]byte, bool) {
	if minCodeSize < 2 || minCodeSize > 8 {
		return nil, false
	}
	clear := 1 << minCodeSize
	end := clear + 1
	codeSize := minCodeSize + 1
	next := end + 1
	prefix := make([]int, 4096)
	suffix := make([]byte, 4096)
	for i := range prefix {
		prefix[i] = -1
	}
	for i := 0; i < clear; i++ {
		suffix[i] = byte(i)
	}
	out := make([]byte, 0, size)
	bitPos := 0
	readCode := func() (int, bool) {
		if bitPos+codeSize > len(data)*8 {
			return 0, false
		}
		value := 0
		for i := 0; i < codeSize; i++ {
			at := bitPos >> 3
			value |= int(data[at]>>uint(bitPos&7)) & 1 << uint(i)
			bitPos++
		}
		return value, true
	}
	first := func(code int) byte {
		for prefix[code] >= 0 {
			code = prefix[code]
		}
		return suffix[code]
	}
	emit := func(code int) {
		var stack [4096]byte
		top := 0
		for code >= 0 && top < len(stack) {
			stack[top] = suffix[code]
			top++
			code = prefix[code]
		}
		for top > 0 && len(out) < size {
			top--
			out = append(out, stack[top])
		}
	}
	previous := -1
	for {
		code, ok := readCode()
		if !ok {
			break
		}
		if code == clear {
			next = end + 1
			codeSize = minCodeSize + 1
			previous = -1
			continue
		}
		if code == end {
			break
		}
		if previous < 0 {
			if code >= clear {
				return nil, false
			}
			emit(code)
			previous = code
			continue
		}
		if code < next {
			if code >= clear && prefix[code] < 0 {
				return nil, false
			}
			emit(code)
			if next < 4096 {
				prefix[next] = previous
				suffix[next] = first(code)
				next++
			}
		} else if code == next {
			if next >= 4096 {
				return nil, false
			}
			prefix[next] = previous
			suffix[next] = first(previous)
			next++
			emit(code)
		} else {
			return nil, false
		}
		previous = code
		if next == 1<<uint(codeSize) && codeSize < 12 {
			codeSize++
		}
	}
	for len(out) < size {
		out = append(out, 0)
	}
	return out, true
}

// gifDefaultTable is the default colour table gif_read_tbid uses when the file
// has neither a global nor a local table: black, white, then a grey ramp.
var gifDefaultTable = func() (table [256 * 3]byte) {
	table[0], table[1], table[2] = 0, 0, 0
	table[3], table[4], table[5] = 255, 255, 255
	for i := 2; i < 256; i++ {
		table[i*3], table[i*3+1], table[i*3+2] = byte(i-1), byte(i-1), byte(i-1)
	}
	return
}()

func gifPaint(colour, alpha, mask []byte, width, height, left, top, imageWidth, imageHeight int,
	interlaced bool, table []byte, entries int, decoded []byte, hasTransparency bool, transparent int) {
	paint := func(y int, sp []byte) {
		if top+y >= height {
			return
		}
		for dx := 0; dx < imageWidth && left+dx < width; dx++ {
			index := int(sp[dx])
			at := (top+y)*width + left + dx
			if hasTransparency && index == transparent {
				if mask[at] == 1 {
					mask[at] = 0
				}
				continue
			}
			mask[at] = 2
			if index >= entries {
				index = entries - 1
			}
			if index < 0 {
				index = 0
			}
			copy(colour[at*3:at*3+3], table[index*3:index*3+3])
			alpha[at] = 255
		}
	}
	if imageWidth <= 0 {
		return
	}
	if interlaced {
		row := 0
		for _, pass := range [4][2]int{{0, 8}, {4, 8}, {2, 4}, {1, 2}} {
			for y := pass[0]; y < imageHeight; y += pass[1] {
				paint(y, decoded[row*imageWidth:])
				row++
			}
		}
		return
	}
	for y := 0; y < imageHeight; y++ {
		paint(y, decoded[y*imageWidth:])
	}
}
