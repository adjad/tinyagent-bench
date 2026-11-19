"""`regex` templates.

write: a description plus 10 positive and 10 negative strings shown; 20 + 20
hidden ones; mode `fullmatch` (per string) or `search`, stated in the prompt.
read:  6-8 labelled strings and a given pattern; the model names the labels
that match; the true set is computed by the generator.
"""
from __future__ import annotations

import random
import re
import string

from edat.sidekick.gen.core import Instance, Template, fence, pick
from edat.sidekick.graders.regex import true_labels


def _fmt_list(items: list[str]) -> str:
    return "\n".join(f"  {s}" for s in items)


def _order_code(rng: random.Random) -> Instance:
    nlet, ndig = pick(rng, [(2, 4), (3, 4), (2, 5), (3, 3)])
    sep = pick(rng, ["-", "_", "/"])
    U = string.ascii_uppercase

    def pos(r):
        return "".join(r.choice(U) for _ in range(nlet)) + sep + "".join(r.choice(string.digits) for _ in range(ndig))

    def neg(r):
        p = pos(r)
        kind = r.randrange(7)
        if kind == 0:
            return p.lower()
        if kind == 1:
            return p.replace(sep, "")
        if kind == 2:
            return p + r.choice(string.digits)
        if kind == 3:
            return p[1:]
        if kind == 4:
            return " " + p
        if kind == 5:
            return p.replace(sep, pick(r, [c for c in "-_/." if c != sep]))
        return p[:-1] + r.choice(U)

    rx = re.compile(f"[A-Z]{{{nlet}}}{re.escape(sep)}[0-9]{{{ndig}}}")

    def gen(f, n, want):
        out = []
        while len(out) < n:
            s = f(rng)
            if bool(rx.fullmatch(s)) == want and s not in out:
                out.append(s)
        return out

    P, N = gen(pos, 30, True), gen(neg, 30, False)
    example = P[0]
    prompt = (f"Write a Python regular expression for an order code: {nlet} capital letters, a `{sep}`, then {ndig} digits, "
              f"e.g. `{example}`. It will be used with `re.fullmatch` on each string.\n\n"
              f"Should match:\n{_fmt_list(P[:10])}\n\nShould not match:\n{_fmt_list(N[:10])}")
    check = {"mode": "fullmatch", "flags": "", "positives": P[:10], "negatives": N[:10],
             "hidden_pos": P[10:], "hidden_neg": N[10:]}
    e = re.escape(sep)
    refs = [fence(f"[A-Z]{{{nlet}}}{e}\\d{{{ndig}}}", "regex"),
            fence(f"^[A-Z]{{{nlet}}}{e}[0-9]{{{ndig}}}$", "regex"),
            fence(f"import re\nCODE = re.compile(r\"[A-Z]{{{nlet}}}{e}[0-9]{{{ndig}}}\")", "python")]
    wrong = [fence(f"[A-Za-z]+{e}\\d+", "regex"), fence(f"[A-Z]{{{nlet}}}.\\d{{{ndig}}}", "regex")]
    return Instance(prompt, "regex", "regex_write", check, refs, wrong)


def _read_optional_group(rng: random.Random) -> Instance:
    a, b = pick(rng, [(3, 2), (3, 4), (2, 3), (4, 2)])
    pattern = f"^\\d{{{a}}}(-\\d{{{b}}})?$"
    d = lambda n: "".join(rng.choice(string.digits) for _ in range(n))
    pool = [d(a), d(a) + "-" + d(b), d(a) + "-" + d(b - 1), d(a - 1) + "-" + d(b + 1), d(a) + "-" + d(b + 1),
            "abc", d(a) + "-", d(a + 1)]
    rng.shuffle(pool)
    n = rng.randint(6, 8)
    labels = {chr(65 + i): s for i, s in enumerate(pool[:n])}
    check = {"labels": labels, "pattern": pattern, "mode": "fullmatch", "flags": ""}
    check["answer"] = true_labels(check)
    listing = "  ".join(f"{k} `{v}`" for k, v in labels.items())
    prompt = (f"Which of these strings does `{pattern}` fully match (Python `re.fullmatch`)? "
              f"Start your answer with the letters, like \"B, D\".\n\n{listing}")
    ans = check["answer"]
    lab = ", ".join(ans) if ans else "None"
    allk = sorted(labels)
    wrong_set = [k for k in allk if k not in ans][:2] or allk[:1]
    refs = [lab, f"{lab}. The group `(-\\d{{{b}}})?` is optional and the anchors require the whole string to match.",
            f"**{lab}**"]
    wrong = [", ".join(wrong_set), ", ".join(allk)]
    return Instance(prompt, "text", "regex_read", check, refs, wrong)


