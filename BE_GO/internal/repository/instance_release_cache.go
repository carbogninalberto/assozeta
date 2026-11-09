package repository

import (
	"context"
	"encoding/binary"
	"errors"
	"math/big"
	"strconv"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
	"github.com/redis/go-redis/v9"
)

// Django cache keys (django-redis, KEY_PREFIX ”, version 1) of
// InstanceReleasesView: the catalog (600 s) and the manual refresh bound
// (cache.add, 30 s).
const (
	ReleaseCatalogKey = ":1:instance:stable-releases:v1"
	ReleaseRefreshKey = ":1:instance:stable-releases:v1:refresh"
)

// ErrReleaseCacheShape is a cached catalog value this port does not read:
// anything but a list of normalize_release dicts (Django would render or
// fail on it).
var ErrReleaseCacheShape = errors.New("cached release catalog not ported")

// ReleaseCache reads and writes the catalog in Django's cache (Redis DB 1).
type ReleaseCache struct{ client *redis.Client }

func NewReleaseCache(client *redis.Client) (*ReleaseCache, error) {
	if client == nil || client.Options().DB != 1 {
		return nil, errors.New("release cache requires Django cache database 1")
	}
	return &ReleaseCache{client: client}, nil
}

// Catalog is cache.get(CATALOG_KEY): nil, false on a miss.
func (c *ReleaseCache) Catalog(ctx context.Context) ([]instanceprobe.Release, bool, error) {
	raw, err := c.client.Get(ctx, ReleaseCatalogKey).Bytes()
	if errors.Is(err, redis.Nil) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	value, err := unpickleCatalog(raw)
	if err != nil {
		return nil, false, ErrReleaseCacheShape
	}
	items, ok := value.([]any)
	if !ok {
		return nil, false, ErrReleaseCacheShape
	}
	releases := make([]instanceprobe.Release, 0, len(items))
	for _, item := range items {
		release, ok := cachedRelease(item)
		if !ok {
			return nil, false, ErrReleaseCacheShape
		}
		releases = append(releases, release)
	}
	return releases, true, nil
}

func cachedRelease(item any) (instanceprobe.Release, bool) {
	var r instanceprobe.Release
	object, ok := item.(map[string]any)
	if !ok || len(object) != 9 {
		return r, false
	}
	id, ok := object["id"].(int64)
	if !ok {
		return r, false
	}
	r.ID = id
	for key, target := range map[string]*string{"tag": &r.Tag, "version": &r.Version, "name": &r.Name,
		"published_at": &r.PublishedAt, "notes": &r.Notes, "url": &r.URL} {
		text, ok := object[key].(string)
		if !ok {
			return r, false
		}
		*target = text
	}
	if _, ok := instanceprobe.VersionTuple(r.Tag); !ok {
		return r, false
	}
	if r.ArtifactsReady, ok = object["artifacts_ready"].(bool); !ok {
		return r, false
	}
	switch reason := object["unavailable_reason"].(type) {
	case nil:
	case string:
		r.UnavailableReason = &reason
	default:
		return r, false
	}
	r.NameIsTag = r.Name == r.Tag
	return r, true
}

const maxCatalogBytes = 64 << 20

type catalogMark struct{}

