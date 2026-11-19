"""Rung 6: a short script that reads a data file and reports on it.

Each case asks for `report.py`, which takes the input file path as its first
argument and prints a report in an exactly specified format. The prompt shows a
sample input and the exact output for it; the hidden tests run the script on
other inputs (edge cases stated in the prompt) and on seeded random inputs, and
compare stdout with a trusted reference function.
"""
from __future__ import annotations

import csv
import io
import json
import random

from edat.sidekick.ceiling.rungs import answer_files, fill, probe_instance, probe_template
from edat.sidekick.gen.core import fence, pick, sample

_INTRO = ("Write a Python script `report.py` (standard library only). It takes the path of an input file as its "
          "first command-line argument (`sys.argv[1]`) and prints a report to stdout, one `print` per line.")

_PRELUDE = r'''import random
import subprocess
import sys
from pathlib import Path

import pytest


def _run(name):
    r = subprocess.run([sys.executable, "report.py", name], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr[-300:]
    out = r.stdout
    return out[:-1] if out.endswith("\n") else out


'''

_TAIL = r'''

HIDDEN = @HIDDEN@


@pytest.mark.parametrize("name", HIDDEN)
def test_fixed(name):
    assert _run(name) == _ref(Path(name).read_text())


def test_random():
    rng = random.Random(1234)
    for i in range(4):
        name = "gen_input_%d@EXT@" % i
        Path(name).write_text(_gen(rng))
        assert _run(name) == _ref(Path(name).read_text()), name
'''


def _csv(rows) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def _mut(sol: str, old: str, new: str) -> str:
    assert old in sol, old
    return sol.replace(old, new, 1)


def _finish(v: dict, *, task: str, sample_name: str, sample_text: str, ref: str, gen: str, sol: str,
            hidden: dict, wrongs: list[str]):
    ns: dict = {}
    exec(fill(ref, **v), ns)
    example = ns["_ref"](sample_text)
    prompt = (fill(_INTRO + "\n\n" + task, **v)
              + f"\n\nThe sample file `{sample_name}` is in the folder:\n\n" + fence(sample_text)
              + "\n\nFor that file the script must print exactly:\n\n" + fence(example))
    ext = "." + sample_name.rsplit(".", 1)[1]
    tests = (_PRELUDE + fill(ref, **v) + "\n\n" + fill(gen, **v)
             + fill(_TAIL, HIDDEN=repr([sample_name] + sorted(hidden)), EXT=ext))
    sol = fill(sol, **v)
    wrongs = [fill(w, **v) for w in wrongs]
    assert len({sol, *wrongs}) == len(wrongs) + 1
    return probe_instance(
        prompt=prompt, targets=["report.py"], hidden_tests=tests,
        files={sample_name: sample_text}, hidden_files=hidden,
        references=[answer_files({"report.py": sol}), answer_files({"report.py": sol}, style="label")],
        wrong=[answer_files({"report.py": w}) for w in wrongs])


# ------------------------------------------------------------------ 1. sales by region (CSV, quoted commas)

_SALES_REF = r'''import csv
import io


def _cents(s):
    return int(round(float(s) * 100))


def _fmt(c):
    return "%d.%02d" % (c // 100, c % 100)


def _ref(text):
    tot, cnt = {}, {}
    for row in csv.DictReader(io.StringIO(text)):
        a = (row["amount"] or "").strip()
        if not a:
            continue
        reg = row["region"].strip()
        tot[reg] = tot.get(reg, 0) + _cents(a)
        cnt[reg] = cnt.get(reg, 0) + 1
    if not tot:
        return "no sales"
    lines = ["%s: %s n=%d" % (r, _fmt(tot[r]), cnt[r]) for r in sorted(tot, key=lambda r: (-tot[r], r))]
    lines.append("@ALL@: %s n=%d" % (_fmt(sum(tot.values())), sum(cnt.values())))
    return "\n".join(lines)
'''

_SALES_GEN = r'''import csv
import io


def _gen(rng):
    regions = @REGIONS@
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["date", "region", "rep", "amount"])
    for i in range(rng.randint(80, 220)):
        k = rng.random()
        if k < 0.1:
            amount = ""
        elif k < 0.5:
            amount = str(rng.randint(1, 40))
        else:
            amount = "%d.%02d" % (rng.randint(0, 300), rng.randint(0, 99))
        region = (" " if rng.random() < 0.05 else "") + rng.choice(regions)
        w.writerow(["2026-03-%02d" % rng.randint(1, 28), region, rng.choice(["Ana", "Bo", "Cy"]), amount])
    return out.getvalue()
'''

_SALES_SOL = r'''import csv
import sys


def cents(s):
    return int(round(float(s) * 100))


def fmt(c):
    return "%d.%02d" % (c // 100, c % 100)


def main():
    tot, cnt = {}, {}
    with open(sys.argv[1], newline="") as f:
        for row in csv.DictReader(f):
            a = (row["amount"] or "").strip()
            if not a:
                continue
            reg = row["region"].strip()
            tot[reg] = tot.get(reg, 0) + cents(a)
            cnt[reg] = cnt.get(reg, 0) + 1
    if not tot:
        print("no sales")
        return
    for reg in sorted(tot, key=lambda r: (-tot[r], r)):
        print("%s: %s n=%d" % (reg, fmt(tot[reg]), cnt[reg]))
    print("@ALL@: %s n=%d" % (fmt(sum(tot.values())), sum(cnt.values())))


main()
'''


def _sales(rng: random.Random):
    pool = ["North", "South", "East", "West", "Central", "Harbor", "Upland"]
    comma = pick(rng, ["Rio, South", "Lima, North", "Oslo, East"])
    a, b = sample(rng, pool, 2)
    v = dict(ALL=pick(rng, ["TOTAL", "ALL REGIONS"]), REGIONS=repr(sample(rng, pool, 4) + [comma]))
    task = """The input is a CSV file with the header `date,region,rep,amount`. Fields may be quoted and then may contain commas (a region can be called `"Rio, South"`). Report the sales per region:

- Skip rows whose `amount` is blank. Trim spaces around the region name. Amounts are plain numbers with at most two decimals.
- Print one line per region, `<region>: <total> n=<count>`, with the total to two decimals and the number of counted rows. Order the lines by total, largest first; regions with equal totals go in ascending order of their name (plain string comparison).
- Then print a last line `@ALL@: <grand total> n=<count of all counted rows>`.
- If no row is counted, print only `no sales`."""
    sample_text = _csv([["date", "region", "rep", "amount"],
                        ["2026-03-01", a, "Ana", "120.50"], ["2026-03-01", comma, "Bo", "80.00"],
                        ["2026-03-02", a, "Cy", ""], ["2026-03-03", b, "Di", "200.5"],
                        ["2026-03-04", comma, "Ana", "120.5"], ["2026-03-04", b, "Bo", "3"]])
    z, y = "Zulu", "Alpha"
    hidden = {
        "h_ties.csv": _csv([["date", "region", "rep", "amount"], ["2026-04-01", z, "Ana", "30"], ["2026-04-01", y, "Bo", "20.25"],
                            ["2026-04-02", z, "Cy", "20"], ["2026-04-02", y, "Bo", "29.75"], ["2026-04-03", "Mid", "Di", "50"],
                            ["2026-04-03", " " + y + " ", "Di", " 0.5 "]]),
        "h_quoted.csv": _csv([["date", "region", "rep", "amount"], ["2026-04-01", comma, "A, B", "10.10"], ["2026-04-01", comma, "Bo", ""],
                              ["2026-04-02", "Solo", "Cy", "1000.05"], ["2026-04-02", comma, "Bo", "0.20"]]),
        "h_empty.csv": "date,region,rep,amount\n",
        "h_blank.csv": _csv([["date", "region", "rep", "amount"], ["2026-04-01", a, "Ana", ""], ["2026-04-01", b, "Bo", ""]]),
    }
    s = _SALES_SOL
    wrongs = [
        _mut(s, "key=lambda r: (-tot[r], r)", "key=lambda r: -tot[r]"),
        _mut(s, "            if not a:\n                continue\n", "            a = a or \"0\"\n"),
        _mut(s, "    with open(sys.argv[1], newline=\"\") as f:\n        for row in csv.DictReader(f):\n",
             "    with open(sys.argv[1]) as f:\n        header = f.readline().strip().split(\",\")\n        for line in f:\n"
             "            row = dict(zip(header, line.rstrip(\"\\n\").split(\",\")))\n"),
        _mut(s, 'reg = row["region"].strip()', 'reg = row["region"]'),
    ]
    return _finish(v, task=task, sample_name="sales.csv", sample_text=sample_text, ref=_SALES_REF, gen=_SALES_GEN,
                   sol=s, hidden=hidden, wrongs=wrongs)


# ------------------------------------------------------------------ 2. slowest API endpoints (log lines)

_EP_REF = r'''from fractions import Fraction


def _ref(text):
    stats = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 5:
            continue
        lat = parts[4]
        if not (lat.endswith("ms") and lat[:-2].isdigit()):
            continue
        key = parts[1] + " " + parts[2].split("?")[0]
        stats.setdefault(key, []).append(int(lat[:-2]))
    if not stats:
        return "no requests"
    ranked = sorted(stats, key=lambda k: (-Fraction(sum(stats[k]), len(stats[k])), -len(stats[k]), k))
    out = []
    for i, k in enumerate(ranked[:@N@], 1):
        n, s = len(stats[k]), sum(stats[k])
        out.append("%d. %s avg=%dms max=%dms n=%d" % (i, k, (2 * s + n) // (2 * n), max(stats[k]), n))
    return "\n".join(out)
'''

_EP_GEN = r'''def _gen(rng):
    eps = ["/api/users", "/api/orders", "/api/search?q=a", "/api/search?q=bb", "/api/cart", "/api/items/list", "/health", "/api/users?id=7"]
    out = []
    for i in range(rng.randint(80, 200)):
        r = rng.random()
        if r < 0.05:
            out.append("")
        elif r < 0.1:
            out.append("#2026-03-01T10:00:01Z GET /api/users 200 999ms")
        else:
            lat = "-" if r < 0.15 else "%dms" % rng.choice([rng.randint(1, 20), rng.randint(1, 400)])
            out.append("2026-03-01T10:%02d:%02dZ %s %s %d %s" % (rng.randint(0, 59), rng.randint(0, 59), rng.choice(["GET", "GET", "POST"]),
                                                                  rng.choice(eps), rng.choice([200, 200, 404, 500]), lat))
    return "\n".join(out) + "\n"
'''

