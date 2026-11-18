package pilimage

// This file ports the WebP lossless (VP8L) bitstream decoder that Pillow uses
// through libwebp. Only the lossless format is deterministic enough to port
// exactly; the lossy VP8 path follows divergence X19 (see others.go).

type vp8lBits struct {
	data []byte
	pos  int
}

func (b *vp8lBits) read(n int) (int, bool) {
	v := 0
	for i := 0; i < n; i++ {
		idx := b.pos >> 3
		if idx >= len(b.data) {
			return 0, false
		}
		bit := (b.data[idx] >> uint(b.pos&7)) & 1
		v |= int(bit) << uint(i)
		b.pos++
	}
	return v, true
}

func (b *vp8lBits) mustRead(n int) int {
	v, _ := b.read(n)
	return v
}

// vp8lHuffman is a canonical prefix code decoded from LSB-first bits.
type vp8lHuffman struct {
	// byLen[len] maps reversed canonical code to symbol.
	byLen  map[int]map[uint32]int
	maxLen int
	single int
	has    bool
}

func (h *vp8lHuffman) decode(b *vp8lBits) (int, bool) {
	if h == nil || !h.has {
		return 0, false
	}
	if h.single >= 0 {
		return h.single, true
	}
	var code uint32
	for l := 1; l <= h.maxLen; l++ {
		v, ok := b.read(1)
		if !ok {
			return 0, false
		}
		code |= uint32(v) << uint(l-1)
		if m, ok := h.byLen[l]; ok {
			if sym, ok := m[code]; ok {
				return sym, true
			}
		}
	}
	return 0, false
}

// buildVP8LHuffman builds the decoder table from code lengths.
func buildVP8LHuffman(lengths []int) (*vp8lHuffman, bool) {
	h := &vp8lHuffman{byLen: map[int]map[uint32]int{}, single: -1, has: true}
	count := make([]int, 16)
	maxLen := 0
	total := 0
	for _, l := range lengths {
		if l > 0 {
			count[l]++
			total++
			if l > maxLen {
				maxLen = l
			}
		}
	}
	if total == 0 {
		h.has = false
		return h, true
	}
	if total == 1 {
		for sym, l := range lengths {
			if l > 0 {
				h.single = sym
				return h, true
			}
		}
	}
	h.maxLen = maxLen
	// Canonical code assignment.
	var next [16]int
	code := 0
	for l := 1; l <= 15; l++ {
		code = (code + count[l-1]) << 1
		next[l] = code
	}
	for sym, l := range lengths {
		if l == 0 {
			continue
		}
		c := next[l]
		next[l]++
		rev := reverseBits32(uint32(c), l)
		if h.byLen[l] == nil {
			h.byLen[l] = map[uint32]int{}
		}
		h.byLen[l][rev] = sym
	}
	return h, true
}

func reverseBits32(v uint32, n int) uint32 {
	var out uint32
	for i := 0; i < n; i++ {
		out = out<<1 | (v & 1)
		v >>= 1
	}
	return out
}

var vp8lCodeLengthOrder = [19]int{17, 18, 0, 1, 2, 3, 4, 5, 16, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}

// readVP8LPrefixCode is Section 3.7.2.1.
func readVP8LPrefixCode(b *vp8lBits, alphabet int) (*vp8lHuffman, bool) {
	simple, ok := b.read(1)
	if !ok {
		return nil, false
	}
	lengths := make([]int, alphabet)
	if simple == 1 {
		numSymbols := b.mustRead(1) + 1
		is8 := b.mustRead(1)
		s0 := b.mustRead(1 + 7*is8)
		if s0 >= alphabet {
			return nil, false
		}
		lengths[s0] = 1
		if numSymbols == 2 {
			s1 := b.mustRead(8)
			if s1 >= alphabet {
				return nil, false
			}
			lengths[s1] = 1
		}
		return buildVP8LHuffman(lengths)
	}
	numCodeLengths := 4 + b.mustRead(4)
	var clLengths [19]int
	for i := 0; i < numCodeLengths; i++ {
		clLengths[vp8lCodeLengthOrder[i]] = b.mustRead(3)
	}
	clCode, ok := buildVP8LHuffman(clLengths[:])
	if !ok {
		return nil, false
	}
	entryLimit := alphabet
	if b.mustRead(1) == 1 {
		nbits := 2 + 2*b.mustRead(3)
		entryLimit = 2 + b.mustRead(nbits)
		if entryLimit > alphabet {
			return nil, false
		}
	}
	symbol := 0
	prev := 8
	entries := 0
	for symbol < alphabet && entries < entryLimit {
		s, ok := clCode.decode(b)
		if !ok {
			return nil, false
		}
		entries++
		switch {
		case s < 16:
			lengths[symbol] = s
			if s != 0 {
				prev = s
			}
			symbol++
		case s == 16:
			n := 3 + b.mustRead(2)
			if symbol+n > alphabet {
				return nil, false
			}
			for i := 0; i < n; i++ {
				lengths[symbol] = prev
				symbol++
			}
		case s == 17:
			n := 3 + b.mustRead(3)
			if symbol+n > alphabet {
				return nil, false
			}
			symbol += n
		case s == 18:
			n := 11 + b.mustRead(7)
			if symbol+n > alphabet {
				return nil, false
			}
			symbol += n
		default:
			return nil, false
		}
	}
	return buildVP8LHuffman(lengths)
}