// unpickleCatalog reads the opcodes CPython's protocol 4 pickler writes for a
// list of release dicts (frames anywhere between opcodes, memo references,
// str, int, bool, None, list and dict); anything else is an error. It is a
// data decoder only: no global, reduce or object construction exists.
func unpickleCatalog(data []byte) (any, error) {
	bad := ErrReleaseCacheShape
	if len(data) < 3 || len(data) > maxCatalogBytes || data[0] != 0x80 || data[1] != 4 {
		return nil, bad
	}
	pos := 2
	var stack []any
	var memo []any
	lists := map[*[]any]bool{}
	take := func(n int) ([]byte, bool) {
		if n < 0 || n > len(data)-pos {
			return nil, false
		}
		value := data[pos : pos+n]
		pos += n
		return value, true
	}
	pop := func() (any, bool) {
		if len(stack) == 0 {
			return nil, false
		}
		value := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		return value, true
	}
	marked := func() ([]any, bool) {
		for i := len(stack) - 1; i >= 0; i-- {
			if _, ok := stack[i].(catalogMark); ok {
				items := append([]any(nil), stack[i+1:]...)
				stack = stack[:i]
				return items, true
			}
		}
		return nil, false
	}
	for pos < len(data) {
		op := data[pos]
		pos++
		switch op {
		case 0x95: // FRAME
			raw, ok := take(8)
			if !ok || binary.LittleEndian.Uint64(raw) > uint64(len(data)-pos) {
				return nil, bad
			}
		case ']':
			list := &[]any{}
			lists[list] = true
			stack = append(stack, list)
		case '}':
			stack = append(stack, map[string]any{})
		case '(':
			stack = append(stack, catalogMark{})
		case 0x94: // MEMOIZE
			if len(stack) == 0 {
				return nil, bad
			}
			memo = append(memo, stack[len(stack)-1])
		case 'h', 'j':
			width := 1
			if op == 'j' {
				width = 4
			}
			raw, ok := take(width)
			if !ok {
				return nil, bad
			}
			index := int(raw[0])
			if width == 4 {
				index = int(binary.LittleEndian.Uint32(raw))
			}
			if index >= len(memo) {
				return nil, bad
			}
			stack = append(stack, memo[index])
		case 0x8c, 'X':
			width := 1
			if op == 'X' {
				width = 4
			}
			raw, ok := take(width)
			if !ok {
				return nil, bad
			}
			size := int(raw[0])
			if width == 4 {
				size = int(binary.LittleEndian.Uint32(raw))
			}
			text, ok := take(size)
			if !ok || !utf8.Valid(text) {
				return nil, bad
			}
			stack = append(stack, string(text))
		case 'K', 'M', 'J':
			width := map[byte]int{'K': 1, 'M': 2, 'J': 4}[op]
			raw, ok := take(width)
			if !ok {
				return nil, bad
			}
			switch op {
			case 'K':
				stack = append(stack, int64(raw[0]))
			case 'M':
				stack = append(stack, int64(binary.LittleEndian.Uint16(raw)))
			default:
				stack = append(stack, int64(int32(binary.LittleEndian.Uint32(raw))))
			}
		case 0x8a: // LONG1
			raw, ok := take(1)
			if !ok {
				return nil, bad
			}
			body, ok := take(int(raw[0]))
			if !ok || len(body) > 8 {
				return nil, bad
			}
			var value int64
			for i := len(body) - 1; i >= 0; i-- {
				value = value<<8 | int64(body[i])
			}
			if len(body) > 0 && len(body) < 8 && body[len(body)-1]&0x80 != 0 {
				value -= 1 << (8 * len(body))
			}
			stack = append(stack, value)
		case 0x88, 0x89:
			stack = append(stack, op == 0x88)
		case 'N':
			stack = append(stack, nil)
		case 'u', 's':
			var items []any
			ok := true
			if op == 'u' {
				items, ok = marked()
			} else if len(stack) >= 2 {
				items = stack[len(stack)-2:]
				stack = stack[:len(stack)-2]
			} else {
				ok = false
			}
			if !ok || len(items)%2 != 0 || len(stack) == 0 {
				return nil, bad
			}
			object, isMap := stack[len(stack)-1].(map[string]any)
			if !isMap {
				return nil, bad
			}
			for i := 0; i < len(items); i += 2 {
				key, isString := items[i].(string)
				if !isString {
					return nil, bad
				}
				object[key] = items[i+1]
			}
		case 'e', 'a':
			var items []any
			ok := true
			if op == 'e' {
				items, ok = marked()
			} else {
				var item any
				item, ok = pop()
				items = []any{item}
			}
			if !ok || len(stack) == 0 {
				return nil, bad
			}
			list, isList := stack[len(stack)-1].(*[]any)
			if !isList {
				return nil, bad
			}
			*list = append(*list, items...)
		case '.':
			value, ok := pop()
			if !ok || len(stack) != 0 || pos != len(data) {
				return nil, bad
			}
			list, isList := value.(*[]any)
			if !isList {
				return nil, bad
			}
			for _, item := range *list {
				if _, nested := item.(*[]any); nested {
					return nil, bad
				}
			}
			return *list, nil
		default:
			return nil, bad
		}
	}
	return nil, bad
}

