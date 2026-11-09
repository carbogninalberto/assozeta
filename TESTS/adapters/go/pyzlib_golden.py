"""Generate golden outputs for BE_GO/internal/pyzlib from CPython's zlib.

Each case compresses a deterministically generated input exactly the way
CPython's zipfile does for ZIP_DEFLATED members:

    zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)

Inputs are stored as recipes (generator kind + seed + size) that the Go test
regenerates byte for byte; the JSON keeps only lengths and SHA-256 digests,
plus the full output hex for small cases.

Run with the reference interpreter in isolated mode, from anywhere:

    python -I TESTS/adapters/go/pyzlib_golden.py [OUTPUT_JSON]

OUTPUT_JSON defaults to BE_GO/internal/pyzlib/testdata/zlib_golden.json.
The input generators must stay in sync with golden_gen_test.go.
"""

import hashlib
import json
import pathlib
import sys
import zlib

MASK64 = (1 << 64) - 1
SMALL_HEX_LIMIT = 512

WORDS = [
    "associazione", "socio", "quota", "rinnovo", "pagamento", "città",
    "perché", "attività", "è", "già", "più", "così", "ragione sociale",
    "codice fiscale", "iscrizione", "tesseramento", "ricevuta", "bonifico",
    "contanti", "Roma", "Milano", "Torino", "Napoli", "Firenze", "Bologna",
    "Venezia", "Genova", "Palermo", "Bari", "über", "naïve", "€",
]


class SplitMix64:
    def __init__(self, seed):
        self.state = seed & MASK64

    def next(self):
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
        return z ^ (z >> 31)


def gen_random(rng, size):
    out = bytearray()
    while len(out) < size:
        out += rng.next().to_bytes(8, "little")
    return bytes(out[:size])


def gen_text(rng, size):
    out = bytearray()
    while len(out) < size:
        r = rng.next()
        out += WORDS[r % len(WORDS)].encode()
        out += b"\n" if (r >> 32) % 11 == 0 else b" "
    return bytes(out[:size])


def gen_json(rng, size):
    out = bytearray(b"[")
    i = 0
    while len(out) < size:
        r = rng.next()
        i += 1
        nwords = 1 + r % 12
        note = []
        for _ in range(nwords):
            note.append(WORDS[rng.next() % len(WORDS)])
        rec = '{"id": %d, "nome": "%s", "città": "%s", "importo": "%d.%02d", "attivo": %s, "note": "%s"}' % (
            i,
            WORDS[(r >> 8) % len(WORDS)],
            WORDS[(r >> 16) % len(WORDS)],
            (r >> 24) % 100000,
            (r >> 44) % 100,
            "true" if (r >> 52) & 1 else "false",
            " ".join(note),
        )
        if i > 1:
            out += b",\n"
        out += rec.encode()
    return bytes(out[:size])


