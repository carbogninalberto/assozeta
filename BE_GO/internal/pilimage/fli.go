package pilimage

// This file ports FliImagePlugin and the "fli" decoder (FliDecode.c).

// loadFLI is ImagingFliDecode over the first frame.
func (im *Image) loadFLI(offset, framesize int) error {
	if offset < 0 || offset > len(im.data) {
		return osError("unrecognized data stream contents when reading image file")
	}
	end := offset + framesize
	if end > len(im.data) {
		end = len(im.data)
	}
	buf := im.data[offset:end]
	if len(buf) < 4 {
		return truncatedError(0)
	}
	if len(buf) < 8 {
		return decoderError(-1)
	}
	if le16(buf[4:]) != 0xF1FA {
		return decoderError(-3)
	}
	chunks := le16(buf[6:])
	ptr := 16
	bytesLeft := len(buf) - 16
	im.pix = make([]byte, im.Width*im.Height)
	oob := func(n int) bool { return ptr+n > len(buf) }
	for c := 0; c < chunks; c++ {
		if bytesLeft < 10 {
			return decoderError(-1)
		}
		data := ptr + 6
		switch le16(buf[ptr+4:]) {
		case 4, 11:
			// palette chunk: ignored at load
		case 7:
			lines := le16(buf[data:])
			data += 2
			l := 0
			y := 0
			for ; l < lines && y < im.Height; l, y = l+1, y+1 {
				if oob(data + 2 - ptr) {
					return decoderError(-1)
				}
				packets := le16(buf[data:])
				data += 2
				for packets&0x8000 != 0 {
					if packets&0x4000 != 0 {
						y += 65536 - packets
						if y >= im.Height {
							return decoderError(-1)
						}
					} else {
						im.pix[y*im.Width+im.Width-1] = byte(packets)
					}
					if oob(data + 2 - ptr) {
						return decoderError(-1)
					}
					packets = le16(buf[data:])
					data += 2
				}
				row := im.pix[y*im.Width:]
				p := 0
				x := 0
				for ; p < packets; p++ {
					if oob(data + 2 - ptr) {
						return decoderError(-1)
					}
					x += int(buf[data])
					if buf[data+1] >= 128 {
						if oob(data + 4 - ptr) {
							return decoderError(-1)
						}
						i := 256 - int(buf[data+1])
						if x+i+i > im.Width {
							break
						}
						for j := 0; j < i; j++ {
							row[x] = buf[data+2]
							x++
							row[x] = buf[data+3]
							x++
						}
						data += 4
					} else {
						i := 2 * int(buf[data+1])
						if x+i > im.Width {
							break
						}
						if oob(data + 2 + i - ptr) {
							return decoderError(-1)
						}
						copy(row[x:], buf[data+2:data+2+i])
						data += 2 + i
						x += i
					}
				}
				if p < packets {
					break
				}
			}
			if l < lines {
				return decoderError(-1)
			}
		case 12:
			y := le16(buf[data:])
			ymax := y + le16(buf[data+2:])
			data += 4
			for ; y < ymax && y < im.Height; y++ {
				row := im.pix[y*im.Width:]
				if oob(data + 1 - ptr) {
					return decoderError(-1)
				}
				packets := int(buf[data])
				data++
				i := 0
				x := 0
				p := 0
				for ; p < packets; p++ {
					if oob(data + 2 - ptr) {
						return decoderError(-1)
					}
					x += int(buf[data])
					if buf[data+1]&0x80 != 0 {
						i = 256 - int(buf[data+1])
						if x+i > im.Width {
							break
						}
						if oob(data + 3 - ptr) {
							return decoderError(-1)
						}
						for j := 0; j < i; j++ {
							row[x+j] = buf[data+2]
						}
						data += 3
					} else {
						i = int(buf[data+1])
						if x+i > im.Width {
							break
						}
						if oob(data + 2 + i - ptr) {
							return decoderError(-1)
						}
						copy(row[x:], buf[data+2:data+2+i])
						data += i + 2
					}
					x += i
				}
				if p < packets {
					break
				}
			}
			if y < ymax {
				return decoderError(-1)
			}
		case 13:
			for y := 0; y < im.Height; y++ {
				row := im.pix[y*im.Width : (y+1)*im.Width]
				for i := range row {
					row[i] = 0
				}
			}
		case 15:
			for y := 0; y < im.Height; y++ {
				row := im.pix[y*im.Width:]
				data++ // packet count byte
				x := 0
				for x < im.Width {
					if oob(data + 2 - ptr) {
						return decoderError(-1)
					}
					if buf[data]&0x80 != 0 {
						i := 256 - int(buf[data])
						if x+i > im.Width {
							break
						}
						if oob(data + i + 1 - ptr) {
							return decoderError(-1)
						}
						copy(row[x:], buf[data+1:data+1+i])
						data += i + 1
						x += i
					} else {
						i := int(buf[data])
						if x+i > im.Width {
							break
						}
						for j := 0; j < i; j++ {
							row[x+j] = buf[data+1]
						}
						data += 2
						x += i
					}
				}
				if x != im.Width {
					return decoderError(-1)
				}
			}
		case 16:
			if im.Width*im.Height > 0x7fffffff {
				return decoderError(-1)
			}
			if data+im.Width*im.Height > ptr+bytesLeft {
				return truncatedError(0)
			}
			for y := 0; y < im.Height; y++ {
				copy(im.pix[y*im.Width:(y+1)*im.Width], buf[data:data+im.Width])
				data += im.Width
			}
		case 18:
			// PSTAMP: ignored
		default:
			return decoderError(-3)
		}
		advance := int(int32(le32(buf[ptr:])))
		if advance == 0 {
			return decoderError(-2)
		}
		if advance < 0 || advance > bytesLeft {
			return decoderError(-1)
		}
		ptr += advance
		bytesLeft -= advance
	}
	return nil
}

