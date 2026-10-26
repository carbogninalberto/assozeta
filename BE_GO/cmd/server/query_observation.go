package main

import (
	"errors"
	"io"
	"os"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/queryobservation"
	"github.com/google/uuid"
)

// openQueryObservation is an explicit, disabled-by-default diagnostic sink.
// Exclusive creation refuses existing files and symlinks. The caller drains
// HTTP and database activity before finalizing; any diagnostic loss is an error.
func openQueryObservation(path string) (*queryobservation.Observer, func(bool) error, error) {
	return openQueryObservationFiles(path, "")
}

// openQueryObservationFiles adds a separate opt-in connection stream. Both
// destinations must be new: O_EXCL rejects existing files, symlinks and aliases
// of the first destination. Failed acquisition leaves owned partial files in
// place; no path is ever truncated or unlinked.
func openQueryObservationFiles(summaryPath, connectionPath string) (*queryobservation.Observer, func(bool) error, error) {
	return openQueryObservationFilesWith(summaryPath, connectionPath, func(path string) (queryObservationSink, error) {
		return os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	})
}

type queryObservationSink interface {
	io.Writer
	Sync() error
	Close() error
}

// The opener seam keeps error/cleanup tests independent of platform I/O faults.
func openQueryObservationFilesWith(summaryPath, connectionPath string, create func(string) (queryObservationSink, error)) (*queryobservation.Observer, func(bool) error, error) {
	if summaryPath == "" {
		if connectionPath != "" {
			return nil, nil, errors.New("query connection observation requires query observation file")
		}
		return nil, func(bool) error { return nil }, nil
	}
	summary, err := create(summaryPath)
	if err != nil {
		return nil, nil, errors.New("cannot create query observation file")
	}
	sinks := []queryObservationSink{summary}
	closeSinks := func(sync bool) error {
		var result error
		for _, sink := range sinks {
			if sync {
				result = errors.Join(result, sink.Sync())
			}
			result = errors.Join(result, sink.Close())
		}
		return result
	}
	var connection queryObservationSink
	if connectionPath != "" {
		connection, err = create(connectionPath)
		if err != nil {
			_ = closeSinks(false)
			return nil, nil, errors.New("cannot create query observation file")
		}
		sinks = append(sinks, connection)
	}
	var observer *queryobservation.Observer
	runID := uuid.NewString()
	if connection == nil {
		observer, err = queryobservation.New(summary, runID)
	} else {
		observer, err = queryobservation.NewWithConnectionEvents(summary, connection, runID)
	}
	if err != nil {
		_ = closeSinks(false)
		return nil, nil, errors.New("cannot initialize query observation file")
	}
	return observer, func(drained bool) error {
		if !drained {
			// Producers may still be active; never serialize a successful footer.
			_ = closeSinks(false)
			return errors.New("query observation aborted before drain completion")
		}
		observeErr := observer.Close()
		closeErr := closeSinks(true)
		if errors.Join(observeErr, closeErr) != nil {
			return errors.New("query observation finalization failed")
		}
		return nil
	}, nil
}
