package pilimage

import "math"

// This file ports Pillow's BC6H and BC7 decoders (src/libImaging/BcnDecode.c,
// decode_bc6_block and decode_bc7_block) and their tables.

// bc7ModeInfo is bc7_mode_info.
type bc7ModeInfo struct {
	ns, pb, rb, isb, cb, ab, epb, spb, ib, ib2 int
}

// bc6ModeInfo is bc6_mode_info.
type bc6ModeInfo struct {
	ns, tr, pb, epb, rb, gb, bb int
}

// generated from src/libImaging/BcnDecode.c (Pillow 13.0.0)
var bc7Modes = [...]bc7ModeInfo{
	{3, 4, 0, 0, 4, 0, 1, 0, 3, 0},
	{2, 6, 0, 0, 6, 0, 0, 1, 3, 0},
	{3, 6, 0, 0, 5, 0, 0, 0, 2, 0},
	{2, 6, 0, 0, 7, 0, 1, 0, 2, 0},
	{1, 0, 2, 1, 5, 6, 0, 0, 2, 3},
	{1, 0, 2, 0, 7, 8, 0, 0, 2, 2},
	{1, 0, 0, 0, 7, 7, 1, 0, 4, 0},
	{2, 6, 0, 0, 5, 5, 1, 0, 2, 0},
}
var bc7SI2 = [...]uint16{0xcccc, 0x8888, 0xeeee, 0xecc8, 0xc880, 0xfeec, 0xfec8, 0xec80, 0xc800, 0xffec, 0xfe80, 0xe800, 0xffe8, 0xff00, 0xfff0, 0xf000, 0xf710, 0x008e, 0x7100, 0x08ce, 0x008c, 0x7310, 0x3100, 0x8cce, 0x088c, 0x3110, 0x6666, 0x366c, 0x17e8, 0x0ff0, 0x718e, 0x399c, 0xaaaa, 0xf0f0, 0x5a5a, 0x33cc, 0x3c3c, 0x55aa, 0x9696, 0xa55a, 0x73ce, 0x13c8, 0x324c, 0x3bdc, 0x6996, 0xc33c, 0x9966, 0x0660, 0x0272, 0x04e4, 0x4e40, 0x2720, 0xc936, 0x936c, 0x39c6, 0x639c, 0x9336, 0x9cc6, 0x817e, 0xe718, 0xccf0, 0x0fcc, 0x7744, 0xee22}
var bc7SI3 = [...]uint32{0xaa685050, 0x6a5a5040, 0x5a5a4200, 0x5450a0a8, 0xa5a50000, 0xa0a05050, 0x5555a0a0, 0x5a5a5050, 0xaa550000, 0xaa555500, 0xaaaa5500, 0x90909090, 0x94949494, 0xa4a4a4a4, 0xa9a59450, 0x2a0a4250, 0xa5945040, 0x0a425054, 0xa5a5a500, 0x55a0a0a0, 0xa8a85454, 0x6a6a4040, 0xa4a45000, 0x1a1a0500, 0x0050a4a4, 0xaaa59090, 0x14696914, 0x69691400, 0xa08585a0, 0xaa821414, 0x50a4a450, 0x6a5a0200, 0xa9a58000, 0x5090a0a8, 0xa8a09050, 0x24242424, 0x00aa5500, 0x24924924, 0x24499224, 0x50a50a50, 0x500aa550, 0xaaaa4444, 0x66660000, 0xa5a0a5a0, 0x50a050a0, 0x69286928, 0x44aaaa44, 0x66666600, 0xaa444444, 0x54a854a8, 0x95809580, 0x96969600, 0xa85454a8, 0x80959580, 0xaa141414, 0x96960000, 0xaaaa1414, 0xa05050a0, 0xa0a5a5a0, 0x96000000, 0x40804080, 0xa9a8a9a8, 0xaaaaaa44, 0x2a4a5254}
var bc7AI0 = [...]uint8{15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 2, 8, 2, 2, 8, 8, 15, 2, 8, 2, 2, 8, 8, 2, 2, 15, 15, 6, 8, 2, 8, 15, 15, 2, 8, 2, 2, 2, 15, 15, 6, 6, 2, 6, 8, 15, 15, 2, 2, 15, 15, 15, 15, 15, 2, 2, 15}
var bc7AI1 = [...]uint8{3, 3, 15, 15, 8, 3, 15, 15, 8, 8, 6, 6, 6, 5, 3, 3, 3, 3, 8, 15, 3, 3, 6, 10, 5, 8, 8, 6, 8, 5, 15, 15, 8, 15, 3, 5, 6, 10, 8, 15, 15, 3, 15, 5, 15, 15, 15, 15, 3, 15, 5, 5, 5, 8, 5, 10, 5, 10, 8, 13, 15, 12, 3, 3}
var bc7AI2 = [...]uint8{15, 8, 8, 3, 15, 15, 3, 8, 15, 15, 15, 15, 15, 15, 15, 8, 15, 8, 15, 3, 15, 8, 15, 8, 3, 15, 6, 10, 15, 15, 10, 8, 15, 3, 15, 10, 10, 8, 9, 10, 6, 15, 8, 15, 3, 6, 6, 8, 15, 3, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 3, 15, 15, 8}
var bc7Weights2 = [...]int{0, 21, 43, 64}
var bc7Weights3 = [...]int{0, 9, 18, 27, 37, 46, 55, 64}
var bc7Weights4 = [...]int{0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64}
var bc6Modes = [...]bc6ModeInfo{
	{2, 1, 5, 10, 5, 5, 5},
	{2, 1, 5, 7, 6, 6, 6},
	{2, 1, 5, 11, 5, 4, 4},
	{2, 1, 5, 11, 4, 5, 4},
	{2, 1, 5, 11, 4, 4, 5},
	{2, 1, 5, 9, 5, 5, 5},
	{2, 1, 5, 8, 6, 5, 5},
	{2, 1, 5, 8, 5, 6, 5},
	{2, 1, 5, 8, 5, 5, 6},
	{2, 0, 5, 6, 6, 6, 6},
	{1, 0, 0, 10, 10, 10, 10},
	{1, 1, 0, 11, 9, 9, 9},
	{1, 1, 0, 12, 8, 8, 8},
	{1, 1, 0, 16, 4, 4, 4},
}
var bc6BitPackings = [][75]uint8{
	{116, 132, 180, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 48, 49, 50, 51, 52, 164, 112, 113, 114, 115, 64, 65, 66, 67, 68, 176, 160, 161, 162, 163, 80, 81, 82, 83, 84, 177, 128, 129, 130, 131, 96, 97, 98, 99, 100, 178, 144, 145, 146, 147, 148, 179},
	{117, 164, 165, 0, 1, 2, 3, 4, 5, 6, 176, 177, 132, 16, 17, 18, 19, 20, 21, 22, 133, 178, 116, 32, 33, 34, 35, 36, 37, 38, 179, 181, 180, 48, 49, 50, 51, 52, 53, 112, 113, 114, 115, 64, 65, 66, 67, 68, 69, 160, 161, 162, 163, 80, 81, 82, 83, 84, 85, 128, 129, 130, 131, 96, 97, 98, 99, 100, 101, 144, 145, 146, 147, 148, 149},
	{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 48, 49, 50, 51, 52, 10, 112, 113, 114, 115, 64, 65, 66, 67, 26, 176, 160, 161, 162, 163, 80, 81, 82, 83, 42, 177, 128, 129, 130, 131, 96, 97, 98, 99, 100, 178, 144, 145, 146, 147, 148, 179},
	{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 48, 49, 50, 51, 10, 164, 112, 113, 114, 115, 64, 65, 66, 67, 68, 26, 160, 161, 162, 163, 80, 81, 82, 83, 42, 177, 128, 129, 130, 131, 96, 97, 98, 99, 176, 178, 144, 145, 146, 147, 116, 179},
	{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 48, 49, 50, 51, 10, 132, 112, 113, 114, 115, 64, 65, 66, 67, 26, 176, 160, 161, 162, 163, 80, 81, 82, 83, 84, 42, 128, 129, 130, 131, 96, 97, 98, 99, 177, 178, 144, 145, 146, 147, 180, 179},
	{0, 1, 2, 3, 4, 5, 6, 7, 8, 132, 16, 17, 18, 19, 20, 21, 22, 23, 24, 116, 32, 33, 34, 35, 36, 37, 38, 39, 40, 180, 48, 49, 50, 51, 52, 164, 112, 113, 114, 115, 64, 65, 66, 67, 68, 176, 160, 161, 162, 163, 80, 81, 82, 83, 84, 177, 128, 129, 130, 131, 96, 97, 98, 99, 100, 178, 144, 145, 146, 147, 148, 179},
	{0, 1, 2, 3, 4, 5, 6, 7, 164, 132, 16, 17, 18, 19, 20, 21, 22, 23, 178, 116, 32, 33, 34, 35, 36, 37, 38, 39, 179, 180, 48, 49, 50, 51, 52, 53, 112, 113, 114, 115, 64, 65, 66, 67, 68, 176, 160, 161, 162, 163, 80, 81, 82, 83, 84, 177, 128, 129, 130, 131, 96, 97, 98, 99, 100, 101, 144, 145, 146, 147, 148, 149},
	{0, 1, 2, 3, 4, 5, 6, 7, 176, 132, 16, 17, 18, 19, 20, 21, 22, 23, 117, 116, 32, 33, 34, 35, 36, 37, 38, 39, 165, 180, 48, 49, 50, 51, 52, 164, 112, 113, 114, 115, 64, 65, 66, 67, 68, 69, 160, 161, 162, 163, 80, 81, 82, 83, 84, 177, 128, 129, 130, 131, 96, 97, 98, 99, 100, 178, 144, 145, 146, 147, 148, 179},
	{0, 1, 2, 3, 4, 5, 6, 7, 177, 132, 16, 17, 18, 19, 20, 21, 22, 23, 133, 116, 32, 33, 34, 35, 36, 37, 38, 39, 181, 180, 48, 49, 50, 51, 52, 164, 112, 113, 114, 115, 64, 65, 66, 67, 68, 176, 160, 161, 162, 163, 80, 81, 82, 83, 84, 85, 128, 129, 130, 131, 96, 97, 98, 99, 100, 178, 144, 145, 146, 147, 148, 179},
	{0, 1, 2, 3, 4, 5, 164, 176, 177, 132, 16, 17, 18, 19, 20, 21, 117, 133, 178, 116, 32, 33, 34, 35, 36, 37, 165, 179, 181, 180, 48, 49, 50, 51, 52, 53, 112, 113, 114, 115, 64, 65, 66, 67, 68, 69, 160, 161, 162, 163, 80, 81, 82, 83, 84, 85, 128, 129, 130, 131, 96, 97, 98, 99, 100, 101, 144, 145, 146, 147, 148, 149},
	{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89},
	{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 48, 49, 50, 51, 52, 53, 54, 55, 56, 10, 64, 65, 66, 67, 68, 69, 70, 71, 72, 26, 80, 81, 82, 83, 84, 85, 86, 87, 88, 42},
	{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 48, 49, 50, 51, 52, 53, 54, 55, 11, 10, 64, 65, 66, 67, 68, 69, 70, 71, 27, 26, 80, 81, 82, 83, 84, 85, 86, 87, 43, 42},
	{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 48, 49, 50, 51, 15, 14, 13, 12, 11, 10, 64, 65, 66, 67, 31, 30, 29, 28, 27, 26, 80, 81, 82, 83, 47, 46, 45, 44, 43, 42},
}

