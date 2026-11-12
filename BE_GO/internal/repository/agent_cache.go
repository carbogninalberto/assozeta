package repository

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strconv"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/redis/go-redis/v9"
)

// AgentCache is the agent's state in Django's cache (django-redis 5.4,
// Redis DB 1, KEY_PREFIX ”, version 1): application.chat.throttling's
// WebSocketThrottle (ws_throttle:<user>) and ConcurrencyGuard
// (ws_concurrent:<user>), and the consumer's agent_save_info:<user>.
type AgentCache struct{ client *redis.Client }

func NewAgentCache(client *redis.Client) (*AgentCache, error) {
	if client == nil || client.Options().DB != 1 {
		return nil, errors.New("agent cache requires Django cache database 1")
	}
	return &AgentCache{client: client}, nil
}

// ErrAgentCacheShape is a stored value outside the ported subset.
var ErrAgentCacheShape = refusal.New("agent cache value not ported")

// django-redis' _incr script: INCRBY only when the key exists.
var agentIncr = redis.NewScript(`
                    local exists = redis.call('EXISTS', KEYS[1])
                    if (exists == 1) then
                        return redis.call('INCRBY', KEYS[1], ARGV[1])
                    else return false end
                    `)

func agentKey(name string) string { return ":1:" + name }

// incr is cache.incr(key, delta): found=false is the ValueError for a missing key.
func (c *AgentCache) incr(ctx context.Context, key string, delta int64) (int64, bool, error) {
	value, err := agentIncr.Run(ctx, c.client, []string{agentKey(key)}, delta).Int64()
	if errors.Is(err, redis.Nil) {
		return 0, false, nil
	}
	var reply redis.Error
	if errors.As(err, &reply) {
		return 0, false, ErrAgentCacheShape // a pickled or out-of-range value (django-redis' slow path)
	}
	return value, err == nil, err
}

// setInt is cache.set(key, n, timeout): integers are stored as their digits.
func (c *AgentCache) setInt(ctx context.Context, key string, value int64, seconds int) error {
	return c.client.Set(ctx, agentKey(key), strconv.FormatInt(value, 10), time.Duration(seconds)*time.Second).Err()
}

// ThrottleAllowed is WebSocketThrottle(user).is_allowed() with max_messages=limit.
func (c *AgentCache) ThrottleAllowed(ctx context.Context, user string, allowed func(count int64) bool) (bool, error) {
	key := "ws_throttle:" + user
	count, found, err := c.incr(ctx, key, 1)
	if err != nil {
		return false, err
	}
	if !found {
		return true, c.setInt(ctx, key, 1, 60)
	}
	return allowed(count), nil
}

// GuardAcquire is ConcurrencyGuard(user).acquire() (max_sessions=1).
func (c *AgentCache) GuardAcquire(ctx context.Context, user string) (bool, error) {
	key := "ws_concurrent:" + user
	count, found, err := c.incr(ctx, key, 1)
	if err != nil {
		return false, err
	}
	if !found {
		return true, c.setInt(ctx, key, 1, 300)
	}
	if count > 1 {
		if _, found, err := c.incr(ctx, key, -1); err != nil || !found {
			if err == nil {
				err = ErrAgentCacheShape // cache.decr raises ValueError
			}
			return false, err
		}
		return false, nil
	}
	return true, nil
}

// GuardRelease is ConcurrencyGuard.release() for an acquired slot.
func (c *AgentCache) GuardRelease(ctx context.Context, user string) error {
	key := "ws_concurrent:" + user
	value, found, err := c.incr(ctx, key, -1)
	if err != nil && !errors.Is(err, ErrAgentCacheShape) {
		return err
	}
	if !found || value <= 0 {
		return c.client.Del(ctx, agentKey(key)).Err()
	}
	return nil
}

// SetSaveInfo is cache.set('agent_save_info:<user>', {...}, 3600).
func (c *AgentCache) SetSaveInfo(ctx context.Context, user string, saveInfo, filename any) error {
	data, err := PickleAgentSaveInfo(saveInfo, filename)
	if err != nil {
		return err
	}
	return c.client.Set(ctx, agentKey("agent_save_info:"+user), data, time.Hour).Err()
}

// SaveInfo is cache.get('agent_save_info:<user>'): nil for a miss.
func (c *AgentCache) SaveInfo(ctx context.Context, user string) (any, error) {
	data, err := c.client.Get(ctx, agentKey("agent_save_info:"+user)).Bytes()
	if errors.Is(err, redis.Nil) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if _, isInt := pythonIntBytes(data); isInt {
		return nil, ErrAgentCacheShape
	}
	value, err := unpickleDataRestoreProgress(data)
	if err != nil {
		return nil, ErrAgentCacheShape
	}
	return agentOrderedPickled(value)
}

