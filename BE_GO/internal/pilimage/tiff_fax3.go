package pilimage

// This file ports libtiff 4.7.2's CCITT decoders (tif_fax3.c): Fax3Decode1D,
// Fax3Decode2D, Fax4Decode and Fax3DecodeRLE with _TIFFFax3fillruns, used for
// TIFF compressions 2, 3, 4 and 32771. The state machine and its tables are
// libtiff's, so the decoded rows are the same.

const (
	faxModeNoRTC     = 0x0001 // no RTC at end of data
	faxModeNoEOL     = 0x0002 // no EOL code at end of row
	faxModeByteAlign = 0x0004 // byte align row
	faxModeWordAlign = 0x0008 // word align row
	faxModeClassF    = faxModeNoRTC
)

// States of a fax table entry.
const (
	faxSNull = iota
	faxSPass
	faxSHoriz
	faxSV0
	faxSVR
	faxSVL
	faxSExt
	faxSTermW
	faxSTermB
	faxSMakeUpW
	faxSMakeUpB
	faxSMakeUp
	faxSEOL
)

var faxFillMasks = [9]byte{0x00, 0x80, 0xc0, 0xe0, 0xf0, 0xf8, 0xfc, 0xfe, 0xff}

// faxReverseBits is libtiff's _TIFFBitRevTable: the bits of each byte in
// reverse order.
var faxReverseBits = func() (t [256]byte) {
	for i := 0; i < 256; i++ {
		v := byte(i)
		var r byte
		for b := 0; b < 8; b++ {
			r = r<<1 | v&1
			v >>= 1
		}
		t[i] = r
	}
	return t
}()

// faxIdentityBits is libtiff's _TIFFNoBitRevTable.
var faxIdentityBits = func() (t [256]byte) {
	for i := range t {
		t[i] = byte(i)
	}
	return t
}()

// faxDecoder is Fax3CodecState plus the locals of one decode call.
type faxDecoder struct {
	src               []byte
	cp, ep            int
	bitAcc            uint32
	bitsAvail         int
	eolcnt            int
	bitmap            *[256]byte
	rowpixels         int
	rowbytes          int
	nruns             int
	mode              int
	line              int
	curruns, refruns  []uint32
	a0, runLength, pa int
	b1, pb            int
	out               []byte
	row               int
}

// faxDecode decodes one strip or tile of a TIFF fax compression into the
// packed rows libtiff would return. rows is the number of rows the strip
// holds; t4options is Group3Options, fillorder the FillOrder tag.
func faxDecode(src []byte, width, rows, compression int, t4options, fillorder int64) ([]byte, bool) {
	rowbytes := (width + 7) / 8
	if rowbytes <= 0 || rows < 0 {
		return nil, false
	}
	needsRefLine := compression == 4 || (compression == 3 && t4options&1 != 0)
	nruns := ((width + 1 + 31) / 32) * 32
	if needsRefLine {
		nruns *= 2
	}
	if nruns < 2 {
		nruns = 32
	}
	d := &faxDecoder{
		src:       src,
		ep:        len(src),
		rowpixels: width,
		rowbytes:  rowbytes,
		nruns:     nruns,
		curruns:   make([]uint32, nruns),
		refruns:   make([]uint32, nruns),
		out:       make([]byte, rowbytes*rows),
	}
	d.refruns[0] = uint32(width)
	d.refruns[1] = 0
	if fillorder == 2 {
		d.bitmap = &faxIdentityBits
	} else {
		d.bitmap = &faxReverseBits
	}
	switch compression {
	case 2: // COMPRESSION_CCITTRLE
		d.mode = faxModeNoRTC | faxModeNoEOL | faxModeByteAlign
	case 32771: // COMPRESSION_CCITTRLEW
		d.mode = faxModeNoRTC | faxModeNoEOL | faxModeWordAlign
	case 3: // COMPRESSION_CCITTFAX3
		d.mode = faxModeClassF
	case 4: // COMPRESSION_CCITTFAX4
		d.mode = faxModeNoRTC
	default:
		return nil, false
	}
	occ := rowbytes * rows
	var ok bool
	switch {
	case compression == 2 || compression == 32771:
		ok = d.decodeRLE(occ)
	case compression == 4:
		ok = d.decode4(occ)
	case t4options&1 != 0:
		ok = d.decode2D(occ)
	default:
		ok = d.decode1D(occ)
	}
	if !ok {
		return nil, false
	}
	return d.out, true
}