# ---------------------------------------------------------------- shared helpers for the new write templates

def _collect(rng, gen, rx, mode, want, n=30, tries=4000):
    """n distinct strings from gen(rng) that the trusted pattern accepts (want) or rejects."""
    f = rx.fullmatch if mode == "fullmatch" else rx.search
    out: list[str] = []
    for _ in range(tries):
        s = gen(rng)
        if bool(f(s)) == want and s not in out:
            out.append(s)
            if len(out) == n:
                return out
    raise RuntimeError("could not generate enough strings")


def _write_instance(rng, desc, pattern, flags, mode, pos, neg, refs, wrong, how=None):
    fl = {"I": re.I}.get(flags, 0)
    rx = re.compile(pattern, fl)
    P, N = _collect(rng, pos, rx, mode, True), _collect(rng, neg, rx, mode, False)
    how = how or ("`re.fullmatch`" if mode == "fullmatch" else "`re.search`")
    extra = " Ignore case." if flags == "I" else ""
    prompt = (f"{desc} It will be used with {how} on each string.{extra}\n\n"
              f"Should match:\n{_fmt_list(P[:10])}\n\nShould not match:\n{_fmt_list(N[:10])}")
    check = {"mode": mode, "flags": flags, "positives": P[:10], "negatives": N[:10],
             "hidden_pos": P[10:], "hidden_neg": N[10:]}
    return Instance(prompt, "regex", "regex_write", check, refs, wrong)


def _rd(r, n):
    return "".join(r.choice(string.digits) for _ in range(n))


