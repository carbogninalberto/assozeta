package domain

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
)

var ErrMedicalCleanupPage = errors.New("invalid medical cleanup page")

const MedicalCleanupPageLimit = 512

// A logical reference snapshot, not deletion authority. Upload admission,
// concurrent attachment, object-store effects and durable recovery are separate.
type MedicalCleanupDocument struct {
	ID           uuid.UUID
	Created      time.Time
	Filename     string
	Filepath     *string
	Size         *int64
	Certificates int64
}
type MedicalCleanupCandidateReader interface {
	ReadMedicalCleanupCandidates(context.Context, *uuid.UUID, int) ([]MedicalCleanupDocument, error)
}
