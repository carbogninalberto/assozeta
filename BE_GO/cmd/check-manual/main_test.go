package main

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"
)

func TestCheckManualArgumentsAndRedaction(t *testing.T) {
	for _, args := range [][]string{nil, {"--root", "private-secret"}, {"--unknown", "private-secret"}, {"--root", "private-secret", "--revision", "x", "--release", "y", "--required", "1"}} {
		var out bytes.Buffer
		if run(args, &out) != 2 || strings.Contains(out.String(), "private-secret") {
			t.Fatal("argument failure was not redacted")
		}
	}
	root := filepath.Join(t.TempDir(), "private-secret")
	for _, required := range []string{"false", "true"} {
		var out bytes.Buffer
		want := 0
		if required == "true" {
			want = 1
		}
		if got := run([]string{"--root", root, "--revision", "", "--release", "", "--required", required}, &out); got != want || strings.Contains(out.String(), root) {
			t.Fatal(got, out.String())
		}
	}
}