// bc7GetWeights is bc7_get_weights.
func bc7GetWeights(n int) []int {
	switch n {
	case 2:
		return bc7Weights2[:]
	case 3:
		return bc7Weights3[:]
	}
	return bc7Weights4[:]
}

// bc7GetSubset is bc7_get_subset.
func bc7GetSubset(ns, partition, n int) int {
	if ns == 2 {
		return int(1 & (bc7SI2[partition] >> uint(n)))
	}
	if ns == 3 {
		return int(3 & (bc7SI3[partition] >> uint(2*n)))
	}
	return 0
}

// bcnGetBit is get_bit.
func bcnGetBit(src []byte, bit int) uint8 {
	return (src[bit>>3] >> uint(bit&7)) & 1
}

// bcnGetBits is get_bits.
func bcnGetBits(src []byte, bit, count int) uint8 {
	if count == 0 {
		return 0
	}
	by := bit >> 3
	off := bit & 7
	if off+count <= 8 {
		return (src[by] >> uint(off)) & uint8((1<<uint(count))-1)
	}
	x := uint16(src[by]) | uint16(src[by+1])<<8
	return uint8((x >> uint(off)) & uint16((1<<uint(count))-1))
}

// expandQuantized is expand_quantized.
func expandQuantized(v uint8, bits int) uint8 {
	v <<= uint(8 - bits)
	return v | (v >> uint(bits))
}