type vp8lGroup struct {
	codes [5]*vp8lHuffman
}

// vp8lDecodeHeaderResult is what the lossless decoder returns.
type vp8lDecodeHeaderResult struct {
	width, height int
	alpha         bool
}

// vp8lParseHeader reads the VP8L image header at data[0] (the 5-byte
// signature and dimensions).
func vp8lParseHeader(data []byte) (vp8lDecodeHeaderResult, bool) {
	if len(data) < 5 || data[0] != 0x2F {
		return vp8lDecodeHeaderResult{}, false
	}
	bits := &vp8lBits{data: data[1:]}
	w := bits.mustRead(14) + 1
	h := bits.mustRead(14) + 1
	alpha := bits.mustRead(1) == 1
	bits.mustRead(3)
	return vp8lDecodeHeaderResult{width: w, height: h, alpha: alpha}, true
}

// decodeVP8L decodes a complete VP8L chunk (starting at the 0x2F signature)
// and returns the ARGB pixels in scan-line order.
func decodeVP8L(data []byte) ([]byte, int, int, bool, error) {
	hdr, ok := vp8lParseHeader(data)
	if !ok {
		return nil, 0, 0, false, osError("could not create decoder object")
	}
	b := &vp8lBits{data: data[1:]}
	b.mustRead(14 + 14 + 1 + 3)

	type transform struct {
		kind int
		bits int
		size int
		data []byte // ARGB image of the transform
	}
	var transforms []transform
	seen := [4]bool{}
	for {
		has := b.mustRead(1)
		if has == 0 {
			break
		}
		kind := b.mustRead(2)
		if kind < 0 || kind > 3 || seen[kind] {
			return nil, 0, 0, false, osError("could not create decoder object")
		}
		seen[kind] = true
		switch kind {
		case 0, 1:
			sizeBits := b.mustRead(3) + 2
			tw := divRoundUp(hdr.width, 1<<uint(sizeBits))
			th := divRoundUp(hdr.height, 1<<uint(sizeBits))
			img, err := vp8lDecodeImage(b, tw, th, false)
			if err != nil {
				return nil, 0, 0, false, err
			}
			transforms = append(transforms, transform{kind: kind, bits: sizeBits, data: img})
		case 2:
			transforms = append(transforms, transform{kind: kind})
		case 3:
			n := b.mustRead(8) + 1
			img, err := vp8lDecodeImage(b, n, 1, false)
			if err != nil {
				return nil, 0, 0, false, err
			}
			widthBits := 0
			switch {
			case n <= 2:
				widthBits = 3
			case n <= 4:
				widthBits = 2
			case n <= 16:
				widthBits = 1
			}
			// The color table is subtraction coded.
			for i := 1; i < n; i++ {
				for c := 0; c < 4; c++ {
					img[4*i+c] = byte(int(img[4*(i-1)+c]) + int(img[4*i+c]))
				}
			}
			transforms = append(transforms, transform{kind: kind, size: n, bits: widthBits, data: img})
		}
		// A color indexing transform reduces the width for the remaining
		// transforms and the main image.
		if kind == 3 {

		}
	}
	width := hdr.width
	for _, t := range transforms {
		if t.kind == 3 {
			width = divRoundUp(width, 1<<uint(t.bits))
		}
	}
	argb, err := vp8lDecodeImage(b, width, hdr.height, true)
	if err != nil {
		return nil, 0, 0, false, err
	}
	// Apply inverse transforms in reverse order.
	for i := len(transforms) - 1; i >= 0; i-- {
		t := transforms[i]
		switch t.kind {
		case 0:
			argb = vp8lInversePredictor(argb, hdr.width, hdr.height, t.data, t.bits)
		case 1:
			argb = vp8lInverseColor(argb, hdr.width, hdr.height, t.data, t.bits)
		case 2:
			vp8lInverseSubtractGreen(argb)
		case 3:
			argb = vp8lInverseColorIndexing(argb, width, hdr.height, hdr.width, t.data, t.size, t.bits)
		}
	}
	return argb, hdr.width, hdr.height, hdr.alpha, nil
}

