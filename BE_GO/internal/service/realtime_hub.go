package service

import (
	"regexp"
	"sync"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// RealtimeHub is the process-local group membership of Go WebSocket
// connections. Group messages arrive from Django's mirrored channel layer
// (one pub/sub envelope per group_send) and are delivered to members like a
// Channels group: at most once, dropped for a member whose queue is full.
type RealtimeHub struct {
	mu     sync.Mutex
	groups map[string]map[*RealtimeMember]struct{}
}

// RealtimeMember is one connection's channel. Its queue has the Channels
// Redis layer's default per-channel capacity.
type RealtimeMember struct {
	queue chan domain.RequestObject
}

const realtimeCapacity = 100

func NewRealtimeHub() *RealtimeHub {
	return &RealtimeHub{groups: map[string]map[*RealtimeMember]struct{}{}}
}

func NewRealtimeMember() *RealtimeMember {
	return &RealtimeMember{queue: make(chan domain.RequestObject, realtimeCapacity)}
}

// Messages yields the group messages delivered to this member.
func (m *RealtimeMember) Messages() <-chan domain.RequestObject { return m.queue }

var groupNamePattern = regexp.MustCompile(`^[a-zA-Z\p{Nd}\-_.]+\n?$`)

// ValidGroupName is Channels' valid_group_name: fewer than 100 characters
// matching ^[a-zA-Z\d\-_.]+$ (a str pattern: \d is any Unicode digit and $ also
// matches before a final newline).
func ValidGroupName(name string) bool {
	return utf8.RuneCountInString(name) < 100 && groupNamePattern.MatchString(name)
}

func (h *RealtimeHub) Join(group string, member *RealtimeMember) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.groups[group] == nil {
		h.groups[group] = map[*RealtimeMember]struct{}{}
	}
	h.groups[group][member] = struct{}{}
}

func (h *RealtimeHub) Leave(group string, member *RealtimeMember) {
	h.mu.Lock()
	defer h.mu.Unlock()
	delete(h.groups[group], member)
	if len(h.groups[group]) == 0 {
		delete(h.groups, group)
	}
}

// LeaveAll removes a member from every group (a connection that ended
// without its disconnect handler running).
func (h *RealtimeHub) LeaveAll(member *RealtimeMember) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for group, members := range h.groups {
		delete(members, member)
		if len(members) == 0 {
			delete(h.groups, group)
		}
	}
}

// Deliver sends one group message to the group's current members.
func (h *RealtimeHub) Deliver(group string, message domain.RequestObject) {
	h.mu.Lock()
	members := make([]*RealtimeMember, 0, len(h.groups[group]))
	for member := range h.groups[group] {
		members = append(members, member)
	}
	h.mu.Unlock()
	for _, member := range members {
		select {
		case member.queue <- message:
		default:
		}
	}
}
