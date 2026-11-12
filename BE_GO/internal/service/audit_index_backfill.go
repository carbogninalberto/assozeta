package service

import (
	"context"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
)

// AuditIndexBackfillOptions are backfill_audit_log_index's options.
type AuditIndexBackfillOptions struct {
	BatchSize   int64
	DryRun      bool
	ContentType string
	Limit       int64 // 0 is no limit, as in Django's "if limit:"
}

// AuditIndexBackfillStore is the storage the command needs.
type AuditIndexBackfillStore interface {
	ContentTypeID(ctx context.Context, appLabel, model string) (int64, bool, error)
	CountPending(ctx context.Context, contentType *int64) (int64, error)
	PendingBatch(ctx context.Context, contentType *int64, offset, size int64) ([]repository.AuditIndexPendingEntry, error)
	CreateIndexes(ctx context.Context, rows []repository.AuditIndexRow) error
}

// ErrAuditIndexBackfillNegativeLimit is Django's queryset slicing error for a
// negative --limit; ErrAuditIndexBackfillZeroBatch is range()'s zero step.
var (
	ErrAuditIndexBackfillNegativeLimit = errors.New("Negative indexing is not supported.")
	ErrAuditIndexBackfillZeroBatch     = errors.New("range() arg 3 must not be zero")
)

// RunAuditIndexBackfill is Command.handle of
// BE/application/management/commands/backfill_audit_log_index.py, writing the
// same stdout lines. Each batch re-evaluates the pending query at its offset,
// as Django's queryset slicing does after the previous batch's inserts.
func RunAuditIndexBackfill(ctx context.Context, store AuditIndexBackfillStore, options AuditIndexBackfillOptions, now func() time.Time, stdout io.Writer) error {
	write := func(format string, args ...any) {
		text := fmt.Sprintf(format, args...)
		if !strings.HasSuffix(text, "\n") {
			text += "\n"
		}
		_, _ = io.WriteString(stdout, text)
	}
	if options.DryRun {
		write("Running in DRY-RUN mode")
	}
	var contentType *int64
	if options.ContentType != "" {
		parts := strings.Split(options.ContentType, ".")
		if len(parts) != 2 {
			write("Invalid content type: %s", options.ContentType)
			return nil
		}
		id, ok, err := store.ContentTypeID(ctx, parts[0], parts[1])
		if err != nil {
			return err
		}
		if !ok {
			write("Invalid content type: %s", options.ContentType)
			return nil
		}
		contentType = &id
		write("Filtering by content type: %s", options.ContentType)
	}
	total, err := store.CountPending(ctx, contentType)
	if err != nil {
		return err
	}
	limit := int64(-1)
	if options.Limit != 0 {
		if options.Limit < 0 {
			return ErrAuditIndexBackfillNegativeLimit
		}
		limit = options.Limit
		total = min(total, limit)
	}
	write("Found %d LogEntry records to process", total)
	if total == 0 {
		write("No records to backfill")
		return nil
	}
	if options.BatchSize == 0 {
		return ErrAuditIndexBackfillZeroBatch
	}
	type stats struct{ indexed, skipped, errors int64 }
	byType := map[string]*stats{}
	var order []string
	var indexed, skipped, processed int64
	for offset := int64(0); options.BatchSize > 0 && offset < total; offset += options.BatchSize {
		high := offset + options.BatchSize
		if limit >= 0 {
			high = min(high, limit)
		}
		var batch []repository.AuditIndexPendingEntry
		if high > offset {
			if batch, err = store.PendingBatch(ctx, contentType, offset, high-offset); err != nil {
				return err
			}
		}
		write("\nProcessing batch %d (%d-%d of %d)", offset/options.BatchSize+1, offset+1, min(offset+options.BatchSize, total), total)
		var rows []repository.AuditIndexRow
		for _, entry := range batch {
			item := byType[entry.ContentKey]
			if item == nil {
				item = &stats{}
				byType[entry.ContentKey] = item
				order = append(order, entry.ContentKey)
			}
			if entry.Resolution != nil {
				rows = append(rows, repository.AuditIndexRow{EntryID: entry.ID, AuditLogIndexResolution: entry.Resolution, CreatedAt: now()})
				indexed++
				item.indexed++
			} else {
				skipped++
				item.skipped++
			}
			processed++
		}
		if !options.DryRun && len(rows) > 0 {
			if err = store.CreateIndexes(ctx, rows); err != nil {
				return err
			}
			write("  Created %d index records", len(rows))
		} else if options.DryRun {
			write("  [DRY-RUN] Would create %d index records", len(rows))
		}
	}
	write("\n" + strings.Repeat("=", 60))
	write("Backfill complete!")
	write("Total processed: %d", processed)
	write("Indexed: %d", indexed)
	write("Skipped (no sport_association): %d", skipped)
	write("\nTop content types:")
	sort.SliceStable(order, func(i, j int) bool { return byType[order[i]].indexed > byType[order[j]].indexed })
	for index, key := range order {
		if index == 10 {
			break
		}
		item := byType[key]
		write("  %s: indexed=%d, skipped=%d, errors=%d", key, item.indexed, item.skipped, item.errors)
	}
	write(strings.Repeat("=", 60))
	return nil
}