func divRoundUp(a, b int) int { return (a + b - 1) / b }

// vp8lDecodeImage decodes one entropy-coded image.
func vp8lDecodeImage(b *vp8lBits, width, height int, meta bool) ([]byte, error) {
	cacheBits := 0
	if b.mustRead(1) == 1 {
		cacheBits = b.mustRead(4)
	}
	cacheSize := 0
	if cacheBits > 0 {
		cacheSize = 1 << uint(cacheBits)
	}
	var entropy []byte
	prefixBits := 0
	if meta && b.mustRead(1) == 1 {
		prefixBits = b.mustRead(3) + 2
		pw := divRoundUp(width, 1<<uint(prefixBits))
		ph := divRoundUp(height, 1<<uint(prefixBits))
		img, err := vp8lDecodeImage(b, pw, ph, false)
		if err != nil {
			return nil, err
		}
		entropy = img
	}
	numGroups := 1
	if entropy != nil {
		maxGroup := 0
		for i := 0; i+3 < len(entropy); i += 4 {
			g := (int(entropy[i+2]) << 8) | int(entropy[i+1])
			if g > maxGroup {
				maxGroup = g
			}
		}
		numGroups = maxGroup + 1
	}
	greenAlphabet := 256 + 24 + cacheSize
	groups := make([]vp8lGroup, numGroups)
	for g := 0; g < numGroups; g++ {
		var ok bool
		groups[g].codes[0], ok = readVP8LPrefixCode(b, greenAlphabet)
		if !ok {
			return nil, osError("could not create decoder object")
		}
		groups[g].codes[1], ok = readVP8LPrefixCode(b, 256)
		if !ok {
			return nil, osError("could not create decoder object")
		}
		groups[g].codes[2], ok = readVP8LPrefixCode(b, 256)
		if !ok {
			return nil, osError("could not create decoder object")
		}
		groups[g].codes[3], ok = readVP8LPrefixCode(b, 256)
		if !ok {
			return nil, osError("could not create decoder object")
		}
		groups[g].codes[4], ok = readVP8LPrefixCode(b, 40)
		if !ok {
			return nil, osError("could not create decoder object")
		}
	}
	out := make([]byte, width*height*4)
	cache := make([]uint32, cacheSize)
	prefixWidth := divRoundUp(width, 1<<uint(prefixBits))
	if entropy == nil {
		prefixWidth = 0
	}
	groupAt := func(x, y int) *vp8lGroup {
		if entropy == nil {
			return &groups[0]
		}
		pos := (y>>uint(prefixBits))*prefixWidth + (x >> uint(prefixBits))
		idx := pos * 4
		if idx+3 >= len(entropy) {
			return &groups[0]
		}
		code := (int(entropy[idx+2]) << 8) | int(entropy[idx+1])
		if code >= len(groups) {
			return &groups[0]
		}
		return &groups[code]
	}
	pos := 0
	for pos < width*height {
		x, y := pos%width, pos/width
		grp := groupAt(x, y)
		s, ok := grp.codes[0].decode(b)
		if !ok {
			return nil, osError("could not create decoder object")
		}
		if s < 256 {
			g := s
			r, ok1 := grp.codes[1].decode(b)
			bl, ok2 := grp.codes[2].decode(b)
			a, ok3 := grp.codes[3].decode(b)
			if !ok1 || !ok2 || !ok3 {
				return nil, osError("could not create decoder object")
			}
			c := uint32(a)<<24 | uint32(r)<<16 | uint32(g)<<8 | uint32(bl)
			out[4*pos] = byte(bl)
			out[4*pos+1] = byte(g)
			out[4*pos+2] = byte(r)
			out[4*pos+3] = byte(a)
			if cacheSize > 0 {
				cache[vp8lCacheIndex(c, cacheBits)] = c
				_ = c
			}
			pos++
			continue
		}
		if s < 280 {
			length := vp8lPrefixValue(b, s-256)
			distSym, ok := grp.codes[4].decode(b)
			if !ok {
				return nil, osError("could not create decoder object")
			}
			distCode := vp8lPrefixValue(b, distSym)
			dist := vp8lDistance(distCode, width)
			if dist > pos || dist <= 0 {
				return nil, osError("could not create decoder object")
			}
			for i := 0; i < length && pos < width*height; i++ {
				src := pos - dist
				copy(out[4*pos:4*pos+4], out[4*src:4*src+4])
				if cacheSize > 0 {
					c := uint32(out[4*pos]) | uint32(out[4*pos+1])<<8 | uint32(out[4*pos+2])<<16 | uint32(out[4*pos+3])<<24
					cache[vp8lCacheIndex(c, cacheBits)] = c
				}
				pos++
			}
			continue
		}
		idx := s - 280
		if idx >= cacheSize {
			return nil, osError("could not create decoder object")
		}
		c := cache[idx]
		out[4*pos] = byte(c)
		out[4*pos+1] = byte(c >> 8)
		out[4*pos+2] = byte(c >> 16)
		out[4*pos+3] = byte(c >> 24)
		pos++
	}
	return out, nil
}

