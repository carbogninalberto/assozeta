package loginimage

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestPythonBase64DataURI(t *testing.T) {
	for _, tc := range []struct {
		input, want string
		bad         bool
	}{
		{"anything,TQ==", "M", false}, {"😀,TWE=", "Ma", false}, {",TWFu", "Man", false},
		{",===TQ==ignored", "M", false}, {",T! Q=\n=after", "M", false}, {",TWE===", "Ma", false},
		{",TWFu====", "Man", false}, {",TQ", "", true}, {",TWE", "", true}, {",A===", "", true},
		{",TQ==é", "", true}, {"missing comma", "", true}, {",", "", false}, {",!%_-$", "", false},
		{",TQ=A=", "M\x00", false},
	} {
		result, err := DecodeDataURI(context.Background(), tc.input, 1024)
		if tc.bad {
			if !errors.Is(err, ErrDataURI) || result != nil {
				t.Fatalf("invalid base64 accepted %q: %v", tc.input, err)
			}
			continue
		}
		if err != nil || string(result) != tc.want {
			t.Fatalf("%q decoded %q want %q: %v", tc.input, result, tc.want, err)
		}
	}
	if data, err := DecodeDataURI(context.Background(), ",TWFu", 2); !errors.Is(err, ErrWorkingLimit) || data != nil {
		t.Fatal("decode budget not enforced", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if data, err := DecodeDataURI(ctx, ",TQ==", 1024); !errors.Is(err, context.Canceled) || data != nil {
		t.Fatal("cancelled decode succeeded", err)
	}
}

func TestCancellationDuringDataURIWorkReturnsNoPartialBuffer(t *testing.T) {
	payload := "opaque," + strings.Repeat("QUJD", 100000)
	for _, trigger := range []int{2, 120} {
		base, cancel := context.WithCancel(context.Background())
		ctx := &cancellingImageContext{Context: base, cancel: cancel, trigger: trigger}
		result, err := DecodeDataURI(ctx, payload, 1024*1024)
		cancel()
		if !errors.Is(err, context.Canceled) || result != nil {
			t.Fatal("partial data URI escaped cancellation", err)
		}
	}
}