func (d *faxDecoder) getBits(n int) int { return int(d.bitAcc & ((1 << uint(n)) - 1)) }

func (d *faxDecoder) clrBits(n int) {
	d.bitsAvail -= n
	d.bitAcc >>= uint(n)
}

// needBits8 is the NeedBits8 macro.
func (d *faxDecoder) needBits8(n int) bool {
	if d.bitsAvail < n {
		if d.cp >= d.ep {
			if d.bitsAvail == 0 {
				return false
			}
			d.bitsAvail = n // pad with zeros
		} else {
			d.bitAcc |= uint32(d.bitmap[d.src[d.cp]]) << uint(d.bitsAvail)
			d.cp++
			d.bitsAvail += 8
		}
	}
	return true
}

// needBits16 is the NeedBits16 macro.
func (d *faxDecoder) needBits16(n int) bool {
	if d.bitsAvail < n {
		if d.cp >= d.ep {
			if d.bitsAvail == 0 {
				return false
			}
			d.bitsAvail = n // pad with zeros
		} else {
			d.bitAcc |= uint32(d.bitmap[d.src[d.cp]]) << uint(d.bitsAvail)
			d.cp++
			if d.bitsAvail += 8; d.bitsAvail < n {
				if d.cp >= d.ep {
					d.bitsAvail = n // pad with zeros
				} else {
					d.bitAcc |= uint32(d.bitmap[d.src[d.cp]]) << uint(d.bitsAvail)
					d.cp++
					d.bitsAvail += 8
				}
			}
		}
	}
	return true
}

// lookup8 is the LOOKUP8 macro; ok is false at the eoflab branch.
func (d *faxDecoder) lookup8(tab *[]faxTabEnt, wid int) (faxTabEnt, bool) {
	if !d.needBits8(wid) {
		return faxTabEnt{}, false
	}
	e := (*tab)[d.getBits(wid)]
	d.clrBits(int(e.width))
	return e, true
}

// lookup16 is the LOOKUP16 macro.
func (d *faxDecoder) lookup16(tab *[]faxTabEnt, wid int) (faxTabEnt, bool) {
	if !d.needBits16(wid) {
		return faxTabEnt{}, false
	}
	e := (*tab)[d.getBits(wid)]
	d.clrBits(int(e.width))
	return e, true
}

// setValue is the SETVALUE macro: append a run to the current row.
func (d *faxDecoder) setValue(x int) bool {
	if d.pa >= d.nruns {
		return false
	}
	d.curruns[d.pa] = uint32(d.runLength + x)
	d.pa++
	d.a0 += x
	d.runLength = 0
	return true
}

// cleanupRuns is the CLEANUP_RUNS macro.
func (d *faxDecoder) cleanupRuns() bool {
	if d.runLength != 0 {
		if !d.setValue(0) {
			return false
		}
	}
	if d.a0 != d.rowpixels {
		for d.a0 > d.rowpixels && d.pa > 0 {
			d.pa--
			d.a0 -= int(d.curruns[d.pa])
		}
		if d.a0 < d.rowpixels {
			if d.a0 < 0 {
				d.a0 = 0
			}
			if d.pa%2 != 0 {
				if !d.setValue(0) {
					return false
				}
			}
			if !d.setValue(d.rowpixels - d.a0) {
				return false
			}
		} else if d.a0 > d.rowpixels {
			if !d.setValue(d.rowpixels) || !d.setValue(0) {
				return false
			}
		}
	}
	return true
}

