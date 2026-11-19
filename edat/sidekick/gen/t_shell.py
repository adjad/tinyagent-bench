"""`shell` templates: one command (a pipeline counts as one) on a shown folder;
graded on the shown folder and on a hidden second folder made by the same
seeded generator with different contents, so a hard-coded answer fails.

The prompt shows a listing of the shown folder only. Expected results are
computed in Python from each folder's contents.
"""
from __future__ import annotations

import random
import re

from edat.sidekick.gen.core import FILE_STEMS, Instance, Template, fence, pick


def _listing(files: dict) -> str:
    return "\n".join(sorted(files))


def _csv_folder(rng: random.Random, stems: list[str]) -> dict:
    files = {}
    for s in stems:
        n = rng.randint(2, 40)
        rows = ["id,value"] + [f"{i},{rng.randint(0, 999)}" for i in range(1, n)]
        files[f"{s}.csv"] = "\n".join(rows) + "\n"
    files["notes.txt"] = "not a csv\nsecond line\n"
    return files


def _count_csv_lines(rng: random.Random) -> Instance:
    stems = rng.sample(FILE_STEMS, 6)
    shown = _csv_folder(rng, stems[:3])
    hidden = _csv_folder(rng, stems[3:] + [pick(rng, ["extra", "more"])])

    def expect(files):
        pairs = [[name, files[name].count("\n")] for name in sorted(files) if name.endswith(".csv")]
        pairs.append(["total", sum(p[1] for p in pairs)])
        return {"output": {"mode": "pairs", "value": pairs}}

    prompt = ("How many lines are in each .csv file in this folder, and in total? Give me one command.\n\n"
              f"```\n$ ls\n{_listing(shown)}\n```")
    check = {"setups": [{"files": shown}, {"files": hidden}], "expect": [expect(shown), expect(hidden)]}
    refs = [fence("wc -l *.csv", "zsh"), "Use `wc`:\n\n" + fence("wc -l -- *.csv", "sh")]
    names = sorted(n for n in shown if n.endswith(".csv"))
    wrong = [fence("wc -l " + " ".join(names[:1]), "zsh"), fence("ls *.csv | wc -l", "zsh")]
    tool = {"files": shown}
    return Instance(prompt, "shell", "shell", check, refs, wrong, given_names=names, tool=tool)


def _delete_ds_store(rng: random.Random) -> Instance:
    def folder(r: random.Random) -> dict:
        files = {}
        dirs = ["", f"{pick(r, FILE_STEMS)} files/", f"src/{pick(r, ['old code', 'v2', 'my stuff'])}/", "docs/"]
        for d in dirs:
            files[f"{d}.DS_Store"] = "binaryish\n"
            for _ in range(r.randint(1, 3)):
                files[f"{d}{pick(r, FILE_STEMS)}{r.randint(1, 99)}.txt"] = f"{r.random()}\n"
        files["keep.DS_Store.txt"] = "not a .DS_Store file\n"
        return files

    shown, hidden = folder(rng), folder(rng)

    def expect(files):
        keep = {k: v for k, v in files.items() if not k.rsplit("/", 1)[-1] == ".DS_Store"}
        return {"tree": {"files": keep, "exact": True}}

    removals = lambda files: [k for k in files if k.rsplit("/", 1)[-1] == ".DS_Store"]
    prompt = ("Delete every `.DS_Store` file under this project, including in folders with spaces in their names. "
              f"Nothing else may change.\n\n```\n$ find . -type f\n" +
              "\n".join("./" + k for k in sorted(shown)) + "\n```")
    check = {"setups": [{"files": shown}, {"files": hidden}], "expect": [expect(shown), expect(hidden)],
             "removals": sorted(set(removals(shown)) | set(removals(hidden)))}
    refs = [fence("find . -name .DS_Store -type f -delete", "zsh"),
            fence("find . -type f -name '.DS_Store' -print0 | xargs -0 rm -f", "zsh"),
            fence("rm -f -- **/.DS_Store(N) .DS_Store(N)", "zsh")]
    wrong = [fence("rm .DS_Store", "zsh"), fence("find . -name '*DS_Store*' -delete", "zsh")]
    tool = {"files": shown}
    return Instance(prompt, "shell", "shell", check, refs, wrong, tool=tool)


def _find_listing(files: dict) -> str:
    return "$ find . -type f\n" + "\n".join("./" + k for k in sorted(files))


def _ls_listing(files: dict) -> str:
    return "$ ls\n" + _listing(files)