func agentOrderedPickled(value any) (any, error) {
	switch typed := value.(type) {
	case *PickledDict:
		result := make(domain.RequestObject, 0, len(typed.Keys))
		for _, key := range typed.Keys {
			item, err := agentOrderedPickled(typed.Values[key])
			if err != nil {
				return nil, err
			}
			result = append(result, domain.RequestField{Name: key, Value: item})
		}
		return result, nil
	case []any:
		result := make([]any, len(typed))
		for i, item := range typed {
			converted, err := agentOrderedPickled(item)
			if err != nil {
				return nil, err
			}
			result[i] = converted
		}
		return result, nil
	case int64:
		return json.Number(strconv.FormatInt(typed, 10)), nil
	case float64, string, bool, nil:
		return typed, nil
	}
	return nil, ErrAgentCacheShape
}

// PickleAgentSaveInfo is pickle.dumps({'save_info': export_data['save_info'],
// 'filename': export_data.get('filename', 'export')}, 4), where export_data
// is json.loads of the tool result: the two keys are code constants, the
// decoder shares one str object per distinct object key across the
// document, values are new objects except the interpreter's empty and
// one-character Latin-1 strings.
func PickleAgentSaveInfo(saveInfo, filename any) ([]byte, error) {
	p := &agentPickler{releasePickler{out: []byte{0x80, 0x04}, frameStart: -1, memo: map[string]int{}}, 0}
	p.boundary()
	p.write('}')
	p.memo["outer"] = len(p.memo)
	p.write(0x94)
	p.write('(')
	p.saveString("const:save_info", "save_info")
	if err := p.save(saveInfo); err != nil {
		return nil, err
	}
	p.saveString("const:filename", "filename")
	if err := p.save(filename); err != nil {
		return nil, err
	}
	p.write('u')
	p.write('.')
	p.commit()
	return p.out, nil
}

type agentPickler struct {
	releasePickler
	fresh int
}

func (p *agentPickler) identity(text string, key bool) string {
	if text == "" {
		return "single:"
	}
	if r, size := utf8.DecodeRuneInString(text); size == len(text) && r < 256 && r != utf8.RuneError {
		return "single:" + text
	}
	if key {
		return "key:" + text
	}
	p.fresh++
	return "fresh:" + strconv.Itoa(p.fresh)
}

func (p *agentPickler) container(opcode byte) {
	p.boundary()
	p.fresh++
	p.write(opcode)
	p.memo["container:"+strconv.Itoa(p.fresh)] = len(p.memo)
	p.write(0x94)
}

func (p *agentPickler) save(value any) error {
	switch typed := value.(type) {
	case nil:
		p.saveNone()
	case bool:
		p.saveBool(typed)
	case string:
		if !utf8.ValidString(typed) {
			return ErrAgentCacheShape
		}
		p.saveString(p.identity(typed, false), typed)
	case json.Number:
		return p.saveNumber(string(typed))
	case float64:
		p.boundary()
		p.write(append([]byte{'G'}, binary.BigEndian.AppendUint64(nil, math.Float64bits(typed))...)...)
	case domain.RequestObject:
		p.container('}')
		switch len(typed) {
		case 0:
		case 1:
			p.saveString(p.identity(typed[0].Name, true), typed[0].Name)
			if err := p.save(typed[0].Value); err != nil {
				return err
			}
			p.write('s')
		default:
			for start := 0; start < len(typed); start += 1000 {
				p.write('(')
				for i := start; i < len(typed) && i < start+1000; i++ {
					p.saveString(p.identity(typed[i].Name, true), typed[i].Name)
					if err := p.save(typed[i].Value); err != nil {
						return err
					}
				}
				p.write('u')
			}
		}
	case []any:
		p.container(']')
		switch len(typed) {
		case 0:
		case 1:
			if err := p.save(typed[0]); err != nil {
				return err
			}
			p.write('a')
		default:
			for start := 0; start < len(typed); start += 1000 {
				p.write('(')
				for i := start; i < len(typed) && i < start+1000; i++ {
					if err := p.save(typed[i]); err != nil {
						return err
					}
				}
				p.write('e')
			}
		}
	default:
		return ErrAgentCacheShape
	}
	return nil
}

func (p *agentPickler) saveNumber(token string) error {
	integer, ok := new(big.Int).SetString(token, 10)
	if !ok {
		value, err := strconv.ParseFloat(token, 64)
		if err != nil || math.IsInf(value, 0) {
			return ErrAgentCacheShape
		}
		p.boundary()
		p.write(append([]byte{'G'}, binary.BigEndian.AppendUint64(nil, math.Float64bits(value))...)...)
		return nil
	}
	if integer.IsInt64() {
		p.saveInt(integer.Int64())
		return nil
	}
	data := longBytes(integer)
	p.boundary()
	if len(data) < 256 {
		p.write(append([]byte{0x8a, byte(len(data))}, data...)...)
		return nil
	}
	header := []byte{0x8b, 0, 0, 0, 0}
	binary.LittleEndian.PutUint32(header[1:], uint32(len(data)))
	p.write(append(header, data...)...)
	return nil
}
