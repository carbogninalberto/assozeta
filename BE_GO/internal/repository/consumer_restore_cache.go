package repository

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"math"
	"math/big"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
	"github.com/redis/go-redis/v9"
)

// ConsumerRestoreCache is Django's default cache (django-redis, database 1,
// key prefix ":1:") as the export and restore tasks use it: cache.set of a
// pickled dict (pickle.HIGHEST_PROTOCOL), cache.get, cache.delete.
type ConsumerRestoreCache struct{ client *redis.Client }

func NewConsumerRestoreCache(client *redis.Client) (*ConsumerRestoreCache, error) {
	if client == nil || client.Options().DB != 1 {
		return nil, errors.New("consumer restore cache requires Django cache database 1")
	}
	return &ConsumerRestoreCache{client: client}, nil
}

// Set is cache.set(key, value, timeout).
func (c *ConsumerRestoreCache) Set(ctx context.Context, key string, value associationexport.Dict, timeout time.Duration) error {
	data, err := PickleDict(value)
	if err != nil {
		return err
	}
	return c.client.Set(ctx, ":1:"+key, data, timeout).Err()
}

// Delete is cache.delete(key).
func (c *ConsumerRestoreCache) Delete(ctx context.Context, key string) error {
	return c.client.Del(ctx, ":1:"+key).Err()
}

// TaskOf reports cache.get(key).get('task_id') for a cached dict: present is
// false for a miss; isDict false for a value that is not a dict.
func (c *ConsumerRestoreCache) TaskOf(ctx context.Context, key string) (taskID any, present, isDict bool, err error) {
	raw, err := c.client.Get(ctx, ":1:"+key).Bytes()
	if errors.Is(err, redis.Nil) {
		return nil, false, false, nil
	}
	if err != nil {
		return nil, false, false, err
	}
	value, err := unpickleDataRestoreProgress(raw)
	if err != nil {
		return nil, true, false, err
	}
	dict, ok := value.(*PickledDict)
	if !ok {
		return nil, true, false, nil
	}
	return dict.Values["task_id"], true, true, nil
}

// PickleDict is a protocol 5 pickle of a dict of str keys with str, int,
// bool, None, float, list and dict values. Python unpickles it to the same
// value pickle.dumps(value, 5) builds; the opcode stream may differ where
// CPython's memo reuses an identical str object (BINGET instead of a second
// copy).
func PickleDict(value associationexport.Dict) ([]byte, error) {
	p := &dictPickler{}
	if err := p.save(value); err != nil {
		return nil, err
	}
	body := append(p.out, '.')
	out := []byte{0x80, 5}
	if len(body) >= 4 {
		out = append(out, 0x95)
		out = binary.LittleEndian.AppendUint64(out, uint64(len(body)))
	}
	return append(out, body...), nil
}

type dictPickler struct{ out []byte }

func (p *dictPickler) memoize() { p.out = append(p.out, 0x94) }

func (p *dictPickler) save(value any) error {
	switch v := value.(type) {
	case nil:
		p.out = append(p.out, 'N')
	case bool:
		if v {
			p.out = append(p.out, 0x88)
		} else {
			p.out = append(p.out, 0x89)
		}
	case int:
		p.saveInt(big.NewInt(int64(v)))
	case int64:
		p.saveInt(big.NewInt(v))
	case associationexport.Int:
		n, ok := new(big.Int).SetString(string(v), 10)
		if !ok {
			return fmt.Errorf("pickle: int %q", v)
		}
		p.saveInt(n)
	case associationexport.Float:
		p.out = append(p.out, 'G')
		p.out = binary.BigEndian.AppendUint64(p.out, math.Float64bits(float64(v)))
	case string:
		data := []byte(v)
		switch {
		case len(data) < 256:
			p.out = append(p.out, 0x8c, byte(len(data)))
		default:
			p.out = append(p.out, 'X')
			p.out = binary.LittleEndian.AppendUint32(p.out, uint32(len(data)))
		}
		p.out = append(p.out, data...)
		p.memoize()
	case []any:
		p.out = append(p.out, ']')
		p.memoize()
		if err := p.batch(len(v), func(i int) error { return p.save(v[i]) }, 'a', 'e'); err != nil {
			return err
		}
	case associationexport.Dict:
		p.out = append(p.out, '}')
		p.memoize()
		if err := p.batch(len(v), func(i int) error {
			if err := p.save(v[i].Key); err != nil {
				return err
			}
			return p.save(v[i].Value)
		}, 's', 'u'); err != nil {
			return err
		}
	default:
		return fmt.Errorf("pickle: unsupported %T", value)
	}
	return nil
}

// batch is _batch_appends / _batch_setitems: one item uses the single
// opcode, larger groups MARK ... APPENDS/SETITEMS in batches of 1000.
func (p *dictPickler) batch(n int, item func(int) error, single, multiple byte) error {
	for start := 0; start < n; start += 1000 {
		end := min(start+1000, n)
		if end-start == 1 {
			if err := item(start); err != nil {
				return err
			}
			p.out = append(p.out, single)
			continue
		}
		p.out = append(p.out, '(')
		for i := start; i < end; i++ {
			if err := item(i); err != nil {
				return err
			}
		}
		p.out = append(p.out, multiple)
	}
	return nil
}

func (p *dictPickler) saveInt(n *big.Int) {
	if n.Sign() >= 0 && n.BitLen() <= 8 {
		p.out = append(p.out, 'K', byte(n.Int64()))
		return
	}
	if n.Sign() >= 0 && n.BitLen() <= 16 {
		p.out = append(p.out, 'M')
		p.out = binary.LittleEndian.AppendUint16(p.out, uint16(n.Int64()))
		return
	}
	if n.IsInt64() && n.Int64() >= math.MinInt32 && n.Int64() <= math.MaxInt32 {
		p.out = append(p.out, 'J')
		p.out = binary.LittleEndian.AppendUint32(p.out, uint32(int32(n.Int64())))
		return
	}
	// LONG1: two's complement little endian, minimal length.
	encoded := pythonLongBytes(n)
	p.out = append(p.out, 0x8a, byte(len(encoded)))
	p.out = append(p.out, encoded...)
}

func pythonLongBytes(n *big.Int) []byte {
	if n.Sign() == 0 {
		return nil
	}
	length := n.BitLen()/8 + 1
	value := new(big.Int).Set(n)
	if n.Sign() < 0 {
		value.Add(value, new(big.Int).Lsh(big.NewInt(1), uint(length*8)))
	}
	big := value.FillBytes(make([]byte, length))
	out := make([]byte, length)
	for i := range big {
		out[i] = big[length-1-i]
	}
	// Drop a redundant sign byte, as encode_long does.
	if length > 1 && ((out[length-1] == 0 && out[length-2]&0x80 == 0) || (out[length-1] == 0xff && out[length-2]&0x80 != 0)) {
		out = out[:length-1]
	}
	return out
}
