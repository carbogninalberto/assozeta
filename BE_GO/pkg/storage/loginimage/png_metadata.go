package loginimage

import (
	"bytes"
	"compress/zlib"
	"context"
	"errors"
	"io"
	"unicode/utf8"
)

const pngTextChunkLimit = 1 << 20
const pngTextMemoryLimit = 64 << 20

func (s *pngHeaderState) textChunk(ctx context.Context, kind string, body []byte) error {
	split := bytes.IndexByte(body, 0)
	if kind == "iCCP" {
		if split < 0 || split+1 >= len(body) || body[split+1] != 0 {
			return ErrPNGHeader
		}
		_, _, err := pngInflateText(ctx, body[split+2:])
		return err
	}
	key, value := body, []byte(nil)
	if split >= 0 {
		key, value = body[:split], body[split+1:]
	}
	international := kind == "iTXt"
	if international {
		if split < 0 || len(value) < 2 {
			return nil
		}
		flag, method := value[0], value[1]
		pieces := bytes.SplitN(value[2:], []byte{0}, 3)
		if len(pieces) != 3 {
			return nil
		}
		value = pieces[2]
		if flag != 0 {
			if method != 0 {
				return nil
			}
			var bad bool
			var err error
			value, bad, err = pngInflateText(ctx, value)
			if err != nil {
				return err
			}
			if bad {
				return nil
			}
		}
		if !utf8.Valid(pieces[0]) || !utf8.Valid(pieces[1]) || !utf8.Valid(value) {
			return nil
		}
	} else if kind == "zTXt" {
		if len(value) > 0 {
			if value[0] != 0 {
				return ErrPNGHeader
			}
			value = value[1:]
		}
		var err error
		value, _, err = pngInflateText(ctx, value)
		if err != nil {
			return err
		}
	}
	if len(key) == 0 && !international {
		return nil
	}
	length := len(value)
	if international {
		length = utf8.RuneCount(value)
	}
	s.textMemory += int64(length)
	if s.textMemory > pngTextMemoryLimit {
		return ErrPNGHeader
	}
	switch string(key) {
	case "comment":
		if len(value) == 0 {
			s.header.Comment = nil
		} else if international {
			s.header.Comment = bytes.Clone(value)
		} else {
			out := make([]byte, 0, len(value))
			for i, c := range value {
				if i&4095 == 0 {
					if err := ctx.Err(); err != nil {
						return err
					}
				}
				out = utf8.AppendRune(out, rune(c))
			}
			s.header.Comment = out
		}
	case "interlace":
		s.header.Interlace = len(value) > 0
	case "default_image":
		s.header.DefaultImage = len(value) > 0
	case "bbox":
		s.hasBBox = true
		s.header.InvalidTile = true
		s.header.Tile = PNGRect{}
	case "disposal":
		s.disposal = -1
	}
	return ctx.Err()
}

// Pillow's metadata inflater accepts incomplete streams and ignores bytes after
// the first zlib stream. Malformed streams have chunk-specific fallback rules.
// Only the bounded decompressed payload is retained; no full text dictionary.
func pngInflateText(ctx context.Context, data []byte) ([]byte, bool, error) {
	if err := ctx.Err(); err != nil {
		return nil, false, err
	}
	reader, err := zlib.NewReader(&pngContextReader{ctx: ctx, reader: bytes.NewReader(data)})
	if err != nil {
		if ctx.Err() != nil {
			return nil, false, ctx.Err()
		}
		return nil, !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF), nil
	}
	defer reader.Close()
	output, err := io.ReadAll(io.LimitReader(reader, pngTextChunkLimit+1))
	if ctx.Err() != nil {
		return nil, false, ctx.Err()
	}
	if len(output) > pngTextChunkLimit {
		return nil, false, ErrPNGHeader
	}
	if err != nil && !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF) {
		return nil, true, nil
	}
	return output, false, nil
}
