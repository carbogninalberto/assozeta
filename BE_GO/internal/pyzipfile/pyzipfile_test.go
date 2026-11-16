package pyzipfile

import (
	"bytes"
	"compress/gzip"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

type corpus struct {
	Archives map[string]string `json:"archives"`
	Cases    []corpusCase      `json:"cases"`
}

type corpusCase struct {
	Name    string       `json:"name"`
	Archive string       `json:"archive"`
	Expect  corpusExpect `json:"expect"`
}

type corpusExpect struct {
	IsZipfile bool                  `json:"is_zipfile"`
	Open      *corpusOpen           `json:"open"`
	Reads     map[string]corpusRead `json:"reads"`
}

type corpusOpen struct {
	Namelist []string              `json:"namelist"`
	Infos    map[string]corpusInfo `json:"infos"`
	Error    []string              `json:"error"`
}

type corpusInfo struct {
	Filename      string `json:"filename"`
	OrigFilename  string `json:"orig_filename"`
	FileSize      uint64 `json:"file_size"`
	CompressSize  uint64 `json:"compress_size"`
	CompressType  uint16 `json:"compress_type"`
	FlagBits      uint16 `json:"flag_bits"`
	CRC           uint32 `json:"CRC"`
	HeaderOffset  int64  `json:"header_offset"`
	ExternalAttrs uint32 `json:"external_attr"`
}

type corpusRead struct {
	All       corpusResult   `json:"all"`
	OpenError []string       `json:"open_error"`
	Chunks    []corpusResult `json:"chunks"`
}

type corpusResult struct {
	OK    string   `json:"ok"`
	Error []string `json:"error"`
}

func loadCorpus(t *testing.T) *corpus {
	t.Helper()
	file, err := os.Open(filepath.Join("testdata", "corpus.json.gz"))
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	gz, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	var c corpus
	if err := json.NewDecoder(gz).Decode(&c); err != nil {
		t.Fatal(err)
	}
	return &c
}

func matchException(t *testing.T, context string, err error, want []string) {
	t.Helper()
	if len(want) != 2 {
		t.Fatalf("%s: bad expectation %v", context, want)
	}
	var exc *Exception
	if !errors.As(err, &exc) {
		t.Fatalf("%s: got error %v (%T), want %s: %s", context, err, err, want[0], want[1])
	}
	if exc.Type != want[0] || exc.Text != want[1] {
		t.Fatalf("%s: got %s: %q, want %s: %q", context, exc.Type, exc.Text, want[0], want[1])
	}
}

func matchBytes(t *testing.T, context string, got []byte, want string) {
	t.Helper()
	expected, err := hex.DecodeString(want)
	if err != nil {
		t.Fatalf("%s: bad corpus hex: %v", context, err)
	}
	if !bytes.Equal(got, expected) {
		t.Fatalf("%s: got %d bytes %x, want %d bytes %x", context, len(got), got, len(expected), expected)
	}
}

func TestCorpus(t *testing.T) {
	c := loadCorpus(t)
	dir := t.TempDir()
	for _, tc := range c.Cases {
		tc := tc
		t.Run(tc.Name, func(t *testing.T) {
			data, err := base64.StdEncoding.DecodeString(c.Archives[tc.Archive])
			if err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(dir, "case.zip")
			if err := os.WriteFile(path, data, 0o600); err != nil {
				t.Fatal(err)
			}
			gotZip, err := IsZipfile(path)
			if err != nil {
				t.Fatalf("IsZipfile: %v", err)
			}
			if gotZip != tc.Expect.IsZipfile {
				t.Fatalf("IsZipfile = %v, want %v", gotZip, tc.Expect.IsZipfile)
			}

			archive, err := Open(path)
			if tc.Expect.Open == nil {
				t.Fatal("corpus case has no open outcome")
			}
			if len(tc.Expect.Open.Error) != 0 {
				if archive != nil {
					archive.Close()
				}
				matchException(t, "Open", err, tc.Expect.Open.Error)
				return
			}
			if err != nil {
				t.Fatalf("Open: %v", err)
			}
			defer archive.Close()

			names := archive.NameList()
			if len(names) != len(tc.Expect.Open.Namelist) {
				t.Fatalf("namelist = %q, want %q", names, tc.Expect.Open.Namelist)
			}
			for i := range names {
				if names[i] != tc.Expect.Open.Namelist[i] {
					t.Fatalf("namelist[%d] = %q, want %q", i, names[i], tc.Expect.Open.Namelist[i])
				}
			}
			for name, want := range tc.Expect.Open.Infos {
				info, ok := archive.Info(name)
				if !ok {
					t.Fatalf("Info(%q) missing", name)
				}
				if info.Filename != want.Filename || info.OrigFilename != want.OrigFilename ||
					info.FileSize != want.FileSize || info.CompressSize != want.CompressSize ||
					info.CompressType != want.CompressType || info.FlagBits != want.FlagBits ||
					info.CRC != want.CRC || info.HeaderOffset != want.HeaderOffset ||
					info.ExternalAttrs != want.ExternalAttrs {
					t.Fatalf("Info(%q) = %+v, want %+v", name, info, want)
				}
			}

			for name, want := range tc.Expect.Reads {
				data, err := archive.Read(name)
				if len(want.All.Error) != 0 {
					matchException(t, "Read("+name+")", err, want.All.Error)
				} else if err != nil {
					t.Fatalf("Read(%q): %v", name, err)
				} else {
					matchBytes(t, "Read("+name+")", data, want.All.OK)
				}

				member, err := archive.OpenMember(name)
				if len(want.OpenError) != 0 {
					if member != nil {
						member.Close()
					}
					matchException(t, "OpenMember("+name+")", err, want.OpenError)
					continue
				}
				if err != nil {
					t.Fatalf("OpenMember(%q): %v", name, err)
				}
				for i, chunk := range want.Chunks {
					got, err := member.Read(4096)
					if len(chunk.Error) != 0 {
						matchException(t, "chunk", err, chunk.Error)
					} else if err != nil {
						t.Fatalf("OpenMember(%q).Read chunk %d: %v", name, i, err)
					} else {
						matchBytes(t, "chunk", got, chunk.OK)
					}
				}
				member.Close()
			}
		})
	}
}
