#!/usr/bin/env python3
"""Long and non-ASCII golden cases for BE_GO/internal/pydateutil.

Run through regen.sh with the oracle interpreter (CPython 3.13 with
python-dateutil 2.9.0.post0):

    gen_extras.py <extras.jsonl.gz>

Every line is one call of the real dateutil.parser.parse with an injected
default (a fake "now" at midnight) and a parserinfo whose century pivot year is
that fake now's year, exactly as gen_corpus.py's worker does. Long cases quote
the text and str(result)/str(exception) as JSON strings; a case whose text or
message holds a lone surrogate (a Python str the JSON escapes cannot carry
through Go, which replaces an unpaired \\udXXX escape with U+FFFD) is written
as hex of its 'utf-8', 'surrogatepass' encoding instead.
"""
import datetime
import gzip
import json
import os
import sys
import time
import warnings

EXPECTED_DATEUTIL = "2.9.0.post0"
NOW = "2026-11-10T15:30:45.123456"
ZONE = "UTC"

# A token of 1,000,001 digits is not recorded: CPython needs about 40 s to
# raise OverflowError for it (the Go port covers it in a test of its own).
LONG_CASES = [
    ("long-spaces", " " * 100000 + "12/03/2020" + " " * 100000, (False, True)),
    ("long-digits-5000", "9" * 5000, (False,)),
    ("long-digits-4300", "9" * 4300, (False,)),
    ("long-digits-4301", "9" * 4301, (False,)),
    ("long-fraction", "1." + "2" * 200000, (False, True)),
    ("long-fraction-seconds", "10:0." + "9" * (1 << 20), (False,)),
    ("long-time", "12:30:" + "0" * 100000, (False,)),
    ("long-words", " ".join(["a", "b"] * 35000), (False,)),
    ("long-twelves", "12 " * 70000, (False,)),
    ("long-word", "q" * 102400, (False,)),
]

SURROGATE_CASES = [
    "12/03/2020 \ud800",
    "\udc00",
    "x\ud83d",
    "\ud800",
    "10:20\ud800",
    "\ud800 10:20",
    "3 Jan 2020 \ud800",
    "12/03/2020 \ud83d\ude00",
    "2020-01-03\ufffd",
    " 12/03/2020\ufffd ",
]


def encode(value, row, key, hex_key):
    """JSON carries value in key when it is plain UTF-8, else as hex."""
    try:
        data = value.encode("utf-8")
    except UnicodeEncodeError:
        row[hex_key] = value.encode("utf-8", "surrogatepass").hex()
    else:
        row[key] = data.decode("utf-8")


def write_extras(path):
    os.environ["TZ"] = ZONE
    time.tzset()
    warnings.simplefilter("ignore")
    import dateutil
    from dateutil import parser as dparser

    if dateutil.__version__ != EXPECTED_DATEUTIL:
        raise SystemExit("python-dateutil %s required, found %s" % (EXPECTED_DATEUTIL, dateutil.__version__))
    now = datetime.datetime.fromisoformat(NOW)
    info = dparser.parserinfo()
    info._year = now.year
    info._century = now.year // 100 * 100
    engine = dparser.parser(info)
    default = now.replace(hour=0, minute=0, second=0, microsecond=0)

    cases = [(group, text, day_first) for group, text, flags in LONG_CASES for day_first in flags]
    cases += [("surrogate", text, day_first) for text in SURROGATE_CASES for day_first in (False, True)]
    lines = []
    for group, text, day_first in cases:
        row = {"g": group, "tz": ZONE, "now": NOW}
        if day_first:
            row["df"] = 1
        try:
            result = engine.parse(text, default=default, dayfirst=day_first)
        except Exception as error:  # noqa: BLE001 - the class is the datum
            row["err"] = [type(error).__name__]
            encode(str(error), row, "errtext", "errhex")
        else:
            row["ok"] = [result.year, result.month, result.day, result.hour, result.minute, result.second,
                         result.microsecond]
            if result.tzinfo is not None:
                raise SystemExit("tzinfo for %r" % text)
            row["str"] = str(result)
            if result.isoformat() != row["str"].replace(" ", "T"):
                raise SystemExit("isoformat differs from str for %r" % text)
        encode(text, row, "in", "inhex")
        lines.append(json.dumps(row, ensure_ascii=True, separators=(",", ":")))
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as handle:
            handle.write(("\n".join(lines) + "\n").encode("ascii"))
    print("%d cases -> %s" % (len(lines), path))


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    write_extras(argv[1])


if __name__ == "__main__":
    main(sys.argv)
