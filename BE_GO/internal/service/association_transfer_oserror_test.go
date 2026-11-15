package service

import (
	"context"
	"errors"
	"os"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"unicode/utf16"
)

func TestPythonOSErrorText(t *testing.T) {
	const path = "/tmp/tmpabcdefgh/xè.zip"
	cases := []struct {
		name string
		err  error
		want string
		ok   bool
	}{
		{"open", &os.PathError{Op: "open", Path: path, Err: syscall.ENAMETOOLONG},
			"[Errno " + strconv.Itoa(int(syscall.ENAMETOOLONG)) + "] File name too long: '" + path + "'", true},
		{"write", &os.PathError{Op: "write", Path: path, Err: syscall.ENOSPC},
			"[Errno " + strconv.Itoa(int(syscall.ENOSPC)) + "] No space left on device", true},
		{"close", &os.PathError{Op: "close", Path: path, Err: syscall.ENOSPC},
			"[Errno " + strconv.Itoa(int(syscall.ENOSPC)) + "] No space left on device", true},
		{"plain", errors.New("boom"), "", false},
		{"other-op", &os.PathError{Op: "stat", Path: path, Err: syscall.ENOENT}, "", false},
	}
	for _, c := range cases {
		got, ok := pythonOSErrorText(c.err)
		if ok != c.ok || got != c.want {
			t.Errorf("%s: pythonOSErrorText = (%q, %v), want (%q, %v)", c.name, got, ok, c.want, c.ok)
		}
	}
}

// TestImportValidateNameOverTheLimit checks that a name under 255 characters
// but over the file system's name limit (255 bytes on Linux, 255 UTF-16 units
// on macOS) fails open() with ENAMETOOLONG, answered as Django's 500.
func TestImportValidateNameOverTheLimit(t *testing.T) {
	name := strings.Repeat("\U0001F4E6", 130) + ".zip"
	units := len(utf16.Encode([]rune(name)))
	if len([]rune(name)) > 255 || len(name) <= 255 || units <= 255 {
		t.Fatalf("unexpected name length %d bytes, %d characters, %d UTF-16 units",
			len(name), len([]rune(name)), units)
	}
	reply, err := (&AssociationTransfer{}).ImportValidate(context.Background(),
		AssociationImportUpload{File: &AssociationImportFile{Name: name, Content: []byte("not a zip")}})
	if err != nil {
		t.Fatal(err)
	}
	if reply == nil || reply.Status != 500 {
		t.Fatalf("unexpected reply %+v", reply)
	}
	message, _ := reply.Body.(map[string]string)["error"]
	if !strings.HasPrefix(message, "Errore durante la validazione: [Errno ") ||
		!strings.Contains(message, "File name too long: '") ||
		!strings.HasSuffix(message, "/"+name+"'") {
		t.Fatalf("unexpected error message %q", message)
	}
}
