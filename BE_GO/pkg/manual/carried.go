package manual

import (
	"errors"
	"os"
	"strings"
	"syscall"
	"unicode"
	"unicode/utf8"
)

// carriedOrigin follows Django's bundle.carried_origin: a release may carry the
// manual verified for an earlier release, declared as "<release> <revision>" in
// MANUAL_CARRIED_FROM next to the bundle directory. A missing or blank marker
// means no carried origin; any other content must be exactly two fields with a
// 40-character lowercase hexadecimal revision.
func carriedOrigin(bundlePath string) (release, revision string, carried bool, err error) {
	// Join lexically, without cleaning: "link/.." must resolve through the link.
	parent := pathlibParent(bundlePath)
	marker := parent + "/MANUAL_CARRIED_FROM"
	if strings.HasSuffix(parent, "/") {
		marker = parent + "MANUAL_CARRIED_FROM"
	}
	info, err := os.Stat(marker)
	if err != nil {
		// pathlib.Path.is_file ignores only these errors.
		if errors.Is(err, syscall.ENOENT) || errors.Is(err, syscall.ENOTDIR) || errors.Is(err, syscall.EBADF) || errors.Is(err, syscall.ELOOP) {
			return "", "", false, nil
		}
		return "", "", false, err
	}
	if !info.Mode().IsRegular() {
		return "", "", false, nil
	}
	raw, err := os.ReadFile(marker)
	if err != nil {
		return "", "", false, err
	}
	if !utf8.Valid(raw) {
		return "", "", false, errEvidence
	}
	parts := strings.FieldsFunc(string(raw), pythonSpace)
	if len(parts) == 0 {
		return "", "", false, nil
	}
	if len(parts) != 2 || !hex40.MatchString(parts[1]) {
		return "", "", false, errEvidence
	}
	return parts[0], parts[1], true, nil
}

// pythonSpace matches str.split() separators, which add the ASCII information
// separators to Unicode white space.
func pythonSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }

// pathlibParent is pathlib.PurePosixPath(path).parent: lexical, with empty and
// "." components dropped, ".." retained and a leading "//" preserved.
func pathlibParent(path string) string {
	root := ""
	if strings.HasPrefix(path, "//") && !strings.HasPrefix(path, "///") {
		root = "//"
	} else if strings.HasPrefix(path, "/") {
		root = "/"
	}
	parts := []string{}
	for _, part := range strings.Split(path, "/") {
		if part != "" && part != "." {
			parts = append(parts, part)
		}
	}
	if len(parts) > 0 {
		parts = parts[:len(parts)-1]
	}
	if len(parts) == 0 {
		if root == "" {
			return "."
		}
		return root
	}
	return root + strings.Join(parts, "/")
}