def _shell(prompt, shown, hidden, expect_fn, refs, wrong, tool=False, **extra) -> Instance:
    check = {"setups": [{"files": shown}, {"files": hidden}], "expect": [expect_fn(shown), expect_fn(hidden)]}
    check.update(extra)
    return Instance(prompt, "shell", "shell", check, refs, wrong, tool={"files": shown} if tool else None)


def _num(v):
    return {"output": {"mode": "number", "value": v}}


# ---- L1: count .log files in a tree (tool mode)

def _count_logs(rng: random.Random) -> Instance:
    def folder(r):
        files, used = {}, set()
        stems = r.sample(FILE_STEMS, 8)
        dirs = ["", "", "archive/", "archive/", "tmp/"]
        for i, d in enumerate(dirs + [pick(r, ["", "archive/"])]):
            files[f"{d}{stems[i]}{r.randint(1, 9)}.log"] = f"{r.random()}\n"
        files["archive/old.log"] = "x\n"
        for i in range(r.randint(2, 4)):
            files[f"{pick(r, ['', 'tmp/', 'archive/'])}{stems[6 + i % 2]}{i}.txt"] = "text\n"
        files["logbook.txt"] = "not a log file\n"
        return files

    shown, hidden = folder(rng), folder(rng)
    exp = lambda f: _num(sum(1 for k in f if k.endswith(".log")))
    prompt = ("How many `.log` files are in this project, including the ones in subfolders? "
              "Print just the number, in one command.\n\n```\n" + _find_listing(shown) + "\n```")
    refs = [fence("find . -name '*.log' | wc -l", "zsh"),
            fence("find . -type f -name '*.log' | wc -l | tr -d ' '", "zsh"),
            "Count them with `find`:\n\n" + fence("find . -type f -name '*.log' -print | grep -c .", "sh")]
    wrong = [fence("ls *.log | wc -l", "zsh"), fence("find . -name '*.log'", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong, tool=True)


# ---- L1: sum of a CSV column

def _csv_sum(rng: random.Random) -> Instance:
    col = pick(rng, ["amount", "total", "price", "weight"])
    stems = rng.sample(FILE_STEMS, 2)

    def folder(r, stem):
        n = r.randint(4, 30)
        rows = [f"id,{pick(r, ['name', 'label'])},status,{col}"]
        for i in range(1, n):
            rows.append(f"{i},{pick(r, FILE_STEMS)},{pick(r, ['open', 'done'])},{r.randint(1, 500)}")
        return {f"{stem}.csv": "\n".join(rows) + "\n", "README.txt": "numbers below are in column 4\n"}

    shown, hidden = folder(rng, stems[0]), folder(rng, stems[1])

    def exp(f):
        name = [k for k in f if k.endswith(".csv")][0]
        return _num(sum(int(l.split(",")[3]) for l in f[name].splitlines()[1:]))

    name = [k for k in shown if k.endswith(".csv")][0]
    head = shown[name].splitlines()[0]
    prompt = (f"The only `.csv` file here has a header row (`{head}`). Print the sum of the `{col}` column "
              "(column 4), nothing else, in one command.\n\n```\n" + _ls_listing(shown) + "\n```")
    refs = [fence("awk -F, 'NR > 1 { s += $4 } END { print s }' *.csv", "zsh"),
            fence("tail -n +2 *.csv | cut -d, -f4 | paste -sd+ - | bc", "sh"),
            fence("awk -F, 'NR>1 {t=t+$4} END {print t}' *.csv", "sh")]
    wrong = [fence("awk -F, '{ s += $3 } END { print s }' *.csv", "zsh"), fence("wc -l *.csv", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong)


# ---- L1: unique values of a column

def _unique_col(rng: random.Random) -> Instance:
    pool = ["north", "south", "east", "west", "central", "harbor", "valley", "ridge"]
    stems = rng.sample(FILE_STEMS, 2)

    def folder(r, stem):
        vals = r.sample(pool, r.randint(3, 5))
        rows = ["id,owner,region,qty"]
        for i in range(1, r.randint(10, 30)):
            rows.append(f"{i},{pick(r, ['ana', 'bo', 'cy', 'di'])},{pick(r, vals) if i > len(vals) else vals[i - 1]},{r.randint(1, 90)}")
        return {f"{stem}.csv": "\n".join(rows) + "\n"}

    shown, hidden = folder(rng, stems[0]), folder(rng, stems[1])

    def exp(f):
        rows = list(f.values())[0].splitlines()[1:]
        return {"output": {"mode": "lines_set", "value": sorted({l.split(",")[2] for l in rows})}}

    prompt = ("Print the distinct values of the `region` column (column 3) of the `.csv` file here, one per line. "
              "Skip the header row. One command.\n\n```\n" + _ls_listing(shown) + "\n```\n\nHeader: `id,owner,region,qty`")
    refs = [fence("tail -n +2 *.csv | cut -d, -f3 | sort -u", "zsh"),
            fence("awk -F, 'NR > 1 { print $3 }' *.csv | sort | uniq", "sh"),
            fence("awk -F, 'NR > 1 && !seen[$3]++ { print $3 }' *.csv", "sh")]
    wrong = [fence("cut -d, -f3 *.csv | sort -u", "zsh"), fence("cut -f3 *.csv | sort -u", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong)


# ---- L1: last three lines of a log

def _tail_log(rng: random.Random) -> Instance:
    stems = rng.sample(FILE_STEMS, 2)
    msgs = ["started", "cache warmed", "job queued", "job finished", "retrying", "idle", "flush done", "config reloaded",
            "health ok", "slow query", "disk 71%", "peer joined"]

    def folder(r, stem):
        lines = [f"10:{i:02d}:{r.randint(0, 59):02d} {pick(r, ['INFO', 'WARN'])} {m}-{r.randint(1, 99)}"
                 for i, m in enumerate(r.sample(msgs, r.randint(6, 10)))]
        return {f"{stem}.log": "\n".join(lines) + "\n"}

    shown, hidden = folder(rng, stems[0]), folder(rng, stems[1])
    exp = lambda f: {"output": {"mode": "exact", "value": "\n".join(list(f.values())[0].splitlines()[-3:])}}
    prompt = ("Show the last 3 lines of the only `.log` file in this folder. One command.\n\n```\n"
              + _ls_listing(shown) + "\n```")
    refs = [fence("tail -n 3 *.log", "zsh"), fence("tail -3 *.log", "sh"), fence("cat *.log | tail -n 3", "sh")]
    wrong = [fence("head -n 3 *.log", "zsh"), fence(f"tail -n 3 {list(shown)[0]}", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong)


# ---- L1: list the markdown files

def _list_md(rng: random.Random) -> Instance:
    def folder(r):
        stems = r.sample(FILE_STEMS, 9)
        files = {}
        for s in stems[:r.randint(2, 4)]:
            files[f"{s}.md"] = "# title\n"
        files[f"{stems[4]}.md.bak"] = "old\n"
        files[f"md-{stems[5]}.txt"] = "plain\n"
        files[f"{stems[6]}.txt"] = "plain\n"
        files[f"{stems[7]}.py"] = "x = 1\n"
        return files

    shown, hidden = folder(rng), folder(rng)
    exp = lambda f: {"output": {"mode": "lines_unordered", "value": sorted(k for k in f if k.endswith(".md"))}}
    prompt = ("List the names of the Markdown files (ending in `.md`) in this folder, one name per line and "
              "nothing else. One command.\n\n```\n" + _ls_listing(shown) + "\n```")
    refs = [fence("ls *.md", "zsh"), fence("ls | grep '\\.md$'", "sh"),
            fence("find . -maxdepth 1 -name '*.md' -exec basename {} \\;", "sh")]
    wrong = [fence("ls *md*", "zsh"), fence("ls | grep md", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong)


# ---- L2: name of the largest file

def _largest_file(rng: random.Random) -> Instance:
    def folder(r):
        stems = r.sample(FILE_STEMS, r.randint(5, 7))
        sizes = r.sample(range(60, 3000), len(stems))
        return {f"{s}.{pick(r, ['txt', 'csv', 'log'])}": "x" * (z - 1) + "\n" for s, z in zip(stems, sizes)}

    shown, hidden = folder(rng), folder(rng)

    def exp(f):
        big = max(f, key=lambda k: len(f[k]))
        pat = lambda n: r"(?<![\w.])" + re.escape(n) + r"(?![\w])"
        return {"output": {"mode": "contains", "value": [pat(big)],
                           "forbid": [pat(n) for n in f if n != big]}}

    prompt = ("Which file in this folder is the largest? Print only its name, in one command. "
              "(Don't count directories.)\n\n```\n" + _ls_listing(shown) + "\n```")
    refs = [fence("ls -S | head -n 1", "zsh"),
            fence("wc -c * | sort -rn | sed -n 2p", "zsh"),
            fence("ls -lS | awk 'NR == 2 { print $NF }'", "sh")]
    wrong = [fence("ls -rS | head -n 1", "zsh"), fence("ls -S | tail -n 1", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong)


# ---- L2: rename .txt to .md

def _rename_txt_md(rng: random.Random) -> Instance:
    def folder(r):
        stems = r.sample(FILE_STEMS, 7)
        files = {}
        for s in stems[:r.randint(3, 4)]:
            files[f"{s}.txt"] = f"{r.random()}\n"
        files[f"{pick(r, ['my notes', 'meeting log', 'draft v2'])}.txt"] = f"{r.random()}\n"
        files[f"{stems[4]}.md"] = "# kept\n"
        files[f"{stems[5]}.txt.bak"] = "backup\n"
        files[f"{stems[6]}.csv"] = "a,b\n"
        return files

    shown, hidden = folder(rng), folder(rng)

    def newname(k):
        return k[:-4] + ".md" if k.endswith(".txt") else k

    def exp(f):
        return {"tree": {"files": {newname(k): v for k, v in f.items()}, "exact": True}}

    olds = sorted({k for f in (shown, hidden) for k in f if k.endswith(".txt")})
    news = sorted({newname(k) for k in olds})
    prompt = ("Rename every `.txt` file in this folder so it ends in `.md` instead (same base name). "
              "Leave every other file as it is. Some names have spaces. One command.\n\n```\n"
              + _ls_listing(shown) + "\n```")
    refs = [fence('for f in *.txt; do mv -- "$f" "${f%.txt}.md"; done', "zsh"),
            fence('for f in *.txt; do mv -- "$f" "${f:r}.md"; done', "zsh"),
            fence("ls -1 | grep '\\.txt$' | while IFS= read -r f; do mv -- \"$f\" \"${f%.txt}.md\"; done", "sh")]
    wrong = [fence("mv *.txt *.md", "zsh"), fence('for f in *.txt; do cp "$f" "${f%.txt}.md"; done', "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong, changes=news, removals=olds)


# ---- L2: most common word

def _common_word(rng: random.Random) -> Instance:
    pool = ["alpha", "bravo", "cedar", "delta", "ember", "fjord", "gamma", "harbor", "indigo", "juniper", "kelp"]
    stems = rng.sample(FILE_STEMS, 2)

    def folder(r, stem):
        words = r.sample(pool, 5)
        counts = r.sample(range(1, 13), 5)
        lines = [w for w, c in zip(words, counts) for _ in range(c)]
        r.shuffle(lines)
        return {f"{stem}.txt": "\n".join(lines) + "\n"}

    shown, hidden = folder(rng, stems[0]), folder(rng, stems[1])

    def exp(f):
        lines = list(f.values())[0].split()
        cnt = {w: lines.count(w) for w in set(lines)}
        top = max(cnt, key=cnt.get)
        return {"output": {"mode": "pairs", "value": [[top, cnt[top]]],
                           "forbid": [r"\b" + w + r"\b" for w in cnt if w != top]}}

    prompt = ("The only `.txt` file here has one word per line. Print the word that appears most often together "
              "with how many times it appears, and nothing else. One command.\n\n```\n" + _ls_listing(shown) + "\n```")
    refs = [fence("sort *.txt | uniq -c | sort -rn | head -n 1", "zsh"),
            fence("sort *.txt | uniq -c | sort -nr | head -1", "sh"),
            fence("awk '{ c[$1]++ } END { for (w in c) print c[w], w }' *.txt | sort -rn | head -n 1", "sh")]
    wrong = [fence("sort *.txt | uniq -c | sort -n | head -n 1", "zsh"), fence("sort *.txt | uniq -c", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong)


# ---- L2: ERROR lines per log

def _grep_count(rng: random.Random) -> Instance:
    def folder(r):
        stems = r.sample(FILE_STEMS, 3)
        files = {}
        for i, s in enumerate(stems):
            lines = []
            n_err = 0 if i == 2 else r.randint(1, 7)
            for _ in range(n_err):
                lines.append(f"ERROR {pick(r, ['disk full', 'timeout', 'bad input', 'lock lost'])} #{r.randint(100, 999)}")
            for _ in range(r.randint(3, 8)):
                lines.append(f"{pick(r, ['INFO', 'WARN'])} {pick(r, ['all good', 'no error found', 'error budget ok', 'slow call'])} #{r.randint(100, 999)}")
            r.shuffle(lines)
            files[f"{s}.log"] = "\n".join(lines) + "\n"
        return files

    shown, hidden = folder(rng), folder(rng)

    def exp(f):
        return {"output": {"mode": "pairs", "value": [[k, sum(1 for l in v.splitlines() if "ERROR" in l)] for k, v in sorted(f.items())]}}

    prompt = ("For each `.log` file in this folder, show the file name and how many of its lines contain the "
              "uppercase word `ERROR` (files with none show 0). One command.\n\n```\n" + _ls_listing(shown) + "\n```")
    refs = [fence("grep -c ERROR *.log", "zsh"),
            fence("for f in *.log; do echo \"$f: $(grep -c ERROR \"$f\")\"; done", "zsh"),
            fence("for f in *.log; do printf '%s %s\\n' \"$f\" \"$(grep ERROR \"$f\" | wc -l)\"; done", "sh")]
    wrong = [fence("grep -ci error *.log", "zsh"), fence(f"grep -c ERROR {sorted(shown)[0]}", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong)


# ---- L2: in-place replace with BSD sed

def _sed_replace(rng: random.Random) -> Instance:
    old, new = rng.sample(["oldhost", "stagebox", "legacyhost", "devnode", "bluehost"], 2)
    new = new + ".example.invalid"

    def folder(r):
        stems = r.sample(FILE_STEMS, 5)
        files = {}
        for s in stems[:r.randint(2, 3)]:
            files[f"{s}.conf"] = (f"name={s}\nserver={old}\nport={r.randint(1000, 9000)}\n"
                                  f"mirror={old}:{r.randint(10, 99)} backup={old}\n")
        files["notes.txt"] = f"remember: {old} is retired in the .conf files only\n"
        files[f"{stems[4]}.ini"] = f"server={old}\n"
        return files

    shown, hidden = folder(rng), folder(rng)

    def exp(f):
        return {"tree": {"files": {k: (v.replace(old, new) if k.endswith(".conf") else v) for k, v in f.items()},
                         "exact": True}}

    confs = sorted({k for f in (shown, hidden) for k in f if k.endswith(".conf")})
    prompt = (f"On this Mac, replace every `{old}` with `{new}` inside the `.conf` files of this folder, editing "
              "them in place. Leave all other files alone. One command.\n\n```\n" + _ls_listing(shown) + "\n```")
    refs = [fence(f"sed -i '' 's/{old}/{new}/g' *.conf", "zsh"),
            fence(f"for f in *.conf; do sed 's/{old}/{new}/g' \"$f\" > \"$f.new\" && mv \"$f.new\" \"$f\"; done", "zsh"),
            fence(f"sed -i.bak 's/{old}/{new}/g' *.conf && rm -f *.conf.bak", "sh")]
    wrong = [fence(f"sed -i 's/{old}/{new}/g' *.conf", "zsh"), fence(f"sed 's/{old}/{new}/g' *.conf", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong, changes=confs)


# ---- L2: delete empty files

def _empty_files(rng: random.Random) -> Instance:
    def folder(r):
        files = {}
        for d in ["", "logs/", "my cache/"]:
            for s in r.sample(FILE_STEMS, r.randint(2, 3)):
                files[f"{d}{s}{r.randint(1, 9)}.{pick(r, ['txt', 'log', 'dat'])}"] = pick(r, ["", "", f"{r.random()}\n"])
        files["keep-blank.txt"] = "\n"
        files["empty-one.txt"] = ""
        return files

    shown, hidden = folder(rng), folder(rng)
    empties = lambda f: [k for k, v in f.items() if v == ""]
    exp = lambda f: {"tree": {"files": {k: v for k, v in f.items() if v != ""}, "exact": True}}
    prompt = ("Delete every empty (zero-byte) file under this folder, including subfolders. Files that have any "
              "content, even one newline, must stay. One command.\n\n```\n" + _find_listing(shown) + "\n```")
    refs = [fence("find . -type f -empty -delete", "zsh"),
            fence("find . -type f -size 0 -exec rm {} +", "zsh"),
            fence("find . -type f -empty -print0 | xargs -0 rm", "sh")]
    wrong = [fence("find . -type f -delete", "zsh"), fence("rm -f *.txt", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong,
                  removals=sorted(set(empties(shown)) | set(empties(hidden))))


# ---- L3: lines of code, blank-aware, nested dirs with spaces

def _loc_py(rng: random.Random) -> Instance:
    def folder(r):
        files = {}
        for d in ["", "pkg/", f"{pick(r, ['old code', 'my tools'])}/"]:
            for s in r.sample(FILE_STEMS, r.randint(1, 2)):
                body = []
                for _ in range(r.randint(3, 9)):
                    body.append(pick(r, ["x = 1", "def f():", "    return 2", "# note", "", "", "    ", "\t", "print('hi')"]))
                files[f"{d}{s}.py"] = "\n".join(body) + "\n    \nend = True\n"
        files["README.txt"] = "line\n\nline\n   \nline\n"
        files["pkg/data.json"] = "{\n\n}\n"
        return files

    shown, hidden = folder(rng), folder(rng)

    def exp(f):
        return _num(sum(1 for k, v in f.items() if k.endswith(".py") for l in v.splitlines() if l.strip()))

    prompt = ("Count the lines of code in all `.py` files in this project, including subfolders (some folder names "
              "have spaces). Do not count blank lines, including lines that contain only spaces or tabs. "
              "Print one number: the total over all files. One command.\n\n```\n" + _find_listing(shown) + "\n```")
    refs = [fence("find . -name '*.py' -exec cat {} + | grep -cv '^[[:space:]]*$'", "zsh"),
            fence("find . -name '*.py' -print0 | xargs -0 cat | grep -c '[^[:space:]]'", "zsh"),
            fence("cat **/*.py | grep -c '[^[:space:]]'", "zsh")]
    wrong = [fence("cat **/*.py | grep -c .", "zsh"), fence("cat **/*.py | wc -l", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong)


# ---- L3: total bytes with spaces in names

def _bytes_spaces(rng: random.Random) -> Instance:
    def folder(r):
        files = {}
        for d in ["", "my docs/", "old/"]:
            for s in r.sample(FILE_STEMS, 2):
                nm = f"{s} {r.randint(1, 9)}" if d != "old/" else s
                files[f"{d}{nm}.txt"] = "".join(f"{r.randint(0, 9999)}\n" for _ in range(r.randint(1, 12)))
        files["data.csv"] = "a,b\n1,2\n"
        files["my docs/skip me.md"] = "not counted\n"
        return files

    shown, hidden = folder(rng), folder(rng)
    exp = lambda f: _num(sum(len(v.encode()) for k, v in f.items() if k.endswith(".txt")))
    prompt = ("What is the total size in bytes of the content of all `.txt` files in this project, including "
              "subfolders? Several names have spaces. Print only the number. One command.\n\n```\n"
              + _find_listing(shown) + "\n```")
    refs = [fence("find . -name '*.txt' -print0 | xargs -0 cat | wc -c", "zsh"),
            fence("cat **/*.txt | wc -c | tr -d ' '", "zsh"),
            fence("find . -name '*.txt' -exec cat {} + | wc -c", "sh")]
    wrong = [fence("find . -name '*.txt' | xargs cat | wc -c", "zsh"), fence("cat *.txt | wc -c", "zsh")]
    return _shell(prompt, shown, hidden, exp, refs, wrong)


TEMPLATES = [
    Template("shell_wc_csv", "shell", "L1", "shell", _count_csv_lines, tool_mode=True),
    Template("shell_delete_ds_store_spaces", "shell", "L3", "shell", _delete_ds_store, tool_mode=True),
    Template("shell_count_logs_tree", "shell", "L1", "shell", _count_logs, tool_mode=True),
    Template("shell_csv_column_sum", "shell", "L1", "shell", _csv_sum),
    Template("shell_unique_column", "shell", "L1", "shell", _unique_col),
    Template("shell_tail_log", "shell", "L1", "shell", _tail_log),
    Template("shell_list_md", "shell", "L1", "shell", _list_md),
    Template("shell_largest_file", "shell", "L2", "shell", _largest_file),
    Template("shell_rename_txt_md", "shell", "L2", "shell", _rename_txt_md),
    Template("shell_common_word", "shell", "L2", "shell", _common_word),
    Template("shell_grep_count_errors", "shell", "L2", "shell", _grep_count),
    Template("shell_sed_replace_inplace", "shell", "L2", "shell", _sed_replace),
    Template("shell_delete_empty_files", "shell", "L2", "shell", _empty_files),
    Template("shell_loc_py_blanks", "shell", "L3", "shell", _loc_py),
    Template("shell_bytes_spaces", "shell", "L3", "shell", _bytes_spaces),
]