def _postal(rng: random.Random) -> Instance:
    n = pick(rng, [4, 5, 6])
    d = lambda r: _rd(r, n)

    def neg(r):
        k = r.randrange(7)
        s = d(r)
        if k == 0:
            return s[:-1]
        if k == 1:
            return s + r.choice(string.digits)
        if k == 2:
            i = r.randrange(n)
            return s[:i] + r.choice("abxz") + s[i + 1:]
        if k == 3:
            return " " + s
        if k == 4:
            return s[:n // 2] + "-" + s[n // 2:]
        if k == 5:
            return s + " "
        return s[:-1] + "O"
    pat = f"\\d{{{n}}}"
    desc = f"Write a Python regular expression for a postal code: exactly {n} digits and nothing else, e.g. `{d(rng)}`."
    refs = [fence(pat, "regex"), fence(f"^[0-9]{{{n}}}$", "regex"),
            fence(f"import re\nPOSTAL = re.compile(r\"[0-9]{{{n}}}\")", "python")]
    wrong = [fence("\\d+", "regex"), fence(f"\\d{{{n - 1},{n + 1}}}", "regex")]
    return _write_instance(rng, desc, pat, "", "fullmatch", d, neg, refs, wrong)


def _iso_date(rng: random.Random) -> Instance:
    sep = pick(rng, ["-", "/", "."])
    e = re.escape(sep)

    def pos(r):
        return f"{r.randint(1900, 2099)}{sep}{r.randint(0, 99):02d}{sep}{r.randint(0, 99):02d}"

    def neg(r):
        k = r.randrange(8)
        y, m, dd = _rd(r, 4), _rd(r, 2), _rd(r, 2)
        if k == 0:
            return f"{y[2:]}{sep}{m}{sep}{dd}"
        if k == 1:
            return f"{y}{sep}{int(m[1])}{sep}{dd}"
        if k == 2:
            return f"{y}{sep}{m}{sep}{int(dd[1])}"
        if k == 3:
            return f"{dd}{sep}{m}{sep}{y}"
        if k == 4:
            return f"{y}{pick(r, [c for c in '-/. ' if c != sep])}{m}{sep}{dd}"
        if k == 5:
            return f"{y}{sep}{m}{sep}{dd}{sep}"
        if k == 6:
            return f"{y}{sep}{m}{sep}{dd}{r.choice('abT')}"
        return f"{y}{sep}{m}{sep}{r.choice('xX')}{dd[1]}"
    pat = f"\\d{{4}}{e}\\d{{2}}{e}\\d{{2}}"
    ex = pos(rng)
    desc = (f"Write a Python regular expression for a date written as `YYYY{sep}MM{sep}DD`, four digits, two digits, two digits, "
            f"e.g. `{ex}`. Check only the shape; do not check that the month or day is a real one.")
    refs = [fence(pat, "regex"), fence(f"^[0-9]{{4}}{e}[0-9]{{2}}{e}[0-9]{{2}}$", "regex"),
            fence(f"import re\nDATE = re.compile(r\"\\d{{4}}{e}\\d{{2}}{e}\\d{{2}}\")", "python")]
    wrong = [fence(f"\\d+{e}\\d+{e}\\d+", "regex"), fence(f"\\d{{4}}.\\d{{2}}.\\d{{2}}", "regex")]
    return _write_instance(rng, desc, pat, "", "fullmatch", pos, neg, refs, wrong)


def _hex_colour(rng: random.Random) -> Instance:
    a, b = pick(rng, [(3, 6), (6, 8)])
    H = "0123456789abcdefABCDEF"

    def pos(r):
        return "#" + "".join(r.choice(H) for _ in range(r.choice([a, b])))

    def neg(r):
        k = r.randrange(7)
        n = r.choice([a, b])
        body = "".join(r.choice(H) for _ in range(n))
        if k == 0:
            return "#" + body[:-1] if n - 1 not in (a, b) else "#" + body + r.choice(H)
        if k == 1:
            return body
        if k == 2:
            return "#" + body + r.choice(H) * 2 if n + 2 not in (a, b) else "#" + body[:-2]
        if k == 3:
            i = r.randrange(n)
            return "#" + body[:i] + r.choice("ghxyzGHXZ") + body[i + 1:]
        if k == 4:
            return "##" + body
        if k == 5:
            return "# " + body
        return "#" + body + " "
    pat = f"#(?:[0-9a-f]{{{a}}}|[0-9a-f]{{{b}}})"
    ex = pos(rng)
    desc = (f"Write a Python regular expression for a hex colour: a `#` followed by exactly {a} or exactly {b} hex digits "
            f"(0-9 and a-f, either case), e.g. `{ex}`.")
    refs = [fence(f"(?:#[0-9a-f]{{{a}}}|#[0-9a-f]{{{b}}})", "regex"), fence(f"^#(?:[0-9a-fA-F]{{{a}}}|[0-9a-fA-F]{{{b}}})$", "regex"),
            fence(f"import re\nCOLOUR = re.compile(r\"#(?:[0-9a-f]{{{a}}}|[0-9a-f]{{{b}}})\", re.I)", "python")]
    wrong = [fence(f"#[0-9a-f]{{{a},{b}}}", "regex"), fence(f"#[0-9a-z]{{{a}}}|#[0-9a-z]{{{b}}}", "regex")]
    return _write_instance(rng, desc, pat, "I", "fullmatch", pos, neg, refs, wrong)


def _semver(rng: random.Random) -> Instance:
    def part(r):
        return str(r.choice([0, 0, r.randint(1, 9), r.randint(10, 99), r.randint(100, 999)]))

    def pos(r):
        return ".".join(part(r) for _ in range(3))

    def neg(r):
        k = r.randrange(9)
        p = [part(r) for _ in range(3)]
        if k == 0:
            return ".".join(p[:2])
        if k == 1:
            return ".".join(p + [part(r)])
        if k == 2:
            return "v" + ".".join(p)
        if k == 3:
            p[r.randrange(3)] = "0" + str(r.randint(1, 9))
            return ".".join(p)
        if k == 4:
            p[r.randrange(3)] = r.choice(["x", "beta", "rc1", "a"])
            return ".".join(p)
        if k == 5:
            return ".".join(p) + "."
        if k == 6:
            return ".".join(p).replace(".", "..", 1)
        if k == 7:
            return ".".join(p) + "-" + r.choice(["beta", "rc1", "dev"])
        return ".".join(p).replace(".", ",", 1)
    num = "(?:0|[1-9]\\d*)"
    pat = f"{num}\\.{num}\\.{num}"
    desc = (f"Write a Python regular expression for a version number `MAJOR.MINOR.PATCH`: three whole numbers separated by dots, "
            f"e.g. `{pos(rng)}`. A number is `0` or has no leading zero. No prefix and no suffix.")
    refs = [fence(pat, "regex"), fence(f"^(0|[1-9][0-9]*)(\\.(0|[1-9][0-9]*)){{2}}$", "regex"),
            fence(f"import re\nVERSION = re.compile(r\"(?:0|[1-9]\\d*)\\.(?:0|[1-9]\\d*)\\.(?:0|[1-9]\\d*)\")", "python")]
    wrong = [fence("\\d+\\.\\d+\\.\\d+", "regex"), fence("\\d\\.\\d\\.\\d", "regex")]
    return _write_instance(rng, desc, pat, "", "fullmatch", pos, neg, refs, wrong)


def _log_level(rng: random.Random) -> Instance:
    lvl = pick(rng, ["ERROR", "FATAL", "ALERT", "SEVERE"])
    others = [w for w in ["ERROR", "FATAL", "ALERT", "SEVERE", "WARN", "INFO", "DEBUG"] if w != lvl]
    msgs = ["disk full", "job {n} failed", "timeout after {n}s", "cannot open cache.db", "retry {n} of 5", "user {n} signed in",
            "queue length {n}", "backup done", "worker {n} restarted"]

    def msg(r):
        return r.choice(msgs).format(n=r.randint(1, 99))

    def pos(r):
        return f"{lvl}: {msg(r)}"

    def neg(r):
        k = r.randrange(8)
        m = msg(r)
        if k == 0:
            return f"{lvl.lower()}: {m}"
        if k == 1:
            return f" {lvl}: {m}"
        if k == 2:
            return f"{r.choice(others)}: {m} {lvl}: again"
        if k == 3:
            return f"[{lvl}] {m}"
        if k == 4:
            return f"{lvl} {m}"
        if k == 5:
            return f"{r.choice(others)}: {m}"
        if k == 6:
            return f"{lvl}S: {m}"
        return f"2031-04-05 {lvl}: {m}"
    pat = f"^{lvl}:"
    desc = (f"Write a Python regular expression that detects a log line that starts with `{lvl}:` "
            f"(exactly that word in capitals, then a colon), e.g. `{pos(rng)}`. A line that only mentions it later on does not count.")
    refs = [fence(pat, "regex"), fence(f"^{lvl}:.*", "regex"),
            fence(f"import re\nIS_LEVEL = re.compile(r\"^{lvl}:\")", "python")]
    wrong = [fence(f"{lvl}:", "regex"), fence(f"^{lvl}", "regex")]
    return _write_instance(rng, desc, pat, "", "search", pos, neg, refs, wrong)


def _time24(rng: random.Random) -> Instance:
    sep = pick(rng, [":", "h", "."])
    e = re.escape(sep)

    def pos(r):
        return f"{r.randint(0, 23):02d}{sep}{r.randint(0, 59):02d}"

    def neg(r):
        k = r.randrange(9)
        h, m = r.randint(0, 23), r.randint(0, 59)
        if k == 0:
            return f"{r.randint(24, 29):02d}{sep}{m:02d}"
        if k == 1:
            return f"{h:02d}{sep}{r.randint(60, 99):02d}"
        if k == 2:
            return f"{r.randint(0, 9)}{sep}{m:02d}"
        if k == 3:
            return f"{h:02d}{sep}{r.randint(0, 9)}"
        if k == 4:
            return f"{h:02d}{m:02d}"
        if k == 5:
            return f"{h:02d}{sep}{m:02d}{sep}{r.randint(0, 59):02d}"
        if k == 6:
            return f"{r.randint(30, 99)}{sep}{m:02d}"
        if k == 7:
            return f"{h:02d}{sep}{m:02d}{r.choice(['am', 'pm', ' '])}"
        return f"{h:02d}{pick(r, [c for c in ':h. ' if c != sep])}{m:02d}"
    pat = f"(?:[01]\\d|2[0-3]){e}[0-5]\\d"
    desc = (f"Write a Python regular expression for a 24-hour clock time `HH{sep}MM`: hours `00` to `23`, minutes `00` to `59`, "
            f"both with two digits, e.g. `{pos(rng)}`.")
    refs = [fence(pat, "regex"), fence(f"^([01][0-9]|2[0-3]){e}[0-5][0-9]$", "regex"),
            fence(f"import re\nCLOCK = re.compile(r\"(?:[01]\\d|2[0-3]){e}[0-5]\\d\")", "python")]
    wrong = [fence(f"\\d\\d{e}\\d\\d", "regex"), fence(f"[0-2]\\d{e}[0-5]\\d", "regex")]
    return _write_instance(rng, desc, pat, "", "fullmatch", pos, neg, refs, wrong)


def _image_names(rng: random.Random) -> Instance:
    exts = pick(rng, [["jpg", "jpeg", "png"], ["htm", "html", "md"], ["tif", "tiff", "bmp"], ["yml", "yaml", "json"]])
    alt = "|".join(exts)
    stems = ["holiday", "scan_01", "my-photo", "report", "IMG_2041", "cover", "draft.v2", "notes", "team", "a", "logo2", "Summer-Trip"]
    wrongext = ["gif", "txt", "pdf", "zip", "webp"]

    def cs(r, s):
        return "".join(c.upper() if r.random() < 0.4 else c for c in s)

    def pos(r):
        return r.choice(stems) + "." + cs(r, r.choice(exts))

    def neg(r):
        k = r.randrange(8)
        st, ex = r.choice(stems), r.choice(exts)
        if k == 0:
            return f"{st}.{ex}.bak"
        if k == 1:
            return st + ex
        if k == 2:
            return f"{st}.{ex[:-1]}"
        if k == 3:
            return f"{st}.{ex}x"
        if k == 4:
            return f"{st}.{r.choice(wrongext)}"
        if k == 5:
            return f"{st}.{ex} "
        if k == 6:
            return f".{ex}"
        return f"{st}.{ex}2"
    pat = f".+\\.(?:{alt})"
    desc = (f"Write a Python regular expression for a file name that ends in `.{exts[0]}`, `.{exts[1]}` or `.{exts[2]}`, "
            f"e.g. `{pos(rng)}`. There must be at least one character before the final dot.")
    refs = [fence(pat, "regex"), fence(f"^.+\\.({alt})$", "regex"),
            fence(f"import re\nPICTURE = re.compile(r\"[\\w.-]+\\.(?:{alt})\", re.I)", "python")]
    wrong = [fence(f".+\\.({exts[0]}|{exts[2]})", "regex"), fence(f"[a-z]+\\.(?:{alt})", "regex")]
    return _write_instance(rng, desc, pat, "I", "fullmatch", pos, neg, refs, wrong)


def _read_instance(rng, pattern, mode, pool, how, why, flags=""):
    for _ in range(50):
        pool2 = list(pool)
        rng.shuffle(pool2)
        n = rng.randint(6, 8)
        labels = {chr(65 + i): s for i, s in enumerate(pool2[:n])}
        check = {"labels": labels, "pattern": pattern, "mode": mode, "flags": flags}
        ans = true_labels(check)
        if 0 < len(ans) < n:
            break
    check["answer"] = ans
    listing = "  ".join(f"{k} `{v}`" for k, v in labels.items())
    prompt = (f"Which of these strings does `{pattern}` match ({how})? "
              f"Start your answer with the letters, like \"B, D\".\n\n{listing}")
    lab = ", ".join(ans)
    allk = sorted(labels)
    wrong_set = [k for k in allk if k not in ans][:2] or allk[:1]
    refs = [lab, f"{lab}. {why}", f"**{lab}**"]
    wrong = [", ".join(wrong_set), ", ".join(allk)]
    check["answer"] = ans
    return Instance(prompt, "text", "regex_read", check, refs, wrong)


def _read_alternation(rng: random.Random) -> Instance:
    a, b = rng.sample(["red", "tea", "sun", "oak", "fig", "ice", "gold", "mint", "blue", "sand"], 2)
    pattern = f"^{a}|{b}$"
    pool = [a, b, a + "x", "x" + a, b + "x", "x" + b, a + "x" + b, b + "x" + a, "q" + a + "q", "zzz", a + " " + b, b + " " + a]
    why = f"The anchors bind only to their own branch: `^{a}` needs the start, `{b}$` needs the end."
    return _read_instance(rng, pattern, "search", pool, "Python `re.search`", why)


def _read_word_boundary(rng: random.Random) -> Instance:
    w = pick(rng, ["cat", "art", "bar", "sun", "pen", "net", "ant", "box"])
    pattern = f"\\b{w}\\b"
    pool = [w, "con" + w, f"the {w} sat", w + "s", "big" + w, f"{w}-food", f"{w}_food", w.upper(), f"my {w}.", w + "2", f"a{w} {w}!"]
    why = f"`\\b` needs a non-word character (or an edge) on both sides of `{w}`; letters, digits and `_` count as word characters, and case matters."
    return _read_instance(rng, pattern, "search", pool, "Python `re.search`", why)


def _read_negated_class(rng: random.Random) -> Instance:
    ex = "".join(sorted(rng.sample("aeiou", 3)))
    pattern = f"[^{ex}]+"
    pool = ["sky", "dog", "cat", "tree", "gym", "plum", "fish", "rhythm", "hot", "crypt", "moon", "lynx", "pig", "ELM", "Sky", "x"]
    why = f"The class `[^{ex}]` accepts any character except those lowercase letters, and `fullmatch` needs every character to pass (uppercase is not excluded)."
    return _read_instance(rng, pattern, "fullmatch", pool, "Python `re.fullmatch`", why)


TEMPLATES = [
    Template("regex_write_order_code", "regex", "L1", "regex", _order_code, variant="write"),
    Template("regex_write_postal", "regex", "L1", "regex", _postal, variant="write"),
    Template("regex_write_iso_date", "regex", "L1", "regex", _iso_date, variant="write"),
    Template("regex_write_hex_colour", "regex", "L2", "regex", _hex_colour, variant="write"),
    Template("regex_write_semver", "regex", "L2", "regex", _semver, variant="write"),
    Template("regex_write_log_level", "regex", "L2", "regex", _log_level, variant="write"),
    Template("regex_write_time24", "regex", "L3", "regex", _time24, variant="write"),
    Template("regex_write_image_names", "regex", "L3", "regex", _image_names, variant="write"),
    Template("regex_read_optional_group", "regex", "L2", "regex", _read_optional_group, variant="read"),
    Template("regex_read_alternation", "regex", "L1", "regex", _read_alternation, variant="read"),
    Template("regex_read_word_boundary", "regex", "L2", "regex", _read_word_boundary, variant="read"),
    Template("regex_read_negated_class", "regex", "L1", "regex", _read_negated_class, variant="read"),
]
