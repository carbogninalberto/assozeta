package service

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/jackc/pgx/v5/pgconn"
)

// ErrSubscriptionSignatureMigrationNotPorted stops the command where Django
// would take a path the port does not reproduce (a generated storage key that
// already exists: get_available_name would pick a random alternative name).
var ErrSubscriptionSignatureMigrationNotPorted = refusal.New("subscription signature migration path not ported")

// SubscriptionSignatureMigrationOutput is BaseCommand.stdout with its style.
type SubscriptionSignatureMigrationOutput interface {
	Write(text string)
	Success(text string)
	Warning(text string)
	Error(text string)
}

// SubscriptionSignatureMigrationOptions are --dry-run and --batch-size.
type SubscriptionSignatureMigrationOptions struct {
	DryRun    bool
	BatchSize int64
}

// SubscriptionSignatureMigrationFatal is an exception handle() does not catch
// (Django prints a traceback and exits 1).
type SubscriptionSignatureMigrationFatal struct{ Message string }

func (e *SubscriptionSignatureMigrationFatal) Error() string { return e.Message }

// SubscriptionSignatureMigrations ports migrate_subscription_signatures_to_s3.
type SubscriptionSignatureMigrations struct {
	store   domain.SubscriptionSignatureMigrationStore
	signs   *SubscriptionSigns
	objects SubscriptionSignObjects
	now     func() time.Time
	logger  *slog.Logger
}

func NewSubscriptionSignatureMigrations(store domain.SubscriptionSignatureMigrationStore, objects SubscriptionSignObjects,
	settings SubscriptionSignSettings, now func() time.Time, logger *slog.Logger) *SubscriptionSignatureMigrations {
	if logger == nil {
		logger = slog.Default()
	}
	// set_signature_from_base64 is shared with subscription/sign.
	signs := NewSubscriptionSigns(nil, objects, settings, nil, now, logger)
	return &SubscriptionSignatureMigrations{store: store, signs: signs, objects: objects, now: now, logger: logger}
}

const (
	subscriptionSignatureInvalidText = "{'msg': ErrorDetail(string='Firma non valida.', code='invalid_signature'), " +
		"'code': ErrorDetail(string='400', code='invalid_signature'), 'ref': ErrorDetail(string='invalid_signature', code='invalid_signature')}"
	subscriptionSignatureStorageText = "{'msg': ErrorDetail(string='Storage temporaneamente non disponibile.', code='storage_unavailable'), " +
		"'code': ErrorDetail(string='503', code='storage_unavailable'), 'ref': ErrorDetail(string='signature_storage', code='storage_unavailable')}"
)

// Run is Command.handle.
func (m *SubscriptionSignatureMigrations) Run(ctx context.Context, options SubscriptionSignatureMigrationOptions,
	out SubscriptionSignatureMigrationOutput) error {
	exists, err := m.store.LegacyColumnExists(ctx)
	if err != nil {
		return err
	}
	if !exists {
		out.Success("No legacy subscription.signature column found")
		return nil
	}
	if options.DryRun {
		out.Warning("Running in DRY-RUN mode - no changes will be made")
	}
	total, err := m.store.LegacyCount(ctx)
	if err != nil {
		return err
	}
	out.Write(fmt.Sprintf("Found %d legacy signatures", total))
	if options.BatchSize == 0 {
		return &SubscriptionSignatureMigrationFatal{Message: "ValueError: range() arg 3 must not be zero"}
	}
	migrated, skipped, failed := 0, 0, 0
	for offset := int64(0); options.BatchSize > 0 && offset < total; offset += options.BatchSize {
		rows, err := m.store.LegacyRows(ctx, options.BatchSize, offset)
		if err != nil {
			return err
		}
		out.Write(fmt.Sprintf("\nProcessing batch %d (%d-%d of %d)", offset/options.BatchSize+1, offset+1,
			min(offset+options.BatchSize, total), total))
		for _, legacy := range rows {
			result, err := m.row(ctx, legacy, options.DryRun, out)
			switch {
			case errors.Is(err, ErrSubscriptionSignatureMigrationNotPorted):
				return err
			case err != nil:
				failed++
				out.Error("Error processing subscription " + legacy.ID + ": " + subscriptionSignatureMigrationErrorText(err))
				m.logger.Error("Error migrating legacy signature for subscription", "subscription_id", legacy.ID, "error", err)
			case result == "migrated":
				migrated++
			default:
				skipped++
			}
		}
	}
	out.Write("\n" + strings.Repeat("=", 60))
	out.Success("Migration complete!")
	out.Write(fmt.Sprintf("Total legacy signatures: %d", total))
	out.Success(fmt.Sprintf("Migrated: %d", migrated))
	out.Warning(fmt.Sprintf("Skipped: %d", skipped))
	if failed > 0 {
		out.Error(fmt.Sprintf("Errors: %d", failed))
	}
	out.Write(strings.Repeat("=", 60))
	return nil
}