// bc7Lerp is bc7_lerp.
func bc7Lerp(dst *[4]byte, e *[6][4]byte, s, s0, s1 int) {
	t0 := 64 - s0
	t1 := 64 - s1
	dst[0] = uint8((t0*int(e[s][0]) + s0*int(e[s+1][0]) + 32) >> 6)
	dst[1] = uint8((t0*int(e[s][1]) + s0*int(e[s+1][1]) + 32) >> 6)
	dst[2] = uint8((t0*int(e[s][2]) + s0*int(e[s+1][2]) + 32) >> 6)
	dst[3] = uint8((t1*int(e[s][3]) + s1*int(e[s+1][3]) + 32) >> 6)
}

// decodeBC7Block is decode_bc7_block.
func decodeBC7Block(col *[16][4]byte, src []byte) {
	var endpoints [6][4]byte
	bit := 0
	mode := int(src[0])
	if mode == 0 {
		for i := 0; i < 16; i++ {
			col[i] = [4]byte{0, 0, 0, 255}
		}
		return
	}
	for mode&(1<<uint(bit)) == 0 {
		bit++
	}
	bit++
	mode = bit - 1
	info := &bc7Modes[mode]
	cb := info.cb
	ab := info.ab
	cw := bc7GetWeights(info.ib)
	aw := cw
	if ab != 0 && info.ib2 != 0 {
		aw = bc7GetWeights(info.ib2)
	}
	load := func(n int) uint8 {
		v := bcnGetBits(src, bit, n)
		bit += n
		return v
	}
	partition := int(load(info.pb))
	rotation := int(load(info.rb))
	indexSel := int(load(info.isb))
	numep := info.ns << 1
	for i := 0; i < numep; i++ {
		endpoints[i][0] = load(cb)
	}
	for i := 0; i < numep; i++ {
		endpoints[i][1] = load(cb)
	}
	for i := 0; i < numep; i++ {
		endpoints[i][2] = load(cb)
	}
	for i := 0; i < numep; i++ {
		if ab != 0 {
			endpoints[i][3] = load(ab)
		} else {
			endpoints[i][3] = 255
		}
	}
	assignP := func(v uint8, x *uint8) { *x = *x<<1 | v }
	if info.epb != 0 {
		cb++
		if ab != 0 {
			ab++
		}
		for i := 0; i < numep; i++ {
			val := load(1)
			assignP(val, &endpoints[i][0])
			assignP(val, &endpoints[i][1])
			assignP(val, &endpoints[i][2])
			if ab != 0 {
				assignP(val, &endpoints[i][3])
			}
		}
	}
	if info.spb != 0 {
		cb++
		if ab != 0 {
			ab++
		}
		for i := 0; i < numep; i += 2 {
			val := load(1)
			for j := 0; j < 2; j++ {
				assignP(val, &endpoints[i+j][0])
				assignP(val, &endpoints[i+j][1])
				assignP(val, &endpoints[i+j][2])
				if ab != 0 {
					assignP(val, &endpoints[i+j][3])
				}
			}
		}
	}
	for i := 0; i < numep; i++ {
		for c := 0; c < 3; c++ {
			endpoints[i][c] = expandQuantized(endpoints[i][c], cb)
		}
		if ab != 0 {
			endpoints[i][3] = expandQuantized(endpoints[i][3], ab)
		}
	}
	cibit := bit
	aibit := cibit + 16*info.ib - info.ns
	for i := 0; i < 16; i++ {
		s := bc7GetSubset(info.ns, partition, i) << 1
		ib := info.ib
		if i == 0 {
			ib--
		} else if info.ns == 2 {
			if i == int(bc7AI0[partition]) {
				ib--
			}
		} else if info.ns == 3 {
			if i == int(bc7AI1[partition]) {
				ib--
			} else if i == int(bc7AI2[partition]) {
				ib--
			}
		}
		i0 := int(bcnGetBits(src, cibit, ib))
		cibit += ib
		if ab != 0 && info.ib2 != 0 {
			ib2 := info.ib2
			if ib2 != 0 && i == 0 {
				ib2--
			}
			i1 := int(bcnGetBits(src, aibit, ib2))
			aibit += ib2
			if indexSel != 0 {
				bc7Lerp(&col[i], &endpoints, s, aw[i1], cw[i0])
			} else {
				bc7Lerp(&col[i], &endpoints, s, cw[i0], aw[i1])
			}
		} else {
			bc7Lerp(&col[i], &endpoints, s, cw[i0], cw[i0])
		}
		if rotation == 1 {
			col[i][0], col[i][3] = col[i][3], col[i][0]
		} else if rotation == 2 {
			col[i][1], col[i][3] = col[i][3], col[i][1]
		} else if rotation == 3 {
			col[i][2], col[i][3] = col[i][3], col[i][2]
		}
	}
}

