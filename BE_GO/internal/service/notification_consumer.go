package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"sort"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// NotificationStorage is Django's notification data (see repository.NotificationStore).
type NotificationStorage interface {
	Broadcasts(ctx context.Context, userID string) ([]*string, error)
	Notifications(ctx context.Context, broadcast string) ([]any, error)
	SaveNotifications(ctx context.Context, broadcast string, items []any) error
	ReadableAssociation(ctx context.Context, userID string) (string, error)
}

// ErrNotificationData is a stored notification NotificationManager raises on.
var ErrNotificationData = errors.New("notification data raises in Django")

// notificationTTL is NOTIFICATION_TTL_SECONDS.
const notificationTTL = 60 * 60 * 24 * 30

// NotificationManager is the part of notifications.utils.NotificationManager
// (and NotificationService) the notification consumer calls.
type NotificationManager struct{ storage NotificationStorage }

func NewNotificationManager(storage NotificationStorage) *NotificationManager {
	return &NotificationManager{storage: storage}
}

func (m *NotificationManager) Broadcasts(ctx context.Context, userID string) ([]*string, error) {
	return m.storage.Broadcasts(ctx, userID)
}

func (m *NotificationManager) ReadableAssociation(ctx context.Context, userID string) (string, error) {
	return m.storage.ReadableAssociation(ctx, userID)
}

func (m *NotificationManager) parse(ctx context.Context, broadcast *string) ([]any, error) {
	if broadcast == nil {
		// redis-py refuses a None key.
		return nil, ErrNotificationData
	}
	return m.storage.Notifications(ctx, *broadcast)
}

// Listed is one _build_notification_dict result with its sort key.
type Listed struct {
	Value  domain.RequestObject
	issued *big.Float
	Read   bool
}

// List is get_notification: every broadcast's notifications in order, then a
// stable sort by issued, newest first, and the unread count.
func (m *NotificationManager) List(ctx context.Context, userKey string, broadcasts []*string) ([]Listed, int, error) {
	var results []Listed
	unread := 0
	for _, broadcast := range broadcasts {
		items, err := m.parse(ctx, broadcast)
		if err != nil {
			return nil, 0, err
		}
		for _, item := range items {
			listed, err := buildNotification(item, userKey)
			if err != nil {
				return nil, 0, err
			}
			results = append(results, listed)
			if !listed.Read {
				unread++
			}
		}
	}
	sort.SliceStable(results, func(i, j int) bool { return results[i].issued.Cmp(results[j].issued) > 0 })
	return results, unread, nil
}

func field(object domain.RequestObject, name string) (any, bool) {
	var found any
	present := false
	for _, f := range object {
		if f.Name == name {
			found, present = f.Value, true
		}
	}
	return found, present
}

func buildNotification(item any, userKey string) (Listed, error) {
	notification, ok := item.(domain.RequestObject)
	if !ok {
		return Listed{}, ErrNotificationData
	}
	kind, present := field(notification, "type")
	if !present {
		kind = "info"
	}
	users, present := field(notification, "users")
	if !present {
		return Listed{}, ErrNotificationData
	}
	read, err := dto.PythonContains(users, userKey)
	if err != nil {
		return Listed{}, ErrNotificationData
	}
	expires, present := field(notification, "expires")
	if !present {
		return Listed{}, ErrNotificationData
	}
	issued, issuedValue, err := subtractTTL(expires)
	if err != nil {
		return Listed{}, err
	}
	date, err := pythonCtime(issued)
	if err != nil {
		return Listed{}, err
	}
	message, present := field(notification, "msg")
	if !present {
		return Listed{}, ErrNotificationData
	}
	id, present := field(notification, "id")
	if !present {
		return Listed{}, ErrNotificationData
	}
	association, _ := field(notification, "association_id")
	return Listed{Value: domain.RequestObject{
		{Name: "id", Value: id}, {Name: "type", Value: kind}, {Name: "read", Value: read}, {Name: "msg", Value: message},
		{Name: "association_id", Value: association}, {Name: "issued", Value: issuedValue}, {Name: "date", Value: date},
	}, issued: issued, Read: read}, nil
}

