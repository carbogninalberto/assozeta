package pilimage

import (
	"encoding/binary"
	"strconv"
)

// This file ports Pillow's "bcn" decoder (src/libImaging/BcnDecode.c) for the
// BC1-BC5 block formats.

// loadBCN decodes n*16-byte blocks starting at offset into im (whole image).
func (im *Image) loadBCN(offset, n, width, height int) error {
	return im.loadBCNData(im.data, offset, n, width, height)
}

// loadBCNData decodes blocks from data[offset:].
func (im *Image) loadBCNData(src []byte, offset, n, width, height int) error {
	return im.loadBCNDataSign(src, offset, n, width, height, false)
}

// loadBCNDataSign decodes blocks, with BC5S's signed alpha interpretation.
func (im *Image) loadBCNDataSign(src []byte, offset, n, width, height int, sign bool) error {
	blockBytes := 16
	if n == 1 || n == 4 {
		blockBytes = 8
	}
	bw, bh := (width+3)/4, (height+3)/4
	need := bw * bh * blockBytes
	if offset+need > len(src) {
		if offset > len(src) {
			return truncatedError(0)
		}
		return truncatedError((len(src) - offset) % blockBytes)
	}
	data := src[offset:]
	samples := im.samplesPerPixel()
	im.pix = make([]byte, width*height*samples)
	for by := 0; by < bh; by++ {
		for bx := 0; bx < bw; bx++ {
			var block []byte
			if blockBytes == 8 {
				block = data[(by*bw+bx)*8:]
			} else {
				block = data[(by*bw+bx)*16:]
			}
			var rgba [16][4]byte
			switch n {
			case 1:
				decodeBC1(block, &rgba)
			case 2:
				decodeBC2(block, &rgba)
			case 3:
				decodeBC3(block, &rgba)
			case 4:
				decodeBC4(block, &rgba)
			case 5:
				decodeBC5(block, &rgba, sign)
			default:
				return ErrFormatNotPorted
			}
			for j := 0; j < 4; j++ {
				for i := 0; i < 4; i++ {
					x, y := bx*4+i, by*4+j
					if x >= width || y >= height {
						continue
					}
					p := (y*width + x) * samples
					switch n {
					case 4:
						im.pix[p] = rgba[j*4+i][0]
					case 5:
						im.pix[p] = rgba[j*4+i][0]
						im.pix[p+1] = rgba[j*4+i][1]
						im.pix[p+2] = rgba[j*4+i][2]
					default:
						for s := 0; s < samples; s++ {
							im.pix[p+s] = rgba[j*4+i][s]
						}
					}
				}
			}
		}
	}
	return nil
}

// expand565 is Pillow's 5/6-bit to 8-bit bit replication.
func expand565(v int) byte { return byte((v << 3) | (v >> 2)) }

// expand6 is the 6-bit to 8-bit bit replication.
func expand6(v int) byte { return byte((v << 2) | (v >> 4)) }

func bcColors(c0, c1 uint16, cols *[4][3]byte) {
	cols[0][0] = expand565(int(c0>>11) & 31)
	cols[0][1] = expand6(int(c0>>5) & 63)
	cols[0][2] = expand565(int(c0) & 31)
	cols[1][0] = expand565(int(c1>>11) & 31)
	cols[1][1] = expand6(int(c1>>5) & 63)
	cols[1][2] = expand565(int(c1) & 31)
	if c0 > c1 {
		for i := 0; i < 3; i++ {
			cols[2][i] = byte((2*int(cols[0][i]) + int(cols[1][i])) / 3)
			cols[3][i] = byte((int(cols[0][i]) + 2*int(cols[1][i])) / 3)
		}
	} else {
		for i := 0; i < 3; i++ {
			cols[2][i] = byte((int(cols[0][i]) + int(cols[1][i])) / 2)
			cols[3][i] = 0
		}
	}
}

