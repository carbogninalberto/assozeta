package repository

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math/big"
	"os"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
)

// TestPickleReleasesGolden replays testdata/release_catalog_pickles.json
// (TESTS/adapters/go/instance_outbound_golden.py): the feed pages through
// instanceprobe's json.loads and normalize_release, then the pickle must be
// byte-identical to Python's pickle.dumps of Django's own catalog, and must
// decode back to the same releases.
func TestPickleReleasesGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/release_catalog_pickles.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Protocol int `json:"protocol"`
		Cases    []struct {
			Name   string   `json:"name"`
			Pages  []string `json:"pages"`
			SHA256 string   `json:"pickle_sha256"`
			Length int      `json:"pickle_length"`
			Head   string   `json:"pickle_head_hex"`
		} `json:"cases"`
	}
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if golden.Protocol != 4 || len(golden.Cases) < 10 {
		t.Fatalf("unexpected golden file: protocol %d, %d cases", golden.Protocol, len(golden.Cases))
	}
	for _, tc := range golden.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			var set instanceprobe.ReleaseSet
			for _, page := range tc.Pages {
				value, err := instanceprobe.LoadJSON([]byte(page))
				if err != nil {
					t.Fatal(err)
				}
				if err = set.Add(value.([]any)); err != nil {
					t.Fatal(err)
				}
			}
			releases := set.Releases()
			data := PickleReleases(releases)
			head, _ := hex.DecodeString(tc.Head)
			if len(data) != tc.Length || string(data[:min(len(data), len(head))]) != string(head) {
				t.Fatalf("pickle differs: length %d want %d\n got %x\nwant %s", len(data), tc.Length, data[:min(len(data), 256)], tc.Head[:min(len(tc.Head), 512)])
			}
			if sum := sha256.Sum256(data); hex.EncodeToString(sum[:]) != tc.SHA256 {
				t.Fatalf("pickle digest differs (length %d)", len(data))
			}
			decoded, err := unpickleCatalog(data)
			if err != nil {
				t.Fatal(err)
			}
			if err == nil {
				items := decoded.([]any)
				if len(items) != len(releases) {
					t.Fatalf("decoded %d releases, want %d", len(items), len(releases))
				}
				for i, item := range items {
					back, ok := cachedRelease(item)
					back.NameIsTag = releases[i].NameIsTag
					if !ok || back.ID != releases[i].ID || back.Notes != releases[i].Notes || back.URL != releases[i].URL {
						t.Fatalf("release %d does not round-trip", i)
					}
				}
			}
		})
	}
}

func TestLongBytes(t *testing.T) {
	for value, want := range map[int64]string{1 << 31: "0000008000", -(1 << 31) - 1: "ffffff7fff", 1 << 40: "000000000001", -1 << 40: "000000000000ff"[2:]} {
		if got := hex.EncodeToString(longBytes(bigInt(value))); got != want {
			t.Errorf("longBytes(%d) = %s, want %s", value, got, want)
		}
	}
}

func bigInt(v int64) *big.Int { return big.NewInt(v) }
