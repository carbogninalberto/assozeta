package repository

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/binary"
	"errors"
	"math/big"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// GoogleCalendars stores the google/* views' User and Course saves (each
// commits on its own with its auditlog entry and index row) and the cache
// session SessionMiddleware creates for the config view.
type GoogleCalendars struct {
	db    *sqlx.DB
	cache *redis.Client
}

func NewGoogleCalendars(db *sqlx.DB, cache *redis.Client) *GoogleCalendars {
	return &GoogleCalendars{db: db, cache: cache}
}

var _ domain.GoogleCalendarStore = (*GoogleCalendars)(nil)

const googleUserColumns = `user_id, first_name, last_name, role, deleted, integration_google_state,
 integration_google_credentials::text AS integration_google_credentials, integration_google_auto_sync`

func (r *GoogleCalendars) User(ctx context.Context, id string) (*domain.GoogleUser, error) {
	var user domain.GoogleUser
	err := r.db.GetContext(ctx, &user, `SELECT `+googleUserColumns+` FROM bakney_user WHERE user_id=$1::uuid`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrGoogleServer
	}
	return &user, err
}

// UserByState is User.objects.filter(integration_google_state=state).first() (live users, by key).
func (r *GoogleCalendars) UserByState(ctx context.Context, state string) (*domain.GoogleUser, error) {
	var user domain.GoogleUser
	err := r.db.GetContext(ctx, &user, `SELECT `+googleUserColumns+` FROM bakney_user
 WHERE integration_google_state=$1 AND NOT deleted ORDER BY user_id LIMIT 1`, state)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &user, err
}

// googleJSONAudit is auditlog's JSONField value: json.dumps(value, sort_keys=True) ('null' for None).
func googleJSONAudit(text *string) (string, error) {
	if text == nil {
		return "null", nil
	}
	value, err := dto.ParsePythonJSON(*text)
	if err != nil {
		return "", err
	}
	return string(dto.PythonJSONDumpsSorted(value)), nil
}

func googleTextAudit(text *string) string {
	if text == nil {
		return "None"
	}
	return *text
}

func googleBoolAudit(value bool) string {
	if value {
		return "True"
	}
	return "False"
}

