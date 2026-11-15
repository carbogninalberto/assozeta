package xlsxexport

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The digit accumulation of precise_xstrtod is fused in the macOS arm64 pandas
// wheel and plain in the Linux ones: Go follows the platform it runs on, and a
// value where the two differ elsewhere is an application-state error.
func TestPreciseXstrtodFollowsPlatform(t *testing.T) {
	const text = "3.14159265358979323846" // golden case eu-long-digits
	fused, _, _, _ := preciseXstrtodWith(text, true)
	plain, _, _, _ := preciseXstrtodWith(text, false)
	if fused == plain {
		t.Fatalf("%s parses the same both ways: pick another value", text)
	}
	saved := xstrtodFMA
	t.Cleanup(func() { xstrtodFMA = saved })
	for mode, want := range map[string]float64{"fused": fused, "plain": plain} {
		xstrtodFMA = mode
		number, _, ok, err := pandasFloatify(text)
		if err != nil || !ok || number != want {
			t.Fatalf("%s: %v %v %v, want %v", mode, number, ok, err, want)
		}
	}
	xstrtodFMA = ""
	if _, _, _, err := pandasFloatify(text); !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("unknown architecture: %v", err)
	}
	// The Linux images answer the plain value (pandas 2.2.3 on x86_64 and
	// aarch64: 0x400921fb54442d17).
	if plain != 0x1.921fb54442d17p+01 {
		t.Fatalf("plain %x", plain)
	}
	// A value both accumulations agree on never depends on the platform.
	if number, _, ok, err := pandasFloatify("12.5"); err != nil || !ok || number != 12.5 {
		t.Fatalf("12.5: %v %v %v", number, ok, err)
	}
}
