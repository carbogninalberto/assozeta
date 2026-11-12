package handler

import (
	"encoding/json"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/wsproto"
)

func object(fields ...any) domain.RequestObject {
	result := make(domain.RequestObject, 0, len(fields)/2)
	for i := 0; i < len(fields); i += 2 {
		result = append(result, domain.RequestField{Name: fields[i].(string), Value: fields[i+1]})
	}
	return result
}

func lookup(value domain.RequestObject, name string) (any, bool) {
	var found any
	present := false
	for _, field := range value {
		if field.Name == name {
			found, present = field.Value, true
		}
	}
	return found, present
}

// jsonContent is AsyncJsonWebsocketConsumer.receive: a non-empty text frame
// decoded with json.loads; binary or empty frames raise.
func jsonContent(message *wsproto.Message) (any, error) {
	if !message.Text || len(message.Data) == 0 {
		return nil, errCrash
	}
	content, err := dto.ParsePythonJSON(string(message.Data))
	if err != nil {
		return nil, errCrash
	}
	return content, nil
}

// messageType is content.get('type'); a non-dict content raises.
func messageType(content any) (any, error) {
	value, isObject := dto.PythonGet(content, "type")
	if !isObject {
		return nil, errCrash
	}
	return value, nil
}

func jsonInt(value int) json.Number { return json.Number(strconv.Itoa(value)) }

func isString(value any, text string) bool {
	s, ok := value.(string)
	return ok && s == text
}

type noGroupEvents struct{}

// A consumer without groups never receives group messages.
func (noGroupEvents) event(*session, domain.RequestObject) error { return errCrash }
func (noGroupEvents) disconnect(*session)                        {}

// ---------------------------------------------------------------------------
// application.consumers.UpdateConsumer
// ---------------------------------------------------------------------------

type updateConsumer struct{ noGroupEvents }

func (updateConsumer) connect(*session) (bool, error) { return true, nil }
func (updateConsumer) afterAccept(*session) error     { return nil }

func (updateConsumer) receive(s *session, message *wsproto.Message) error {
	// receive(self, text_data) has no bytes_data parameter.
	if !message.Text {
		return errCrash
	}
	content, err := dto.ParsePythonJSON(string(message.Data))
	if err != nil {
		return errCrash
	}
	value, err := dto.PythonIndex(content, "message")
	if err != nil {
		return errCrash
	}
	return s.send(object("message", value))
}

// ---------------------------------------------------------------------------
// application.health_consumer.HealthConsumer
// ---------------------------------------------------------------------------

type healthConsumer struct{ noGroupEvents }

func (healthConsumer) connect(*session) (bool, error) { return true, nil }

func (healthConsumer) health(s *session) error {
	return s.send(object("type", "health", "status", "ok", "msg", "I am good", "version", s.h.version))
}

func (c healthConsumer) afterAccept(s *session) error { return c.health(s) }

func (c healthConsumer) receive(s *session, message *wsproto.Message) error {
	content, err := jsonContent(message)
	if err != nil {
		return err
	}
	kind, err := messageType(content)
	if err != nil {
		return err
	}
	switch {
	case isString(kind, "ping"):
		timestamp, _ := dto.PythonGet(content, "timestamp")
		return s.send(object("type", "pong", "timestamp", timestamp))
	case isString(kind, "check"):
		return c.health(s)
	}
	return s.send(object("type", "error", "message", "Unknown message type: "+dto.PythonStr(kind)))
}

// ---------------------------------------------------------------------------
// notifications.consumers.NotificationConsumer
// ---------------------------------------------------------------------------

type notificationConsumer struct {
	staffBoard string
	groups     []string
}

func (c *notificationConsumer) join(s *session, group string) error {
	if !service.ValidGroupName(group) {
		// Channels' valid_group_name raises TypeError.
		return errCrash
	}
	s.h.hub.Join(group, s.member)
	c.groups = append(c.groups, group)
	return nil
}

func (c *notificationConsumer) connect(s *session) (bool, error) {
	if s.user == nil {
		// close(code=4001) before accept.
		return false, nil
	}
	if err := c.join(s, "notifications_user_"+s.user.ID); err != nil {
		return false, err
	}
	association, err := s.h.notifications.ReadableAssociation(s.ctx, s.user.ID)
	if err != nil {
		return false, err
	}
	c.staffBoard = association
	if association != "" {
		if err := c.join(s, "staff_board_"+association); err != nil {
			return false, err
		}
	}
	broadcasts, err := s.h.notifications.Broadcasts(s.ctx, s.user.ID)
	if err != nil {
		return false, err
	}
	for _, broadcast := range broadcasts {
		name := "None"
		if broadcast != nil {
			name = *broadcast
		}
		if err := c.join(s, "notifications_broadcast_"+name); err != nil {
			return false, err
		}
	}
	// Groups are joined before accepting, so an initial refetch cannot miss an update.
	return true, nil
}

func (*notificationConsumer) afterAccept(*session) error { return nil }

func (c *notificationConsumer) disconnect(s *session) {
	for _, group := range c.groups {
		s.h.hub.Leave(group, s.member)
	}
}

func (c *notificationConsumer) receive(s *session, message *wsproto.Message) error {
	content, err := jsonContent(message)
	if err != nil {
		return err
	}
	kind, err := messageType(content)
	if err != nil {
		return err
	}
	// Everything below runs inside the consumer's try/except Exception.
	if err := c.handle(s, content, kind); err != nil {
		return c.sendError(s, "Internal server error")
	}
	return nil
}

