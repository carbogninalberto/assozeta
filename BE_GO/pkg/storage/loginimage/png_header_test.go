package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"testing"
)

func pngChunk(kind string, body []byte) []byte {
	out := make([]byte, 12+len(body))
	binary.BigEndian.PutUint32(out, uint32(len(body)))
	copy(out[4:], kind)
	copy(out[8:], body)
	binary.BigEndian.PutUint32(out[8+len(body):], crc32.ChecksumIEEE(out[4:8+len(body)]))
	return out
}
func pngHeaderOnly(w, h uint32) []byte {
	data := []byte("\x89PNG\r\n\x1a\n")
	body := make([]byte, 13)
	binary.BigEndian.PutUint32(body, w)
	binary.BigEndian.PutUint32(body[4:], h)
	body[8], body[9] = 8, 2
	data = append(data, pngChunk("IHDR", body)...)
	return append(data, []byte{0, 0, 0, 100, 'I', 'D', 'A', 'T'}...)
}
func TestPNGHeaderLazyBoundsAndCancellation(t *testing.T) {
	input := pngHeaderOnly(200, 100)
	h, err := ReadPNGHeader(context.Background(), input, len(input))
	if err != nil || h.Width != 200 || h.Height != 100 || h.Mode != "RGB" || h.HeaderBytes != len(input) || h.DataLength != 100 || h.Frames != 1 {
		t.Fatal("lazy PNG header rejected", h, err)
	}
	if got, err := ReadPNGHeader(context.Background(), input, len(input)-1); got.Width != 0 || !errors.Is(err, ErrWorkingLimit) {
		t.Fatal("PNG header budget ignored", err)
	}
	bad := append([]byte(nil), input...)
	bad[29] ^= 1
	if got, err := ReadPNGHeader(context.Background(), bad, len(bad)); got.Width != 0 || !errors.Is(err, ErrPNGHeader) {
		t.Fatal("invalid header CRC accepted", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := ReadPNGHeader(ctx, input, len(input)); got.Width != 0 || !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled PNG returned header", err)
	}
}

func TestPNGHeaderMetadataOwnershipAndMidReadCancellation(t *testing.T) {
	base := pngHeaderOnly(200, 100)
	text := pngChunk("tEXt", []byte("comment\x00caf\xe8"))
	input := append(append(append([]byte(nil), base[:33]...), text...), base[33:]...)
	header, err := ReadPNGHeader(context.Background(), input, len(input))
	if err != nil {
		t.Fatal(err)
	}
	for i := range input {
		input[i] = 0
	}
	if string(header.Comment) != "cafè" {
		t.Fatal("PNG metadata aliases input")
	}
	// Trailing compressed bytes are outside the consumed-header limit.
	extended := append(append([]byte(nil), base...), make([]byte, 2<<20)...)
	if h, err := ReadPNGHeader(context.Background(), extended, len(base)); err != nil || h.HeaderBytes != len(base) {
		t.Fatal("pixel data consumed by header reader", err)
	}
	payload := make([]byte, 1<<20)
	for i := range payload {
		payload[i] = 0xe9
	}
	input = append(append(append([]byte(nil), base[:33]...), pngChunk("tEXt", append([]byte("comment\x00"), payload...))...), base[33:]...)
	for _, trigger := range []int{2, 8, 50, 100} {
		parent, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: trigger}
		h, err := ReadPNGHeader(ctx, input, len(input))
		cancel()
		if !errors.Is(err, context.Canceled) || h.Width != 0 || h.Comment != nil {
			t.Fatal("cancelled metadata conversion exposed partial header", err)
		}
	}
}

func TestPNGMetadataTextLimitsAndCancellation(t *testing.T) {
	state := pngHeaderState{textMemory: pngTextMemoryLimit - 1}
	text := []byte("note\x00\x00\x00\x00\x00é")
	if err := state.textChunk(context.Background(), "iTXt", text); err != nil {
		t.Fatal("UTF-8 bytes counted instead of codepoints", err)
	}
	if err := state.textChunk(context.Background(), "iTXt", text); !errors.Is(err, ErrPNGHeader) {
		t.Fatal("cumulative text limit ignored", err)
	}
	raw := make([]byte, 100000)
	seed := uint32(42)
	for i := range raw {
		seed ^= seed << 13
		seed ^= seed >> 17
		seed ^= seed << 5
		raw[i] = byte(seed)
	}
	packed := pngCompressed(t, raw)
	parent, cancel := context.WithCancel(context.Background())
	ctx := &cancellingImageContext{Context: parent, cancel: cancel, trigger: 4}
	got, _, err := pngInflateText(ctx, packed)
	cancel()
	if !errors.Is(err, context.Canceled) || got != nil {
		t.Fatal("cancelled text inflation exposed bytes", err)
	}
}
