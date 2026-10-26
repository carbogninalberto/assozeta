package main

import (
	"errors"
	"os"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/queryobservation"
	"github.com/google/uuid"
)

// openQueryObservation is an explicit, disabled-by-default diagnostic sink.
// Exclusive creation refuses existing files and symlinks. The caller drains
// HTTP and database activity before finalizing; any diagnostic loss is an error.
func openQueryObservation(path string) (*queryobservation.Observer, func(bool) error, error) {
	if path == "" {
		return nil, func(bool) error { return nil }, nil
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return nil, nil, errors.New("cannot create query observation file")
	}
	observer, err := queryobservation.New(file, uuid.NewString())
	if err != nil {
		_ = file.Close()
		return nil, nil, errors.New("cannot initialize query observation file")
	}
	return observer, func(drained bool) error {
		if !drained {
			_ = file.Close()
			return errors.New("query observation aborted before drain completion")
		}
		observeErr := observer.Close()
		syncErr := file.Sync()
		closeErr := file.Close()
		if errors.Join(observeErr, syncErr, closeErr) != nil {
			return errors.New("query observation finalization failed")
		}
		return nil
	}, nil
}