// checkB1 is the CHECK_b1 macro.
func (d *faxDecoder) checkB1() bool {
	if d.pa != 0 {
		for d.b1 <= d.a0 && d.b1 < d.rowpixels {
			if d.pb+1 >= d.nruns {
				return false
			}
			d.b1 += int(d.refruns[d.pb] + d.refruns[d.pb+1])
			d.pb += 2
		}
	}
	return true
}

// fillRow is the fax fill callback call: _TIFFFax3fillruns into the current
// output row.
func (d *faxDecoder) fillRow() {
	if d.row*d.rowbytes >= len(d.out) {
		return
	}
	faxFillRuns(d.out[d.row*d.rowbytes:(d.row+1)*d.rowbytes], d.curruns[:d.pa], d.rowpixels)
	d.row++
}

// faxFillRuns is _TIFFFax3fillruns: set the bits of one packed row from the
// white/black run lengths.
func faxFillRuns(buf []byte, runs []uint32, lastx int) {
	if len(runs)%2 != 0 {
		runs = append(runs, 0)
	}
	x := 0
	for i := 0; i+1 < len(runs); i += 2 {
		run := int(runs[i])
		if x+run > lastx || run > lastx {
			run = lastx - x
			runs[i] = uint32(run)
		}
		if run > 0 {
			// White run: clear bits.
			cp := x >> 3
			bx := x & 7
			if run > 8-bx {
				if bx != 0 {
					buf[cp] &= byte(0xff << uint(8-bx))
					cp++
					run -= 8 - bx
				}
				n := run >> 3
				for j := 0; j < n; j++ {
					buf[cp] = 0
					cp++
				}
				run &= 7
				if run != 0 {
					buf[cp] &= byte(0xff >> uint(run))
				}
			} else {
				buf[cp] &= ^(faxFillMasks[run] >> uint(bx))
			}
			x += int(runs[i])
		}
		run = int(runs[i+1])
		if x+run > lastx || run > lastx {
			run = lastx - x
			runs[i+1] = uint32(run)
		}
		if run > 0 {
			// Black run: set bits.
			cp := x >> 3
			bx := x & 7
			if run > 8-bx {
				if bx != 0 {
					buf[cp] |= byte(0xff >> uint(bx))
					cp++
					run -= 8 - bx
				}
				n := run >> 3
				for j := 0; j < n; j++ {
					buf[cp] = 0xff
					cp++
				}
				run &= 7
				if run != 0 {
					buf[cp] = byte(int(buf[cp]) | (0xff00 >> uint(run)))
				}
			} else {
				buf[cp] |= faxFillMasks[run] >> uint(bx)
			}
			x += int(runs[i+1])
		}
	}
}

// expand1D is the EXPAND1D macro of libtiff. It returns one of the
// expandResult codes and leaves the runs ready for fillRow.
func (d *faxDecoder) expand1D() int {
	for {
		// White runs.
		for {
			e, ok := d.lookup16(&faxWhiteTableArr, 12)
			if !ok {
				if !d.cleanupRuns() {
					return expandOverflow
				}
				return expandEOF
			}
			switch e.state {
			case faxSEOL:
				d.eolcnt = 1
				if !d.cleanupRuns() {
					return expandOverflow
				}
				return expandDone
			case faxSTermW:
				if !d.setValue(int(e.param)) {
					return expandOverflow
				}
				goto whiteDone
			case faxSMakeUpW, faxSMakeUp:
				d.a0 += int(e.param)
				d.runLength += int(e.param)
			default:
				if !d.cleanupRuns() {
					return expandOverflow
				}
				return expandDone
			}
		}
	whiteDone:
		if d.a0 >= d.rowpixels {
			if !d.cleanupRuns() {
				return expandOverflow
			}
			return expandDone
		}
		// Black runs.
		for {
			e, ok := d.lookup16(&faxBlackTableArr, 13)
			if !ok {
				if !d.cleanupRuns() {
					return expandOverflow
				}
				return expandEOF
			}
			switch e.state {
			case faxSEOL:
				d.eolcnt = 1
				if !d.cleanupRuns() {
					return expandOverflow
				}
				return expandDone
			case faxSTermB:
				if !d.setValue(int(e.param)) {
					return expandOverflow
				}
				goto blackDone
			case faxSMakeUpB, faxSMakeUp:
				d.a0 += int(e.param)
				d.runLength += int(e.param)
			default:
				if !d.cleanupRuns() {
					return expandOverflow
				}
				return expandDone
			}
		}
	blackDone:
		if d.a0 >= d.rowpixels {
			if !d.cleanupRuns() {
				return expandOverflow
			}
			return expandDone
		}
		if d.pa >= 2 && d.curruns[d.pa-1] == 0 && d.curruns[d.pa-2] == 0 {
			d.pa -= 2
		}
	}
}