// subscriptionSignatureMigrationError is str(e) of an exception raised while
// processing one row.
type subscriptionSignatureMigrationError struct{ text string }

func (e *subscriptionSignatureMigrationError) Error() string { return e.text }

func subscriptionSignatureMigrationErrorText(err error) string {
	var known *subscriptionSignatureMigrationError
	var database *pgconn.PgError
	switch {
	case errors.As(err, &known):
		return known.text
	case errors.Is(err, ErrSubscriptionSignInvalid):
		return subscriptionSignatureInvalidText
	case errors.Is(err, ErrSubscriptionSignStorage):
		return subscriptionSignatureStorageText
	case errors.As(err, &database):
		// psycopg2: the server's primary message and a newline.
		return database.Message + "\n"
	}
	return err.Error()
}

func (m *SubscriptionSignatureMigrations) row(ctx context.Context, legacy domain.SubscriptionSignatureLegacyRow, dryRun bool,
	out SubscriptionSignatureMigrationOutput) (string, error) {
	subscription, err := m.store.Subscription(ctx, legacy.ID)
	if err != nil {
		return "", err
	}
	if subscription == nil {
		return "", &subscriptionSignatureMigrationError{text: "Subscription matching query does not exist."}
	}
	return m.migrate(ctx, *subscription, legacy.Signature, dryRun, out)
}

// migrate is Command.migrate_signature.
func (m *SubscriptionSignatureMigrations) migrate(ctx context.Context, row domain.SubscriptionSignatureMigrationRow, legacy string,
	dryRun bool, out SubscriptionSignatureMigrationOutput) (string, error) {
	if legacy == "" {
		return "skipped", nil
	}
	if (row.Key != nil && *row.Key != "") || (row.URL != nil && *row.URL != "") {
		out.Warning("Skipping " + row.ID + " - destination already populated")
		return "skipped", nil
	}
	if strings.HasPrefix(legacy, "http://") || strings.HasPrefix(legacy, "https://") {
		if dryRun {
			out.Success("[DRY-RUN] Would keep legacy URL for " + row.ID)
			return "migrated", nil
		}
		if text := subscriptionSignatureMigrationSaveError(row); text != "" {
			return "", &subscriptionSignatureMigrationError{text: text}
		}
		url := legacy
		if err := m.store.Save(ctx, row, nil, &url, m.now().UTC().Truncate(time.Microsecond), false); err != nil {
			return "", err
		}
		out.Success("Kept legacy URL for " + row.ID)
		return "migrated", nil
	}
	data := legacy
	if _, after, found := strings.Cut(legacy, ","); found {
		data = after
	}
	if !subscriptionSignatureLenientBase64(subscriptionSignatureMigrationPrefix(data, 100)) {
		out.Warning("Skipping " + row.ID + " - not valid base64 or URL")
		return "skipped", nil
	}
	if dryRun {
		out.Success("[DRY-RUN] Would migrate " + row.ID + " to configured storage")
		return "migrated", nil
	}
	// transaction.atomic(): set_signature_from_base64, then save; a stored
	// object is deleted when the save fails.
	name, public, err := m.signs.upload(ctx, row.ID, legacy)
	if errors.Is(err, ErrSubscriptionSignNotPorted) {
		return "", ErrSubscriptionSignatureMigrationNotPorted
	}
	if err != nil {
		return "", err
	}
	saveErr := error(nil)
	if text := subscriptionSignatureMigrationSaveError(row); text != "" {
		saveErr = &subscriptionSignatureMigrationError{text: text}
	} else {
		saveErr = m.store.Save(ctx, row, &name, public, m.now().UTC().Truncate(time.Microsecond), true)
	}
	if saveErr != nil {
		key, err := documentkey.S3Key(name, m.objects.Location())
		if err == nil {
			err = m.objects.Delete(context.WithoutCancel(ctx), key)
		}
		if err != nil {
			return "", err
		}
		return "", saveErr
	}
	out.Success("Migrated " + row.ID + " to storage key " + name)
	return "migrated", nil
}

