package service

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/channels"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

// SubscriptionNotificationRedis is the NotificationManager store (Redis DB 0)
// and get_user_broadcasts.
type SubscriptionNotificationRedis interface {
	NotificationStorage
	ResetNotifications(ctx context.Context, broadcast string) error
}

// GroupSender is the channel layer's group_send.
type GroupSender interface {
	GroupSend(ctx context.Context, group string, message channels.Dict) error
}

// SubscriptionNotifier ports NotificationService.send_notification for one
// user and one message with an association id.
type SubscriptionNotifier struct {
	rows  domain.SubscriptionNotificationStore
	redis SubscriptionNotificationRedis
	layer GroupSender
	now   func() time.Time
}

func NewSubscriptionNotifier(rows domain.SubscriptionNotificationStore, redis SubscriptionNotificationRedis, layer GroupSender,
	now func() time.Time) *SubscriptionNotifier {
	return &SubscriptionNotifier{rows: rows, redis: redis, layer: layer, now: now}
}

var errNotificationPush = errors.New("notification push value not supported")

// Send stores the notification (validate_broadcast, add_notification_role_broadcast)
// and pushes it to notifications_user_<id>; push failures are swallowed as in
// _push_to_websocket and returned only for logging.
func (n *SubscriptionNotifier) Send(ctx context.Context, user, kind, message, association string, audit domain.LoginAuditContext) error {
	subscribed, err := n.rows.ValidateBroadcast(ctx, user, "broadcast_"+user, audit, n.now().UTC())
	if err != nil {
		return err
	}
	return n.Deliver(ctx, user, kind, message, association, subscribed)
}

// Deliver is Send after validate_broadcast's rows (subscribed: a new user
// subscription, whose store is reset first).
func (n *SubscriptionNotifier) Deliver(ctx context.Context, user, kind, message, association string, subscribed bool) error {
	broadcast := "broadcast_" + user
	var err error
	if subscribed {
		if err = n.redis.ResetNotifications(ctx, broadcast); err != nil {
			return err
		}
	}
	items, err := n.redis.Notifications(ctx, broadcast)
	if err != nil {
		return err
	}
	id, err := uuid.NewUUID()
	if err != nil {
		return err
	}
	expires := n.now().Unix() + notificationTTL
	items = append(items, domain.RequestObject{{Name: "id", Value: id.String()}, {Name: "type", Value: kind}, {Name: "msg", Value: message},
		{Name: "association_id", Value: association}, {Name: "users", Value: []any{}},
		{Name: "expires", Value: json.Number(jsonInt(expires))}})
	if err = n.redis.SaveNotifications(ctx, broadcast, items); err != nil {
		return err
	}
	broadcasts, err := n.redis.Broadcasts(ctx, user)
	if err != nil {
		return err
	}
	listed, _, err := NewNotificationManager(n.redis).List(ctx, user, broadcasts)
	if err != nil {
		return err
	}
	for _, item := range listed {
		text, _ := field(item.Value, "msg")
		owner, _ := field(item.Value, "association_id")
		if text != message || owner != association {
			continue
		}
		push, err := channelsValue(item.Value)
		if err != nil {
			return err
		}
		return n.layer.GroupSend(ctx, "notifications_user_"+user, channels.Dict{{Key: "type", Value: "notification_push"},
			{Key: "notification", Value: push}})
	}
	return nil
}

func jsonInt(value int64) string {
	encoded, _ := json.Marshal(value)
	return string(encoded)
}

func channelsValue(value any) (any, error) {
	switch v := value.(type) {
	case nil, bool, string:
		return v, nil
	case json.Number:
		number, err := v.Int64()
		if err != nil {
			return nil, errNotificationPush
		}
		return number, nil
	case domain.RequestObject:
		result := make(channels.Dict, 0, len(v))
		for _, f := range v {
			item, err := channelsValue(f.Value)
			if err != nil {
				return nil, err
			}
			result = append(result, channels.Item{Key: f.Name, Value: item})
		}
		return result, nil
	case []any:
		result := make([]any, 0, len(v))
		for _, entry := range v {
			item, err := channelsValue(entry)
			if err != nil {
				return nil, err
			}
			result = append(result, item)
		}
		return result, nil
	}
	return nil, errNotificationPush
}