// Result codes of the row expanders.
const (
	expandDone     = 0 // row decoded (a bad code word also ends it)
	expandEOF      = 1 // premature EOF: cleanup already ran, caller fills and fails
	expandOverflow = 2 // run array overflow, which libtiff reports as an error
)

// expand2D is the EXPAND2D macro of libtiff.
func (d *faxDecoder) expand2D() int {
	for d.a0 < d.rowpixels {
		if d.pa >= d.nruns {
			return expandOverflow
		}
		e, ok := d.lookup8(&faxMainTableArr, 7)
		if !ok {
			d.cleanupRuns()
			return expandEOF
		}
		switch e.state {
		case faxSPass:
			if !d.checkB1() {
				return expandOverflow
			}
			if d.pb+1 >= d.nruns {
				return expandOverflow
			}
			d.b1 += int(d.refruns[d.pb])
			d.pb++
			d.runLength += d.b1 - d.a0
			d.a0 = d.b1
			d.b1 += int(d.refruns[d.pb])
			d.pb++
		case faxSHoriz:
			if d.pa%2 != 0 {
				// Black first.
				for {
					e, ok := d.lookup16(&faxBlackTableArr, 13)
					if !ok {
						d.cleanupRuns()
						return expandEOF
					}
					if e.state == faxSTermB {
						if !d.setValue(int(e.param)) {
							return expandOverflow
						}
						break
					}
					if e.state != faxSMakeUpB && e.state != faxSMakeUp {
						if !d.cleanupRuns() {
							return expandOverflow
						}
						return expandDone
					}
					d.a0 += int(e.param)
					d.runLength += int(e.param)
				}
				for {
					e, ok := d.lookup16(&faxWhiteTableArr, 12)
					if !ok {
						d.cleanupRuns()
						return expandEOF
					}
					if e.state == faxSTermW {
						if !d.setValue(int(e.param)) {
							return expandOverflow
						}
						break
					}
					if e.state != faxSMakeUpW && e.state != faxSMakeUp {
						if !d.cleanupRuns() {
							return expandOverflow
						}
						return expandDone
					}
					d.a0 += int(e.param)
					d.runLength += int(e.param)
				}
			} else {
				// White first.
				for {
					e, ok := d.lookup16(&faxWhiteTableArr, 12)
					if !ok {
						d.cleanupRuns()
						return expandEOF
					}
					if e.state == faxSTermW {
						if !d.setValue(int(e.param)) {
							return expandOverflow
						}
						break
					}
					if e.state != faxSMakeUpW && e.state != faxSMakeUp {
						if !d.cleanupRuns() {
							return expandOverflow
						}
						return expandDone
					}
					d.a0 += int(e.param)
					d.runLength += int(e.param)
				}
				for {
					e, ok := d.lookup16(&faxBlackTableArr, 13)
					if !ok {
						d.cleanupRuns()
						return expandEOF
					}
					if e.state == faxSTermB {
						if !d.setValue(int(e.param)) {
							return expandOverflow
						}
						break
					}
					if e.state != faxSMakeUpB && e.state != faxSMakeUp {
						if !d.cleanupRuns() {
							return expandOverflow
						}
						return expandDone
					}
					d.a0 += int(e.param)
					d.runLength += int(e.param)
				}
			}
			if !d.checkB1() {
				return expandOverflow
			}
		case faxSV0:
			if !d.checkB1() {
				return expandOverflow
			}
			if !d.setValue(d.b1 - d.a0) {
				return expandOverflow
			}
			if d.pb >= d.nruns {
				return expandOverflow
			}
			d.b1 += int(d.refruns[d.pb])
			d.pb++
		case faxSVR:
			if !d.checkB1() {
				return expandOverflow
			}
			if !d.setValue(d.b1 - d.a0 + int(e.param)) {
				return expandOverflow
			}
			if d.pb >= d.nruns {
				return expandOverflow
			}
			d.b1 += int(d.refruns[d.pb])
			d.pb++
		case faxSVL:
			if !d.checkB1() {
				return expandOverflow
			}
			if d.b1 < d.a0+int(e.param) {
				if !d.cleanupRuns() {
					return expandOverflow
				}
				return expandDone
			}
			if !d.setValue(d.b1 - d.a0 - int(e.param)) {
				return expandOverflow
			}
			d.pb--
			if d.pb < 0 || d.pb >= d.nruns {
				return expandOverflow
			}
			d.b1 -= int(d.refruns[d.pb])
		case faxSExt:
			if d.pa >= d.nruns {
				return expandOverflow
			}
			d.curruns[d.pa] = uint32(d.rowpixels - d.a0)
			d.pa++
			if !d.cleanupRuns() {
				return expandOverflow
			}
			return expandDone
		case faxSEOL:
			if d.pa >= d.nruns {
				return expandOverflow
			}
			d.curruns[d.pa] = uint32(d.rowpixels - d.a0)
			d.pa++
			if !d.needBits8(4) {
				d.cleanupRuns()
				return expandEOF
			}
			d.clrBits(4)
			d.eolcnt = 1
			if !d.cleanupRuns() {
				return expandOverflow
			}
			return expandDone
		default:
			if !d.cleanupRuns() {
				return expandOverflow
			}
			return expandDone
		}
	}
	if d.runLength != 0 {
		if d.runLength+d.a0 < d.rowpixels {
			if !d.needBits8(1) {
				d.cleanupRuns()
				return expandEOF
			}
			if d.getBits(1) == 0 {
				if !d.cleanupRuns() {
					return expandOverflow
				}
				return expandDone
			}
			d.clrBits(1)
		}
		if !d.setValue(0) {
			return expandOverflow
		}
	}
	if !d.cleanupRuns() {
		return expandOverflow
	}
	return expandDone
}

