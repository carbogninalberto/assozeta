package repository

import (
	"encoding/binary"
	"testing"
)

func medicalPickle(protocol byte, value string) []byte {
	body := append([]byte{0x8c, byte(len(value))}, []byte(value)...)
	body = append(body, 0x94, '.')
	frame := make([]byte, 11)
	frame[0] = 0x80
	frame[1] = protocol
	frame[2] = 0x95
	binary.LittleEndian.PutUint64(frame[3:], uint64(len(body)))
	return append(frame, body...)
}
func TestMedicalUploadClosedCodec(t *testing.T) {
	id := "00000000-0000-4000-8000-000000000080"
	for _, protocol := range []byte{4, 5} {
		encoded := medicalPickle(protocol, id)
		got, err := decodeMedicalUpload(encoded)
		if err != nil || got != id {
			t.Fatal("UUID-string protocol mismatch")
		}
		for end := 0; end < len(encoded); end++ {
			if _, err := decodeMedicalUpload(encoded[:end]); err == nil {
				t.Fatal("truncated grant accepted")
			}
		}
		if _, err := decodeMedicalUpload(append(encoded, '.')); err == nil {
			t.Fatal("trailing opcode accepted")
		}
	}
	for _, malicious := range [][]byte{[]byte("cos\nsystem\n(S'echo never'\ntR."), {0x80, 4, 'R', '.'}, {0x80, 4, 0x93, '.'}} {
		if _, err := decodeMedicalUpload(malicious); err == nil {
			t.Fatal("executable pickle opcode accepted")
		}
	}
}
func FuzzMedicalUploadClosedCodec(f *testing.F) {
	f.Add(medicalPickle(4, "00000000-0000-4000-8000-000000000080"))
	f.Add([]byte{0x80, 4, 'R', '.'})
	f.Fuzz(func(t *testing.T, data []byte) { _, _ = decodeMedicalUpload(data) })
}