func vp8lCacheIndex(c uint32, bits int) int {
	return int((c * 0x1e35a7bd) >> uint(32-bits))
}

// vp8lPrefixValue is the LZ77 prefix code value from Section 3.6.2.2.
func vp8lPrefixValue(b *vp8lBits, prefix int) int {
	if prefix < 4 {
		return prefix + 1
	}
	extra := (prefix - 2) >> 1
	offset := (2 + (prefix & 1)) << uint(extra)
	return offset + b.mustRead(extra) + 1
}

var vp8lDistanceMap = [120][2]int{
	{0, 1}, {1, 0}, {1, 1}, {-1, 1}, {0, 2}, {2, 0}, {1, 2}, {-1, 2},
	{2, 1}, {-2, 1}, {2, 2}, {-2, 2}, {0, 3}, {3, 0}, {1, 3}, {-1, 3},
	{3, 1}, {-3, 1}, {2, 3}, {-2, 3}, {3, 2}, {-3, 2}, {0, 4}, {4, 0},
	{1, 4}, {-1, 4}, {4, 1}, {-4, 1}, {3, 3}, {-3, 3}, {2, 4}, {-2, 4},
	{4, 2}, {-4, 2}, {0, 5}, {3, 4}, {-3, 4}, {4, 3}, {-4, 3}, {5, 0},
	{1, 5}, {-1, 5}, {5, 1}, {-5, 1}, {2, 5}, {-2, 5}, {5, 2}, {-5, 2},
	{4, 4}, {-4, 4}, {3, 5}, {-3, 5}, {5, 3}, {-5, 3}, {0, 6}, {6, 0},
	{1, 6}, {-1, 6}, {6, 1}, {-6, 1}, {2, 6}, {-2, 6}, {6, 2}, {-6, 2},
	{4, 5}, {-4, 5}, {5, 4}, {-5, 4}, {3, 6}, {-3, 6}, {6, 3}, {-6, 3},
	{0, 7}, {7, 0}, {1, 7}, {-1, 7}, {5, 5}, {-5, 5}, {7, 1}, {-7, 1},
	{4, 6}, {-4, 6}, {6, 4}, {-6, 4}, {2, 7}, {-2, 7}, {7, 2}, {-7, 2},
	{3, 7}, {-3, 7}, {7, 3}, {-7, 3}, {5, 6}, {-5, 6}, {6, 5}, {-6, 5},
	{8, 0}, {4, 7}, {-4, 7}, {7, 4}, {-7, 4}, {8, 1}, {8, 2}, {6, 6},
	{-6, 6}, {8, 3}, {5, 7}, {-5, 7}, {7, 5}, {-7, 5}, {8, 4}, {6, 7},
	{-6, 7}, {7, 6}, {-7, 6}, {8, 5}, {7, 7}, {-7, 7}, {8, 6}, {8, 7},
}

func vp8lDistance(code, width int) int {
	if code > 120 {
		return code - 120
	}
	xy := vp8lDistanceMap[code-1]
	d := xy[0] + xy[1]*width
	if d < 1 {
		d = 1
	}
	return d
}