// syncEOL is the SYNC_EOL macro. It returns 0 when an EOL was consumed, 1 at
// the eoflab branch and 2 at the noEOLFound branch.
func (d *faxDecoder) syncEOL() int {
	if d.mode&faxModeNoEOL == 0 {
		if d.eolcnt == 0 {
			for {
				if !d.needBits16(11) {
					return 1
				}
				if d.getBits(11) == 0 {
					break
				}
				d.clrBits(1)
			}
		}
		for {
			if !d.needBits8(8) {
				return 2
			}
			if d.getBits(8) != 0 {
				break
			}
			d.clrBits(8)
		}
		for d.getBits(1) == 0 {
			d.clrBits(1)
		}
		d.clrBits(1)
		d.eolcnt = 0
	}
	return 0
}

// faxSnapshot is the decoder state CACHE_STATE/UNCACHE_STATE preserve.
type faxSnapshot struct {
	bitAcc, bitsAvail, eolcnt, cp int
}

func (d *faxDecoder) snapshot() faxSnapshot {
	return faxSnapshot{bitAcc: int(d.bitAcc), bitsAvail: d.bitsAvail, eolcnt: d.eolcnt, cp: d.cp}
}

func (d *faxDecoder) restore(s faxSnapshot) {
	d.bitAcc = uint32(s.bitAcc)
	d.bitsAvail = s.bitsAvail
	d.eolcnt = s.eolcnt
	d.cp = s.cp
}

