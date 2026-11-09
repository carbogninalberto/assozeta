package repository

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"errors"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// BroadcastCacheTTL is NotificationService.BROADCAST_CACHE_TTL.
const BroadcastCacheTTL = 300 * time.Second

// ErrNotificationStore is any stored value NotificationManager cannot read.
var ErrNotificationStore = errors.New("notification store value is unreadable")

// NotificationStore reads and writes Django's notification data: broadcast
// subscriptions (database, cached in the Django cache for five minutes), the
// JSON notification lists NotificationManager keeps in Redis database 0 under
// each broadcast name, and the staff-board read association.
type NotificationStore struct {
	db    *sqlx.DB
	store *redis.Client
	cache *redis.Client
}

func NewNotificationStore(db *sqlx.DB, store, cache *redis.Client) (*NotificationStore, error) {
	if store == nil || store.Options().DB != 0 || cache == nil || cache.Options().DB != 1 {
		return nil, errors.New("notification store requires Redis databases 0 (store) and 1 (Django cache)")
	}
	return &NotificationStore{db: db, store: store, cache: cache}, nil
}

// Broadcasts is NotificationService.get_user_broadcasts: the cached list, or
// the subscribed broadcast names (nil for a NULL name) plus
// "all_notifications", cached for five minutes.
func (s *NotificationStore) Broadcasts(ctx context.Context, userID string) ([]*string, error) {
	key := ":1:user_broadcasts:" + userID
	raw, err := s.cache.Get(ctx, key).Bytes()
	if err == nil {
		return decodePickledNames(raw)
	}
	if !errors.Is(err, redis.Nil) {
		return nil, err
	}
	// The broadcasts query Django's values_list issues (no ORDER BY).
	var names []sql.NullString
	if err := s.db.SelectContext(ctx, &names, `SELECT "notifications_notificationsbroadcast"."broadcast_name"
FROM "notifications_usernotificationsbroadcast"
INNER JOIN "notifications_notificationsbroadcast" ON ("notifications_usernotificationsbroadcast"."broadcast_id" = "notifications_notificationsbroadcast"."broadcast_id")
WHERE "notifications_usernotificationsbroadcast"."user_id" = $1`, userID); err != nil {
		return nil, err
	}
	result := make([]*string, 0, len(names)+1)
	for _, name := range names {
		if name.Valid {
			value := name.String
			result = append(result, &value)
		} else {
			result = append(result, nil)
		}
	}
	all := "all_notifications"
	result = append(result, &all)
	if encoded, ok := encodePickledNames(result); ok {
		if err := s.cache.Set(ctx, key, encoded, BroadcastCacheTTL).Err(); err != nil {
			return nil, err
		}
	}
	return result, nil
}

// Notifications is NotificationManager.parse_notification: the stored list for
// a broadcast (empty when the key is absent or its messages are "").
func (s *NotificationStore) Notifications(ctx context.Context, broadcast string) ([]any, error) {
	raw, err := s.store.Get(ctx, broadcast).Bytes()
	if errors.Is(err, redis.Nil) {
		return []any{}, nil
	}
	if err != nil {
		return nil, err
	}
	if !utf8.Valid(raw) {
		return nil, ErrNotificationStore
	}
	value, err := dto.ParsePythonJSON(string(raw))
	if err != nil {
		return nil, ErrNotificationStore
	}
	object, ok := value.(domain.RequestObject)
	if !ok {
		return nil, ErrNotificationStore
	}
	messages, present := lookupField(object, "messages")
	if !present {
		return nil, ErrNotificationStore
	}
	switch v := messages.(type) {
	case string:
		if v == "" {
			return []any{}, nil
		}
	case []any:
		return v, nil
	case domain.RequestObject:
		if len(v) == 0 {
			return []any{}, nil
		}
	}
	return nil, ErrNotificationStore
}

func lookupField(object domain.RequestObject, name string) (any, bool) {
	for _, field := range object {
		if field.Name == name {
			return field.Value, true
		}
	}
	return nil, false
}

// SaveNotifications is NotificationManager's r.set(broadcast, json.dumps({"messages": ...})).
func (s *NotificationStore) SaveNotifications(ctx context.Context, broadcast string, items []any) error {
	value := domain.RequestObject{{Name: "messages", Value: items}}
	return s.store.Set(ctx, broadcast, dto.PythonJSONDumps(value), 0).Err()
}