// subscriptionSignatureMigrationPrefix is text[:n] in code points.
func subscriptionSignatureMigrationPrefix(text string, n int) string {
	count := 0
	for index := range text {
		if count == n {
			return text[:index]
		}
		count++
	}
	return text
}

// subscriptionSignatureLenientBase64 reports whether base64.b64decode(text)
// (validate=False) succeeds: a str must be ASCII, characters outside the
// alphabet are discarded, decoding stops at a complete pad sequence, and the
// data characters must not leave an incomplete quantum (CPython 3.13's
// binascii.a2b_base64 in non-strict mode).
func subscriptionSignatureLenientBase64(text string) bool {
	quad, pads := 0, 0
	for index := 0; index < len(text); index++ {
		c := text[index]
		if c >= utf8.RuneSelf {
			return false
		}
		if c == '=' {
			// quad_pos >= 2 && quad_pos + ++pads >= 4: pads only count after two data characters.
			if quad >= 2 {
				pads++
				if quad+pads >= 4 {
					return true
				}
			}
			continue
		}
		if !(c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '+' || c == '/') {
			continue
		}
		pads = 0
		quad = (quad + 1) % 4
	}
	return quad == 0
}

// pythonDatetimeError is the ValueError datetime(year, month, day) raises.
func pythonDatetimeError(year, month, day int) string {
	switch {
	case year < 1 || year > 9999:
		return "year " + strconv.Itoa(year) + " is out of range"
	case month < 1 || month > 12:
		return "month must be in 1..12"
	case day < 1 || day > time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day():
		return "day is out of range for month"
	}
	return ""
}

// subscriptionSignatureMigrationSaveError is the ValueError Subscription.save()
// raises before writing, from BalanceSheetData.get_range_from_year_and_starting_date
// (TIME_ZONE UTC), or "".
func subscriptionSignatureMigrationSaveError(row domain.SubscriptionSignatureMigrationRow) string {
	month, day := row.StartMonth, row.StartDay
	if month < 1 || month > 12 {
		return "Month must be between 1 and 12"
	}
	if day < 1 || day > 31 {
		return "Day must be between 1 and 31"
	}
	created := row.CreationDate.UTC()
	year := created.Year()
	if text := pythonDatetimeError(year, month, day); text != "" {
		return "Invalid date combination: " + text
	}
	if text := pythonDatetimeError(year-1, month, day); text != "" {
		return text
	}
	start := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if created.Before(start) {
		start = time.Date(year-1, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	}
	if row.CustomEnd && row.EndMonth != 0 && row.EndDay != 0 {
		if text := pythonDatetimeError(start.Year(), row.EndMonth, row.EndDay); text != "" {
			return text
		}
		end := time.Date(start.Year(), time.Month(row.EndMonth), row.EndDay, 0, 0, 0, 0, time.UTC)
		if end.Before(start) {
			return pythonDatetimeError(start.Year()+1, row.EndMonth, row.EndDay)
		}
		return ""
	}
	return pythonDatetimeError(start.Year()+1, month, day)
}