// bc6SignExtend is bc6_sign_extend.
func bc6SignExtend(v *uint16, prec int) {
	x := int(*v)
	if x&(1<<uint(prec-1)) != 0 {
		x |= -(1 << uint(prec))
	}
	*v = uint16(x)
}

// bc6Unquantize is bc6_unquantize.
func bc6Unquantize(v uint16, prec int, sign int) int {
	s := 0
	x := 0
	if sign == 0 {
		x = int(v)
		if prec >= 15 {
			return x
		}
		if x == 0 {
			return 0
		}
		if x == (1<<uint(prec))-1 {
			return 0xffff
		}
		return ((x << 15) + 0x4000) >> uint(prec-1)
	}
	x = int(int16(v))
	if prec >= 16 {
		return x
	}
	if x < 0 {
		s = 1
		x = -x
	}
	if x != 0 {
		if x >= (1<<uint(prec-1))-1 {
			x = 0x7fff
		} else {
			x = ((x << 15) + 0x4000) >> uint(prec-1)
		}
	}
	if s != 0 {
		return -x
	}
	return x
}

// halfToFloat is half_to_float.
func halfToFloat(h uint16) float32 {
	m := math.Float32frombits(0x77800000)
	o := math.Float32frombits(uint32(h&0x7fff) << 13)
	o *= m
	bits := math.Float32bits(o)
	if o >= math.Float32frombits(0x47800000) {
		bits |= 255 << 23
	}
	bits |= uint32(h&0x8000) << 16
	return math.Float32frombits(bits)
}