def gen_mixed(rng, size):
    out = bytearray()
    while len(out) < size:
        r = rng.next()
        kind = r % 5
        n = 1 + (r >> 8) % 20000
        if kind == 0:
            seg = gen_json(rng, n)
        elif kind == 1:
            seg = gen_random(rng, n)
        elif kind == 2:
            seg = bytes([(r >> 32) & 0xFF]) * n
        elif kind == 3:
            pat = gen_random(rng, 1 + (r >> 32) % 7)
            seg = (pat * (n // len(pat) + 1))[:n]
        else:
            seg = gen_text(rng, n)
        out += seg
    return bytes(out[:size])


GENERATORS = {
    "random": gen_random,
    "text": gen_text,
    "json": gen_json,
    "mixed": gen_mixed,
}


def build_input(recipe):
    kind = recipe["kind"]
    if kind == "hex":
        return bytes.fromhex(recipe["hex"])
    if kind == "run":
        return bytes([recipe["byte"]]) * recipe["size"]
    if kind == "pattern":
        pat = bytes.fromhex(recipe["pattern"])
        size = recipe["size"]
        return (pat * (size // len(pat) + 1))[:size]
    if kind == "random_period":
        pat = gen_random(SplitMix64(recipe["seed"]), recipe["period"])
        size = recipe["size"]
        return (pat * (size // len(pat) + 1))[:size]
    if kind in GENERATORS:
        return GENERATORS[kind](SplitMix64(recipe["seed"]), recipe["size"])
    raise ValueError("unknown recipe kind %r" % kind)


def zip_deflate(data, chunks=None):
    co = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
    if not chunks:
        return co.compress(data) + co.flush()
    out = []
    pos = 0
    i = 0
    while pos < len(data):
        n = chunks[i % len(chunks)]
        out.append(co.compress(data[pos:pos + n]))
        pos += n
        i += 1
    out.append(co.flush())
    return b"".join(out)


def cases():
    yield "empty", {"kind": "hex", "hex": ""}, None
    yield "one_byte", {"kind": "hex", "hex": "41"}, None
    yield "two_bytes", {"kind": "hex", "hex": "4141"}, None
    yield "three_same", {"kind": "hex", "hex": "000000"}, None
    yield "short_ascii", {"kind": "hex", "hex": b"Hello, world! Hello, zlib. Hello, Go.".hex()}, None
    yield "short_utf8", {"kind": "hex", "hex": "Perché la città è già più bella così.".encode().hex()}, None
    for size in (3, 4, 257, 258, 259, 261, 262, 263, 1000, 4096):
        yield "text_%d" % size, {"kind": "text", "seed": 7, "size": size}, None
    yield "json_2k", {"kind": "json", "seed": 11, "size": 2000}, None
    yield "json_220k", {"kind": "json", "seed": 1, "size": 220_000}, None
    yield "json_1m", {"kind": "json", "seed": 2, "size": 1_000_000}, None
    yield "json_220k_chunked", {"kind": "json", "seed": 1, "size": 220_000}, [1, 7, 4093, 65537, 13]
    yield "run_a_300k", {"kind": "run", "byte": 97, "size": 300_000}, None
    yield "run_zero_70k", {"kind": "run", "byte": 0, "size": 70_000}, None
    yield "pattern_abc_100k", {"kind": "pattern", "pattern": b"abc".hex(), "size": 100_000}, None
    yield "period_300_200k", {"kind": "random_period", "seed": 5, "period": 300, "size": 200_000}, None
    yield "period_32506_150k", {"kind": "random_period", "seed": 6, "period": 32_506, "size": 150_000}, None
    yield "period_32507_150k", {"kind": "random_period", "seed": 6, "period": 32_507, "size": 150_000}, None
    yield "period_40000_150k", {"kind": "random_period", "seed": 6, "period": 40_000, "size": 150_000}, None
    yield "random_1k", {"kind": "random", "seed": 3, "size": 1000}, None
    yield "random_100k", {"kind": "random", "seed": 3, "size": 100_000}, None
    yield "random_100k_chunked", {"kind": "random", "seed": 3, "size": 100_000}, [8192]
    yield "mixed_1m", {"kind": "mixed", "seed": 9, "size": 1_000_000}, None
    yield "mixed_1m_chunked", {"kind": "mixed", "seed": 9, "size": 1_000_000}, [3, 100_000, 17, 32768]
    yield "large_mixed_6m", {"kind": "mixed", "seed": 42, "size": 6_000_000}, None
    yield "large_text_3m", {"kind": "text", "seed": 43, "size": 3_000_000}, None
    for i in range(48):
        seed = 1000 + i
        size = (seed * 7919) % 90_000
        yield "fuzz_mixed_%02d" % i, {"kind": "mixed", "seed": seed, "size": size}, None


def main(argv):
    repo = pathlib.Path(__file__).resolve().parents[3]
    default_out = repo / "BE_GO" / "internal" / "pyzlib" / "testdata" / "zlib_golden.json"
    if len(argv) > 2:
        sys.exit("usage: pyzlib_golden.py [OUTPUT_JSON]")
    out_arg = argv[1] if len(argv) == 2 else str(default_out)
    if not out_arg.strip():
        sys.exit("refusing to run: output path is empty")
    out_path = pathlib.Path(out_arg)
    if not out_path.parent.is_dir():
        sys.exit("refusing to run: output directory %s does not exist" % out_path.parent)

    version = zlib.ZLIB_RUNTIME_VERSION
    if not (version.startswith("1.2.1") or version.startswith("1.3")):
        sys.exit("unexpected zlib runtime %s (want 1.2.12..1.3.x)" % version)

    records = []
    for name, recipe, chunks in cases():
        data = build_input(recipe)
        out = zip_deflate(data)
        rec = {
            "name": name,
            "recipe": recipe,
            "input_len": len(data),
            "input_sha256": hashlib.sha256(data).hexdigest(),
            "output_len": len(out),
            "output_sha256": hashlib.sha256(out).hexdigest(),
        }
        if len(out) <= SMALL_HEX_LIMIT:
            rec["output_hex"] = out.hex()
        if chunks:
            chunked = zip_deflate(data, chunks)
            rec["chunks"] = chunks
            rec["chunked_output_sha256"] = hashlib.sha256(chunked).hexdigest()
        records.append(rec)

    doc = {
        "generator": "TESTS/adapters/go/pyzlib_golden.py",
        "python": sys.version.split()[0],
        "zlib_runtime_version": version,
        "compressobj": "zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)",
        "cases": records,
    }
    out_path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote %d cases to %s (zlib %s)" % (len(records), out_path, version))


if __name__ == "__main__":
    main(sys.argv)
