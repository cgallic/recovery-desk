"""
recovery_desk/miniyaml.py — a tiny, stdlib-only YAML reader.

The core autonomous loop (selftest / demo / run) must close on a CLEAN CLONE
with zero installs. PyYAML is the only dependency the grader actually needs to
read a rubric, so we vendor a minimal parser that covers exactly the YAML shape
our rubric files use:

  * top-level `key: value` scalars (int / float / quoted / bare string)
  * block scalars folded with `>` (joined to a single spaced line)
  * block sequences (`- item`) of scalars
  * block sequences of mappings (`- id: x` then indented `weight: 1`)
  * nested mappings one level deep (e.g. an empty `cta_markers: []`)
  * `[]` inline empty list, `# comments`, and blank lines

It is intentionally NOT a general YAML implementation. `grader.load_rubric`
prefers real PyYAML when it is importable and only falls back to this so the
demo never needs `pip install`. `selftest` asserts both readers agree when
PyYAML is present, so this stays honest.
"""

from __future__ import annotations

from typing import Any, List, Tuple


def _strip_comment(line: str) -> str:
    """Remove a trailing ` # comment` outside of quotes."""
    out = []
    in_s = in_d = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "'" and not in_d:
            in_s = not in_s
        elif c == '"' and not in_s:
            in_d = not in_d
        elif c == "#" and not in_s and not in_d:
            # only a comment if preceded by whitespace or at start
            if i == 0 or line[i - 1] in " \t":
                break
        out.append(c)
        i += 1
    return "".join(out)


def _scalar(token: str) -> Any:
    token = token.strip()
    if token == "" or token == "~" or token.lower() == "null":
        return None
    if token == "[]":
        return []
    if token == "{}":
        return {}
    if token.lower() in ("true", "yes"):
        return True
    if token.lower() in ("false", "no"):
        return False
    if (token[0] == '"' and token[-1] == '"') or (token[0] == "'" and token[-1] == "'"):
        return token[1:-1]
    # numbers
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


class _Reader:
    def __init__(self, lines: List[str]):
        # keep (indent, content) for non-blank, comment-stripped lines
        self.rows: List[Tuple[int, str]] = []
        for raw in lines:
            stripped = _strip_comment(raw.rstrip("\n"))
            if stripped.strip() == "":
                continue
            self.rows.append((_indent(stripped), stripped.strip()))
        self.i = 0

    def peek(self) -> Tuple[int, str] | None:
        return self.rows[self.i] if self.i < len(self.rows) else None

    def _block_scalar(self, base_indent: int) -> str:
        """Consume an indented block (after a `>` folded scalar marker)."""
        parts: List[str] = []
        while True:
            nxt = self.peek()
            if nxt is None or nxt[0] <= base_indent:
                break
            parts.append(nxt[1])
            self.i += 1
        return " ".join(parts)

    def parse_map(self, base_indent: int) -> dict:
        out: dict = {}
        while True:
            nxt = self.peek()
            if nxt is None or nxt[0] < base_indent:
                break
            indent, content = nxt
            if indent != base_indent:
                break
            if content.startswith("- "):
                break  # a sequence at this level belongs to the parent key
            if ":" not in content:
                break
            key, _, rest = content.partition(":")
            key = key.strip()
            rest = rest.strip()
            self.i += 1
            if rest == ">" or rest == "|":
                out[key] = self._block_scalar(indent)
            elif rest == "":
                # could be a nested map or a sequence on following deeper lines
                child = self.peek()
                if child is not None and child[0] > indent and child[1].startswith("- "):
                    out[key] = self.parse_seq(child[0])
                elif child is not None and child[0] > indent:
                    out[key] = self.parse_map(child[0])
                else:
                    out[key] = None
            else:
                out[key] = _scalar(rest)
        return out

    def parse_seq(self, base_indent: int) -> list:
        out: list = []
        while True:
            nxt = self.peek()
            if nxt is None or nxt[0] != base_indent or not nxt[1].startswith("- "):
                break
            indent, content = nxt
            item = content[2:].strip()
            self.i += 1
            if ":" in item and not (item.startswith('"') or item.startswith("'")):
                # a mapping item: first key is inline, rest are deeper-indented
                m: dict = {}
                key, _, rest = item.partition(":")
                key = key.strip()
                rest = rest.strip()
                if rest == ">" or rest == "|":
                    m[key] = self._block_scalar(indent)
                else:
                    m[key] = _scalar(rest)
                # consume the rest of this mapping's keys (indented past the dash)
                child = self.peek()
                if child is not None and child[0] > indent:
                    m.update(self.parse_map(child[0]))
                out.append(m)
            else:
                out.append(_scalar(item))
        return out


def safe_load(text: str) -> dict:
    """Parse the subset of YAML our rubric files use. Returns a dict."""
    reader = _Reader(text.splitlines())
    result: dict = {}
    while True:
        nxt = reader.peek()
        if nxt is None:
            break
        indent, content = nxt
        if content.startswith("- "):
            # top-level sequence (not used by rubrics, but handle gracefully)
            return {"_list": reader.parse_seq(indent)}  # pragma: no cover
        result.update(reader.parse_map(indent))
        # parse_map stops at a sequence belonging to the last key; attach it
        nxt2 = reader.peek()
        if nxt2 is not None and nxt2[1].startswith("- "):
            # the sequence belongs to the most recently set empty key
            # find the last key whose value is None
            last_none = next((k for k in reversed(list(result)) if result[k] is None), None)
            if last_none is not None:
                result[last_none] = reader.parse_seq(nxt2[0])
            else:  # pragma: no cover - defensive
                reader.parse_seq(nxt2[0])
    return result