// decode1D is Fax3Decode1D.
func (d *faxDecoder) decode1D(occ int) bool {
	if occ%d.rowbytes != 0 {
		return false
	}
retry1D:
	save := d.snapshot()
	for occ > 0 {
		d.a0, d.runLength, d.pa = 0, 0, 0
		switch d.syncEOL() {
		case 1:
			if !d.cleanupRuns() {
				return false
			}
			d.fillRow()
			return false
		case 2:
			d.mode |= faxModeNoEOL
			d.restore(save)
			goto retry1D
		}
		st := d.expand1D()
		if st == expandEOF {
			d.fillRow()
			return false
		}
		if st == expandOverflow {
			return false
		}
		d.fillRow()
		occ -= d.rowbytes
		d.line++
	}
	return true
}

// decode2D is Fax3Decode2D.
func (d *faxDecoder) decode2D(occ int) bool {
	if occ%d.rowbytes != 0 {
		return false
	}
retry2D:
	save := d.snapshot()
	for occ > 0 {
		d.a0, d.runLength, d.pa = 0, 0, 0
		switch d.syncEOL() {
		case 1:
			if !d.cleanupRuns() {
				return false
			}
			d.fillRow()
			return false
		case 2:
			d.mode |= faxModeNoEOL
			d.restore(save)
			goto retry2D
		}
		if !d.needBits8(1) {
			d.cleanupRuns()
			d.fillRow()
			return false
		}
		is1D := d.getBits(1) != 0
		d.clrBits(1)
		d.pb = 1
		d.b1 = int(d.refruns[0])
		var st int
		if is1D {
			st = d.expand1D()
		} else {
			st = d.expand2D()
		}
		if st == expandEOF {
			d.fillRow()
			return false
		}
		if st == expandOverflow {
			return false
		}
		d.fillRow()
		if d.pa < d.nruns {
			if !d.setValue(0) {
				return false
			}
		}
		d.curruns, d.refruns = d.refruns, d.curruns
		occ -= d.rowbytes
		d.line++
	}
	return true
}

// decode4 is Fax4Decode.
func (d *faxDecoder) decode4(occ int) bool {
	if occ%d.rowbytes != 0 {
		return false
	}
	start := d.line
	for occ > 0 {
		d.a0, d.runLength, d.pa = 0, 0, 0
		d.pb = 1
		d.b1 = int(d.refruns[0])
		st := d.expand2D()
		if st == expandOverflow {
			return false
		}
		if st == expandEOF || d.eolcnt != 0 {
			return d.fax4End(occ, start)
		}
		if (d.rowpixels+7)>>3 > occ {
			return false
		}
		d.fillRow()
		if !d.setValue(0) {
			return false
		}
		d.curruns, d.refruns = d.refruns, d.curruns
		occ -= d.rowbytes
		d.line++
	}
	return true
}

// fax4End is Fax4Decode's EOFG4 branch: consume the 13-bit EOFB if present,
// fill the current row, and succeed only when at least one row was decoded.
func (d *faxDecoder) fax4End(occ, start int) bool {
	if d.needBits16(13) {
		d.clrBits(13)
	}
	if (d.rowpixels+7)>>3 > occ {
		return false
	}
	d.fillRow()
	return d.line != start
}

// decodeRLE is Fax3DecodeRLE (compressions 2 and 32771).
func (d *faxDecoder) decodeRLE(occ int) bool {
	if occ%d.rowbytes != 0 {
		return false
	}
	for occ > 0 {
		d.a0, d.runLength, d.pa = 0, 0, 0
		st := d.expand1D()
		if st == expandEOF {
			d.fillRow()
			return false
		}
		if st == expandOverflow {
			return false
		}
		d.fillRow()
		if d.mode&faxModeByteAlign != 0 {
			n := d.bitsAvail - (d.bitsAvail & ^7)
			d.clrBits(n)
		} else if d.mode&faxModeWordAlign != 0 {
			n := d.bitsAvail - (d.bitsAvail & ^15)
			d.clrBits(n)
			if d.bitsAvail == 0 && d.cp%2 != 0 {
				d.cp++
			}
		}
		occ -= d.rowbytes
		d.line++
	}
	return true
}