// --- inverse transforms ----------------------------------------------------

func vp8lChannel(argb uint32, shift uint) int { return int((argb >> shift) & 0xFF) }

func vp8lPack(a, r, g, bl int) uint32 {
	return uint32(a&0xFF)<<24 | uint32(r&0xFF)<<16 | uint32(g&0xFF)<<8 | uint32(bl&0xFF)
}

func vp8lInverseSubtractGreen(argb []byte) {
	for i := 0; i+3 < len(argb); i += 4 {
		g := int(argb[i+1])
		argb[i] = byte((int(argb[i]) + g) & 0xFF)
		argb[i+2] = byte((int(argb[i+2]) + g) & 0xFF)
	}
}

func vp8lInverseColorIndexing(argb []byte, reducedWidth, height, outWidth int, palette []byte, colors, widthBits int) []byte {
	out := make([]byte, outWidth*height*4)
	if widthBits == 0 {
		for y := 0; y < height; y++ {
			for x := 0; x < outWidth && x < reducedWidth; x++ {
				green := int(argb[4*(y*reducedWidth+x)+1])
				placeIndexed(out, palette, colors, x, y, outWidth, green)
			}
		}
		return out
	}
	perPixel := 8 >> uint(widthBits)
	mask := (1 << uint(perPixel)) - 1
	for y := 0; y < height; y++ {
		for x := 0; x < outWidth; x++ {
			srcX := x >> uint(widthBits)
			if srcX >= reducedWidth {
				continue
			}
			green := int(argb[4*(y*reducedWidth+srcX)+1])
			idx := (green >> uint((x&((1<<uint(widthBits))-1))*perPixel)) & mask
			placeIndexed(out, palette, colors, x, y, outWidth, idx)
		}
	}
	return out
}

func placeIndexed(out, palette []byte, colors, x, y, ow, idx int) {
	var c uint32
	if idx < colors {
		c = uint32(palette[4*idx]) | uint32(palette[4*idx+1])<<8 | uint32(palette[4*idx+2])<<16 | uint32(palette[4*idx+3])<<24
	}
	i := (y*ow + x) * 4
	out[i] = byte(c)
	out[i+1] = byte(c >> 8)
	out[i+2] = byte(c >> 16)
	out[i+3] = byte(c >> 24)
}

func vp8lAvg2(a, b int) int { return (a + b) / 2 }

func vp8lClamp(v int) int {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return v
}

func vp8lSelect(l, t, tl uint32) uint32 {
	pl := absInt(vp8lChannel(l, 24)+vp8lChannel(t, 24)-vp8lChannel(tl, 24)-vp8lChannel(l, 24)) +
		absInt(vp8lChannel(l, 16)+vp8lChannel(t, 16)-vp8lChannel(tl, 16)-vp8lChannel(l, 16)) +
		absInt(vp8lChannel(l, 8)+vp8lChannel(t, 8)-vp8lChannel(tl, 8)-vp8lChannel(l, 8)) +
		absInt(vp8lChannel(l, 0)+vp8lChannel(t, 0)-vp8lChannel(tl, 0)-vp8lChannel(l, 0))
	pt := absInt(vp8lChannel(l, 24)+vp8lChannel(t, 24)-vp8lChannel(tl, 24)-vp8lChannel(t, 24)) +
		absInt(vp8lChannel(l, 16)+vp8lChannel(t, 16)-vp8lChannel(tl, 16)-vp8lChannel(t, 16)) +
		absInt(vp8lChannel(l, 8)+vp8lChannel(t, 8)-vp8lChannel(tl, 8)-vp8lChannel(t, 8)) +
		absInt(vp8lChannel(l, 0)+vp8lChannel(t, 0)-vp8lChannel(tl, 0)-vp8lChannel(t, 0))
	if pl < pt {
		return l
	}
	return t
}