// SetCatalog is cache.set(CATALOG_KEY, releases, timeout=600).
func (c *ReleaseCache) SetCatalog(ctx context.Context, releases []instanceprobe.Release) error {
	return c.client.Set(ctx, ReleaseCatalogKey, PickleReleases(releases), 600*time.Second).Err()
}

// AddRefresh is cache.add(CATALOG_KEY + ':refresh', True, timeout=30).
func (c *ReleaseCache) AddRefresh(ctx context.Context) (bool, error) {
	return c.client.SetNX(ctx, ReleaseRefreshKey, []byte{0x80, 0x04, 0x88, '.'}, 30*time.Second).Result()
}

const (
	frameTarget  = 64 * 1024
	frameMinimum = 4
	frameHeader  = 9
)

// releasePickler is CPython's C pickler at protocol 4 (pickle.DEFAULT_PROTOCOL
// in 3.13, django-redis' default) for a list of release dicts: framing
// (a frame of 64 KiB or more is committed when the next save() starts, a
// frame under 4 bytes loses its header, payloads of 64 KiB or more are
// written unframed), MEMOIZE for every container and string, BINGET for a
// string object pickled before.
type releasePickler struct {
	out        []byte
	frameStart int
	memo       map[string]int
}

func (p *releasePickler) write(b ...byte) {
	if p.frameStart < 0 {
		p.frameStart = len(p.out)
		p.out = append(p.out, make([]byte, frameHeader)...)
	}
	p.out = append(p.out, b...)
}

func (p *releasePickler) commit() {
	if p.frameStart < 0 {
		return
	}
	length := len(p.out) - p.frameStart - frameHeader
	if length >= frameMinimum {
		p.out[p.frameStart] = 0x95
		binary.LittleEndian.PutUint64(p.out[p.frameStart+1:], uint64(length))
	} else {
		copy(p.out[p.frameStart:], p.out[p.frameStart+frameHeader:])
		p.out = p.out[:len(p.out)-frameHeader]
	}
	p.frameStart = -1
}

// boundary is the commit at the start of every save() (pickle.py's
// framer.commit_frame(); the C pickler matches it): a frame of 64 KiB or
// more is closed before the next object.
func (p *releasePickler) boundary() {
	if p.frameStart >= 0 && len(p.out)-p.frameStart-frameHeader >= frameTarget {
		p.commit()
	}
}

// saveString pickles a str object; identity names the Python object (equal
// identities are the same object, so the second save is a BINGET).
func (p *releasePickler) saveString(identity, text string) {
	p.boundary()
	if index, ok := p.memo[identity]; ok {
		if index < 256 {
			p.write('h', byte(index))
		} else {
			var b [4]byte
			binary.LittleEndian.PutUint32(b[:], uint32(index))
			p.write(append([]byte{'j'}, b[:]...)...)
		}
		return
	}
	data := []byte(text)
	var header []byte
	if len(data) <= 0xff {
		header = []byte{0x8c, byte(len(data))}
	} else {
		header = make([]byte, 5)
		header[0] = 'X'
		binary.LittleEndian.PutUint32(header[1:], uint32(len(data)))
	}
	if len(data) >= frameTarget {
		p.commit()
		p.out = append(p.out, header...)
		p.out = append(p.out, data...)
	} else {
		p.write(header...)
		p.write(data...)
	}
	index := len(p.memo)
	p.write(0x94)
	p.memo[identity] = index
}