// SaveUser is User.save() of the Google fields: an update entry with the
// changed fields, indexed through the user's association, else the actor's.
func (r *GoogleCalendars) SaveUser(ctx context.Context, id string, fields domain.GoogleUserFields, audit domain.LoginAuditContext, at time.Time) error {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var old domain.GoogleUser
	if err = tx.tx.GetContext(ctx, &old, `SELECT `+googleUserColumns+` FROM bakney_user WHERE user_id=$1::uuid FOR UPDATE`, id); err != nil {
		return err
	}
	var credentials any
	if fields.Credentials != nil {
		credentials = *fields.Credentials
	}
	if _, err = tx.tx.ExecContext(ctx, `UPDATE bakney_user SET integration_google_state=$2, integration_google_credentials=$3::jsonb,
 integration_google_auto_sync=$4 WHERE user_id=$1::uuid`, id, fields.State, credentials, fields.AutoSync); err != nil {
		return err
	}
	changes := map[string][2]string{}
	if before, after := googleTextAudit(old.State), googleTextAudit(fields.State); before != after {
		changes["integration_google_state"] = [2]string{before, after}
	}
	before, err := googleJSONAudit(old.Credentials)
	if err != nil {
		return err
	}
	after, err := googleJSONAudit(fields.Credentials)
	if err != nil {
		return err
	}
	if before != after {
		changes["integration_google_credentials"] = [2]string{before, after}
	}
	if before, after := googleBoolAudit(old.AutoSync), googleBoolAudit(fields.AutoSync); before != after {
		changes["integration_google_auto_sync"] = [2]string{before, after}
	}
	if len(changes) > 0 {
		entry := modelAuditEntry{Model: "user", Key: id, Representation: "name: " + old.FirstName + " " + old.LastName, Action: 1,
			Changes: changes, Context: audit, At: at}
		// user_resolver reads the user through User.objects (live users only).
		if !old.Deleted {
			if err = userIndex(ctx, tx.tx, id, "user.sport_association", &entry); err != nil {
				return err
			}
		}
		if entry.AssociationID == nil {
			if err = actorIndex(ctx, tx.tx, audit, &entry); err != nil {
				return err
			}
		}
		if err = appendModelAudit(ctx, tx.tx, entry); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// Course is Course.objects.filter(course_id=id).first(): live and group-aware.
func (r *GoogleCalendars) Course(ctx context.Context, id string, group *string) (*domain.GoogleCourse, error) {
	var course domain.GoogleCourse
	err := r.db.GetContext(ctx, &course, `SELECT course_id, title, sport_association_id, google_calendar_id, google_background_color
 FROM application_course WHERE course_id=$1::uuid AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)
 ORDER BY course_id LIMIT 1`, id, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &course, err
}

// SaveCourse is course.save() with google_calendar_id possibly changed: an
// update entry only when it changed, indexed directly (or through the actor
// for a course without association).
func (r *GoogleCalendars) SaveCourse(ctx context.Context, course domain.GoogleCourse, audit domain.LoginAuditContext, at time.Time) error {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var old domain.GoogleCourse
	if err = tx.tx.GetContext(ctx, &old, `SELECT course_id, title, sport_association_id, google_calendar_id, google_background_color
 FROM application_course WHERE course_id=$1::uuid FOR UPDATE`, course.ID); err != nil {
		return err
	}
	if _, err = tx.tx.ExecContext(ctx, `UPDATE application_course SET google_calendar_id=$2 WHERE course_id=$1::uuid`,
		course.ID, course.CalendarID); err != nil {
		return err
	}
	if before, after := googleTextAudit(old.CalendarID), googleTextAudit(course.CalendarID); before != after {
		entry := modelAuditEntry{Model: "course", Key: course.ID, Representation: "Course object (" + course.ID + ")", Action: 1,
			Changes: map[string][2]string{"google_calendar_id": {before, after}}, Context: audit, At: at}
		if old.Association != nil {
			path := "direct"
			entry.AssociationID, entry.ResolutionPath = old.Association, &path
		} else if err = actorIndex(ctx, tx.tx, audit, &entry); err != nil {
			return err
		}
		if err = appendModelAudit(ctx, tx.tx, entry); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// FirstRegistry is AttendanceRegistry.objects.filter(course_id=...).first().
func (r *GoogleCalendars) FirstRegistry(ctx context.Context, course string) (*domain.GoogleRegistry, error) {
	var registry domain.GoogleRegistry
	err := r.db.GetContext(ctx, &registry, `SELECT attendance_registry_id, events::text AS events FROM application_attendanceregistry
 WHERE course_id=$1::uuid ORDER BY attendance_registry_id LIMIT 1`, course)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &registry, err
}

const googleSessionCharacters = "abcdefghijklmnopqrstuvwxyz0123456789"

// googleSessionAge is SESSION_COOKIE_AGE.
const googleSessionAge = 1209600 * time.Second

// googleSessionPickle is django_redis' pickle (protocol 4) of {'state': state}.
func googleSessionPickle(state string) []byte {
	var body []byte
	body = append(body, '}', 0x94, 0x8c, 5)
	body = append(body, "state"...)
	body = append(body, 0x94)
	if len(state) < 256 {
		body = append(body, 0x8c, byte(len(state)))
	} else {
		body = append(body, 'X')
		body = binary.LittleEndian.AppendUint32(body, uint32(len(state)))
	}
	body = append(body, state...)
	body = append(body, 0x94, 's', '.')
	frame := make([]byte, 11, 11+len(body))
	frame[0], frame[1], frame[2] = 0x80, 4, 0x95
	binary.LittleEndian.PutUint64(frame[3:], uint64(len(body)))
	return append(frame, body...)
}

// CreateSession is the cache backend's create(): a fresh 32-character key
// added (SET NX) with the session dictionary for SESSION_COOKIE_AGE.
func (r *GoogleCalendars) CreateSession(ctx context.Context, state string) (string, error) {
	limit := big.NewInt(int64(len(googleSessionCharacters)))
	for {
		key := make([]byte, 32)
		for i := range key {
			n, err := rand.Int(rand.Reader, limit)
			if err != nil {
				return "", err
			}
			key[i] = googleSessionCharacters[n.Int64()]
		}
		added, err := r.cache.SetNX(ctx, djangoSessionPrefix+string(key), googleSessionPickle(state), googleSessionAge).Result()
		if err != nil {
			return "", err
		}
		if added {
			return string(key), nil
		}
	}
}
