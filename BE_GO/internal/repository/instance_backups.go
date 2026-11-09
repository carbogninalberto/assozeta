package repository

import (
	"context"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// InstanceBackups holds the database and Django cache (Redis DB 1, key
// prefix ':1:', pickle protocol 4 as django-redis writes it on Python 3.13)
// state of BE/instance/backups.py.
type InstanceBackups struct {
	db    *sqlx.DB
	cache *redis.Client
}

func NewInstanceBackups(db *sqlx.DB, cache *redis.Client) (*InstanceBackups, error) {
	if cache == nil || cache.Options().DB != 1 {
		return nil, errors.New("instance backups require Django cache database 1")
	}
	return &InstanceBackups{db: db, cache: cache}, nil
}

const (
	instanceBackupsSizesKey    = ":1:instance:backup-data-sizes:v1"
	instanceBackupsAttemptsKey = ":1:instance:backup-download-attempts:"
	instanceBackupsUsedKey     = ":1:instance:backup-download-used:"
)

// ErrInstanceBackupsCacheShape is a cached value outside the decoded subset.
var ErrInstanceBackupsCacheShape = errors.New("cached backup value not ported")

// DatabaseBytes is SELECT pg_database_size(current_database()).
func (r *InstanceBackups) DatabaseBytes(ctx context.Context) (int64, error) {
	var size int64
	err := r.db.GetContext(ctx, &size, `SELECT pg_database_size(current_database())`)
	return size, err
}

// CachedSizes is cache.get(SIZES_KEY): found=false for a miss (or a stored
// None); otherwise int64 for django-redis' plain integers or the unpickled
// value (*PickledDict, []any, string, int64, float64, bool).
func (r *InstanceBackups) CachedSizes(ctx context.Context) (any, bool, error) {
	raw, err := r.cache.Get(ctx, instanceBackupsSizesKey).Bytes()
	if errors.Is(err, redis.Nil) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	if value, ok := instanceBackupsInt(raw); ok {
		return value, true, nil
	}
	value, err := unpickleDataRestoreProgress(raw)
	if err != nil {
		return nil, false, ErrInstanceBackupsCacheShape
	}
	return value, value != nil, nil
}

// StoreSizes is cache.set(SIZES_KEY, {'database': ..., 'objects': ...}, timeout=600).
func (r *InstanceBackups) StoreSizes(ctx context.Context, database, objects *int64) error {
	value := instanceBackupsPickleDict([]string{"database", "objects"}, []*int64{database, objects})
	return r.cache.Set(ctx, instanceBackupsSizesKey, value, 600*time.Second).Err()
}

// Attempts is cache.get(attempts_key, 0) for the integer values the view
// stores; other stored values are not ported.
func (r *InstanceBackups) Attempts(ctx context.Context, actor string) (int64, error) {
	raw, err := r.cache.Get(ctx, instanceBackupsAttemptsKey+actor).Bytes()
	if errors.Is(err, redis.Nil) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	value, ok := instanceBackupsInt(raw)
	if !ok {
		return 0, ErrInstanceBackupsCacheShape
	}
	return value, nil
}

// django-redis _incr: INCRBY only while the key exists.
var instanceBackupsIncr = redis.NewScript(`
local exists = redis.call('EXISTS', KEYS[1])
if (exists == 1) then
return redis.call('INCRBY', KEYS[1], ARGV[1])
else return false end`)

// FailAttempt is cache.add(key, 0, timeout=900) then cache.incr(key); a key
// that vanished in between is incr's ValueError.
func (r *InstanceBackups) FailAttempt(ctx context.Context, actor string) error {
	key := instanceBackupsAttemptsKey + actor
	if err := r.cache.SetNX(ctx, key, "0", 900*time.Second).Err(); err != nil {
		return err
	}
	_, err := instanceBackupsIncr.Run(ctx, r.cache, []string{key}, 1).Result()
	if errors.Is(err, redis.Nil) {
		return errors.New("backup attempts key vanished before incr")
	}
	return err
}

// ClearAttempts is cache.delete(attempts_key).
func (r *InstanceBackups) ClearAttempts(ctx context.Context, actor string) error {
	return r.cache.Del(ctx, instanceBackupsAttemptsKey+actor).Err()
}

// MarkUsed is cache.add(used_key, True, timeout=360): false when the key exists.
func (r *InstanceBackups) MarkUsed(ctx context.Context, id string) (bool, error) {
	return r.cache.SetNX(ctx, instanceBackupsUsedKey+id, []byte{0x80, 0x04, 0x88, '.'}, 360*time.Second).Result()
}

// Instance is InstanceConfiguration.objects.first() with its primary owner.
func (r *InstanceBackups) Instance(ctx context.Context) (*domain.DataRestoreInstance, error) {
	return dataRestoreInstance(ctx, r.db)
}

// InstanceBackupsLogTarget is the configuration row an ACCESS entry refers to.
type InstanceBackupsLogTarget struct {
	ID     int64  `db:"id"`
	Name   string `db:"name"`
	Domain string `db:"domain"`
}

// LogTarget is InstanceConfiguration.get_config() (objects.first()).
func (r *InstanceBackups) LogTarget(ctx context.Context) (*InstanceBackupsLogTarget, error) {
	var rows []InstanceBackupsLogTarget
	if err := r.db.SelectContext(ctx, &rows, `SELECT id, name, domain FROM instance_instanceconfiguration ORDER BY id ASC LIMIT 1`); err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &rows[0], nil
}

// Account is User.objects.filter(pk=id).first() (ActiveUserManager).
func (r *InstanceBackups) Account(ctx context.Context, id string) (*domain.Account, error) {
	var rows []domain.Account
	err := r.db.SelectContext(ctx, &rows, `SELECT user_id::text AS user_id, password, is_active, deleted, is_superuser
 FROM bakney_user WHERE user_id = $1::uuid AND NOT deleted`, id)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

// Password is the actor's stored hash.
func (r *InstanceBackups) Password(ctx context.Context, id string) (string, error) {
	var encoded string
	err := r.db.GetContext(ctx, &encoded, `SELECT password FROM bakney_user WHERE user_id = $1::uuid`, id)
	return encoded, err
}

// SetPassword is check_password's setter: save(update_fields=['password']).
func (r *InstanceBackups) SetPassword(ctx context.Context, id, encoded string) error {
	_, err := r.db.ExecContext(ctx, `UPDATE bakney_user SET password = $2 WHERE user_id = $1::uuid`, id, encoded)
	return err
}

// InstanceBackupsAccess is LogEntry.objects.log_create(config,
// action=ACCESS, actor=actor, changes={'backup_download': [”, name]},
// additional_data={'backup': name, 'actor_id': actor}, force_log=True).
type InstanceBackupsAccess struct {
	Target  InstanceBackupsLogTarget
	ActorID string
	Backup  string
	Audit   domain.LoginAuditContext
	At      time.Time
}

// AppendAccess writes the entry outside any transaction, then the
// AuditLogIndex signal: no resolver is registered for the configuration and
// the changes hold no sport_association, so the actor's first active
// association is used (best effort, as the signal swallows its errors).
func (r *InstanceBackups) AppendAccess(ctx context.Context, event InstanceBackupsAccess) error {
	changes, err := json.Marshal(map[string][2]string{"backup_download": {"", event.Backup}})
	if err != nil {
		return err
	}
	additional, err := json.Marshal(map[string]string{"backup": event.Backup, "actor_id": event.ActorID})
	if err != nil {
		return err
	}
	var remotePort any
	if event.Audit.RemotePort != nil {
		remotePort = string(*event.Audit.RemotePort)
	}
	var id int64
	err = r.db.GetContext(ctx, &id, `INSERT INTO auditlog_logentry
 (content_type_id,object_pk,object_id,object_repr,serialized_data,action,changes_text,changes,actor_id,cid,remote_addr,remote_port,timestamp,additional_data,actor_email)
 SELECT id,$1,$2,$3,NULL,3,'',$4::jsonb,$5::uuid,$6,$7::inet,$8::text::integer,$9,$10::jsonb,NULL
 FROM django_content_type WHERE app_label='instance' AND model='instanceconfiguration' RETURNING id`,
		strconv.FormatInt(event.Target.ID, 10), event.Target.ID, "Instance: "+event.Target.Name+" ("+event.Target.Domain+")",
		string(changes), event.ActorID, event.Audit.CorrelationID, event.Audit.RemoteAddress, remotePort, event.At, string(additional))
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("audit model content type missing")
	}
	if err != nil {
		return err
	}
	var indexed bool
	if err = r.db.GetContext(ctx, &indexed, `SELECT EXISTS(SELECT 1 FROM application_auditlogindex WHERE log_entry_id=$1)`, id); err != nil {
		return err
	}
	if indexed {
		return nil
	}
	var association string
	err = r.db.GetContext(ctx, &association, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1::uuid AND NOT deleted ORDER BY sport_association_id LIMIT 1`, event.ActorID)
	if err == nil {
		_, _ = r.db.ExecContext(ctx, `INSERT INTO application_auditlogindex(audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at)
 VALUES($1,$2,$3,'actor.sport_association',$4)`, uuid.NewString(), id, association, event.At)
	}
	return nil
}

// instanceBackupsInt is django-redis' int(value) for the integer forms it
// stores: an optional sign and ASCII digits (surrounding whitespace allowed).
func instanceBackupsInt(raw []byte) (int64, bool) {
	text := strings.Trim(string(raw), " \t\n\r\v\f")
	negative := false
	if text != "" && (text[0] == '+' || text[0] == '-') {
		negative = text[0] == '-'
		text = text[1:]
	}
	if text == "" || text[0] == '_' || text[len(text)-1] == '_' || strings.Contains(text, "__") {
		return 0, false
	}
	var value int64
	for _, c := range []byte(text) {
		if c == '_' {
			continue
		}
		if c < '0' || c > '9' || value > (math.MaxInt64-int64(c-'0'))/10 {
			return 0, false
		}
		value = value*10 + int64(c-'0')
	}
	if negative {
		value = -value
	}
	return value, true
}

// instanceBackupsPickleDict is pickle.dumps(dict, 4) for str keys and int or
// None values, byte for byte as CPython writes it: PROTO 4, one FRAME,
// EMPTY_DICT, MEMOIZE, then SETITEM (one item) or MARK ... SETITEMS, each
// key SHORT_BINUNICODE + MEMOIZE, ints as BININT1/BININT2/BININT/LONG1.
func instanceBackupsPickleDict(keys []string, values []*int64) []byte {
	var body []byte
	body = append(body, '}', 0x94)
	if len(keys) > 1 {
		body = append(body, '(')
	}
	for i, key := range keys {
		body = append(body, 0x8c, byte(len(key)))
		body = append(body, key...)
		body = append(body, 0x94)
		body = instanceBackupsPickleInt(body, values[i])
	}
	switch {
	case len(keys) == 1:
		body = append(body, 's')
	case len(keys) > 1:
		body = append(body, 'u')
	}
	body = append(body, '.')
	out := []byte{0x80, 0x04, 0x95}
	out = binary.LittleEndian.AppendUint64(out, uint64(len(body)))
	return append(out, body...)
}

func instanceBackupsPickleInt(out []byte, value *int64) []byte {
	if value == nil {
		return append(out, 'N')
	}
	v := *value
	switch {
	case v >= 0 && v <= 0xff:
		return append(out, 'K', byte(v))
	case v >= 0 && v <= 0xffff:
		return append(out, 'M', byte(v), byte(v>>8))
	case v >= math.MinInt32 && v <= math.MaxInt32:
		return binary.LittleEndian.AppendUint32(append(out, 'J'), uint32(int32(v)))
	}
	// LONG1: the shortest little-endian two's complement encoding.
	var encoded []byte
	for i := 0; i < 8; i++ {
		encoded = append(encoded, byte(v>>(8*i)))
	}
	for len(encoded) > 1 {
		last, previous := encoded[len(encoded)-1], encoded[len(encoded)-2]
		if (last == 0x00 && previous&0x80 == 0) || (last == 0xff && previous&0x80 != 0) {
			encoded = encoded[:len(encoded)-1]
			continue
		}
		break
	}
	out = append(out, 0x8a, byte(len(encoded)))
	return append(out, encoded...)
}
