package instanceprobe

import (
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// TestLoadJSONWideEncodings: json.loads of bytes in UTF-16 and UTF-32
// (detect_encoding, then 'surrogatepass'). Expected results come from
// CPython 3.13's json.loads: "S:" the code points of a string, "V:" the
// repr of another value, "E" a ValueError.
func TestLoadJSONWideEncodings(t *testing.T) {
	cases := []struct{ name, raw, want string }{
		{"utf-16-le-plain-nobom", "22006100620063002200", "S:61,62,63"},
		{"utf-16-le-plain-bom", "fffe22006100620063002200", "S:61,62,63"},
		{"utf-16-le-astral-nobom", "220061003dd880de62002200", "S:61,1f680,62"},
		{"utf-16-le-astral-bom", "fffe220061003dd880de62002200", "S:61,1f680,62"},
		{"utf-16-le-lone-high-nobom", "2200610000d862002200", "S:61,d800,62"},
		{"utf-16-le-lone-high-bom", "fffe2200610000d862002200", "S:61,d800,62"},
		{"utf-16-le-lone-low-nobom", "220000dc2200", "S:dc00"},
		{"utf-16-le-lone-low-bom", "fffe220000dc2200", "S:dc00"},
		{"utf-16-le-high-end-nobom", "22003dd82200", "S:d83d"},
		{"utf-16-le-high-end-bom", "fffe22003dd82200", "S:d83d"},
		{"utf-16-le-feff-nobom", "2200fffe2200", "S:feff"},
		{"utf-16-le-feff-bom", "fffe2200fffe2200", "S:feff"},
		{"utf-16-le-array-nobom", "5b002200e80022002c00200031005d00", "V:['è', 1]"},
		{"utf-16-le-array-bom", "fffe5b002200e80022002c00200031005d00", "V:['è', 1]"},
		{"utf-16-le-ws-nobom", "20002200780022002000", "S:78"},
		{"utf-16-le-ws-bom", "fffe20002200780022002000", "S:78"},
		{"utf-16-be-plain-nobom", "00220061006200630022", "S:61,62,63"},
		{"utf-16-be-plain-bom", "feff00220061006200630022", "S:61,62,63"},
		{"utf-16-be-astral-nobom", "00220061d83dde8000620022", "S:61,1f680,62"},
		{"utf-16-be-astral-bom", "feff00220061d83dde8000620022", "S:61,1f680,62"},
		{"utf-16-be-lone-high-nobom", "00220061d80000620022", "S:61,d800,62"},
		{"utf-16-be-lone-high-bom", "feff00220061d80000620022", "S:61,d800,62"},
		{"utf-16-be-lone-low-nobom", "0022dc000022", "S:dc00"},
		{"utf-16-be-lone-low-bom", "feff0022dc000022", "S:dc00"},
		{"utf-16-be-high-end-nobom", "0022d83d0022", "S:d83d"},
		{"utf-16-be-high-end-bom", "feff0022d83d0022", "S:d83d"},
		{"utf-16-be-feff-nobom", "0022feff0022", "S:feff"},
		{"utf-16-be-feff-bom", "feff0022feff0022", "S:feff"},
		{"utf-16-be-array-nobom", "005b002200e80022002c00200031005d", "V:['è', 1]"},
		{"utf-16-be-array-bom", "feff005b002200e80022002c00200031005d", "V:['è', 1]"},
		{"utf-16-be-ws-nobom", "00200022007800220020", "S:78"},
		{"utf-16-be-ws-bom", "feff00200022007800220020", "S:78"},
		{"utf-32-le-plain-nobom", "2200000061000000620000006300000022000000", "S:61,62,63"},
		{"utf-32-le-plain-bom", "fffe00002200000061000000620000006300000022000000", "S:61,62,63"},
		{"utf-32-le-astral-nobom", "220000006100000080f601006200000022000000", "S:61,1f680,62"},
		{"utf-32-le-astral-bom", "fffe0000220000006100000080f601006200000022000000", "S:61,1f680,62"},
		{"utf-32-le-lone-high-nobom", "220000006100000000d800006200000022000000", "S:61,d800,62"},
		{"utf-32-le-lone-high-bom", "fffe0000220000006100000000d800006200000022000000", "S:61,d800,62"},
		{"utf-32-le-lone-low-nobom", "2200000000dc000022000000", "S:dc00"},
		{"utf-32-le-lone-low-bom", "fffe00002200000000dc000022000000", "S:dc00"},
		{"utf-32-le-high-end-nobom", "220000003dd8000022000000", "S:d83d"},
		{"utf-32-le-high-end-bom", "fffe0000220000003dd8000022000000", "S:d83d"},
		{"utf-32-le-feff-nobom", "22000000fffe000022000000", "S:feff"},
		{"utf-32-le-feff-bom", "fffe000022000000fffe000022000000", "S:feff"},
		{"utf-32-le-array-nobom", "5b00000022000000e8000000220000002c00000020000000310000005d000000", "V:['è', 1]"},
		{"utf-32-le-array-bom", "fffe00005b00000022000000e8000000220000002c00000020000000310000005d000000", "V:['è', 1]"},
		{"utf-32-le-ws-nobom", "2000000022000000780000002200000020000000", "S:78"},
		{"utf-32-le-ws-bom", "fffe00002000000022000000780000002200000020000000", "S:78"},
		{"utf-32-be-plain-nobom", "0000002200000061000000620000006300000022", "S:61,62,63"},
		{"utf-32-be-plain-bom", "0000feff0000002200000061000000620000006300000022", "S:61,62,63"},
		{"utf-32-be-astral-nobom", "00000022000000610001f6800000006200000022", "S:61,1f680,62"},
		{"utf-32-be-astral-bom", "0000feff00000022000000610001f6800000006200000022", "S:61,1f680,62"},
		{"utf-32-be-lone-high-nobom", "00000022000000610000d8000000006200000022", "S:61,d800,62"},
		{"utf-32-be-lone-high-bom", "0000feff00000022000000610000d8000000006200000022", "S:61,d800,62"},
		{"utf-32-be-lone-low-nobom", "000000220000dc0000000022", "S:dc00"},
		{"utf-32-be-lone-low-bom", "0000feff000000220000dc0000000022", "S:dc00"},
		{"utf-32-be-high-end-nobom", "000000220000d83d00000022", "S:d83d"},
		{"utf-32-be-high-end-bom", "0000feff000000220000d83d00000022", "S:d83d"},
		{"utf-32-be-feff-nobom", "000000220000feff00000022", "S:feff"},
		{"utf-32-be-feff-bom", "0000feff000000220000feff00000022", "S:feff"},
		{"utf-32-be-array-nobom", "0000005b00000022000000e8000000220000002c00000020000000310000005d", "V:['è', 1]"},
		{"utf-32-be-array-bom", "0000feff0000005b00000022000000e8000000220000002c00000020000000310000005d", "V:['è', 1]"},
		{"utf-32-be-ws-nobom", "0000002000000022000000780000002200000020", "S:78"},
		{"utf-32-be-ws-bom", "0000feff0000002000000022000000780000002200000020", "S:78"},
		{"utf-16-le-odd", "22006100220078", "E"},
		{"utf-32-le-big", "220000000000110022000000", "E"},
		{"utf-16-le-two", "3100", "V:1"},
		{"utf-16-be-two", "0031", "V:1"},
		{"utf-16-le-raw-high-then-quote", "220000d82200", "S:d800"},
		{"utf-16-le-bom-only", "fffe", "E"},
		{"utf-32-bom-only", "fffe0000", "E"},
		{"utf-16-be-bom-feff", "fefffeff002200780022", "E"},
		{"utf-16-le-nobom-feff-start", "feff220078002200", "E"},
	}
	for _, c := range cases {
		raw, err := hex.DecodeString(c.raw)
		if err != nil {
			t.Fatal(err)
		}
		value, err := LoadJSON(raw)
		got := "E"
		if err == nil {
			switch v := value.(type) {
			case Int:
				got = "V:" + string(v)
			case string:
				points := []string{}
				for _, r := range pystr.Runes(v) {
					points = append(points, fmt.Sprintf("%x", r))
				}
				got = "S:" + strings.Join(points, ",")
			case []any:
				items := []string{}
				for _, item := range v {
					switch typed := item.(type) {
					case string:
						items = append(items, "'"+typed+"'")
					case Int:
						items = append(items, string(typed))
					}
				}
				got = "V:[" + strings.Join(items, ", ") + "]"
			}
		} else if !errors.Is(err, ErrJSON) {
			t.Errorf("%s: %v is not a ValueError", c.name, err)
		}
		if got != c.want {
			t.Errorf("%s: got %s, want %s", c.name, got, c.want)
		}
	}
}