func absInt(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

func vp8lPredict(mode int, l, t, tr, tl uint32) uint32 {
	switch mode {
	case 0:
		return 0xFF000000
	case 1:
		return l
	case 2:
		return t
	case 3:
		return tr
	case 4:
		return tl
	case 5:
		return vp8lPack(vp8lAvg2(vp8lAvg2(vp8lChannel(l, 24), vp8lChannel(tr, 24)), vp8lChannel(t, 24)),
			vp8lAvg2(vp8lAvg2(vp8lChannel(l, 16), vp8lChannel(tr, 16)), vp8lChannel(t, 16)),
			vp8lAvg2(vp8lAvg2(vp8lChannel(l, 8), vp8lChannel(tr, 8)), vp8lChannel(t, 8)),
			vp8lAvg2(vp8lAvg2(vp8lChannel(l, 0), vp8lChannel(tr, 0)), vp8lChannel(t, 0)))
	case 6:
		return vp8lPack(vp8lAvg2(vp8lChannel(l, 24), vp8lChannel(tl, 24)),
			vp8lAvg2(vp8lChannel(l, 16), vp8lChannel(tl, 16)),
			vp8lAvg2(vp8lChannel(l, 8), vp8lChannel(tl, 8)),
			vp8lAvg2(vp8lChannel(l, 0), vp8lChannel(tl, 0)))
	case 7:
		return vp8lPack(vp8lAvg2(vp8lChannel(l, 24), vp8lChannel(t, 24)),
			vp8lAvg2(vp8lChannel(l, 16), vp8lChannel(t, 16)),
			vp8lAvg2(vp8lChannel(l, 8), vp8lChannel(t, 8)),
			vp8lAvg2(vp8lChannel(l, 0), vp8lChannel(t, 0)))
	case 8:
		return vp8lPack(vp8lAvg2(vp8lChannel(tl, 24), vp8lChannel(t, 24)),
			vp8lAvg2(vp8lChannel(tl, 16), vp8lChannel(t, 16)),
			vp8lAvg2(vp8lChannel(tl, 8), vp8lChannel(t, 8)),
			vp8lAvg2(vp8lChannel(tl, 0), vp8lChannel(t, 0)))
	case 9:
		return vp8lPack(vp8lAvg2(vp8lChannel(t, 24), vp8lChannel(tr, 24)),
			vp8lAvg2(vp8lChannel(t, 16), vp8lChannel(tr, 16)),
			vp8lAvg2(vp8lChannel(t, 8), vp8lChannel(tr, 8)),
			vp8lAvg2(vp8lChannel(t, 0), vp8lChannel(tr, 0)))
	case 10:
		return vp8lPack(vp8lAvg2(vp8lAvg2(vp8lChannel(l, 24), vp8lChannel(tl, 24)), vp8lAvg2(vp8lChannel(t, 24), vp8lChannel(tr, 24))),
			vp8lAvg2(vp8lAvg2(vp8lChannel(l, 16), vp8lChannel(tl, 16)), vp8lAvg2(vp8lChannel(t, 16), vp8lChannel(tr, 16))),
			vp8lAvg2(vp8lAvg2(vp8lChannel(l, 8), vp8lChannel(tl, 8)), vp8lAvg2(vp8lChannel(t, 8), vp8lChannel(tr, 8))),
			vp8lAvg2(vp8lAvg2(vp8lChannel(l, 0), vp8lChannel(tl, 0)), vp8lAvg2(vp8lChannel(t, 0), vp8lChannel(tr, 0))))
	case 11:
		return vp8lSelect(l, t, tl)
	case 12:
		return vp8lPack(vp8lClamp(vp8lChannel(l, 24)+vp8lChannel(t, 24)-vp8lChannel(tl, 24)),
			vp8lClamp(vp8lChannel(l, 16)+vp8lChannel(t, 16)-vp8lChannel(tl, 16)),
			vp8lClamp(vp8lChannel(l, 8)+vp8lChannel(t, 8)-vp8lChannel(tl, 8)),
			vp8lClamp(vp8lChannel(l, 0)+vp8lChannel(t, 0)-vp8lChannel(tl, 0)))
	case 13:
		return vp8lPack(
			vp8lClamp(vp8lAvg2(vp8lChannel(l, 24), vp8lChannel(t, 24))+(vp8lAvg2(vp8lChannel(l, 24), vp8lChannel(t, 24))-vp8lChannel(tl, 24))/2),
			vp8lClamp(vp8lAvg2(vp8lChannel(l, 16), vp8lChannel(t, 16))+(vp8lAvg2(vp8lChannel(l, 16), vp8lChannel(t, 16))-vp8lChannel(tl, 16))/2),
			vp8lClamp(vp8lAvg2(vp8lChannel(l, 8), vp8lChannel(t, 8))+(vp8lAvg2(vp8lChannel(l, 8), vp8lChannel(t, 8))-vp8lChannel(tl, 8))/2),
			vp8lClamp(vp8lAvg2(vp8lChannel(l, 0), vp8lChannel(t, 0))+(vp8lAvg2(vp8lChannel(l, 0), vp8lChannel(t, 0))-vp8lChannel(tl, 0))/2))
	}
	return 0xFF000000
}

func vp8lInversePredictor(argb []byte, width, height int, modeImg []byte, sizeBits int) []byte {
	tw := divRoundUp(width, 1<<uint(sizeBits))
	out := make([]byte, len(argb))
	for y := 0; y < height; y++ {
		for x := 0; x < width; x++ {
			i := 4 * (y*width + x)
			res := uint32(argb[i+3])<<24 | uint32(argb[i+2])<<16 | uint32(argb[i+1])<<8 | uint32(argb[i])
			var pred uint32
			switch {
			case x == 0 && y == 0:
				pred = 0xFF000000
			case y == 0:
				pred = uint32(out[4*(i/4-1)+3])<<24 | uint32(out[4*(i/4-1)+2])<<16 | uint32(out[4*(i/4-1)+1])<<8 | uint32(out[4*(i/4-1)])
			case x == 0:
				above := 4 * ((y-1)*width + 0)
				pred = uint32(out[above+3])<<24 | uint32(out[above+2])<<16 | uint32(out[above+1])<<8 | uint32(out[above])
			default:
				modeIdx := (y>>uint(sizeBits))*tw + (x >> uint(sizeBits))
				mode := 0
				if 4*modeIdx+1 < len(modeImg) {
					mode = int(modeImg[4*modeIdx+1])
				}
				li := 4 * (y*width + x - 1)
				ti := 4 * ((y-1)*width + x)
				tli := 4 * ((y-1)*width + x - 1)
				l := uint32(out[li+3])<<24 | uint32(out[li+2])<<16 | uint32(out[li+1])<<8 | uint32(out[li])
				t := uint32(out[ti+3])<<24 | uint32(out[ti+2])<<16 | uint32(out[ti+1])<<8 | uint32(out[ti])
				tl := uint32(out[tli+3])<<24 | uint32(out[tli+2])<<16 | uint32(out[tli+1])<<8 | uint32(out[tli])
				var tr uint32
				if x+1 < width {
					tri := 4 * ((y-1)*width + x + 1)
					tr = uint32(out[tri+3])<<24 | uint32(out[tri+2])<<16 | uint32(out[tri+1])<<8 | uint32(out[tri])
				} else {
					tri := 4 * (y * width)
					tr = uint32(out[tri+3])<<24 | uint32(out[tri+2])<<16 | uint32(out[tri+1])<<8 | uint32(out[tri])
				}
				pred = vp8lPredict(mode, l, t, tr, tl)
			}
			out[i+3] = byte(vp8lChannel(res, 24) + vp8lChannel(pred, 24))
			out[i+2] = byte(vp8lChannel(res, 16) + vp8lChannel(pred, 16))
			out[i+1] = byte(vp8lChannel(res, 8) + vp8lChannel(pred, 8))
			out[i] = byte(vp8lChannel(res, 0) + vp8lChannel(pred, 0))
		}
	}
	return out
}

func vp8lInverseColor(argb []byte, width, height int, cteImg []byte, sizeBits int) []byte {
	tw := divRoundUp(width, 1<<uint(sizeBits))
	out := make([]byte, len(argb))
	copy(out, argb)
	for y := 0; y < height; y++ {
		for x := 0; x < width; x++ {
			i := 4 * (y*width + x)
			bi := (y>>uint(sizeBits))*tw + (x >> uint(sizeBits))
			var g2r, g2b, r2b int
			if 4*bi+3 < len(cteImg) {
				r2b = int(cteImg[4*bi])
				g2b = int(cteImg[4*bi+1])
				g2r = int(cteImg[4*bi+2])
			}
			g := int(argb[i+1])
			r := int(argb[i+2])
			bl := int(argb[i])
			tr := int8(r2b)
			tg := int8(g2b)
			tg2 := int8(g2r)
			tmpR := r + (int(tg2)*int(int8(g)))>>5
			tmpB := bl + (int(tg)*int(int8(g)))>>5
			tmpB += (int(tr) * int(int8(tmpR&0xFF))) >> 5
			out[i+2] = byte(tmpR & 0xFF)
			out[i] = byte(tmpB & 0xFF)
		}
	}
	return out
}