// subtractTTL is expires - NOTIFICATION_TTL_SECONDS for an int, float or bool.
func subtractTTL(expires any) (*big.Float, any, error) {
	switch v := expires.(type) {
	case bool:
		value := int64(-notificationTTL)
		if v {
			value++
		}
		return new(big.Float).SetInt64(value), json.Number(fmt.Sprint(value)), nil
	case json.Number:
		if integer, ok := new(big.Int).SetString(string(v), 10); ok {
			integer.Sub(integer, big.NewInt(notificationTTL))
			return new(big.Float).SetInt(integer), json.Number(integer.String()), nil
		}
		number, err := v.Float64()
		if err != nil && !math.IsInf(number, 0) {
			return nil, nil, ErrNotificationData
		}
		number -= notificationTTL
		if math.IsInf(number, 0) || math.IsNaN(number) {
			return nil, nil, ErrNotificationData
		}
		return big.NewFloat(number), number, nil
	}
	return nil, nil, ErrNotificationData
}

// pythonCtime is time.ctime() in Django's process zone (TIME_ZONE = 'UTC').
func pythonCtime(issued *big.Float) (string, error) {
	// time_t conversion floors a float timestamp.
	seconds, accuracy := issued.Int(nil)
	if seconds == nil || !seconds.IsInt64() {
		return "", ErrNotificationData
	}
	if accuracy == big.Above {
		seconds.Sub(seconds, big.NewInt(1))
	}
	moment := time.Unix(seconds.Int64(), 0).UTC()
	if moment.Year() < 1 || moment.Year() > 9999 {
		return "", ErrNotificationData
	}
	return fmt.Sprintf("%s %s%3d %02d:%02d:%02d %d", moment.Format("Mon"), moment.Format("Jan"), moment.Day(),
		moment.Hour(), moment.Minute(), moment.Second(), moment.Year()), nil
}

// Read is read_notification: in each broadcast, the first notification with
// this id the user has not read gets the user appended and the list saved.
func (m *NotificationManager) Read(ctx context.Context, userKey string, id any, broadcasts []*string) error {
	for _, broadcast := range broadcasts {
		items, err := m.parse(ctx, broadcast)
		if err != nil {
			return err
		}
		for _, item := range items {
			notification, ok := item.(domain.RequestObject)
			if !ok {
				return ErrNotificationData
			}
			users, present := field(notification, "users")
			if !present {
				return ErrNotificationData
			}
			read, err := dto.PythonContains(users, userKey)
			if err != nil {
				return ErrNotificationData
			}
			if read {
				continue
			}
			stored, present := field(notification, "id")
			if !present {
				return ErrNotificationData
			}
			if !dto.PythonEqual(stored, id) {
				continue
			}
			if err := appendUser(notification, users, userKey); err != nil {
				return err
			}
			if err := m.storage.SaveNotifications(ctx, *broadcast, items); err != nil {
				return err
			}
			break
		}
	}
	return nil
}

// appendUser is notification['users'].append(user_key) on the parsed list.
func appendUser(notification domain.RequestObject, users any, userKey string) error {
	list, ok := users.([]any)
	if !ok {
		return ErrNotificationData
	}
	for i := len(notification) - 1; i >= 0; i-- {
		if notification[i].Name == "users" {
			notification[i].Value = append(list, userKey)
			return nil
		}
	}
	return ErrNotificationData
}

// ReadAll is read_all_notification: every unread notification gets the user
// appended and its broadcast saved after each one; any exception ends the loop
// (the consumer still confirms). update_broadcast swallows save failures.
func (m *NotificationManager) ReadAll(ctx context.Context, userKey string, broadcasts []*string) {
	for _, broadcast := range broadcasts {
		items, err := m.parse(ctx, broadcast)
		if err != nil {
			return
		}
		for _, item := range items {
			notification, ok := item.(domain.RequestObject)
			if !ok {
				return
			}
			users, present := field(notification, "users")
			if !present {
				return
			}
			read, err := dto.PythonContains(users, userKey)
			if err != nil {
				return
			}
			if read {
				continue
			}
			if appendUser(notification, users, userKey) != nil {
				return
			}
			_ = m.storage.SaveNotifications(ctx, *broadcast, items)
		}
	}
}
