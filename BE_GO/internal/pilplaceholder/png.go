package pilplaceholder

import (
	"bytes"
	"encoding/binary"
	"hash/crc32"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pilplaceholder/zlibclassic"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pilplaceholder/zlibng"
)

// maxBlock is ImageFile.MAXBLOCK.
const maxBlock = 65536

// encodePNG mirrors PngImagePlugin._save for an "RGB" image saved with no
// options: signature, IHDR, the zlib stream cut into one IDAT chunk per
// encoder.encode(bufsize) call, IEND. row fills the 3*width bytes of line y.
func encodePNG(width, height int, row func(y int, rgb []byte), z Zlib) []byte {
	out := []byte("\x89PNG\r\n\x1a\n")
	var ihdr [13]byte
	binary.BigEndian.PutUint32(ihdr[0:], uint32(width))
	binary.BigEndian.PutUint32(ihdr[4:], uint32(height))
	ihdr[8] = 8 // bit depth
	ihdr[9] = 2 // colour type: truecolour
	out = appendChunk(out, "IHDR", ihdr[:])

	// ImageFile._save: bufsize = max(MAXBLOCK, bufsize, im.size[0] * 4). The
	// IDAT wrapper has no fileno, so every encode() result becomes one chunk.
	enc := newZipEncoder(width, height, row)
	bufsize := max(maxBlock, width*4)
	if z == ZlibClassic {
		// Classic zlib's stream does not depend on how deflate is called, so
		// each encode() call simply returns the next bufsize bytes of it; the
		// last call always has at least the Adler-32 trailer left to return.
		stream := enc.classicStream()
		for len(stream) > bufsize {
			out = appendChunk(out, "IDAT", stream[:bufsize])
			stream = stream[bufsize:]
		}
		out = appendChunk(out, "IDAT", stream)
		return appendChunk(out, "IEND", nil)
	}
	buf := make([]byte, bufsize)
	for {
		n, done := enc.encode(buf)
		out = appendChunk(out, "IDAT", buf[:n])
		if done {
			break
		}
	}
	return appendChunk(out, "IEND", nil)
}

// appendChunk mirrors putchunk.
func appendChunk(out []byte, cid string, data []byte) []byte {
	out = binary.BigEndian.AppendUint32(out, uint32(len(data)))
	start := len(out)
	out = append(out, cid...)
	out = append(out, data...)
	return binary.BigEndian.AppendUint32(out, crc32.ChecksumIEEE(out[start:]))
}

// zipEncoder mirrors ImagingZipEncode (ZipEncode.c) in ZIP_PNG mode without
// the optimize option: each line is filtered with whichever of None, Up, Sub
// and Paeth has the smallest sum of absolute values (tried in that order,
// strict improvement only) and handed to deflate on its own with Z_NO_FLUSH.
// zlib-ng's output depends on that call pattern, including where a call stops
// because the output buffer is full, so encode keeps the state machine as is.
type zipEncoder struct {
	z      *zlibng.Deflater
	row    func(y int, rgb []byte)
	state  int
	y      int
	ysize  int
	bytes  int
	buffer []byte

	previous, prior, up, paeth []byte
}

const pngBPP = 3 // (bits + 7) / 8 for 24-bit RGB

func newZipEncoder(width, height int, row func(y int, rgb []byte)) *zipEncoder {
	n := width * pngBPP
	e := &zipEncoder{z: zlibng.NewDeflater(), row: row, state: 1, ysize: height, bytes: n}
	e.buffer = make([]byte, n+1)
	e.previous = make([]byte, n+1)
	e.prior = make([]byte, n+1)
	e.up = make([]byte, n+1)
	e.paeth = make([]byte, n+1)
	e.prior[0] = 1
	e.up[0] = 2
	e.paeth[0] = 4
	return e
}

func filterCost(v byte) int {
	if v < 128 {
		return int(v)
	}
	return 256 - int(v)
}

// filter mirrors the filter selection of ImagingZipEncode and returns the
// buffer to compress.
func (e *zipEncoder) filter() []byte {
	buffer, previous := e.buffer, e.previous
	output := buffer
	n := e.bytes

	// 0. No filter.
	sum := 0
	for i := 1; i <= n; i++ {
		sum += filterCost(buffer[i])
	}

	// 2. Up.
	if sum > 0 {
		s := 0
		for i := 1; i <= n; i++ {
			v := buffer[i] - previous[i]
			e.up[i] = v
			s += filterCost(v)
		}
		if s < sum {
			output = e.up
			sum = s
		}
	}

	// 1. Prior.
	if sum > 0 {
		s := 0
		i := 1
		for ; i <= pngBPP && i <= n; i++ {
			v := buffer[i]
			e.prior[i] = v
			s += filterCost(v)
		}
		for ; i <= n; i++ {
			v := buffer[i] - buffer[i-pngBPP]
			e.prior[i] = v
			s += filterCost(v)
		}
		if s < sum {
			output = e.prior
			sum = s
		}
	}

	// 4. Paeth.
	if sum > 0 {
		s := 0
		i := 1
		for ; i <= pngBPP && i <= n; i++ {
			v := buffer[i] - previous[i]
			e.paeth[i] = v
			s += filterCost(v)
		}
		for ; i <= n; i++ {
			a := int(buffer[i-pngBPP])
			b := int(previous[i])
			c := int(previous[i-pngBPP])
			pa := abs(b - c)
			pb := abs(a - c)
			pc := abs(a + b - 2*c)
			pred := c
			if pa <= pb && pa <= pc {
				pred = a
			} else if pb <= pc {
				pred = b
			}
			v := buffer[i] - byte(pred)
			e.paeth[i] = v
			s += filterCost(v)
		}
		if s < sum {
			output = e.paeth
		}
	}
	return output
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

// classicStream returns the whole zlib stream as classic zlib writes it.
func (e *zipEncoder) classicStream() []byte {
	var stream bytes.Buffer
	w := zlibclassic.Compress(&stream)
	for ; e.y < e.ysize; e.y++ {
		e.row(e.y, e.buffer[1:])
		_, _ = w.Write(e.filter()) // a bytes.Buffer never fails
		e.buffer, e.previous = e.previous, e.buffer
	}
	_ = w.Close()
	return stream.Bytes()
}

// encode mirrors one ImagingZipEncode call with a buffer of len(buf) bytes. It
// returns the number of bytes produced and whether the stream is complete.
func (e *zipEncoder) encode(buf []byte) (int, bool) {
	pos := 0
	if e.z.AvailIn() > 0 {
		// Data from the previous round: deflate it first.
		n, _ := e.z.Deflate(buf, zlibng.NoFlush)
		pos += n
	}

	if e.state == 1 {
		for pos < len(buf) {
			if e.y >= e.ysize {
				// End of image; now flush compressor buffers.
				e.state = 2
				break
			}
			e.row(e.y, e.buffer[1:])
			e.y++

			e.z.SetInput(e.filter())
			n, _ := e.z.Deflate(buf[pos:], zlibng.NoFlush)
			pos += n

			e.buffer, e.previous = e.previous, e.buffer
		}
		if pos == len(buf) {
			return pos, false // buffer full
		}
	}

	for pos < len(buf) {
		n, ret := e.z.Deflate(buf[pos:], zlibng.Finish)
		pos += n
		if ret == zlibng.StreamEnd {
			return pos, true
		}
	}
	return pos, false
}