func (p *releasePickler) saveInt(value int64) {
	p.boundary()
	switch {
	case value >= 0 && value <= 0xff:
		p.write('K', byte(value))
	case value >= 0 && value <= 0xffff:
		p.write('M', byte(value), byte(value>>8))
	case value >= -1<<31 && value <= 1<<31-1:
		var b [4]byte
		binary.LittleEndian.PutUint32(b[:], uint32(int32(value)))
		p.write(append([]byte{'J'}, b[:]...)...)
	default:
		data := longBytes(big.NewInt(value))
		p.write(append([]byte{0x8a, byte(len(data))}, data...)...)
	}
}

// longBytes is encode_long: little-endian two's complement, minimal length.
func longBytes(value *big.Int) []byte {
	if value.Sign() == 0 {
		return nil
	}
	n := value.BitLen()/8 + 1
	twos := new(big.Int).Set(value)
	if value.Sign() < 0 {
		twos.Add(twos, new(big.Int).Lsh(big.NewInt(1), uint(n*8)))
	}
	be := twos.FillBytes(make([]byte, n))
	out := make([]byte, n)
	for i := range be {
		out[i] = be[n-1-i]
	}
	if value.Sign() < 0 && n > 1 && out[n-1] == 0xff && out[n-2]&0x80 != 0 {
		out = out[:n-1]
	}
	return out
}

func (p *releasePickler) saveBool(value bool) {
	p.boundary()
	if value {
		p.write(0x88)
	} else {
		p.write(0x89)
	}
}

func (p *releasePickler) saveNone() {
	p.boundary()
	p.write('N')
}

// stringIdentity names the str object: CPython shares the empty string and
// one-character Latin-1 strings; other strings from the feed are distinct
// objects.
func stringIdentity(fresh, text string) string {
	if text == "" {
		return "single:"
	}
	if r, size := utf8.DecodeRuneInString(text); size == len(text) && r < 256 && r != utf8.RuneError {
		return "single:" + text
	}
	return fresh
}

// PickleReleases is pickle.dumps(fetch_releases(), 4): the list of
// normalize_release dicts, whose keys and unavailable_reason are code
// constants (one object each), name the tag object when the feed's name is
// falsy and notes the empty string when the body is.
func PickleReleases(releases []instanceprobe.Release) []byte {
	p := &releasePickler{out: []byte{0x80, 0x04}, frameStart: -1, memo: map[string]int{}}
	p.write(']')
	p.memo["list"] = 0
	p.write(0x94)
	switch len(releases) {
	case 0:
	case 1:
		p.saveRelease(0, releases[0])
		p.write('a')
	default:
		for start := 0; start < len(releases); start += 1000 {
			p.write('(')
			for i := start; i < len(releases) && i < start+1000; i++ {
				p.saveRelease(i, releases[i])
			}
			p.write('e')
		}
	}
	p.write('.')
	p.commit()
	return p.out
}

func (p *releasePickler) saveRelease(index int, r instanceprobe.Release) {
	p.boundary()
	object := "release:" + strconv.Itoa(index) + ":"
	p.write('}')
	p.memo[object] = len(p.memo)
	p.write(0x94)
	p.write('(')
	key := func(name string) { p.saveString("key:"+name, name) }
	key("id")
	p.saveInt(r.ID)
	key("tag")
	tag := stringIdentity(object+"tag", r.Tag)
	p.saveString(tag, r.Tag)
	key("version")
	p.saveString(stringIdentity(object+"version", r.Version), r.Version)
	key("name")
	if r.NameIsTag {
		p.saveString(tag, r.Tag)
	} else {
		p.saveString(stringIdentity(object+"name", r.Name), r.Name)
	}
	key("published_at")
	p.saveString(stringIdentity(object+"published_at", r.PublishedAt), r.PublishedAt)
	key("notes")
	p.saveString(stringIdentity(object+"notes", r.Notes), r.Notes)
	key("url")
	p.saveString(stringIdentity(object+"url", r.URL), r.URL)
	key("artifacts_ready")
	p.saveBool(r.ArtifactsReady)
	key("unavailable_reason")
	if r.UnavailableReason == nil {
		p.saveNone()
	} else {
		p.saveString("const:unavailable_reason", *r.UnavailableReason)
	}
	p.write('u')
}