_EP_SOL = r'''import sys
from fractions import Fraction

N = @N@


def main():
    stats = {}
    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 5:
                continue
            lat = parts[4]
            if not (lat.endswith("ms") and lat[:-2].isdigit()):
                continue
            path = parts[2].split("?")[0]
            stats.setdefault(parts[1] + " " + path, []).append(int(lat[:-2]))
    if not stats:
        print("no requests")
        return
    ranked = sorted(stats, key=lambda k: (-Fraction(sum(stats[k]), len(stats[k])), -len(stats[k]), k))
    for i, k in enumerate(ranked[:N], 1):
        n, s = len(stats[k]), sum(stats[k])
        print("%d. %s avg=%dms max=%dms n=%d" % (i, k, (2 * s + n) // (2 * n), max(stats[k]), n))


main()
'''


def _endpoints(rng: random.Random):
    v = dict(N=pick(rng, [3, 4, 5]))
    p1, p2, p3, p4 = sample(rng, ["/api/users", "/api/orders", "/api/cart", "/api/items", "/api/invoices", "/api/reports"], 4)
    task = """The input is a web server log, one request per line, five space-separated fields: `<timestamp> <METHOD> <path> <status> <latency>`, for example `2026-03-01T10:00:01Z GET /api/users?id=4 200 120ms`. The latency is a whole number followed by `ms`.

- Ignore blank lines, lines starting with `#`, lines that do not have exactly five fields, and lines whose latency is not a whole number followed by `ms` (for example `-`).
- An endpoint is the method plus the path with any query string (everything from `?`) removed, written `<METHOD> <path>`.
- Rank the endpoints by average latency, slowest first (compare the exact averages). Equal averages: more requests first, then ascending endpoint name.
- Print the top @N@ endpoints (fewer if there are fewer), one per line: `<rank>. <endpoint> avg=<A>ms max=<M>ms n=<requests>`, with `<rank>` counting from 1 and `<A>` the average rounded to a whole number, a half rounding up (2.5 becomes 3).
- If no request counts, print only `no requests`."""
    L = lambda m, p, lat: "2026-03-01T09:%02d:00Z %s %s 200 %s" % (len(m) and 5, m, p, lat)  # noqa: E731
    sample_text = "\n".join([
        "# access log, March 1",
        "2026-03-01T09:00:01Z GET %s?id=4 200 120ms" % p1, "2026-03-01T09:00:02Z GET %s 200 80ms" % p2,
        "", "2026-03-01T09:00:05Z GET %s?id=9 200 100ms" % p1, "2026-03-01T09:00:07Z POST %s 201 410ms" % p3,
        "2026-03-01T09:00:09Z GET %s 500 -" % p4, "2026-03-01T09:00:11Z GET %s 200 95ms" % p2,
        "2026-03-01T09:00:12Z POST %s 201 390ms" % p3, "2026-03-01T09:00:15Z GET %s 200 30ms" % p4]) + "\n"
    hidden = {
        "h_ties.log": "\n".join([
            "2026-03-02T01:00:00Z GET /b 200 100ms", "2026-03-02T01:00:01Z GET /a 200 100ms", "2026-03-02T01:00:02Z GET /a 200 100ms",
            "2026-03-02T01:00:03Z GET /c 200 100ms", "2026-03-02T01:00:04Z GET /c 200 100ms", "2026-03-02T01:00:05Z GET /c 200 100ms",
            "2026-03-02T01:00:06Z GET /d 200 2ms", "2026-03-02T01:00:07Z GET /d 200 3ms", "2026-03-02T01:00:08Z GET /e 200 7ms"]) + "\n",
        "h_comments.log": "\n".join([
            "#2026-03-02T01:00:00Z GET /x 200 9999ms", "   ", "2026-03-02T01:00:01Z GET /x?a=1 200 10ms",
            "2026-03-02T01:00:02Z GET /x?a=2 200 -", "2026-03-02T01:00:03Z GET /x 200 12ms", "garbage line",
            "2026-03-02T01:00:04Z POST /x 200 11ms", "2026-03-02T01:00:05Z GET /y?z=1 200 5ms"]) + "\n",
        "h_empty.log": "# nothing here\n\n",
        "h_halves.log": "\n".join(["2026-03-02T01:00:0%dZ GET /q 200 %dms" % (i, x) for i, x in enumerate([4, 5, 4, 5, 4, 5, 5, 4])]
                                   + ["2026-03-02T01:01:00Z GET /r 200 1ms", "2026-03-02T01:01:01Z GET /r 200 2ms"]) + "\n",
    }
    s = _EP_SOL
    wrongs = [
        _mut(s, 'path = parts[2].split("?")[0]', "path = parts[2]"),
        _mut(s, "(2 * s + n) // (2 * n)", "round(s / n)"),
        _mut(s, "-len(stats[k]), k))", "k))"),
        _mut(s, 'if not line or line.startswith("#"):', "if not line:"),
    ]
    return _finish(v, task=task, sample_name="access.log", sample_text=sample_text, ref=_EP_REF, gen=_EP_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


# ------------------------------------------------------------------ 3. attendance per student (CSV)

_ATT_REF = r'''import csv
import io
from fractions import Fraction


def _ref(text):
    last, names = {}, {}
    for row in csv.DictReader(io.StringIO(text)):
        name = row["student"].strip()
        status = row["status"].strip().upper()
        if not name or status not in ("P", "A", "L"):
            continue
        key = name.lower()
        names.setdefault(key, name)
        last[(key, row["date"].strip())] = status
    total, present = {}, {}
    for (key, _), st in last.items():
        total[key] = total.get(key, 0) + 1
        if st in ("P", "L"):
            present[key] = present.get(key, 0) + 1
    if not total:
        return "no records"
    out = []
    for key in sorted(total, key=lambda k: (Fraction(present.get(k, 0), total[k]), k)):
        p, t = present.get(key, 0), total[key]
        out.append("%s: %d/%d %d%%%s" % (names[key], p, t, (200 * p + t) // (2 * t), " LOW" if p * 100 < @LOW@ * t else ""))
    return "\n".join(out)
'''

_ATT_GEN = r'''import csv
import io


def _gen(rng):
    names = ["Ana Ruiz", "Bo Lind", "Cy Park", "Di Mora", "Eli Stone", "Fay Chen"]
    rows = [["student", "date", "status"]]
    for i in range(rng.randint(60, 160)):
        n = rng.choice(names)
        if rng.random() < 0.1:
            n = " " + n.lower() + " "
        rows.append([n, "2026-03-%02d" % rng.randint(1, 12), rng.choice(["P", "P", "P", "A", "L", "p", "a", "x"])])
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()
'''

_ATT_SOL = r'''import csv
import sys
from fractions import Fraction

LOW = @LOW@


def main():
    last, names = {}, {}
    with open(sys.argv[1], newline="") as f:
        for row in csv.DictReader(f):
            name = row["student"].strip()
            status = row["status"].strip().upper()
            if not name or status not in ("P", "A", "L"):
                continue
            key = name.lower()
            names.setdefault(key, name)
            last[(key, row["date"].strip())] = status
    total, present = {}, {}
    for (key, _), st in last.items():
        total[key] = total.get(key, 0) + 1
        if st in ("P", "L"):
            present[key] = present.get(key, 0) + 1
    if not total:
        print("no records")
        return
    for key in sorted(total, key=lambda k: (Fraction(present.get(k, 0), total[k]), k)):
        p, t = present.get(key, 0), total[key]
        flag = " LOW" if p * 100 < LOW * t else ""
        print("%s: %d/%d %d%%%s" % (names[key], p, t, (200 * p + t) // (2 * t), flag))


main()
'''


def _attendance(rng: random.Random):
    v = dict(LOW=pick(rng, [70, 75, 80]))
    a, b, c = sample(rng, ["Ana Ruiz", "Bo Lind", "Cy Park", "Di Mora", "Eli Stone", "Fay Chen"], 3)
    task = """The input is a CSV file with the header `student,date,status`, one row per student per school day. `status` is `P` (present), `A` (absent) or `L` (late), in any letter case.

- Trim spaces around the student name and the status. Rows with an empty name or any other status are ignored completely (they do not replace an earlier row either).
- Two names are the same student when they are equal ignoring letter case (after trimming). The student is shown with the spelling of their first row.
- If a student has several rows for the same date, only the last of them counts.
- A late day counts as present for the attendance rate.
- Print one line per student: `<name>: <present days>/<days> <percent>%`, the percent rounded to a whole number with halves rounding up, followed by ` LOW` when the exact rate is below @LOW@%. Order the lines by exact attendance rate, lowest first, then by lower-case name ascending.
- If there are no counted rows, print only `no records`."""
    sample_text = _csv([["student", "date", "status"], [a, "2026-03-02", "P"], [b, "2026-03-02", "A"], [a, "2026-03-03", "L"],
                        [b, "2026-03-03", "P"], [" " + a.lower(), "2026-03-04", "a"], [c, "2026-03-04", "x"],
                        [b, "2026-03-03", "A"], [c, "2026-03-05", "P"]])
    rows = [["student", "date", "status"]]
    for i in range(20):  # exactly at the threshold: LOW% of 20 days
        rows.append(["Edge Case", "2026-04-%02d" % (i + 1), "P" if i < LOW_DAYS(v["LOW"]) else "A"])
    rows += [["Dup Case", "2026-04-01", "A"], ["Dup Case", "2026-04-01", "P"], ["Dup Case", "2026-04-02", "A"], ["Dup Case", "2026-04-02", "x"],
             ["Late Case", "2026-04-01", "L"], ["late case ", "2026-04-02", "l"], ["Late Case", "2026-04-03", "A"],
             ["Third", "2026-04-01", "P"], ["Third", "2026-04-02", "P"], ["Third", "2026-04-03", "A"],
             ["Close", "2026-04-01", "P"]] + [["Close", "2026-04-%02d" % d, "A" if d % 3 else "P"] for d in range(2, 8)]
    rows += [["", "2026-04-01", "P"]]
    hidden = {
        "h_mixed.csv": _csv(rows),
        "h_empty.csv": "student,date,status\n",
        "h_ignored.csv": _csv([["student", "date", "status"], ["Zed", "2026-04-01", "?"], ["", "2026-04-01", "P"]]),
        "h_rounding.csv": _csv([["student", "date", "status"]] + [["Two Thirds", "2026-04-%02d" % d, "P" if d != 3 else "A"] for d in range(1, 4)]
                               + [["Eighth", "2026-04-%02d" % d, "P" if d <= 5 else "A"] for d in range(1, 9)]
                               + [["Sixty Seven", "2026-04-%02d" % d, "A" if d <= 33 else "P"] for d in range(1, 101)]),
    }
    s = _ATT_SOL
    wrongs = [
        _mut(s, 'if st in ("P", "L"):', 'if st == "P":'),
        _mut(s, 'last[(key, row["date"].strip())] = status', 'last[(key, len(last))] = status'),
        _mut(s, "p * 100 < LOW * t", "p * 100 <= LOW * t"),
        _mut(s, "(Fraction(present.get(k, 0), total[k]), k)", "(k,)"),
        _mut(s, 'status = row["status"].strip().upper()', 'status = row["status"].strip()'),
    ]
    return _finish(v, task=task, sample_name="attendance.csv", sample_text=sample_text, ref=_ATT_REF, gen=_ATT_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


def LOW_DAYS(low: int) -> int:
    return low * 20 // 100


# ------------------------------------------------------------------ 4. inventory value by category (CSV)

_INV_REF = r'''import csv
import io


def _cents(s):
    return int(round(float(s.strip().lstrip("$")) * 100))


def _fmt(c):
    return "%d.%02d" % (c // 100, c % 100)


def _ref(text):
    final = {}
    for row in csv.DictReader(io.StringIO(text)):
        sku = row["sku"].strip()
        if sku:
            final[sku] = row
    if not final:
        return "no stock"
    cats, out = {}, []
    for sku, row in final.items():
        q = (row["qty"] or "").strip()
        qty = int(q) if q else 0
        if qty <= 0:
            out.append(sku)
            continue
        c = cats.setdefault(row["category"].strip().lower(), [0, 0, 0])
        c[0] += 1
        c[1] += qty
        c[2] += qty * _cents(row["unit_price"])
    lines = ["%s: items=%d units=%d value=%s" % (k, c[0], c[1], _fmt(c[2])) for k, c in sorted(cats.items())]
    lines.append("@TOT@ value=%s" % _fmt(sum(c[2] for c in cats.values())))
    if out:
        lines.append("OUT OF STOCK: " + ", ".join(sorted(out)))
    return "\n".join(lines)
'''

_INV_GEN = r'''import csv
import io


def _gen(rng):
    rows = [["sku", "name", "category", "qty", "unit_price"]]
    cats = ["Tools", "tools", "Paint", "Garden ", "Fasteners"]
    for i in range(rng.randint(60, 150)):
        q = rng.choice(["", "0", "-1", str(rng.randint(1, 50)), str(rng.randint(1, 9))])
        price = rng.choice(["%d.%02d" % (rng.randint(0, 80), rng.randint(0, 99)), "$%d.50" % rng.randint(1, 9), "%d" % rng.randint(1, 9)])
        rows.append(["S%03d" % rng.randint(1, 45), rng.choice(["Hammer, claw", "Brush", "Nails (200)"]), rng.choice(cats), q, price])
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()
'''

_INV_SOL = r'''import csv
import sys


def cents(s):
    return int(round(float(s.strip().lstrip("$")) * 100))


def fmt(c):
    return "%d.%02d" % (c // 100, c % 100)


def main():
    final = {}
    with open(sys.argv[1], newline="") as f:
        for row in csv.DictReader(f):
            sku = row["sku"].strip()
            if sku:
                final[sku] = row
    if not final:
        print("no stock")
        return
    cats, out = {}, []
    for sku, row in final.items():
        q = (row["qty"] or "").strip()
        qty = int(q) if q else 0
        if qty <= 0:
            out.append(sku)
            continue
        c = cats.setdefault(row["category"].strip().lower(), [0, 0, 0])
        c[0] += 1
        c[1] += qty
        c[2] += qty * cents(row["unit_price"])
    for k, c in sorted(cats.items()):
        print("%s: items=%d units=%d value=%s" % (k, c[0], c[1], fmt(c[2])))
    print("@TOT@ value=%s" % fmt(sum(c[2] for c in cats.values())))
    if out:
        print("OUT OF STOCK: " + ", ".join(sorted(out)))


main()
'''


def _inventory(rng: random.Random):
    v = dict(TOT=pick(rng, ["TOTAL", "STOCK VALUE"]))
    task = """The input is a CSV file with the header `sku,name,category,qty,unit_price`. Item names may be quoted and contain commas. `qty` is a whole number (it can be blank), `unit_price` a decimal number that may start with `$`.

- Rows with an empty `sku` are ignored. If a `sku` appears more than once, only its last row counts (it is a later correction).
- An item whose final `qty` is blank, zero or negative is out of stock: it is left out of the categories and totals.
- Categories are compared ignoring letter case and surrounding spaces, and shown in lower case. An item's value is `qty * unit_price`.
- For each category in ascending name order print `<category>: items=<number of items> units=<sum of qty> value=<sum of values>`, values with two decimals.
- Then print `@TOT@ value=<sum of all category values>`.
- If there are out-of-stock items, print last `OUT OF STOCK: ` followed by their skus in ascending order, separated by `, `.
- If the file has no counted rows at all (no valid sku), print only `no stock`."""
    sample_text = _csv([["sku", "name", "category", "qty", "unit_price"],
                        ["T100", "Hammer, claw", "Tools", "4", "$12.50"], ["P200", "Brush", "paint", "10", "3.2"],
                        ["T101", "Saw", "tools ", "0", "20"], ["P200", "Brush", "paint", "12", "3.25"],
                        ["G300", "Rake", "Garden", "", "15"], ["T100", "Hammer, claw", "Tools", "5", "12.5"], ["T102", "Level", "TOOLS", "2", "8"]])
    hidden = {
        "h_mixed.csv": _csv([["sku", "name", "category", "qty", "unit_price"], ["A1", "One, two", "Tools", "3", "1.10"],
                             ["A2", "Two", "tools", "2", "$2"], ["B1", "Three", "Paint ", "1", "9.99"], ["A1", "One, two", "Tools", "-4", "1.10"],
                             ["Z9", "Nine", "Zip", "0", "5"], ["", "No sku", "Tools", "5", "5"], ["B2", "Four", "paint", "7", "0.15"]]),
        "h_empty.csv": "sku,name,category,qty,unit_price\n",
        "h_allout.csv": _csv([["sku", "name", "category", "qty", "unit_price"], ["Q2", "Two", "x", "0", "5"], ["Q1", "One", "y", "", "6"]]),
        "h_dups.csv": _csv([["sku", "name", "category", "qty", "unit_price"], ["D1", "A", "Cat", "1", "10"], ["D1", "A", "Cat", "1", "10"],
                            ["D2", "B", "cat", "2", "$0.55"]]),
    }
    s = _INV_SOL
    wrongs = [
        _mut(s, 'final[sku] = row', 'final[sku + str(len(final))] = row'),
        _mut(s, "if qty <= 0:", "if qty < 0:"),
        _mut(s, 'row["category"].strip().lower()', 'row["category"].strip()'),
        _mut(s, ".lstrip(\"$\")", ""),
        _mut(s, "                final[sku] = row\n", "                final[sku] = row\n").replace(
            "    with open(sys.argv[1], newline=\"\") as f:\n        for row in csv.DictReader(f):\n",
            "    with open(sys.argv[1]) as f:\n        header = f.readline().strip().split(\",\")\n        for line in f:\n"
            "            row = dict(zip(header, line.rstrip(\"\\n\").split(\",\")))\n"),
    ]
    return _finish(v, task=task, sample_name="stock.csv", sample_text=sample_text, ref=_INV_REF, gen=_INV_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


# ------------------------------------------------------------------ 5. monthly expenses (CSV)

_EXP_REF = r'''import csv
import io
import re


def _fmt(c):
    return ("-" if c < 0 else "") + "%d.%02d" % (abs(c) // 100, abs(c) % 100)


def _ref(text):
    months = {}
    for row in csv.DictReader(io.StringIO(text)):
        d = row["date"].strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            continue
        try:
            c = int(round(float(row["amount"].strip().replace("$", "").replace(",", "")) * 100))
        except ValueError:
            continue
        m = months.setdefault(d[:7], {"total": 0, "days": set(), "cats": {}})
        m["total"] += c
        m["days"].add(d)
        cat = row["category"].strip().lower()
        m["cats"][cat] = m["cats"].get(cat, 0) + c
    if not months:
        return "no expenses"
    out = []
    for key in sorted(months):
        m = months[key]
        t, d = m["total"], len(m["days"])
        top = sorted(m["cats"].items(), key=lambda kv: (-kv[1], kv[0]))[0]
        out.append("%s: total=%s days=%d avg/day=%s top=%s (%s)" % (key, _fmt(t), d, _fmt((2 * t + d) // (2 * d)), top[0], _fmt(top[1])))
    return "\n".join(out)
'''

_EXP_GEN = r'''import csv
import io


def _gen(rng):
    rows = [["date", "category", "amount", "note"]]
    for i in range(rng.randint(60, 160)):
        r = rng.random()
        if r < 0.05:
            d = "March 5"
        elif r < 0.08:
            d = "2026-3-05"
        else:
            d = "2026-%02d-%02d" % (rng.randint(1, 3), rng.randint(1, 6))
        r = rng.random()
        if r < 0.06:
            amt = "n/a"
        elif r < 0.12:
            amt = "-%d.%02d" % (rng.randint(1, 20), rng.randint(0, 99))
        elif r < 0.2:
            amt = "1,%03d.00" % rng.randint(0, 999)
        elif r < 0.3:
            amt = "$%d.%d" % (rng.randint(1, 90), rng.randint(0, 9))
        else:
            amt = "%d.%02d" % (rng.randint(1, 90), rng.randint(0, 99))
        rows.append([d, rng.choice(["Food", "food", "Rent ", "Fuel", "Gifts", "Misc"]), amt, "n"])
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()
'''

_EXP_SOL = r'''import csv
import re
import sys

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fmt(c):
    return ("-" if c < 0 else "") + "%d.%02d" % (abs(c) // 100, abs(c) % 100)


def main():
    months = {}
    with open(sys.argv[1], newline="") as f:
        for row in csv.DictReader(f):
            d = row["date"].strip()
            if not DATE.match(d):
                continue
            try:
                c = int(round(float(row["amount"].strip().replace("$", "").replace(",", "")) * 100))
            except ValueError:
                continue
            m = months.setdefault(d[:7], {"total": 0, "days": set(), "cats": {}})
            m["total"] += c
            m["days"].add(d)
            cat = row["category"].strip().lower()
            m["cats"][cat] = m["cats"].get(cat, 0) + c
    if not months:
        print("no expenses")
        return
    for key in sorted(months):
        m = months[key]
        t, d = m["total"], len(m["days"])
        top = sorted(m["cats"].items(), key=lambda kv: (-kv[1], kv[0]))[0]
        print("%s: total=%s days=%d avg/day=%s top=%s (%s)" % (key, fmt(t), d, fmt((2 * t + d) // (2 * d)), top[0], fmt(top[1])))


main()
'''


def _expenses(rng: random.Random):
    v: dict = {}
    c1, c2, c3 = sample(rng, ["food", "rent", "fuel", "gifts", "books", "tools"], 3)
    task = """The input is a CSV file with the header `date,category,amount,note`. Fields may be quoted: an amount like `"1,250.00"` has a thousands comma inside quotes.

- A row counts only if its date looks like `YYYY-MM-DD` (four digits, a dash, two digits, a dash, two digits; no calendar check) and its amount is a number after removing any `$` and any commas. Other rows are skipped. A negative amount is a refund and is subtracted.
- Group the counted rows by month (`YYYY-MM`). Categories are compared ignoring letter case and surrounding spaces and shown in lower case.
- For each month in ascending order print `<month>: total=<sum> days=<d> avg/day=<avg> top=<category> (<category sum>)` where `<d>` is the number of different dates that have at least one counted row, `<avg>` is the total divided by `<d>`, rounded to the nearest cent with halves rounding up, and `top` is the category with the largest sum (equal sums: the alphabetically first). Money has two decimals, with a leading `-` when negative.
- If no row counts, print only `no expenses`."""
    sample_text = _csv([["date", "category", "amount", "note"], ["2026-03-01", c1.capitalize(), "40.25", "a"],
                        ["2026-03-01", c2, "1,200.00", "b"], ["2026-03-02", c1, "$9.5", "c"], ["2026-03-02", c3, "-4.00", "refund"],
                        ["2026-04-10", c3, "15", "d"], ["04/11/2026", c3, "99", "bad date"], ["2026-04-11", c2 + " ", "abc", "bad amount"],
                        ["2026-04-12", c3, "0.35", "e"]])
    hidden = {
        "h_halves.csv": _csv([["date", "category", "amount", "note"], ["2026-05-01", "a", "0.05", ""], ["2026-05-02", "b", "0.00", ""],
                              ["2026-06-01", "a", "0.01", ""], ["2026-06-02", "a", "0.02", ""], ["2026-06-03", "a", "0.02", ""], ["2026-06-04", "a", "0.01", ""],
                              ["2026-07-01", "z", "2.50", ""], ["2026-07-01", "y", "2.50", ""], ["2026-07-02", "w", "0.00", ""]]),
        "h_quoted.csv": _csv([["date", "category", "amount", "note"], ["2026-01-05", "Rent", "1,250.00", "x, y"], ["2026-01-05", "rent", "$1,000", ""],
                              ["2026-01-06", "Food ", "12.34", ""], ["2026-1-07", "Food", "99", "bad"], ["2026-01-07", "Food", "", "blank amt"]]),
        "h_empty.csv": "date,category,amount,note\n",
        "h_refund.csv": _csv([["date", "category", "amount", "note"], ["2026-02-01", "x", "10", ""], ["2026-02-01", "x", "-3.50", ""],
                              ["2026-02-03", "y", "5.25", ""], ["2026-02-03", "y", "-0.25", ""]]),
    }
    s = _EXP_SOL
    wrongs = [
        _mut(s, "(2 * t + d) // (2 * d)", "round(t / d)"),
        _mut(s, '.replace(",", "")', ""),
        _mut(s, 'm["days"].add(d)', 'm["days"].add(len(m["days"]) + m["total"])'),
        _mut(s, "key=lambda kv: (-kv[1], kv[0])", "key=lambda kv: -kv[1]"),
        _mut(s, "            if not DATE.match(d):\n                continue\n", ""),
    ]
    return _finish(v, task=task, sample_name="expenses.csv", sample_text=sample_text, ref=_EXP_REF, gen=_EXP_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


# ------------------------------------------------------------------ 6. errors per hour (log lines)

_ERR_REF = r'''import re

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):\d{2}:\d{2} \[(DEBUG|INFO|WARN|ERROR|FATAL)\] (.*)$")


def _ref(text):
    hours = {}
    for line in text.splitlines():
        m = LINE.match(line)
        if not m or m.group(3) not in ("ERROR", "FATAL"):
            continue
        h = hours.setdefault((m.group(1), m.group(2)), [0, 0, None])
        h[0 if m.group(3) == "ERROR" else 1] += 1
        if h[2] is None:
            msg = m.group(4).strip()
            h[2] = msg[:@MAX@] + "..." if len(msg) > @MAX@ else msg
    if not hours:
        return "no errors"
    out, best = [], None
    for key in sorted(hours):
        e, f, first = hours[key]
        out.append("%s %s:00 errors=%d fatal=%d first=%s" % (key[0], key[1], e, f, first))
        if best is None or e + f > best[1]:
            best = (key, e + f)
    out.append("peak: %s %s:00 (%d)" % (best[0][0], best[0][1], best[1]))
    return "\n".join(out)
'''

_ERR_GEN = r'''def _gen(rng):
    msgs = ["disk almost full", "connection reset by peer while syncing the nightly export", "retry after ERROR 5", "ok",
            "payment gateway timeout (upstream)  ", "cache miss"]
    lines = []
    for i in range(rng.randint(80, 200)):
        lvl = rng.choice(["INFO", "INFO", "DEBUG", "WARN", "ERROR", "ERROR", "FATAL"])
        lines.append("2026-03-%02d %02d:%02d:%02d [%s] %s" % (rng.randint(1, 2), rng.randint(0, 5), rng.randint(0, 59),
                                                              rng.randint(0, 59), lvl, rng.choice(msgs)))
        r = rng.random()
        if r < 0.08:
            lines.append("    at worker.run (job.py:%d) ERROR" % rng.randint(1, 99))
        elif r < 0.12:
            lines.append("")
        elif r < 0.15:
            lines.append("2026-03-01 07:00:00 [error] lower case level")
    return "\n".join(lines) + "\n"
'''

_ERR_SOL = r'''import re
import sys

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):\d{2}:\d{2} \[(DEBUG|INFO|WARN|ERROR|FATAL)\] (.*)$")
MAXLEN = @MAX@


def main():
    hours = {}
    with open(sys.argv[1]) as f:
        for line in f:
            m = LINE.match(line.rstrip("\n"))
            if not m:
                continue
            level = m.group(3)
            if level not in ("ERROR", "FATAL"):
                continue
            h = hours.setdefault((m.group(1), m.group(2)), [0, 0, None])
            if level == "ERROR":
                h[0] += 1
            else:
                h[1] += 1
            if h[2] is None:
                msg = m.group(4).strip()
                h[2] = msg[:MAXLEN] + "..." if len(msg) > MAXLEN else msg
    if not hours:
        print("no errors")
        return
    best = None
    for key in sorted(hours):
        e, fatal, first = hours[key]
        print("%s %s:00 errors=%d fatal=%d first=%s" % (key[0], key[1], e, fatal, first))
        if best is None or e + fatal > best[1]:
            best = (key, e + fatal)
    print("peak: %s %s:00 (%d)" % (best[0][0], best[0][1], best[1]))


main()
'''


def _errors(rng: random.Random):
    v = dict(MAX=pick(rng, [20, 24, 30]))
    task = """The input is an application log. A log line looks like `2026-03-01 10:15:02 [ERROR] message text`: a date, a time, a level in square brackets (one of `DEBUG`, `INFO`, `WARN`, `ERROR`, `FATAL`, exactly in upper case) and a message. Lines that do not have this shape (blank lines, stack-trace continuation lines, anything else) are ignored. The file may not be in time order.

- Only `ERROR` and `FATAL` lines count; the level must be the bracketed one (a message that merely mentions ERROR does not count).
- Group the counted lines by hour (date plus the hour of the time). For each hour that has any counted line, in chronological order, print `<date> <HH>:00 errors=<number of ERROR lines> fatal=<number of FATAL lines> first=<message>`, where the message is that of the first counted line of the hour in file order, stripped of surrounding spaces; if it is longer than @MAX@ characters, cut it to @MAX@ characters and append `...`.
- Then print `peak: <date> <HH>:00 (<errors plus fatal>)` for the hour with the most counted lines; on a tie, the earliest hour.
- If nothing counts, print only `no errors`."""
    sample_text = "\n".join([
        "2026-03-01 09:58:10 [INFO] service started", "2026-03-01 10:15:02 [ERROR] database connection lost, will retry later today",
        "    at db.connect (db.py:42)", "2026-03-01 10:15:09 [INFO] retry after ERROR 5", "2026-03-01 10:40:00 [FATAL] out of memory",
        "", "2026-03-01 10:41:33 [ERROR] timeout", "2026-03-01 11:02:00 [WARN] slow query", "2026-03-01 11:30:00 [ERROR] disk full",
        "2026-03-01 09:05:00 [ERROR] late line, earlier hour"]) + "\n"
    hidden = {
        "h_peak.log": "\n".join(["2026-03-02 01:00:00 [ERROR] a", "2026-03-02 01:05:00 [ERROR] b", "2026-03-02 02:00:00 [FATAL] c",
                                 "2026-03-02 02:05:00 [ERROR] d", "2026-03-02 03:00:00 [ERROR] e", "2026-03-02 03:10:00 [FATAL] f",
                                 "2026-03-02 03:20:00 [WARN] g", "2026-03-01 23:59:59 [ERROR] x" + "y" * 40,
                                 "2026-03-02 04:00:00 [ERROR]   padded message   ", "2026-03-02 04:01:00 [ERROR] " + "z" * MAXLEN_OF(v)]) + "\n",
        "h_noise.log": "\n".join(["Traceback (most recent call last):", "2026-03-02 01:00:00 [INFO] ERROR counted? no",
                                   "2026-03-02 01:00:00 [error] lower", "2026-03-02 01:00:00 ERROR no brackets", " 2026-03-02 01:00:00 [ERROR] indented",
                                   "2026-03-02 01:00:00 [FATAL] real one"]) + "\n",
        "h_none.log": "2026-03-02 01:00:00 [INFO] all good\n2026-03-02 01:00:01 [WARN] hmm\n",
        "h_empty.log": "",
    }
    s = _ERR_SOL
    wrongs = [
        _mut(s, 'if level == "ERROR":', "if True:"),
        _mut(s, 'h[2] = msg[:MAXLEN] + "..." if len(msg) > MAXLEN else msg', "h[2] = msg"),
        _mut(s, "e + fatal > best[1]", "e + fatal >= best[1]"),
        _mut(s, "msg = m.group(4).strip()", "msg = m.group(4)"),
        _mut(s, "            if h[2] is None:\n", "            if True:\n"),
    ]
    return _finish(v, task=task, sample_name="app.log", sample_text=sample_text, ref=_ERR_REF, gen=_ERR_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


def MAXLEN_OF(v: dict) -> int:
    return v["MAX"]


# ------------------------------------------------------------------ 7. grade distribution (JSON Lines)

_GR_REF = r'''import json

CUTS = @CUTS@


def _fmt(t):
    return "%d.%d" % (t // 10, t % 10)


def _ref(text):
    best = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        name, score = rec.get("student"), rec.get("score")
        if not isinstance(name, str) or not name.strip():
            continue
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        t = int(round(score * 10))
        if t < 0 or t > 1000:
            continue
        name = name.strip()
        if name not in best or t > best[name]:
            best[name] = t
    if not best:
        return "no scores"
    counts = {g: 0 for g, _ in CUTS}
    counts["F"] = 0
    for t in best.values():
        r = (t + 5) // 10
        for g, cut in CUTS:
            if r >= cut:
                counts[g] += 1
                break
        else:
            counts["F"] += 1
    out = ["%s: %d %s" % (g, counts[g], "#" * counts[g]) for g in [g for g, _ in CUTS] + ["F"]]
    ts = sorted(best.values())
    n = len(ts)
    mean = (2 * sum(ts) + n) // (2 * n)
    med = ts[n // 2] if n % 2 else (ts[n // 2 - 1] + ts[n // 2] + 1) // 2
    out += ["students: %d" % n, "mean: " + _fmt(mean), "median: " + _fmt(med)]
    return "\n".join(out)
'''

_GR_GEN = r'''import json


def _gen(rng):
    lines = []
    names = ["s%02d" % i for i in range(30)]
    for i in range(rng.randint(60, 140)):
        r = rng.random()
        if r < 0.05:
            lines.append("")
        elif r < 0.08:
            lines.append("{not json")
        else:
            score = rng.choice([None, "n/a", True, rng.randint(0, 100), rng.randint(-5, 105), rng.randint(0, 200) / 2, rng.randint(560, 999) / 10])
            rec = {"student": rng.choice(names), "score": score}
            if rng.random() < 0.05:
                del rec["score"]
            lines.append(json.dumps(rec))
    return "\n".join(lines) + "\n"
'''

_GR_SOL = r'''import json
import sys

CUTS = @CUTS@


def fmt(t):
    return "%d.%d" % (t // 10, t % 10)


def main():
    best = {}
    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            name, score = rec.get("student"), rec.get("score")
            if not isinstance(name, str) or not name.strip():
                continue
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                continue
            t = int(round(score * 10))
            if t < 0 or t > 1000:
                continue
            name = name.strip()
            if name not in best or t > best[name]:
                best[name] = t
    if not best:
        print("no scores")
        return
    counts = {g: 0 for g, _ in CUTS}
    counts["F"] = 0
    for t in best.values():
        r = (t + 5) // 10
        for g, cut in CUTS:
            if r >= cut:
                counts[g] += 1
                break
        else:
            counts["F"] += 1
    for g in [g for g, _ in CUTS] + ["F"]:
        print("%s: %d %s" % (g, counts[g], "#" * counts[g]))
    ts = sorted(best.values())
    n = len(ts)
    print("students: %d" % n)
    print("mean: " + fmt((2 * sum(ts) + n) // (2 * n)))
    print("median: " + fmt(ts[n // 2] if n % 2 else (ts[n // 2 - 1] + ts[n // 2] + 1) // 2))


main()
'''


def _grades(rng: random.Random):
    cuts = pick(rng, [(91, 81, 71, 61), (95, 85, 75, 65), (93, 83, 73, 63)])
    v = dict(CUTS=repr(list(zip("ABCD", cuts))))
    task = ("""The input is a JSON Lines file: one JSON object per line, like `{"student": "ana", "score": 87.5}`. Scores have at most one decimal.

- Ignore blank lines, lines that are not valid JSON objects, and records whose `student` is missing or empty (trim spaces) or whose `score` is missing, `null`, a string, a boolean, below 0 or above 100.
- A student with several valid records counts once, with their highest score. Names are case-sensitive.
- Each student's score is rounded to a whole number, halves rounding up (84.5 becomes 85), and then graded: A for at least %d, B for at least %d, C for at least %d, D for at least %d, otherwise F.
- Print five lines, for A, B, C, D, F in that order: `<grade>: <count> <bar>`, where the bar is one `#` per student (empty for zero, so the line then ends in a space).
- Then print `students: <number of students>`, `mean: <mean of the unrounded best scores>` and `median: <median of the unrounded best scores>`, both with one decimal, halves rounding up (for an even number of students the median is the mean of the two middle scores).
- If no record is valid, print only `no scores`.""" % cuts)
    a, b, c, d, e = sample(rng, ["ana", "bo", "cy", "di", "eli", "fay", "gus", "hal"], 5)
    J = lambda n, s: '{"student": "%s", "score": %s}' % (n, s)  # noqa: E731
    sample_text = "\n".join([J(a, "97"), J(b, "%d.5" % (cuts[1] - 2)), "", J(c, "null"), J(d, '"n/a"'), J(a, "60"), "not json at all",
                             J(e, "%d" % (cuts[3] + 5)), J(b, "%d" % (cuts[2] - 10)), '{"student": "%s"}' % d, J(d, "104"), J(d, "%d.4" % (cuts[3] - 3))]) + "\n"
    edge = "\n".join([J("a1", "%d.5" % (c0 - 1)) for c0 in cuts] + [J("a2", "%d.4" % (c0 - 1)) for c0 in cuts] + [J("a3", "100"), J("a4", "0"), J("a5", "-0.5"), J("a6", "100.5")]) + "\n"
    hidden = {
        "h_edges.jsonl": edge,
        "h_best.jsonl": "\n".join([J("x", "50"), J("x", "95"), J("x", "70"), J(" y ", "60"), J("y", "61"), J("", "99"), "[1, 2]", "42", J("z", "true"),
                                   J("w", "%d.5" % (cuts[0] - 1)), J("w", "0")]) + "\n",
        "h_mean.jsonl": "\n".join([J("a", "0.2"), J("b", "0.3")]) + "\n",
        "h_even.jsonl": "\n".join([J("a", "70.5"), J("b", "71"), J("c", "88.2"), J("d", "90.1")]) + "\n",
        "h_empty.jsonl": "\n\n",
        "h_novalid.jsonl": "\n".join(["{bad", J("q", "null"), J("q", '"90"')]) + "\n",
    }
    s = _GR_SOL
    wrongs = [
        _mut(s, "if name not in best or t > best[name]:", "if True:"),
        _mut(s, "r = (t + 5) // 10", "r = int(round(t / 10))"),
        _mut(s, "            if t < 0 or t > 1000:\n                continue\n", ""),
        _mut(s, "(2 * sum(ts) + n) // (2 * n)", "round(sum(ts) / n)"),
    ]
    return _finish(v, task=task, sample_name="scores.jsonl", sample_text=sample_text, ref=_GR_REF, gen=_GR_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


# ------------------------------------------------------------------ 8. order fulfilment times (CSV)

_ORD_REF = r'''import csv
import io
from datetime import datetime

FMT = "%Y-%m-%d %H:%M"


def _fmt(t):
    return "%d.%d" % (t // 10, t % 10)


def _ref(text):
    seen, stats, pending = set(), {}, 0
    for row in csv.DictReader(io.StringIO(text)):
        oid = row["order_id"].strip()
        if not oid or oid in seen:
            continue
        seen.add(oid)
        try:
            placed = datetime.strptime(row["placed"].strip(), FMT)
        except ValueError:
            continue
        sh = (row["shipped"] or "").strip()
        if not sh:
            pending += 1
            continue
        try:
            shipped = datetime.strptime(sh, FMT)
        except ValueError:
            continue
        if shipped < placed:
            continue
        mins = int((shipped - placed).total_seconds() // 60)
        pri = (row["priority"] or "").strip().lower() or "standard"
        stats.setdefault(pri, []).append(mins)
    out = [] if stats else ["no fulfilled orders"]
    for pri in sorted(stats):
        m = stats[pri]
        n = len(m)
        out.append("%s: n=%d avg=%sh max=%sh late=%d" % (pri, n, _fmt((20 * sum(m) + 60 * n) // (120 * n)),
                                                       _fmt((20 * max(m) + 60) // 120), sum(1 for x in m if x > @LATE@ * 60)))
    out.append("pending: %d" % pending)
    return "\n".join(out)
'''

_ORD_GEN = r'''import csv
import io


def _gen(rng):
    rows = [["order_id", "placed", "shipped", "priority"]]
    for i in range(rng.randint(60, 150)):
        oid = "O%03d" % rng.randint(1, 120)
        day = rng.randint(1, 9)
        placed = "2026-03-%02d %02d:%02d" % (day, rng.randint(0, 23), rng.randint(0, 59))
        r = rng.random()
        if r < 0.15:
            shipped = ""
        elif r < 0.2:
            shipped = "soon"
        else:
            shipped = "2026-03-%02d %02d:%02d" % (day + rng.randint(-1, 4) if day > 1 else day + rng.randint(0, 4), rng.randint(0, 23), rng.randint(0, 59))
        rows.append([oid, placed, shipped, rng.choice(["express", "Express", "standard", "economy ", "", "ECONOMY"])])
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()
'''

_ORD_SOL = r'''import csv
import sys
from datetime import datetime

FMT = "%Y-%m-%d %H:%M"
LATE_HOURS = @LATE@


def fmt(t):
    return "%d.%d" % (t // 10, t % 10)


def main():
    seen, stats, pending = set(), {}, 0
    with open(sys.argv[1], newline="") as f:
        for row in csv.DictReader(f):
            oid = row["order_id"].strip()
            if not oid or oid in seen:
                continue
            seen.add(oid)
            try:
                placed = datetime.strptime(row["placed"].strip(), FMT)
            except ValueError:
                continue
            sh = (row["shipped"] or "").strip()
            if not sh:
                pending += 1
                continue
            try:
                shipped = datetime.strptime(sh, FMT)
            except ValueError:
                continue
            if shipped < placed:
                continue
            mins = int((shipped - placed).total_seconds() // 60)
            pri = (row["priority"] or "").strip().lower() or "standard"
            stats.setdefault(pri, []).append(mins)
    if not stats:
        print("no fulfilled orders")
    for pri in sorted(stats):
        m = stats[pri]
        n = len(m)
        avg = (20 * sum(m) + 60 * n) // (120 * n)
        late = sum(1 for x in m if x > LATE_HOURS * 60)
        print("%s: n=%d avg=%sh max=%sh late=%d" % (pri, n, fmt(avg), fmt((20 * max(m) + 60) // 120), late))
    print("pending: %d" % pending)


main()
'''


def _orders(rng: random.Random):
    v = dict(LATE=pick(rng, [24, 36, 48]))
    task = """The input is a CSV file with the header `order_id,placed,shipped,priority`. `placed` and `shipped` look like `2026-03-01 14:30` (to the minute); `shipped` is blank for an order that has not shipped yet.

- Only the first row of each `order_id` counts; later rows with the same id are ignored completely. Rows with an empty id or an unreadable `placed` time are ignored.
- An order with a blank `shipped` is pending. A row whose `shipped` is unreadable, or earlier than `placed`, is ignored.
- Every other order is fulfilled; its fulfilment time is `shipped - placed` in whole minutes (a time can be longer than a day). The priority is trimmed and lower-cased; a blank priority means `standard`.
- For each priority in ascending name order print `<priority>: n=<fulfilled orders> avg=<A>h max=<M>h late=<L>`. Times are in hours with one decimal, halves rounding up (the average is the exact mean of the minutes, converted). An order is late when its fulfilment time is more than @LATE@ hours.
- Last print `pending: <number of pending orders>`. If there is no fulfilled order, print `no fulfilled orders` before that last line."""
    sample_text = _csv([["order_id", "placed", "shipped", "priority"], ["A1", "2026-03-01 09:00", "2026-03-01 15:30", "express"],
                        ["A2", "2026-03-01 10:15", "2026-03-03 09:00", "Standard"], ["A3", "2026-03-02 08:00", "", "standard"],
                        ["A1", "2026-03-04 08:00", "2026-03-05 08:00", "express"], ["A4", "2026-03-02 12:00", "2026-03-02 11:00", "express"],
                        ["A5", "2026-03-02 12:00", "2026-03-04 13:00", ""], ["A6", "2026-03-05 07:45", "2026-03-05 08:15", "express"]])
    hidden = {
        "h_long.csv": _csv([["order_id", "placed", "shipped", "priority"], ["B1", "2026-03-01 00:00", "2026-03-03 00:00", "standard"],
                            ["B2", "2026-03-01 00:00", "2026-03-%02d 00:00" % (2 + v["LATE"] // 24 - 1), "standard "],
                            ["B3", "2026-03-01 00:00", "2026-03-01 00:%02d" % 1, "ECONOMY"], ["B3", "2026-03-01 00:00", "2026-03-09 00:00", "economy"],
                            ["B4", "2026-03-01 00:00", "2026-03-01 %02d:00" % 23, "economy"], ["", "2026-03-01 00:00", "2026-03-01 01:00", "x"],
                            ["B5", "bad", "2026-03-01 01:00", "x"], ["B6", "2026-03-01 00:00", "2026-03-01 00:00", "x"],
                            ["B7", "2026-03-01 00:00", "", "x"], ["B8", "2026-03-01 00:00", "later", "x"]]),
        "h_late.csv": _csv([["order_id", "placed", "shipped", "priority"]] + [["L%d" % i, "2026-03-01 00:00", "2026-03-%02d %02d:%02d" % (
            1 + (v["LATE"] * 60 + dm) // 1440, ((v["LATE"] * 60 + dm) % 1440) // 60, (v["LATE"] * 60 + dm) % 60), "p"] for i, dm in enumerate([0, 1, -1, 7, 8, 8])]),
        "h_minutes.csv": _csv([["order_id", "placed", "shipped", "priority"], ["M1", "2026-03-01 00:00", "2026-03-01 00:09", "q"],
                               ["M2", "2026-03-01 00:00", "2026-03-01 00:03", "r"], ["M3", "2026-03-01 00:00", "2026-03-01 07:21", "s"]]),
        "h_pending.csv": _csv([["order_id", "placed", "shipped", "priority"], ["P1", "2026-03-01 00:00", "", "a"], ["P2", "2026-03-01 00:00", " ", "b"]]),
        "h_empty.csv": "order_id,placed,shipped,priority\n",
    }
    s = _ORD_SOL
    wrongs = [
        _mut(s, "int((shipped - placed).total_seconds() // 60)", "(shipped - placed).seconds // 60"),
        _mut(s, "if not oid or oid in seen:", "if not oid:"),
        _mut(s, "x > LATE_HOURS * 60", "x >= LATE_HOURS * 60"),
        _mut(s, ' or "standard"', ""),
        _mut(s, "avg = (20 * sum(m) + 60 * n) // (120 * n)", "avg = int(round(sum(m) / 60 / n, 1) * 10)"),
    ]
    return _finish(v, task=task, sample_name="orders.csv", sample_text=sample_text, ref=_ORD_REF, gen=_ORD_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


# ------------------------------------------------------------------ 9. room bookings (JSON Lines)

_BK_REF = r'''import json
from datetime import date


def _ref(text):
    rooms = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("status") != "confirmed" or not isinstance(rec.get("room"), str):
            continue
        try:
            s, e = date.fromisoformat(rec["start"]), date.fromisoformat(rec["end"])
        except (KeyError, ValueError, TypeError):
            continue
        if e <= s:
            continue
        rooms.setdefault(rec["room"], []).append((s, e))
    if not rooms:
        return "no bookings"
    out = []
    for room in sorted(rooms):
        b = rooms[room]
        conflicts = sum(1 for i in range(len(b)) for j in range(i + 1, len(b)) if b[i][0] < b[j][1] and b[j][0] < b[i][1])
        out.append("%s: bookings=%d nights=%d conflicts=%d" % (room, len(b), sum((e - s).days for s, e in b), conflicts))
    return "\n".join(out)
'''

_BK_GEN = r'''import json


def _gen(rng):
    lines = []
    for i in range(rng.randint(50, 120)):
        d = rng.randint(1, 28)
        e = d + rng.choice([-1, 0, 1, 2, 3, 5, 9])
        rec = {"room": rng.choice(["A1", "A2", "B7", "C3"]), "guest": "g%d" % i, "start": "2026-04-%02d" % d,
               "end": "2026-04-%02d" % max(1, min(e, 30)), "status": rng.choice(["confirmed", "confirmed", "confirmed", "cancelled", "pending"])}
        if rng.random() < 0.04:
            rec["end"] = "2026-04-31"
        if rng.random() < 0.03:
            del rec["end"]
        lines.append(json.dumps(rec))
        if rng.random() < 0.05:
            lines.append("")
    return "\n".join(lines) + "\n"
'''

_BK_SOL = r'''import json
import sys
from datetime import date


def main():
    rooms = {}
    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("status") != "confirmed" or not isinstance(rec.get("room"), str):
                continue
            try:
                s, e = date.fromisoformat(rec["start"]), date.fromisoformat(rec["end"])
            except (KeyError, ValueError, TypeError):
                continue
            if e <= s:
                continue
            rooms.setdefault(rec["room"], []).append((s, e))
    if not rooms:
        print("no bookings")
        return
    for room in sorted(rooms):
        b = rooms[room]
        conflicts = 0
        for i in range(len(b)):
            for j in range(i + 1, len(b)):
                if b[i][0] < b[j][1] and b[j][0] < b[i][1]:
                    conflicts += 1
        nights = sum((e - s).days for s, e in b)
        print("%s: bookings=%d nights=%d conflicts=%d" % (room, len(b), nights, conflicts))


main()
'''


def _bookings(rng: random.Random):
    v: dict = {}
    r1, r2, r3 = sample(rng, ["A1", "A2", "B4", "B7", "C3", "D9"], 3)
    task = """The input is a JSON Lines file of room bookings, one JSON object per line, like `{"room": "A1", "guest": "g1", "start": "2026-04-03", "end": "2026-04-06", "status": "confirmed"}`. A guest arrives on `start` and leaves on `end`, so the booking occupies the nights from `start` up to but not including `end`.

- Ignore blank lines, lines that are not JSON objects, and bookings whose `status` is not exactly `confirmed`, whose `room` is not a string, whose `start` or `end` is missing or not a valid `YYYY-MM-DD` date, or whose `end` is not after `start`.
- Two bookings of the same room conflict when their nights overlap. A booking that starts on the day another one ends does not conflict with it. Count each conflicting pair once.
- For each room in ascending name order print `<room>: bookings=<number of bookings> nights=<total nights> conflicts=<number of conflicting pairs>`.
- If no booking counts, print only `no bookings`."""
    B = lambda room, s, e, st="confirmed": json.dumps({"room": room, "guest": "g", "start": s, "end": e, "status": st})  # noqa: E731
    sample_text = "\n".join([B(r1, "2026-04-03", "2026-04-06"), B(r1, "2026-04-06", "2026-04-08"), B(r2, "2026-04-01", "2026-04-04"),
                             B(r1, "2026-04-05", "2026-04-07"), B(r2, "2026-04-02", "2026-04-03", "cancelled"), "", "oops",
                             B(r3, "2026-04-10", "2026-04-10"), B(r3, "2026-04-09", "2026-04-12", "pending"), B(r2, "2026-04-03", "2026-04-05")]) + "\n"
    hidden = {
        "h_three.jsonl": "\n".join([B("X", "2026-05-01", "2026-05-10"), B("X", "2026-05-03", "2026-05-04"), B("X", "2026-05-05", "2026-05-06"),
                                    B("X", "2026-05-10", "2026-05-11"), B("Y", "2026-05-01", "2026-05-03"), B("Y", "2026-05-03", "2026-05-05"),
                                    B("Y", "2026-05-05", "2026-05-07"), B("X", "2026-02-27", "2026-03-02"), B("X", "2026-03-31", "2026-04-01")]) + "\n",
        "h_invalid.jsonl": "\n".join([B("Z", "2026-02-30", "2026-03-02"), B("Z", "2026-03-05", "2026-03-04"), '{"room": "Z", "start": "2026-03-01"}',
                                      '{"room": 7, "start": "2026-03-01", "end": "2026-03-03", "status": "confirmed"}', "[]",
                                      B("Z", "2026-03-01", "2026-03-03", "Confirmed"), B("Z", "2026-03-01", "2026-03-03"),
                                      B("Z", "2026-03-02", "2026-03-04"), B("Z", "bad", "2026-03-04")]) + "\n",
        "h_none.jsonl": B("Q", "2026-03-01", "2026-03-03", "cancelled") + "\n",
        "h_empty.jsonl": "",
    }
    s = _BK_SOL
    wrongs = [
        _mut(s, "b[i][0] < b[j][1] and b[j][0] < b[i][1]", "b[i][0] <= b[j][1] and b[j][0] <= b[i][1]"),
        _mut(s, ' or rec.get("status") != "confirmed"', ""),
        _mut(s, "nights = sum((e - s).days for s, e in b)", "nights = sum((e - s).days + 1 for s, e in b)"),
        _mut(s, "                    conflicts += 1\n", "                    conflicts += 2\n"),
        _mut(s, "            if e <= s:\n                continue\n", ""),
    ]
    return _finish(v, task=task, sample_name="bookings.jsonl", sample_text=sample_text, ref=_BK_REF, gen=_BK_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


# ------------------------------------------------------------------ 10. CI job history (log lines)

_CI_REF = r'''def _ref(text):
    runs = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3 or parts[2] not in ("ok", "fail", "skipped"):
            continue
        if parts[2] == "skipped":
            continue
        runs.setdefault(parts[1], []).append((parts[0], parts[2]))
    if not runs:
        return "no runs"
    out = []
    for job in sorted(runs):
        rs = sorted(runs[job], key=lambda r: r[0])
        n, ok = len(rs), sum(1 for _, st in rs if st == "ok")
        streak = best = 0
        for _, st in rs:
            streak = streak + 1 if st == "fail" else 0
            best = max(best, streak)
        out.append("%s: runs=%d pass=%d%% streak=%d last=%s" % (job, n, (200 * ok + n) // (2 * n), best, rs[-1][1]))
    return "\n".join(out)
'''

_CI_GEN = r'''def _gen(rng):
    jobs = ["build", "lint", "unit-tests", "deploy-docs"]
    lines = []
    for i in range(rng.randint(60, 160)):
        r = rng.random()
        if r < 0.04:
            lines.append("")
        elif r < 0.07:
            lines.append("# 2026-03-01T00:00:00 build fail")
        elif r < 0.09:
            lines.append("2026-03-01T00:00:00 build error")
        else:
            lines.append("2026-03-%02dT%02d:%02d:00 %s %s" % (rng.randint(1, 3), rng.randint(0, 23), rng.randint(0, 59), rng.choice(jobs),
                                                              rng.choice(["ok", "ok", "fail", "fail", "skipped"])))
    return "\n".join(lines) + "\n"
'''

_CI_SOL = r'''import sys


def main():
    runs = {}
    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 3 or parts[2] not in ("ok", "fail", "skipped"):
                continue
            if parts[2] == "skipped":
                continue
            runs.setdefault(parts[1], []).append((parts[0], parts[2]))
    if not runs:
        print("no runs")
        return
    for job in sorted(runs):
        rs = sorted(runs[job], key=lambda r: r[0])
        n = len(rs)
        ok = sum(1 for _, st in rs if st == "ok")
        streak = best = 0
        for _, st in rs:
            streak = streak + 1 if st == "fail" else 0
            best = max(best, streak)
        print("%s: runs=%d pass=%d%% streak=%d last=%s" % (job, n, (200 * ok + n) // (2 * n), best, rs[-1][1]))


main()
'''


def _ci(rng: random.Random):
    v: dict = {}
    j1, j2, j3 = sample(rng, ["build", "lint", "unit-tests", "docs", "package", "e2e"], 3)
    task = """The input is a CI history log, one run per line: `<timestamp> <job> <status>`, for example `2026-03-01T10:00:00 build ok`. Timestamps all have the shape `YYYY-MM-DDTHH:MM:SS`, `<status>` is `ok`, `fail` or `skipped`, and the lines are not necessarily in time order.

- Ignore blank lines, lines starting with `#`, and lines that do not have exactly three fields or have another status. A `skipped` run is not a run at all: it is not counted and does not interrupt a streak.
- For each job in ascending name order print `<job>: runs=<number of runs> pass=<percent>% streak=<k> last=<status>`. `<percent>` is the share of `ok` runs, rounded to a whole number with halves rounding up. `<k>` is the longest streak of consecutive `fail` runs when the runs are put in time order (runs with equal timestamps keep their order in the file). `<status>` is the status of the latest run.
- A job with no counted run is not listed. If no job is listed, print only `no runs`."""
    sample_text = "\n".join(["# ci history", "2026-03-02T09:00:00 %s fail" % j1, "2026-03-01T09:00:00 %s ok" % j1, "2026-03-03T09:00:00 %s fail" % j1,
                             "2026-03-02T12:00:00 %s ok" % j2, "2026-03-01T12:00:00 %s skipped" % j2, "", "2026-03-02T10:00:00 %s skipped" % j1,
                             "2026-03-04T09:00:00 %s fail" % j1, "2026-03-03T12:00:00 %s fail" % j2, "2026-03-03T13:00:00 %s skipped" % j3,
                             "2026-03-05T09:00:00 %s ok" % j1, "2026-03-04T12:00:00 %s fail" % j2]) + "\n"
    hidden = {
        "h_streaks.log": "\n".join(["2026-03-05T00:00:03 a fail", "2026-03-05T00:00:01 a fail", "2026-03-05T00:00:02 a ok", "2026-03-05T00:00:04 a fail",
                                    "2026-03-05T00:00:05 a fail", "2026-03-05T00:00:06 a fail", "2026-03-05T00:00:07 a ok",
                                    "2026-03-06T00:00:00 b ok", "2026-03-06T00:00:00 b fail", "2026-03-06T00:00:00 b ok",
                                    "2026-03-06T00:00:00 c fail", "2026-03-06T00:00:01 c skipped", "2026-03-06T00:00:02 c fail",
                                    "2026-03-06T00:00:00 d ok", "2026-03-06T00:00:01 d ok", "2026-03-06T00:00:02 d fail"]) + "\n",
        "h_percent.log": "\n".join(["2026-03-0%dT00:00:00 p %s" % (1 + i % 9, st) for i, st in enumerate(["ok", "fail", "fail"])]
                                   + ["2026-03-01T00:00:0%d q %s" % (i, st) for i, st in enumerate(["ok", "ok", "ok", "fail", "fail", "fail", "fail", "fail"])]
                                   + ["2026-03-01T00:00:0%d r %s" % (i, st) for i, st in enumerate(["ok"] * 5 + ["fail"] * 3)]
                                   + ["2026-03-01T00:00:0%d s %s" % (i, st) for i, st in enumerate(["ok", "fail"])]) + "\n",
        "h_junk.log": "\n".join(["#2026-03-01T00:00:00 x fail", "2026-03-01T00:00:00 x", "2026-03-01T00:00:00 x fail extra", "2026-03-01T00:00:00 x error",
                                 "2026-03-01T00:00:00 x OK", "2026-03-01T00:00:01 x ok", "2026-03-01T00:00:02 y skipped", "   "]) + "\n",
        "h_empty.log": "\n",
    }
    s = _CI_SOL
    wrongs = [
        _mut(s, "rs = sorted(runs[job], key=lambda r: r[0])", "rs = runs[job]"),
        _mut(s, '            if parts[2] == "skipped":\n                continue\n', ""),
        _mut(s, "(200 * ok + n) // (2 * n)", "100 * ok // n"),
        _mut(s, "streak = streak + 1 if st == \"fail\" else 0", "streak = streak + 1 if st == \"fail\" else streak"),
    ]
    return _finish(v, task=task, sample_name="history.log", sample_text=sample_text, ref=_CI_REF, gen=_CI_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


# ------------------------------------------------------------------ 11. library fines (CSV)

_LIB_REF = r'''import csv
import io
from datetime import date

PERIOD, RATE, CAP = @PERIOD@, @RATE@, @CAP@


def _fmt(c):
    return "%d.%02d" % (c // 100, c % 100)


def _ref(text):
    fines, open_ = {}, []
    for row in csv.DictReader(io.StringIO(text)):
        member, book = row["member"].strip(), row["book"].strip()
        if not member:
            continue
        try:
            b = date.fromisoformat(row["borrowed"].strip())
        except ValueError:
            continue
        r = (row["returned"] or "").strip()
        if not r:
            open_.append((member, book))
            continue
        try:
            ret = date.fromisoformat(r)
        except ValueError:
            continue
        if ret < b:
            continue
        over = (ret - b).days - PERIOD
        if over > 0:
            fines[member] = fines.get(member, 0) + min(CAP, over * RATE)
    out = ["FINE %s %s" % (m, _fmt(fines[m])) for m in sorted(fines, key=lambda m: (-fines[m], m))]
    out += ["OPEN %s | %s" % (m, bk) for m, bk in sorted(open_)]
    return "\n".join(out) if out else "all clear"
'''

_LIB_GEN = r'''import csv
import io


def _gen(rng):
    rows = [["member", "book", "borrowed", "returned"]]
    for i in range(rng.randint(50, 130)):
        d = rng.randint(1, 28)
        r = rng.random()
        if r < 0.1:
            ret = ""
        elif r < 0.13:
            ret = "never"
        else:
            ret = "2026-%02d-%02d" % (rng.choice([3, 4, 5]), rng.randint(1, 28))
        rows.append([rng.choice(["ana", "bo ", "cy", "di"]), rng.choice(["Dune, Part 1", "Emma", "Ulysses", "Kim"]), "2026-03-%02d" % d, ret])
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()
'''

_LIB_SOL = r'''import csv
import sys
from datetime import date

PERIOD = @PERIOD@
RATE = @RATE@
CAP = @CAP@


def fmt(c):
    return "%d.%02d" % (c // 100, c % 100)


def main():
    fines, open_ = {}, []
    with open(sys.argv[1], newline="") as f:
        for row in csv.DictReader(f):
            member, book = row["member"].strip(), row["book"].strip()
            if not member:
                continue
            try:
                b = date.fromisoformat(row["borrowed"].strip())
            except ValueError:
                continue
            r = (row["returned"] or "").strip()
            if not r:
                open_.append((member, book))
                continue
            try:
                ret = date.fromisoformat(r)
            except ValueError:
                continue
            if ret < b:
                continue
            over = (ret - b).days - PERIOD
            if over > 0:
                fines[member] = fines.get(member, 0) + min(CAP, over * RATE)
    lines = ["FINE %s %s" % (m, fmt(fines[m])) for m in sorted(fines, key=lambda m: (-fines[m], m))]
    lines += ["OPEN %s | %s" % (m, bk) for m, bk in sorted(open_)]
    print("\n".join(lines) if lines else "all clear")


main()
'''


def _library(rng: random.Random):
    v = dict(PERIOD=pick(rng, [14, 21]), RATE=pick(rng, [25, 30, 50]), CAP=pick(rng, [500, 800, 1000]))
    P, R, C = v["PERIOD"], v["RATE"], v["CAP"]
    task = """The input is a CSV file with the header `member,book,borrowed,returned`; dates are `YYYY-MM-DD`, book titles may be quoted and contain commas. `returned` is blank while the book is still out.

- Loans last @PERIOD@ days: a book returned on or before the @PERIOD@th day after `borrowed` (the difference in days is at most @PERIOD@) is on time. Each full day beyond that costs $%s, but one loan never costs more than $%s. Fines are summed per member.
- Ignore rows with an empty member, an unreadable `borrowed` date, an unreadable `returned` date (when not blank), or a `returned` date before `borrowed`. Trim spaces around member and book.
- Print `FINE <member> <total>` (two decimals) for every member whose total is above zero, largest total first, equal totals by ascending member name.
- Then print `OPEN <member> | <book>` for every loan that is still out, sorted by member and then by book (plain string order).
- If neither kind of line exists, print only `all clear`.""" % (fmt_c(R), fmt_c(C))

    def dstr(day):  # a date by day offset inside March-April 2026
        import datetime
        return (datetime.date(2026, 3, 1) + datetime.timedelta(days=day)).isoformat()
    sample_text = _csv([["member", "book", "borrowed", "returned"], ["ana", "Dune, Part 1", dstr(0), dstr(P)], ["ana", "Emma", dstr(0), dstr(P + 3)],
                        ["bo", "Kim", dstr(2), dstr(2 + P + 40)], ["bo ", "Ulysses", dstr(4), ""], ["cy", "Emma", dstr(4), dstr(3)],
                        ["cy", "Kim", dstr(4), dstr(4 + P + 1)], ["di", "Dune, Part 1", dstr(5), "soon"], ["ana", "Dune, Part 1", dstr(6), ""]])
    hidden = {
        "h_cap.csv": _csv([["member", "book", "borrowed", "returned"]]
                          + [["zed", "B%d" % i, dstr(0), dstr(P + 200)] for i in range(2)] + [["zed", "Small", dstr(0), dstr(P + 2)]]
                          + [["amy", "B", dstr(0), dstr(P + 1)], ["amy", "C", dstr(0), dstr(P)], ["bob", "A", dstr(0), dstr(P + 1)]]),
        "h_sort.csv": _csv([["member", "book", "borrowed", "returned"], ["m2", "z", dstr(0), ""], ["m2", "Z, second", dstr(0), ""], ["m1", "b", dstr(0), ""],
                            ["m1", "a", dstr(0), ""], [" m1 ", "c", dstr(0), ""], ["x", "k", dstr(1), dstr(1 + P + 2)], ["y", "k", dstr(1), dstr(1 + P + 2)], ["", "k", dstr(1), ""],
                            ["w", "k", "bad", ""]]),
        "h_clear.csv": _csv([["member", "book", "borrowed", "returned"], ["a", "b", dstr(0), dstr(P)], ["c", "d", dstr(5), dstr(1)]]),
        "h_empty.csv": "member,book,borrowed,returned\n",
    }
    s = _LIB_SOL
    wrongs = [
        _mut(s, "min(CAP, over * RATE)", "over * RATE"),
        _mut(s, "over = (ret - b).days - PERIOD", "over = (ret - b).days - PERIOD + 1"),
        _mut(s, "fines[member] = fines.get(member, 0) + min(CAP, over * RATE)", "fines[member] = min(CAP, fines.get(member, 0) + over * RATE)"),
        _mut(s, 'member, book = row["member"].strip(), row["book"].strip()', 'member, book = row["member"], row["book"]'),
        _mut(s, "sorted(fines, key=lambda m: (-fines[m], m))", "sorted(fines)"),
    ]
    return _finish(v, task=task, sample_name="loans.csv", sample_text=sample_text, ref=_LIB_REF, gen=_LIB_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


def fmt_c(c: int) -> str:
    return "%d.%02d" % (c // 100, c % 100)


# ------------------------------------------------------------------ 12. meter readings (JSON Lines)

_MT_REF = r'''import json


def _fmt(t):
    return "%d.%d" % (t // 10, t % 10)


def _ref(text):
    meters = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        m, ts, kwh = rec.get("meter"), rec.get("ts"), rec.get("kwh")
        if not isinstance(m, str) or not isinstance(ts, str):
            continue
        if isinstance(kwh, bool) or not isinstance(kwh, (int, float)):
            continue
        meters.setdefault(m, []).append((ts, int(round(kwh * 10))))
    if not meters:
        return "no readings"
    out = []
    for m in sorted(meters):
        rs = sorted(meters[m], key=lambda r: r[0])
        total = peak = n = 0
        prev = None
        for _, t in rs:
            if prev is not None:
                d = t - prev
                if d >= 0:
                    total += d
                    peak = max(peak, d)
                    n += 1
            prev = t
        out.append("%s: no intervals" % m if n == 0 else "%s: total=%s peak=%s intervals=%d" % (m, _fmt(total), _fmt(peak), n))
    return "\n".join(out)
'''

_MT_GEN = r'''import json


def _gen(rng):
    lines = []
    for mname in ["M1", "M2", "M3"]:
        t, kwh = 0, rng.randint(0, 5000) / 10
        for i in range(rng.randint(0, 40)):
            kwh += rng.choice([0, 0.1, 1.5, 4.2, 12.0, 30.7])
            if rng.random() < 0.08:
                kwh = rng.randint(0, 20) / 10
            t += rng.randint(1, 3)
            rec = {"meter": mname, "ts": "2026-03-%02dT%02d:00:00" % (1 + t // 24, t % 24), "kwh": round(kwh, 1)}
            if rng.random() < 0.07:
                rec["kwh"] = rng.choice([None, "12.5", True])
            lines.append(json.dumps(rec))
    rng.shuffle(lines)
    return "\n".join(lines) + "\n"
'''

_MT_SOL = r'''import json
import sys


def fmt(t):
    return "%d.%d" % (t // 10, t % 10)


def main():
    meters = {}
    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            m, ts, kwh = rec.get("meter"), rec.get("ts"), rec.get("kwh")
            if not isinstance(m, str) or not isinstance(ts, str):
                continue
            if isinstance(kwh, bool) or not isinstance(kwh, (int, float)):
                continue
            meters.setdefault(m, []).append((ts, int(round(kwh * 10))))
    if not meters:
        print("no readings")
        return
    for m in sorted(meters):
        rs = sorted(meters[m], key=lambda r: r[0])
        total = peak = n = 0
        prev = None
        for _, t in rs:
            if prev is not None:
                d = t - prev
                if d >= 0:
                    total += d
                    peak = max(peak, d)
                    n += 1
            prev = t
        if n == 0:
            print("%s: no intervals" % m)
        else:
            print("%s: total=%s peak=%s intervals=%d" % (m, fmt(total), fmt(peak), n))


main()
'''


def _meters(rng: random.Random):
    v: dict = {}
    m1, m2, m3 = sample(rng, ["boiler", "wing-a", "wing-b", "pump", "annex", "lab"], 3)
    task = """The input is a JSON Lines file of cumulative electricity meter readings, one object per line: `{"meter": "wing-a", "ts": "2026-03-01T08:00:00", "kwh": 1204.5}`. `kwh` is the meter's running total and has at most one decimal; timestamps all have the form `YYYY-MM-DDTHH:MM:SS`. The lines are not necessarily in time order.

- Ignore blank lines, lines that are not JSON objects, and records whose `meter` or `ts` is not a string or whose `kwh` is not a number (`null`, strings and booleans are not numbers).
- Per meter, put the readings in time order (equal timestamps keep their file order). The consumption of an interval is the difference between two consecutive readings. If a reading is lower than the one before it, the meter was reset or replaced: that interval is not counted, and the new lower reading becomes the baseline for the next interval. An interval with a difference of zero is counted.
- For each meter in ascending name order print `<meter>: total=<sum of counted intervals> peak=<largest counted interval> intervals=<number of counted intervals>` with one decimal for the kWh values, or `<meter>: no intervals` when it has none.
- If no record counts, print only `no readings`."""
    R = lambda m, ts, k: json.dumps({"meter": m, "ts": "2026-03-%s:00:00" % ts, "kwh": k})  # noqa: E731
    sample_text = "\n".join([R(m1, "01T08", 100.0), R(m1, "01T10", 112.5), R(m2, "01T08", 50), R(m1, "01T09", 101.5), "", R(m1, "01T11", 3.2),
                             R(m1, "01T12", 10.2), R(m2, "01T09", None), R(m3, "01T09", 7.7), R(m2, "01T10", 50.0), R(m2, "01T07", "12")]) + "\n"
    hidden = {
        "h_resets.jsonl": "\n".join([R("a", "02T03", 5.0), R("a", "02T01", 10.0), R("a", "02T02", 2.0), R("a", "02T04", 7.0), R("a", "02T05", 6.0),
                                     R("a", "02T06", 9.5), R("b", "02T01", 1.0), R("b", "02T02", 0.0), R("b", "02T03", 4.0), R("b", "02T04", 3.9),
                                     R("c", "02T01", 1.0), R("d", "02T01", 5.0), R("d", "02T01", 5.0), R("d", "02T01", 4.0)]) + "\n",
        "h_junk.jsonl": "\n".join(['{"meter": "z", "ts": "2026-03-01T00:00:00", "kwh": true}', '{"meter": 1, "ts": "2026-03-01T00:00:00", "kwh": 3}',
                                   '{"meter": "z", "ts": 5, "kwh": 3}', "[3]", "oops", R("z", "01T01", 3), R("z", "01T02", 4.4), R("z", "01T03", 1.1),
                                   R("z", "01T04", 1.1), R("z", "01T05", 100)]) + "\n",
        "h_none.jsonl": "oops\n\n[]\n",
        "h_empty.jsonl": "",
    }
    s = _MT_SOL
    wrongs = [
        _mut(s, 'rs = sorted(meters[m], key=lambda r: r[0])', "rs = meters[m]"),
        _mut(s, "if d >= 0:", "if True:"),
        _mut(s, "            prev = t\n", "            prev = t if prev is None or t >= prev else prev\n"),
        _mut(s, "if d >= 0:", "if d > 0:"),
    ]
    return _finish(v, task=task, sample_name="meters.jsonl", sample_text=sample_text, ref=_MT_REF, gen=_MT_GEN, sol=s,
                   hidden=hidden, wrongs=wrongs)


TEMPLATES = [
    probe_template("data", "data_sales_by_region", _sales),
    probe_template("data", "data_slowest_endpoints", _endpoints),
    probe_template("data", "data_attendance_rates", _attendance),
    probe_template("data", "data_inventory_value", _inventory),
    probe_template("data", "data_monthly_expenses", _expenses),
    probe_template("data", "data_errors_per_hour", _errors),
    probe_template("data", "data_grade_distribution", _grades),
    probe_template("data", "data_fulfilment_times", _orders),
    probe_template("data", "data_room_bookings", _bookings),
    probe_template("data", "data_ci_streaks", _ci),
    probe_template("data", "data_library_fines", _library),
    probe_template("data", "data_meter_intervals", _meters),
]
