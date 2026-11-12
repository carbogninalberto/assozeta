package pyzlib

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"strconv"
	"strings"
)

// Input generators mirroring TESTS/adapters/go/pyzlib_golden.py. Keep in sync.

var goldenWords = []string{
	"associazione", "socio", "quota", "rinnovo", "pagamento", "città",
	"perché", "attività", "è", "già", "più", "così", "ragione sociale",
	"codice fiscale", "iscrizione", "tesseramento", "ricevuta", "bonifico",
	"contanti", "Roma", "Milano", "Torino", "Napoli", "Firenze", "Bologna",
	"Venezia", "Genova", "Palermo", "Bari", "über", "naïve", "€",
}

type splitMix64 struct{ state uint64 }

func (r *splitMix64) next() uint64 {
	r.state += 0x9E3779B97F4A7C15
	z := r.state
	z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9
	z = (z ^ (z >> 27)) * 0x94D049BB133111EB
	return z ^ (z >> 31)
}

func word(r uint64) string { return goldenWords[r%uint64(len(goldenWords))] }

func genRandom(rng *splitMix64, size int) []byte {
	out := make([]byte, 0, size+8)
	for len(out) < size {
		out = binary.LittleEndian.AppendUint64(out, rng.next())
	}
	return out[:size]
}

func genText(rng *splitMix64, size int) []byte {
	var out []byte
	for len(out) < size {
		r := rng.next()
		out = append(out, word(r)...)
		if (r>>32)%11 == 0 {
			out = append(out, '\n')
		} else {
			out = append(out, ' ')
		}
	}
	return out[:size]
}

func genJSON(rng *splitMix64, size int) []byte {
	out := []byte("[")
	i := 0
	for len(out) < size {
		r := rng.next()
		i++
		nwords := 1 + r%12
		note := make([]string, 0, nwords)
		for k := uint64(0); k < nwords; k++ {
			note = append(note, word(rng.next()))
		}
		attivo := "false"
		if (r>>52)&1 == 1 {
			attivo = "true"
		}
		rec := fmt.Sprintf(`{"id": %d, "nome": "%s", "città": "%s", "importo": "%d.%02d", "attivo": %s, "note": "%s"}`,
			i, word(r>>8), word(r>>16), (r>>24)%100000, (r>>44)%100, attivo, strings.Join(note, " "))
		if i > 1 {
			out = append(out, ",\n"...)
		}
		out = append(out, rec...)
	}
	return out[:size]
}

func repeatTo(pat []byte, size int) []byte {
	if size == 0 {
		return []byte{}
	}
	return bytes.Repeat(pat, size/len(pat)+1)[:size]
}

func genMixed(rng *splitMix64, size int) []byte {
	var out []byte
	for len(out) < size {
		r := rng.next()
		kind := r % 5
		n := int(1 + (r>>8)%20000)
		var seg []byte
		switch kind {
		case 0:
			seg = genJSON(rng, n)
		case 1:
			seg = genRandom(rng, n)
		case 2:
			seg = bytes.Repeat([]byte{byte(r >> 32)}, n)
		case 3:
			seg = repeatTo(genRandom(rng, int(1+(r>>32)%7)), n)
		default:
			seg = genText(rng, n)
		}
		out = append(out, seg...)
	}
	return out[:size]
}

type goldenRecipe struct {
	Kind    string `json:"kind"`
	Hex     string `json:"hex"`
	Byte    int    `json:"byte"`
	Pattern string `json:"pattern"`
	Seed    uint64 `json:"seed"`
	Period  int    `json:"period"`
	Size    int    `json:"size"`
}

func buildInput(rc goldenRecipe) ([]byte, error) {
	rng := &splitMix64{state: rc.Seed}
	switch rc.Kind {
	case "hex":
		return hex.DecodeString(rc.Hex)
	case "run":
		return bytes.Repeat([]byte{byte(rc.Byte)}, rc.Size), nil
	case "pattern":
		pat, err := hex.DecodeString(rc.Pattern)
		if err != nil {
			return nil, err
		}
		return repeatTo(pat, rc.Size), nil
	case "random_period":
		return repeatTo(genRandom(rng, rc.Period), rc.Size), nil
	case "random":
		return genRandom(rng, rc.Size), nil
	case "text":
		return genText(rng, rc.Size), nil
	case "json":
		return genJSON(rng, rc.Size), nil
	case "mixed":
		return genMixed(rng, rc.Size), nil
	}
	return nil, fmt.Errorf("unknown recipe kind %s", strconv.Quote(rc.Kind))
}