// openFLI is FliImageFile._open.
func openFLI(data []byte) (*Image, error) {
	if len(data) < 128 {
		return nil, syntaxError("not an FLI/FLC file")
	}
	s := data[:128]
	if !(acceptFLI(s[:16]) && bytesEqualZeros(s[20:22]) && bytesEqualZeros(s[42:80]) && bytesEqualZeros(s[88:128])) {
		return nil, syntaxError("not an FLI/FLC file")
	}
	mode := "P"
	w, h := le16(s[8:]), le16(s[10:])
	palette := make([]byte, 768)
	for i := 0; i < 256; i++ {
		palette[3*i] = byte(i)
		palette[3*i+1] = byte(i)
		palette[3*i+2] = byte(i)
	}
	pos := 128
	if pos+16 > len(data) {
		return nil, shortError()
	}
	chunk := data[pos : pos+16]
	offset := 128
	if le16(chunk[4:]) == 0xF100 {
		offset = 128 + int(int32(le32(chunk)))
		if offset+16 > len(data) {
			return nil, shortError()
		}
		chunk = data[offset : offset+16]
	}
	if le16(chunk[4:]) == 0xF1FA {
		number := le16(chunk[6:])
		chunkPos := offset + 16
		chunkSize := 0
		for n := 0; n < number; n++ {
			if chunkSize != 0 {
				chunkPos += chunkSize - 6
			}
			if chunkPos+6 > len(data) {
				return nil, shortError()
			}
			head := data[chunkPos : chunkPos+6]
			typ := le16(head[4:])
			if typ == 4 || typ == 11 {
				shift := 0
				if typ == 11 {
					shift = 2
				}
				if err := fliPalette(data, chunkPos+6, palette, shift); err != nil {
					return nil, err
				}
				break
			}
			chunkSize = int(le32(head))
			if chunkSize == 0 {
				break
			}
		}
	}
	im := newImage(data, "FLI", mode, w, h)
	im.setPaletteRGB(palette, palette)
	rewind := pos
	_ = rewind
	// seek(0): the frame tile starts at 128.
	if 128+4 > len(data) {
		return nil, &Error{Class: "EOFError", Text: "missing frame size"}
	}
	framesize := int(le32(data[128:]))
	im.loadFn = func() error { return im.loadFLI(128, framesize) }
	return im, nil
}

// fliPalette is FliImageFile._palette.
func fliPalette(data []byte, pos int, palette []byte, shift int) error {
	if pos+2 > len(data) {
		return shortError()
	}
	count := le16(data[pos:])
	pos += 2
	i := 0
	for e := 0; e < count; e++ {
		if pos+2 > len(data) {
			return shortError()
		}
		i += int(data[pos])
		n := int(data[pos+1])
		pos += 2
		if n == 0 {
			n = 256
		}
		if i+n > 256 {
			return valueError("too much palette data")
		}
		if pos+n*3 > len(data) {
			return shortError()
		}
		for j := 0; j < n; j++ {
			r := int(data[pos]) << uint(shift)
			g := int(data[pos+1]) << uint(shift)
			b := int(data[pos+2]) << uint(shift)
			pos += 3
			if r > 255 || g > 255 || b > 255 {
				return valueError("palette data out of range")
			}
			palette[3*i] = byte(r)
			palette[3*i+1] = byte(g)
			palette[3*i+2] = byte(b)
			i++
		}
	}
	return nil
}

func bytesEqualZeros(b []byte) bool {
	for _, c := range b {
		if c != 0 {
			return false
		}
	}
	return true
}
