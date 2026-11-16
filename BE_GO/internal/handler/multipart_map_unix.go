//go:build darwin || linux

package handler

import (
	"fmt"
	"os"
	"syscall"
)

// mapFileReadOnly maps the file's bytes read-only and returns the mapping with
// the function that releases it. The file may be closed once it is mapped.
func mapFileReadOnly(file *os.File) ([]byte, func(), error) {
	info, err := file.Stat()
	if err != nil {
		return nil, nil, err
	}
	size := info.Size()
	if size == 0 {
		return nil, func() {}, nil
	}
	if int64(int(size)) != size {
		// syscall.Mmap takes an int length; no upload reaches this on 32-bit.
		return nil, nil, fmt.Errorf("multipart body of %d bytes cannot be mapped", size)
	}
	mapped, err := syscall.Mmap(int(file.Fd()), 0, int(size), syscall.PROT_READ, syscall.MAP_PRIVATE)
	if err != nil {
		return nil, nil, err
	}
	return mapped, func() { _ = syscall.Munmap(mapped) }, nil
}