// bc6Finalize is bc6_finalize.
func bc6Finalize(v, sign int) float32 {
	if sign != 0 {
		if v < 0 {
			v = ((-v) * 31) / 32
			return halfToFloat(uint16(0x8000 | v))
		}
		return halfToFloat(uint16((v * 31) / 32))
	}
	return halfToFloat(uint16((v * 31) / 64))
}

// bc6Clamp is bc6_clamp.
func bc6Clamp(value float32) uint8 {
	if value < 0 {
		return 0
	}
	if value > 1 {
		return 255
	}
	return uint8(value * 255.0)
}

// bc6Lerp is bc6_lerp.
func bc6Lerp(col *[4]byte, e0, e1 *[3]int, s, sign int) {
	t := 64 - s
	r := (e0[0]*t + e1[0]*s) >> 6
	g := (e0[1]*t + e1[1]*s) >> 6
	b := (e0[2]*t + e1[2]*s) >> 6
	col[0] = bc6Clamp(bc6Finalize(r, sign))
	col[1] = bc6Clamp(bc6Finalize(g, sign))
	col[2] = bc6Clamp(bc6Finalize(b, sign))
}

// decodeBC6Block is decode_bc6_block.
func decodeBC6Block(col *[16][4]byte, src []byte, sign int) {
	var endpoints [12]uint16
	var ueps [12]int
	bit := 5
	epbits := 75
	ib := 3
	mode := int(src[0] & 0x1f)
	if mode&3 == 0 || mode&3 == 1 {
		mode &= 3
		bit = 2
	} else if mode&3 == 2 {
		mode = 2 + (mode >> 2)
		epbits = 72
	} else {
		mode = 10 + (mode >> 2)
		epbits = 60
		ib = 4
	}
	if mode >= 14 {
		for i := 0; i < 16; i++ {
			col[i] = [4]byte{}
		}
		return
	}
	info := &bc6Modes[mode]
	cw := bc7GetWeights(ib)
	numep := 6
	if info.ns == 2 {
		numep = 12
	}
	for i := 0; i < epbits; i++ {
		di := int(bc6BitPackings[mode][i])
		dw := di >> 4
		di &= 15
		endpoints[dw] |= uint16(bcnGetBit(src, bit+i)) << uint(di)
	}
	bit += epbits
	partition := int(bcnGetBits(src, bit, info.pb))
	bit += info.pb
	mask := (1 << uint(info.epb)) - 1
	if sign != 0 {
		bc6SignExtend(&endpoints[0], info.epb)
		bc6SignExtend(&endpoints[1], info.epb)
		bc6SignExtend(&endpoints[2], info.epb)
	}
	if sign != 0 || info.tr != 0 {
		for i := 3; i < numep; i += 3 {
			bc6SignExtend(&endpoints[i], info.rb)
			bc6SignExtend(&endpoints[i+1], info.gb)
			bc6SignExtend(&endpoints[i+2], info.bb)
		}
	}
	if info.tr != 0 {
		for i := 3; i < numep; i += 3 {
			endpoints[i] = uint16((int(endpoints[i]) + int(endpoints[0])) & mask)
			endpoints[i+1] = uint16((int(endpoints[i+1]) + int(endpoints[1])) & mask)
			endpoints[i+2] = uint16((int(endpoints[i+2]) + int(endpoints[2])) & mask)
		}
	}
	for i := 0; i < numep; i++ {
		ueps[i] = bc6Unquantize(endpoints[i], info.epb, sign)
	}
	for i := 0; i < 16; i++ {
		s := bc7GetSubset(info.ns, partition, i) * 6
		ib2 := ib
		if i == 0 {
			ib2--
		} else if info.ns == 2 {
			if i == int(bc7AI0[partition]) {
				ib2--
			}
		}
		i0 := int(bcnGetBits(src, bit, ib2))
		bit += ib2
		bc6Lerp(&col[i], (*[3]int)(ueps[s:s+3]), (*[3]int)(ueps[s+3:s+6]), cw[i0], sign)
	}
}