func decodeBC1(block []byte, out *[16][4]byte) {
	c0 := binary.LittleEndian.Uint16(block[0:])
	c1 := binary.LittleEndian.Uint16(block[2:])
	bits := binary.LittleEndian.Uint32(block[4:])
	var cols [4][3]byte
	bcColors(c0, c1, &cols)
	for i := 0; i < 16; i++ {
		code := int(bits>>(2*uint(i))) & 3
		out[i][0], out[i][1], out[i][2] = cols[code][0], cols[code][1], cols[code][2]
		out[i][3] = 255
		if code == 3 && c0 <= c1 {
			out[i][3] = 0
		}
	}
}

func decodeBC2(block []byte, out *[16][4]byte) {
	c0 := binary.LittleEndian.Uint16(block[8:])
	c1 := binary.LittleEndian.Uint16(block[10:])
	bits := binary.LittleEndian.Uint32(block[12:])
	var cols [4][3]byte
	bcColors(c0, c1, &cols)
	for i := 0; i < 16; i++ {
		code := int(bits>>(2*uint(i))) & 3
		out[i][0], out[i][1], out[i][2] = cols[code][0], cols[code][1], cols[code][2]
		nib := int(block[i/2])
		if i%2 == 0 {
			nib &= 0x0F
		} else {
			nib >>= 4
		}
		out[i][3] = byte(nib * 17)
	}
}

func decodeBC3(block []byte, out *[16][4]byte) {
	a0, a1 := int(block[0]), int(block[1])
	var alpha [8]byte
	alpha[0], alpha[1] = byte(a0), byte(a1)
	if a0 > a1 {
		for i := 0; i < 6; i++ {
			alpha[2+i] = byte(((6-i)*a0 + (1+i)*a1) / 7)
		}
	} else {
		for i := 0; i < 4; i++ {
			alpha[2+i] = byte(((4-i)*a0 + (1+i)*a1) / 5)
		}
		alpha[6] = 0
		alpha[7] = 255
	}
	var acodes uint64
	for i := 0; i < 6; i++ {
		acodes |= uint64(block[2+i]) << (8 * uint(i))
	}
	c0 := binary.LittleEndian.Uint16(block[8:])
	c1 := binary.LittleEndian.Uint16(block[10:])
	bits := binary.LittleEndian.Uint32(block[12:])
	var cols [4][3]byte
	bcColors(c0, c1, &cols)
	for i := 0; i < 16; i++ {
		code := int(bits>>(2*uint(i))) & 3
		out[i][0], out[i][1], out[i][2] = cols[code][0], cols[code][1], cols[code][2]
		out[i][3] = alpha[(acodes>>(3*uint(i)))&7]
	}
}

func decodeBC4(block []byte, out *[16][4]byte) {
	decodeBC4Signed(block, out, false)
}

func decodeBC4Signed(block []byte, out *[16][4]byte, sign bool) {
	a0, a1 := int(block[0]), int(block[1])
	if sign {
		a0 += 128
		a1 += 128
	}
	var alpha [8]byte
	alpha[0], alpha[1] = byte(a0), byte(a1)
	if a0 > a1 {
		for i := 0; i < 6; i++ {
			alpha[2+i] = byte(((6-i)*a0 + (1+i)*a1) / 7)
		}
	} else {
		for i := 0; i < 4; i++ {
			alpha[2+i] = byte(((4-i)*a0 + (1+i)*a1) / 5)
		}
		alpha[6] = 0
		alpha[7] = 255
	}
	var acodes uint64
	for i := 0; i < 6; i++ {
		acodes |= uint64(block[2+i]) << (8 * uint(i))
	}
	for i := 0; i < 16; i++ {
		out[i][0] = alpha[(acodes>>(3*uint(i)))&7]
	}
}

func decodeBC5(block []byte, out *[16][4]byte, sign bool) {
	var r, g [16][4]byte
	decodeBC4Signed(block[:8], &r, sign)
	decodeBC4Signed(block[8:16], &g, sign)
	fill := byte(0)
	if sign {
		fill = 128
	}
	for i := 0; i < 16; i++ {
		out[i][0] = r[i][0]
		out[i][1] = g[i][0]
		out[i][2] = fill
		out[i][3] = fill
	}
}

var _ = strconv.Itoa
