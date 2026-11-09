package pyzlib

import (
	"bytes"
	"compress/flate"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"testing"
	"time"
)

type goldenCase struct {
	Name          string       `json:"name"`
	Recipe        goldenRecipe `json:"recipe"`
	InputLen      int          `json:"input_len"`
	InputSHA256   string       `json:"input_sha256"`
	OutputLen     int          `json:"output_len"`
	OutputSHA256  string       `json:"output_sha256"`
	OutputHex     string       `json:"output_hex"`
	Chunks        []int        `json:"chunks"`
	ChunkedSHA256 string       `json:"chunked_output_sha256"`
}

func loadGolden(t *testing.T) []goldenCase {
	t.Helper()
	raw, err := os.ReadFile("testdata/zlib_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Cases []goldenCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	if len(doc.Cases) == 0 {
		t.Fatal("no golden cases")
	}
	return doc.Cases
}

func sha(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

func deflateChunked(t *testing.T, data []byte, chunks []int) []byte {
	t.Helper()
	var b bytes.Buffer
	w := Compress(&b)
	for pos, i := 0, 0; pos < len(data); i++ {
		n := min(chunks[i%len(chunks)], len(data)-pos)
		if _, err := w.Write(data[pos : pos+n]); err != nil {
			t.Fatal(err)
		}
		pos += n
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}

func inflate(t *testing.T, comp []byte) []byte {
	t.Helper()
	r := flate.NewReader(bytes.NewReader(comp))
	defer r.Close()
	out, err := io.ReadAll(r)
	if err != nil {
		t.Fatalf("inflate: %v", err)
	}
	return out
}

func TestGolden(t *testing.T) {
	for _, gc := range loadGolden(t) {
		t.Run(gc.Name, func(t *testing.T) {
			t.Parallel()
			data, err := buildInput(gc.Recipe)
			if err != nil {
				t.Fatal(err)
			}
			if len(data) != gc.InputLen || sha(data) != gc.InputSHA256 {
				t.Fatalf("input generator mismatch: len %d want %d", len(data), gc.InputLen)
			}
			got := Deflate(data)
			if gc.OutputHex != "" && hex.EncodeToString(got) != gc.OutputHex {
				t.Fatalf("output mismatch:\n got %x\nwant %s", got, gc.OutputHex)
			}
			if len(got) != gc.OutputLen || sha(got) != gc.OutputSHA256 {
				t.Fatalf("output mismatch: len %d want %d", len(got), gc.OutputLen)
			}
			if gc.ChunkedSHA256 != "" && gc.ChunkedSHA256 != gc.OutputSHA256 {
				t.Errorf("CPython output depends on chunking %v", gc.Chunks)
			}
			chunkings := [][]int{{1 << 20}, {8192}, {4093, 1, 65537, 13}}
			if gc.Chunks != nil {
				chunkings = append(chunkings, gc.Chunks)
			}
			if len(data) <= 5000 {
				chunkings = append(chunkings, []int{1})
			}
			for _, ch := range chunkings {
				if c := deflateChunked(t, data, ch); !bytes.Equal(c, got) {
					t.Fatalf("output depends on write chunking %v", ch)
				}
			}
			if !bytes.Equal(inflate(t, got), data) {
				t.Fatal("round trip through compress/flate failed")
			}
		})
	}
}

func TestWriteAfterClose(t *testing.T) {
	var b bytes.Buffer
	w := Compress(&b)
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := w.Write([]byte("x")); err == nil {
		t.Fatal("expected error writing after Close")
	}
	if !bytes.Equal(b.Bytes(), []byte{0x03, 0x00}) {
		t.Fatalf("empty stream = %x, want 0300", b.Bytes())
	}
}

func TestThroughput10MB(t *testing.T) {
	if testing.Short() {
		t.Skip("short")
	}
	data := genMixed(&splitMix64{state: 77}, 10<<20)
	start := time.Now()
	out := Deflate(data)
	el := time.Since(start)
	t.Logf("10 MiB mixed -> %d bytes in %v (%.1f MB/s)", len(out), el, float64(len(data))/1e6/el.Seconds())
	text := genJSON(&splitMix64{state: 78}, 10<<20)
	start = time.Now()
	out = Deflate(text)
	el = time.Since(start)
	t.Logf("10 MiB json  -> %d bytes in %v (%.1f MB/s)", len(out), el, float64(len(text))/1e6/el.Seconds())
}

func BenchmarkDeflate10MB(b *testing.B) {
	data := genMixed(&splitMix64{state: 77}, 10<<20)
	b.SetBytes(int64(len(data)))
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		Deflate(data)
	}
}
