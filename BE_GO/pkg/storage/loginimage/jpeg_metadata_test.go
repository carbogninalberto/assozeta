package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
	"testing"
)

func metadataIFD(entries ...struct {
	tag, typ     uint16
	count, value uint32
}) []byte {
	data := make([]byte, 8+2+len(entries)*12+4)
	copy(data, []byte{'I', 'I', 42, 0, 8, 0, 0, 0})
	binary.LittleEndian.PutUint16(data[8:], uint16(len(entries)))
	for i, e := range entries {
		p := data[10+i*12:]
		binary.LittleEndian.PutUint16(p, e.tag)
		binary.LittleEndian.PutUint16(p[2:], e.typ)
		binary.LittleEndian.PutUint32(p[4:], e.count)
		binary.LittleEndian.PutUint32(p[8:], e.value)
	}
	return data
}
func TestJPEGMetadataAdmission(t *testing.T) {
	base := jpegHeaderOnly(17, 13, 3)
	got, err := InspectJPEG(context.Background(), base, 1024)
	if err != nil || got.Format != "JPEG" || got.Frames != 1 || got.Header.Width != 17 {
		t.Fatal("ordinary JPEG rejected", err)
	}
	type entry = struct {
		tag, typ     uint16
		count, value uint32
	}
	exif := metadataIFD(entry{0x128, 3, 1, 2}, entry{0x11a, 1, 1, 72})
	prefix := jpegMarker(225, append([]byte("Exif\x00\x00"), exif...))
	input := append(append([]byte{255, 216}, prefix...), base[2:]...)
	if got, err := InspectJPEG(context.Background(), input, 1024); got.Format != "" || !errors.Is(err, ErrJPEGHeader) {
		t.Fatal("one-byte EXIF resolution accepted", err)
	}
	// A valid JFIF DPI bypasses EXIF DPI interpretation, including malformed EXIF.
	jfif := []byte{'J', 'F', 'I', 'F', 0, 1, 1, 1, 0, 72, 0, 72}
	input = append(append([]byte{255, 216}, jpegMarker(224, jfif)...), input[2:]...)
	if got, err := InspectJPEG(context.Background(), input, 1024); err != nil || got.Format != "JPEG" {
		t.Fatal("JFIF DPI did not bypass EXIF", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := InspectJPEG(ctx, base, 1024); got.Format != "" || !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled inspection returned image", err)
	}
}
func TestJPEGMetadataMalformedMPFAndTruncatedEntries(t *testing.T) {
	base := jpegHeaderOnly(17, 13, 3)
	wrap := func(mpf []byte) []byte {
		return append(append([]byte{255, 216}, jpegMarker(226, append([]byte("MPF\x00"), mpf...))...), base[2:]...)
	}
	if got, err := InspectJPEG(context.Background(), wrap([]byte("bad")), 1024); err != nil || got.Format != "JPEG" {
		t.Fatal("malformed MPF must retain base JPEG", err)
	}
	type entry = struct {
		tag, typ     uint16
		count, value uint32
	}
	// Two records are declared, but the byte-valued entry has just one inline byte.
	mpf := metadataIFD(entry{0xb001, 4, 1, 2}, entry{0xb002, 7, 1, 0})
	if got, err := InspectJPEG(context.Background(), wrap(mpf), 1024); got.Format != "" || !errors.Is(err, ErrJPEGHeader) {
		t.Fatal("truncated MP records accepted", err)
	}
}

func TestJPEGMetadataCancellationInsideDirectoriesAndRecords(t *testing.T) {
	type entry = struct {
		tag, typ     uint16
		count, value uint32
	}
	entries := make([]entry, 1000)
	for i := range entries {
		entries[i] = entry{999, 4, 1, 1}
	}
	directory := metadataIFD(entries...)
	base, cancel := context.WithCancel(context.Background())
	ctx := &cancellingImageContext{Context: base, cancel: cancel, trigger: 500}
	result, err := readJPEGDirectory(ctx, directory)
	cancel()
	if !errors.Is(err, context.Canceled) || result.values != nil {
		t.Fatal("directory cancellation exposed partial tags", err)
	}
	mpf := metadataIFD(entry{0xb001, 4, 1, 1000}, entry{0xb002, 7, 16000, 38})
	mpf = append(mpf, make([]byte, 16000)...)
	input := append(append([]byte{255, 216}, jpegMarker(226, append([]byte("MPF\x00"), mpf...))...), jpegHeaderOnly(17, 13, 3)[2:]...)
	base, cancel = context.WithCancel(context.Background())
	ctx = &cancellingImageContext{Context: base, cancel: cancel, trigger: 500}
	info, err := InspectJPEG(ctx, input, len(input))
	cancel()
	if !errors.Is(err, context.Canceled) || info.Format != "" || info.Header.MPF != nil {
		t.Fatal("MP record cancellation exposed partial image", err)
	}
}