// ReadableAssociation is communications.staff_board.readable_association_id.
func (s *NotificationStore) ReadableAssociation(ctx context.Context, userID string) (string, error) {
	var user struct {
		Role             int            `db:"role"`
		ConnectedUserID  sql.NullString `db:"connected_user_id"`
		CollaboratorRole int            `db:"collaborator_role"`
		Permissions      []byte         `db:"collaborator_permissions"`
	}
	err := s.db.GetContext(ctx, &user, `SELECT role, connected_user_id, collaborator_role, collaborator_permissions
FROM bakney_user WHERE user_id = $1 AND is_active AND NOT deleted ORDER BY user_id LIMIT 1`, userID)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	owner := userID
	switch user.Role {
	case domain.RoleAssociation:
	case domain.RoleCollaborator:
		if !user.ConnectedUserID.Valid {
			return "", nil
		}
		if user.CollaboratorRole != 1 {
			granted, err := permissionGranted(user.Permissions, "association.communication.messages.read")
			if err != nil || !granted {
				return "", err
			}
		}
		owner = user.ConnectedUserID.String
	default:
		return "", nil
	}
	var association string
	err = s.db.GetContext(ctx, &association, `SELECT a.sport_association_id FROM application_sportassociation a
INNER JOIN bakney_user u ON (a.user_id = u.user_id)
WHERE NOT a.deleted AND a.user_id = $1 AND u.is_active ORDER BY a.sport_association_id ASC LIMIT 1`, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return association, err
}

// permissionGranted is `permission in (collaborator_permissions or [])`.
func permissionGranted(raw []byte, permission string) (bool, error) {
	if len(raw) == 0 {
		return false, nil
	}
	value, err := dto.ParsePythonJSON(string(raw))
	if err != nil {
		return false, err
	}
	if !dto.PythonTruthy(value) {
		return false, nil
	}
	switch v := value.(type) {
	case []any:
		for _, item := range v {
			if text, ok := item.(string); ok && text == permission {
				return true, nil
			}
		}
		return false, nil
	case string:
		return bytes.Contains([]byte(v), []byte(permission)), nil
	case domain.RequestObject:
		_, present := lookupField(v, permission)
		return present, nil
	}
	// `in` on a number or True raises TypeError.
	return false, ErrNotificationStore
}

// RealtimeChannel is notifications.realtime_layer.REALTIME_CHANNEL.
const RealtimeChannel = "assozeta:realtime:v1"

// RealtimeSubscription is the subscription to the group messages Django's
// mirrored channel layer publishes.
type RealtimeSubscription struct{ pubsub *redis.PubSub }

// SubscribeRealtime subscribes and waits for Redis to confirm, so messages
// published after it returns are received.
func SubscribeRealtime(ctx context.Context, client *redis.Client) (*RealtimeSubscription, error) {
	pubsub := client.Subscribe(ctx, RealtimeChannel)
	if _, err := pubsub.Receive(ctx); err != nil {
		pubsub.Close()
		return nil, err
	}
	return &RealtimeSubscription{pubsub: pubsub}, nil
}

// Run delivers each version-1 envelope {"version": 1, "group": ..., "message":
// {...}} until the subscription is closed; other payloads are ignored.
func (s *RealtimeSubscription) Run(deliver func(group string, message domain.RequestObject)) {
	for message := range s.pubsub.Channel() {
		value, err := dto.ParsePythonJSON(message.Payload)
		if err != nil {
			continue
		}
		envelope, ok := value.(domain.RequestObject)
		if !ok {
			continue
		}
		version, _ := lookupField(envelope, "version")
		group, _ := lookupField(envelope, "group")
		body, _ := lookupField(envelope, "message")
		name, isText := group.(string)
		content, isObject := body.(domain.RequestObject)
		if number, isNumber := version.(json.Number); !isNumber || number != "1" || !isText || !isObject {
			continue
		}
		deliver(name, content)
	}
}

func (s *RealtimeSubscription) Close() error { return s.pubsub.Close() }

// --- django-redis pickle (protocol 4) of a list of str/None ---------------

func decodePickledNames(data []byte) ([]*string, error) {
	fail := func() ([]*string, error) { return nil, ErrNotificationStore }
	if len(data) < 4 || data[0] != 0x80 || data[1] < 2 || data[1] > 5 {
		return fail()
	}
	pos := 2
	var memo []any
	var stack []any
	marks := []int{}
	var list []*string
	haveList := false
	for pos < len(data) {
		op := data[pos]
		pos++
		switch op {
		case 0x95: // FRAME
			if pos+8 > len(data) {
				return fail()
			}
			pos += 8
		case ']': // EMPTY_LIST
			if haveList {
				return fail()
			}
			haveList = true
			stack = append(stack, &list)
		case 0x94: // MEMOIZE
			if len(stack) == 0 {
				return fail()
			}
			memo = append(memo, stack[len(stack)-1])
		case '(':
			marks = append(marks, len(stack))
		case 'N':
			stack = append(stack, (*string)(nil))
		case 0x8c, 'X', 0x8d:
			width := map[byte]int{0x8c: 1, 'X': 4, 0x8d: 8}[op]
			if pos+width > len(data) {
				return fail()
			}
			var size uint64
			switch width {
			case 1:
				size = uint64(data[pos])
			case 4:
				size = uint64(binary.LittleEndian.Uint32(data[pos:]))
			default:
				size = binary.LittleEndian.Uint64(data[pos:])
			}
			pos += width
			if size > uint64(len(data)-pos) {
				return fail()
			}
			text := string(data[pos : pos+int(size)])
			pos += int(size)
			stack = append(stack, &text)
		case 'h', 'j': // BINGET, LONG_BINGET
			width := 1
			if op == 'j' {
				width = 4
			}
			if pos+width > len(data) {
				return fail()
			}
			index := int(data[pos])
			if width == 4 {
				index = int(binary.LittleEndian.Uint32(data[pos:]))
			}
			pos += width
			if index >= len(memo) {
				return fail()
			}
			stack = append(stack, memo[index])
		case 'a', 'e': // APPEND, APPENDS
			start := len(stack) - 1
			if op == 'e' {
				if len(marks) == 0 {
					return fail()
				}
				start = marks[len(marks)-1]
				marks = marks[:len(marks)-1]
			}
			if start < 1 || start > len(stack) {
				return fail()
			}
			if _, ok := stack[start-1].(*[]*string); !ok {
				return fail()
			}
			for _, item := range stack[start:] {
				name, ok := item.(*string)
				if !ok {
					return fail()
				}
				list = append(list, name)
			}
			stack = stack[:start]
		case '.':
			if pos != len(data) || len(stack) != 1 || !haveList {
				return fail()
			}
			if list == nil {
				list = []*string{}
			}
			return list, nil
		default:
			return fail()
		}
	}
	return fail()
}

// encodePickledNames is pickle.dumps(names, 4) as the C pickler writes it:
// one frame, every str memoized, a single item APPENDed and longer lists in
// MARK/APPENDS batches of 1000. Lists of 64 KiB or more (several frames) are
// not cached.
func encodePickledNames(names []*string) ([]byte, bool) {
	var body bytes.Buffer
	body.WriteByte(']')
	body.WriteByte(0x94)
	writeName := func(name *string) {
		if name == nil {
			body.WriteByte('N')
			return
		}
		if len(*name) < 256 {
			body.WriteByte(0x8c)
			body.WriteByte(byte(len(*name)))
		} else {
			body.WriteByte('X')
			_ = binary.Write(&body, binary.LittleEndian, uint32(len(*name)))
		}
		body.WriteString(*name)
		body.WriteByte(0x94)
	}
	if len(names) == 1 {
		writeName(names[0])
		body.WriteByte('a')
	} else {
		for start := 0; start < len(names); start += 1000 {
			end := min(start+1000, len(names))
			body.WriteByte('(')
			for _, name := range names[start:end] {
				writeName(name)
			}
			body.WriteByte('e')
		}
	}
	body.WriteByte('.')
	if body.Len() >= 64*1024 {
		return nil, false
	}
	encoded := []byte{0x80, 4}
	if body.Len() >= 4 {
		encoded = append(encoded, 0x95)
		encoded = binary.LittleEndian.AppendUint64(encoded, uint64(body.Len()))
	}
	return append(encoded, body.Bytes()...), true
}
