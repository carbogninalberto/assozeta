//go:build !darwin && !linux

package handler

import "os"

// mapFileReadOnly reads the file's bytes: syscall.Mmap is not available on
// this platform, so a large upload is held in memory instead of mapped.
func mapFileReadOnly(file *os.File) ([]byte, func(), error) {
	data, err := os.ReadFile(file.Name())
	if err != nil {
		return nil, nil, err
	}
	return data, func() {}, nil
}