func (c *notificationConsumer) sendError(s *session, text string) error {
	return s.send(object("type", "error", "message", text))
}

func (c *notificationConsumer) handle(s *session, content, kind any) error {
	switch {
	case isString(kind, "ping"):
		timestamp, _ := dto.PythonGet(content, "timestamp")
		return s.send(object("type", "pong", "timestamp", timestamp))
	case isString(kind, "fetch"):
		return c.fetch(s)
	case isString(kind, "mark_read"):
		id, _ := dto.PythonGet(content, "id")
		if !dto.PythonTruthy(id) {
			return c.sendError(s, "Missing 'id' for mark_read")
		}
		return c.markRead(s, id)
	case isString(kind, "mark_all_read"):
		return c.markAllRead(s)
	}
	return c.sendError(s, "Unknown message type: "+dto.PythonStr(kind))
}

// visible is _visible_notification: everything without an association scope,
// otherwise only notifications of the scoped association.
func (c *notificationConsumer) visible(s *session, notification any) (bool, error) {
	if s.association == "" {
		return true, nil
	}
	association, isObject := dto.PythonGet(notification, "association_id")
	if !isObject {
		return false, errCrash
	}
	return dto.PythonStr(association) == s.association, nil
}

func (c *notificationConsumer) listing(s *session, broadcasts []*string) ([]service.Listed, error) {
	items, _, err := s.h.notifications.List(s.ctx, s.user.ID, broadcasts)
	return items, err
}

func (c *notificationConsumer) fetch(s *session) error {
	broadcasts, err := s.h.notifications.Broadcasts(s.ctx, s.user.ID)
	if err != nil {
		return err
	}
	items, unread, err := s.h.notifications.List(s.ctx, s.user.ID, broadcasts)
	if err != nil {
		return err
	}
	data := []any{}
	if s.association != "" {
		unread = 0
	}
	for _, item := range items {
		if ok, _ := c.visible(s, item.Value); !ok {
			continue
		}
		data = append(data, item.Value)
		if s.association != "" && !item.Read {
			unread++
		}
	}
	return s.send(object("type", "notifications", "data", data, "unread", jsonInt(unread)))
}

func (c *notificationConsumer) markRead(s *session, id any) error {
	broadcasts, err := s.h.notifications.Broadcasts(s.ctx, s.user.ID)
	if err != nil {
		return err
	}
	read := true
	if s.association != "" {
		items, err := c.listing(s, broadcasts)
		if err != nil {
			return err
		}
		read = false
		for _, item := range items {
			stored, _ := lookup(item.Value, "id")
			if ok, _ := c.visible(s, item.Value); ok && dto.PythonStr(stored) == dto.PythonStr(id) {
				read = true
				break
			}
		}
	}
	if read {
		if err := s.h.notifications.Read(s.ctx, s.user.ID, id, broadcasts); err != nil {
			return err
		}
	}
	return s.send(object("type", "read_confirmed", "id", id))
}

func (c *notificationConsumer) markAllRead(s *session) error {
	broadcasts, err := s.h.notifications.Broadcasts(s.ctx, s.user.ID)
	if err != nil {
		return err
	}
	if s.association != "" {
		items, err := c.listing(s, broadcasts)
		if err != nil {
			return err
		}
		for _, item := range items {
			if ok, _ := c.visible(s, item.Value); !ok {
				continue
			}
			id, _ := lookup(item.Value, "id")
			if err := s.h.notifications.Read(s.ctx, s.user.ID, id, broadcasts); err != nil {
				return err
			}
		}
	} else {
		s.h.notifications.ReadAll(s.ctx, s.user.ID, broadcasts)
	}
	return s.send(object("type", "all_read_confirmed"))
}

// event is the Channels dispatch of a group message to the handler named by
// its type ("." becomes "_"). Types without a declared event handler raise.
func (c *notificationConsumer) event(s *session, message domain.RequestObject) error {
	value, present := lookup(message, "type")
	kind, isText := value.(string)
	if !present || !isText || strings.HasPrefix(kind, "_") {
		return errCrash
	}
	switch name := strings.ReplaceAll(kind, ".", "_"); name {
	case "notification_push":
		notification, present := lookup(message, "notification")
		if !present {
			return errCrash
		}
		ok, err := c.visible(s, notification)
		if err != nil || !ok {
			return err
		}
		return s.send(object("type", "notification_push", "notification", notification))
	case "staff_board_changed":
		// Permissions may have changed since this socket connected.
		association, err := s.h.notifications.ReadableAssociation(s.ctx, s.user.ID)
		if err != nil {
			return err
		}
		if association != "" && association == c.staffBoard {
			return s.send(object("type", "staff_board_changed"))
		}
		return nil
	case "restore_progress", "export_progress", "export_completed", "export_failed":
		if s.association != "" {
			return nil
		}
		value, present := lookup(message, "payload")
		payload, isObject := value.(domain.RequestObject)
		if !present || !isObject {
			return errCrash
		}
		// {'type': name, **payload}: a payload 'type' replaces the value in place.
		result := object("type", name)
		for _, field := range payload {
			if field.Name == "type" {
				result[0].Value = field.Value
				continue
			}
			result = append(result, field)
		}
		return s.send(result)
	}
	return errCrash
}
