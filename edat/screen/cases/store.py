"""Read and write screen case files (YAML lists, or JSON / JSON Lines)."""
from __future__ import annotations

import json
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def load_cases(path: str | Path) -> list[dict]:
    p = Path(path)
    if p.is_dir():
        out: list[dict] = []
        for f in sorted(p.rglob("*")):
            if f.suffix in (".yaml", ".yml", ".json", ".jsonl") and f.name not in ("counts.json", "manifest.json"):
                out.extend(load_cases(f))
        return out
    text = p.read_text()
    if p.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if p.suffix == ".json":
        data = json.loads(text)
        return data if isinstance(data, list) else data.get("cases", [])
    if yaml is None:
        raise RuntimeError("PyYAML is needed to read YAML case files")
    data = yaml.safe_load(text) or []
    return data if isinstance(data, list) else data.get("cases", [])


class _Dumper(yaml.SafeDumper if yaml else object):  # type: ignore[misc]
    pass


if yaml is not None:
    def _str(dumper, data):
        style = "|" if "\n" in data else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)
    _Dumper.add_representer(str, _str)


def dump_cases(cases: list[dict]) -> str:
    if yaml is None:
        return json.dumps(cases, indent=1, ensure_ascii=False) + "\n"
    return yaml.dump(cases, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=110)


def save_cases(path: str | Path, cases: list[dict], header: str = "") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = dump_cases(cases)
    p.write_text((header if header.endswith("\n") or not header else header + "\n") + body)
