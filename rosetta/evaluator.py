"""Safe, partial Excel formula evaluator for what-if recalculation.

DEPRECATED CANDIDATE (v1.5) — v2 Option B will replace this with rosetta/evaluator_v2.py
built on the `formulas` pip package. Do not add new function implementations here.
Currently still used by rosetta/tools.py::_what_if in v1.5.

We support a pragmatic subset sufficient for the demo workbooks:
  - arithmetic: + - * / ^ % unary -
  - comparisons: = <> > >= < <=
  - string concat via &
  - functions: SUM, AVERAGE, MIN, MAX, COUNT, COUNTA, PRODUCT, ROUND, ABS,
    IF, IFERROR, IFNA, AND, OR, NOT, SUMIF, SUMIFS, COUNTIF, COUNTIFS,
    AVERAGEIF, AVERAGEIFS, SUMPRODUCT, VLOOKUP, HLOOKUP, XLOOKUP, INDEX, MATCH,
    DATE, YEAR, MONTH, DAY, TODAY, NOW
  - references: cell, range, cross-sheet, named range

This is not a full Excel engine. Unsupported formulas return None and are
reported in what-if output.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any, Callable

from .formula_parser import expand_range, index_to_col, col_to_index
from .models import WorkbookModel


class EvalError(Exception):
    pass


class Evaluator:
    def __init__(self, wb: WorkbookModel, overrides: dict[str, Any] | None = None):
        self.wb = wb
        self.overrides: dict[str, Any] = overrides or {}
        self._eval_stack: set[str] = set()
        self._memo: dict[str, Any] = {}
        self.unsupported: set[str] = set()

    # --- Public API ---
    def value_of(self, ref: str) -> Any:
        """Return the value of a cell, applying overrides and recomputing formulas."""
        if ref in self.overrides:
            return self.overrides[ref]
        if ref in self._memo:
            return self._memo[ref]
        cell = self.wb.cells.get(ref)
        if not cell:
            return None
        if cell.formula is None:
            self._memo[ref] = cell.value
            return cell.value
        if ref in self._eval_stack:
            # Circular — return last cached value
            return cell.value
        self._eval_stack.add(ref)
        try:
            v = self._eval_formula(cell.formula, cell.sheet, ref)
        except Exception as e:
            self.unsupported.add(ref)
            v = cell.value  # fallback to cached
        self._eval_stack.discard(ref)
        self._memo[ref] = v
        return v

    # --- Internal: tokenizer ---
    def _tokenize(self, s: str) -> list[tuple[str, str]]:
        tokens: list[tuple[str, str]] = []
        i = 0
        n = len(s)
        while i < n:
            ch = s[i]
            if ch.isspace():
                i += 1
                continue
            if ch == '"':
                j = i + 1
                buf = []
                while j < n:
                    if s[j] == '"':
                        if j + 1 < n and s[j + 1] == '"':
                            buf.append('"')
                            j += 2
                            continue
                        break
                    buf.append(s[j])
                    j += 1
                tokens.append(("str", "".join(buf)))
                i = j + 1
                continue
            if ch == "'":
                j = i + 1
                while j < n and s[j] != "'":
                    j += 1
                sheet = s[i + 1 : j]
                i = j + 1
                # Expect !
                if i < n and s[i] == "!":
                    i += 1
                    ref_str, consumed = self._consume_ref(s, i)
                    tokens.append(("ref", f"{sheet}!{ref_str}"))
                    i += consumed
                continue
            if ch.isdigit() or (ch == "." and i + 1 < n and s[i + 1].isdigit()):
                m = re.match(r"\d+(\.\d+)?([eE][+-]?\d+)?", s[i:])
                tokens.append(("num", m.group(0)))
                i += m.end()
                continue
            # Operators
            two = s[i : i + 2]
            if two in ("<=", ">=", "<>"):
                tokens.append(("op", two))
                i += 2
                continue
            if ch in "+-*/^%&=<>(),:":
                tokens.append(("op", ch))
                i += 1
                continue
            # Identifier / function / sheet-prefixed ref / named range / cell
            m = re.match(r"[A-Za-z_][A-Za-z0-9_\.]*", s[i:])
            if m:
                ident = m.group(0)
                end = i + m.end()
                # Sheet ref?
                if end < n and s[end] == "!":
                    i = end + 1
                    ref_str, consumed = self._consume_ref(s, i)
                    tokens.append(("ref", f"{ident}!{ref_str}"))
                    i += consumed
                    continue
                # Function?
                peek = end
                while peek < n and s[peek].isspace():
                    peek += 1
                if peek < n and s[peek] == "(":
                    tokens.append(("func", ident.upper()))
                    i = end
                    continue
                # Cell ref like A1?
                cell_m = re.match(r"\$?([A-Z]{1,3})\$?(\d{1,7})$", ident)
                if cell_m:
                    # Could be followed by :A2 => range
                    if end < n and s[end] == ":":
                        # Consume next ref
                        j = end + 1
                        cell2_m = re.match(r"\$?[A-Z]{1,3}\$?\d{1,7}", s[j:])
                        if cell2_m:
                            rng = f"{ident.replace('$','')}:{cell2_m.group(0).replace('$','')}"
                            tokens.append(("ref", rng))
                            i = j + cell2_m.end()
                            continue
                    tokens.append(("ref", ident.replace("$", "")))
                    i = end
                    continue
                # Whole column ref like A:A
                col_m = re.match(r"\$?([A-Z]{1,3})$", ident)
                if col_m and end < n and s[end] == ":":
                    j = end + 1
                    col2_m = re.match(r"\$?([A-Z]{1,3})(?!\d)", s[j:])
                    if col2_m:
                        tokens.append(("ref", f"{ident.replace('$','')}:{col2_m.group(0).replace('$','')}"))
                        i = j + col2_m.end()
                        continue
                # Named range?
                if any(nr.name.upper() == ident.upper() for nr in self.wb.named_ranges):
                    tokens.append(("name", ident))
                    i = end
                    continue
                # Boolean
                if ident.upper() == "TRUE":
                    tokens.append(("num", "1"))
                    i = end
                    continue
                if ident.upper() == "FALSE":
                    tokens.append(("num", "0"))
                    i = end
                    continue
                tokens.append(("name", ident))  # unresolved name — will error later
                i = end
                continue
            i += 1
        return tokens

    def _consume_ref(self, s: str, i: int) -> tuple[str, int]:
        m = re.match(r"\$?[A-Z]{1,3}\$?\d{1,7}(:\$?[A-Z]{1,3}\$?\d{1,7})?", s[i:])
        if m:
            return m.group(0).replace("$", ""), m.end()
        m = re.match(r"\$?[A-Z]{1,3}:\$?[A-Z]{1,3}", s[i:])
        if m:
            return m.group(0).replace("$", ""), m.end()
        return "", 0

    # --- Parser (recursive descent with precedence) ---
    def _eval_formula(self, formula: str, sheet: str, self_ref: str) -> Any:
        # Save/restore parser state so recursive evaluations (via value_of)
        # do not clobber the caller's tokens/position.
        saved = (getattr(self, "_tokens", None), getattr(self, "_pos", None),
                 getattr(self, "_sheet", None), getattr(self, "_self", None))
        self._tokens = self._tokenize(formula)
        self._pos = 0
        self._sheet = sheet
        self._self = self_ref
        try:
            v = self._parse_expr()
        finally:
            self._tokens, self._pos, self._sheet, self._self = saved
        return v

    def _peek(self) -> tuple[str, str] | None:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _consume(self) -> tuple[str, str]:
        t = self._tokens[self._pos]
        self._pos += 1
        return t

    def _parse_expr(self) -> Any:
        return self._parse_compare()

    def _parse_compare(self) -> Any:
        left = self._parse_concat()
        while True:
            t = self._peek()
            if not t or t[0] != "op" or t[1] not in ("=", "<>", "<", ">", "<=", ">="):
                return left
            op = self._consume()[1]
            right = self._parse_concat()
            if op == "=":
                left = _eq(left, right)
            elif op == "<>":
                left = not _eq(left, right)
            elif op == "<":
                left = _num(left) < _num(right)
            elif op == ">":
                left = _num(left) > _num(right)
            elif op == "<=":
                left = _num(left) <= _num(right)
            elif op == ">=":
                left = _num(left) >= _num(right)

    def _parse_concat(self) -> Any:
        left = self._parse_add()
        while True:
            t = self._peek()
            if not t or t != ("op", "&"):
                return left
            self._consume()
            right = self._parse_add()
            left = f"{_stringify(left)}{_stringify(right)}"

    def _parse_add(self) -> Any:
        left = self._parse_mul()
        while True:
            t = self._peek()
            if not t or t[0] != "op" or t[1] not in ("+", "-"):
                return left
            op = self._consume()[1]
            right = self._parse_mul()
            if op == "+":
                left = _num(left) + _num(right)
            else:
                left = _num(left) - _num(right)

    def _parse_mul(self) -> Any:
        left = self._parse_pow()
        while True:
            t = self._peek()
            if not t or t[0] != "op" or t[1] not in ("*", "/"):
                return left
            op = self._consume()[1]
            right = self._parse_pow()
            if op == "*":
                left = _num(left) * _num(right)
            else:
                rv = _num(right)
                left = _num(left) / rv if rv != 0 else float("inf")

    def _parse_pow(self) -> Any:
        left = self._parse_pct()
        while True:
            t = self._peek()
            if not t or t != ("op", "^"):
                return left
            self._consume()
            right = self._parse_pct()
            left = _num(left) ** _num(right)

    def _parse_pct(self) -> Any:
        left = self._parse_unary()
        t = self._peek()
        if t == ("op", "%"):
            self._consume()
            return _num(left) / 100
        return left

    def _parse_unary(self) -> Any:
        t = self._peek()
        if t == ("op", "-"):
            self._consume()
            return -_num(self._parse_unary())
        if t == ("op", "+"):
            self._consume()
            return _num(self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> Any:
        t = self._consume()
        kind, val = t
        if kind == "num":
            return float(val) if "." in val or "e" in val.lower() else int(val)
        if kind == "str":
            return val
        if kind == "op" and val == "(":
            v = self._parse_expr()
            nxt = self._consume()
            if nxt != ("op", ")"):
                raise EvalError("expected )")
            return v
        if kind == "func":
            return self._call_func(val)
        if kind == "ref":
            return self._resolve_ref(val)
        if kind == "name":
            return self._resolve_name(val)
        raise EvalError(f"unexpected token {t}")

    def _parse_args(self) -> list[Any]:
        nxt = self._consume()
        if nxt != ("op", "("):
            raise EvalError("expected (")
        args: list[Any] = []
        if self._peek() == ("op", ")"):
            self._consume()
            return args
        while True:
            # For functions like SUMIF that accept ranges, capture the raw refs
            args.append(self._parse_arg())
            t = self._peek()
            if t == ("op", ","):
                self._consume()
                continue
            if t == ("op", ")"):
                self._consume()
                return args
            raise EvalError(f"expected , or ), got {t}")

    def _parse_arg(self) -> Any:
        # Only wrap RANGE refs as RangeArg so that functions like SUMIF/VLOOKUP can iterate.
        # Scalar refs should resolve to values via normal expression evaluation.
        if self._peek() and self._peek()[0] == "ref":
            save_pos = self._pos
            ref_tok = self._consume()
            nxt = self._peek()
            is_range = ":" in ref_tok[1]
            if is_range and nxt and nxt[0] == "op" and nxt[1] in (",", ")"):
                return RangeArg(ref_tok[1], self._sheet, self)
            self._pos = save_pos
        return self._parse_expr()

    def _call_func(self, name: str) -> Any:
        args = self._parse_args()
        fn = _FUNCS.get(name)
        if fn is None:
            raise EvalError(f"unsupported function {name}")
        return fn(self, args)

    def _resolve_ref(self, ref: str) -> Any:
        # ref could be "Sheet!A1", "A1", "Sheet!A1:B2", "A1:B2"
        if "!" in ref:
            sheet, r = ref.split("!", 1)
        else:
            sheet, r = self._sheet, ref
        if ":" in r:
            return RangeArg(ref, self._sheet, self).values()
        canonical = f"{sheet}!{r}"
        return self.value_of(canonical)

    def _resolve_name(self, nm: str) -> Any:
        nr = next((n for n in self.wb.named_ranges if n.name.upper() == nm.upper()), None)
        if not nr:
            raise EvalError(f"unknown name {nm}")
        if len(nr.resolved_refs) == 1:
            return self._resolve_ref(nr.resolved_refs[0])
        return [self._resolve_ref(r) for r in nr.resolved_refs]


class RangeArg:
    """Wraps a range reference so functions can iterate cells and values."""
    def __init__(self, ref: str, default_sheet: str, ev: "Evaluator"):
        if "!" in ref:
            self.sheet, rng = ref.split("!", 1)
        else:
            self.sheet, rng = default_sheet, ref
        self.raw = ref
        self.ev = ev
        # Build list of refs
        if ":" in rng:
            start, end = rng.split(":")
            self.refs = expand_range(self.sheet, start, end, max_row=ev.wb.sheets[0].max_row if ev.wb.sheets else 10000)
            # If expansion returned a logical-range-only item, fall back
            if len(self.refs) == 1 and ":" in self.refs[0]:
                # Try to enumerate using workbook-known cells for this sheet/col range
                self.refs = self._enumerate_cells(start.replace("$", ""), end.replace("$", ""))
        else:
            self.refs = [f"{self.sheet}!{rng.replace('$','')}"]

    def _enumerate_cells(self, start: str, end: str) -> list[str]:
        """For whole-column refs like A:A, list cells actually present in workbook."""
        s_col = "".join(ch for ch in start if ch.isalpha())
        e_col = "".join(ch for ch in end if ch.isalpha())
        s_row = "".join(ch for ch in start if ch.isdigit())
        e_row = "".join(ch for ch in end if ch.isdigit())
        c1 = col_to_index(s_col); c2 = col_to_index(e_col)
        c1, c2 = min(c1, c2), max(c1, c2)
        r1 = int(s_row) if s_row else 1
        r2 = int(e_row) if e_row else 1048576
        r1, r2 = min(r1, r2), max(r1, r2)
        refs = []
        for ref in self.ev.wb.cells:
            if not ref.startswith(f"{self.sheet}!"):
                continue
            coord = ref.split("!", 1)[1]
            col = "".join(ch for ch in coord if ch.isalpha())
            row = int("".join(ch for ch in coord if ch.isdigit()))
            ci = col_to_index(col)
            if c1 <= ci <= c2 and r1 <= row <= r2:
                refs.append(ref)
        return refs

    def values(self) -> list[Any]:
        return [self.ev.value_of(r) for r in self.refs]


# --- Helpers ---

def _num(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return 0.0
    if hasattr(v, "toordinal"):
        return float(v.toordinal() - date(1899, 12, 30).toordinal())
    return 0.0


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return _stringify(a).lower() == _stringify(b).lower()
    return _num(a) == _num(b)


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _flat(arg: Any) -> list[Any]:
    if isinstance(arg, RangeArg):
        return arg.values()
    if isinstance(arg, list):
        out = []
        for x in arg:
            out.extend(_flat(x))
        return out
    return [arg]


def _numerics(arg: Any) -> list[float]:
    return [_num(v) for v in _flat(arg) if isinstance(v, (int, float)) or (isinstance(v, str) and _is_num_str(v))]


def _is_num_str(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _match_criteria(value: Any, criterion: Any) -> bool:
    if isinstance(criterion, str):
        c = criterion.strip()
        for op in (">=", "<=", "<>", ">", "<", "="):
            if c.startswith(op):
                rhs = c[len(op):].strip()
                try:
                    rhs_n = float(rhs)
                    v_n = _num(value)
                    if op == ">=":
                        return v_n >= rhs_n
                    if op == "<=":
                        return v_n <= rhs_n
                    if op == ">":
                        return v_n > rhs_n
                    if op == "<":
                        return v_n < rhs_n
                    if op == "<>":
                        return _stringify(value).lower() != rhs.lower()
                    if op == "=":
                        return _stringify(value).lower() == rhs.lower()
                except ValueError:
                    return _stringify(value).lower() != rhs.lower() if op == "<>" else _stringify(value).lower() == rhs.lower()
        return _stringify(value).lower() == c.lower()
    return _eq(value, criterion)


def _sum(ev: Evaluator, args: list[Any]) -> float:
    total = 0.0
    for a in args:
        for v in _flat(a):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total += v
    return total


def _avg(ev: Evaluator, args: list[Any]) -> float:
    vals = []
    for a in args:
        for v in _flat(a):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(v)
    return sum(vals) / len(vals) if vals else 0.0


def _min(ev: Evaluator, args: list[Any]) -> float:
    vals = _numerics(args)
    return min(vals) if vals else 0.0


def _max(ev: Evaluator, args: list[Any]) -> float:
    vals = _numerics(args)
    return max(vals) if vals else 0.0


def _count(ev: Evaluator, args: list[Any]) -> int:
    c = 0
    for a in args:
        for v in _flat(a):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                c += 1
    return c


def _counta(ev: Evaluator, args: list[Any]) -> int:
    c = 0
    for a in args:
        for v in _flat(a):
            if v is not None and v != "":
                c += 1
    return c


def _product(ev: Evaluator, args: list[Any]) -> float:
    p = 1.0
    for a in args:
        for v in _flat(a):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                p *= v
    return p


def _if(ev: Evaluator, args: list[Any]) -> Any:
    cond = args[0]
    truthy = bool(cond) if not isinstance(cond, (int, float)) else cond != 0
    if truthy:
        return args[1] if len(args) > 1 else True
    return args[2] if len(args) > 2 else False


def _iferror(ev: Evaluator, args: list[Any]) -> Any:
    val = args[0]
    if isinstance(val, str) and val.startswith("#"):
        return args[1] if len(args) > 1 else 0
    return val


def _and(ev, args):
    return all(bool(a) for a in _flat(args))


def _or(ev, args):
    return any(bool(a) for a in _flat(args))


def _not(ev, args):
    return not bool(args[0])


def _round(ev, args):
    v = _num(args[0])
    d = int(_num(args[1])) if len(args) > 1 else 0
    return round(v, d)


def _abs(ev, args):
    return abs(_num(args[0]))


def _sumif(ev, args):
    rng: RangeArg = args[0]
    crit = args[1]
    sum_rng: RangeArg = args[2] if len(args) > 2 else rng
    if not isinstance(rng, RangeArg) or not isinstance(sum_rng, RangeArg):
        return 0
    vals = rng.values()
    svals = sum_rng.values()
    total = 0.0
    for v, sv in zip(vals, svals):
        if _match_criteria(v, crit):
            if isinstance(sv, (int, float)) and not isinstance(sv, bool):
                total += sv
    return total


def _sumifs(ev, args):
    sum_rng: RangeArg = args[0]
    if not isinstance(sum_rng, RangeArg):
        return 0
    svals = sum_rng.values()
    crit_ranges: list[tuple[RangeArg, Any]] = []
    i = 1
    while i + 1 <= len(args) - 1:
        crit_ranges.append((args[i], args[i + 1]))
        i += 2
    total = 0.0
    for idx, sv in enumerate(svals):
        ok = True
        for r, c in crit_ranges:
            if not isinstance(r, RangeArg):
                ok = False
                break
            rv = r.values()
            if idx >= len(rv) or not _match_criteria(rv[idx], c):
                ok = False
                break
        if ok and isinstance(sv, (int, float)) and not isinstance(sv, bool):
            total += sv
    return total


def _countif(ev, args):
    rng = args[0]
    crit = args[1]
    if not isinstance(rng, RangeArg):
        return 0
    return sum(1 for v in rng.values() if _match_criteria(v, crit))


def _countifs(ev, args):
    pairs = []
    i = 0
    while i + 1 <= len(args) - 1:
        pairs.append((args[i], args[i + 1]))
        i += 2
    if not pairs:
        return 0
    length = len(pairs[0][0].values()) if isinstance(pairs[0][0], RangeArg) else 0
    c = 0
    for idx in range(length):
        ok = True
        for r, crit in pairs:
            if not isinstance(r, RangeArg):
                ok = False
                break
            rv = r.values()
            if idx >= len(rv) or not _match_criteria(rv[idx], crit):
                ok = False
                break
        if ok:
            c += 1
    return c


def _averageif(ev, args):
    rng = args[0]
    crit = args[1]
    avg_rng = args[2] if len(args) > 2 else rng
    if not isinstance(rng, RangeArg) or not isinstance(avg_rng, RangeArg):
        return 0
    tot, cnt = 0.0, 0
    for v, av in zip(rng.values(), avg_rng.values()):
        if _match_criteria(v, crit) and isinstance(av, (int, float)) and not isinstance(av, bool):
            tot += av
            cnt += 1
    return tot / cnt if cnt else 0


def _averageifs(ev, args):
    avg_rng = args[0]
    if not isinstance(avg_rng, RangeArg):
        return 0
    svals = avg_rng.values()
    pairs = []
    i = 1
    while i + 1 <= len(args) - 1:
        pairs.append((args[i], args[i + 1]))
        i += 2
    tot, cnt = 0.0, 0
    for idx, sv in enumerate(svals):
        ok = True
        for r, crit in pairs:
            if not isinstance(r, RangeArg):
                ok = False; break
            rv = r.values()
            if idx >= len(rv) or not _match_criteria(rv[idx], crit):
                ok = False; break
        if ok and isinstance(sv, (int, float)) and not isinstance(sv, bool):
            tot += sv
            cnt += 1
    return tot / cnt if cnt else 0


def _sumproduct(ev, args):
    arrays = [a.values() if isinstance(a, RangeArg) else _flat(a) for a in args]
    length = min(len(x) for x in arrays)
    total = 0.0
    for i in range(length):
        p = 1.0
        for arr in arrays:
            v = arr[i]
            if isinstance(v, bool):
                p *= 1 if v else 0
            elif isinstance(v, (int, float)):
                p *= v
            else:
                p *= 0
        total += p
    return total


def _vlookup(ev, args):
    lookup = args[0]
    table = args[1]
    col_idx = int(_num(args[2]))
    approx = bool(args[3]) if len(args) > 3 else True
    if not isinstance(table, RangeArg):
        return None
    sheet = table.sheet
    # Determine key column and row bounds from the table's raw spec
    raw = table.raw.split("!", 1)[-1] if "!" in table.raw else table.raw
    if ":" in raw:
        start, end = raw.split(":", 1)
    else:
        start = end = raw
    def _split_opt(coord):
        col = "".join(ch for ch in coord if ch.isalpha())
        row = "".join(ch for ch in coord if ch.isdigit())
        return col, (int(row) if row else None)
    c1, r1 = _split_opt(start.replace("$", ""))
    c2, r2 = _split_opt(end.replace("$", ""))
    c1i, c2i = sorted([col_to_index(c1), col_to_index(c2)])
    key_col = index_to_col(c1i)
    target_col = index_to_col(c1i + col_idx - 1)
    # Row range: if whole-column, scan known cells in workbook for that sheet/col
    if r1 is None or r2 is None:
        rows_seen = sorted({
            int("".join(ch for ch in ref.split("!", 1)[1] if ch.isdigit()))
            for ref in ev.wb.cells if ref.startswith(f"{sheet}!{key_col}")
        })
        row_range = rows_seen
    else:
        r1i, r2i = sorted([r1, r2])
        row_range = list(range(r1i, r2i + 1))
    for r in row_range:
        key_ref = f"{sheet}!{key_col}{r}"
        key_val = ev.value_of(key_ref)
        if _eq(key_val, lookup):
            return ev.value_of(f"{sheet}!{target_col}{r}")
    return None


def _xlookup(ev, args):
    lookup = args[0]
    lookup_arr = args[1]
    return_arr = args[2]
    if_not_found = args[3] if len(args) > 3 else None
    if not isinstance(lookup_arr, RangeArg) or not isinstance(return_arr, RangeArg):
        return if_not_found
    lvals = lookup_arr.values()
    rvals = return_arr.values()
    for i, v in enumerate(lvals):
        if _eq(v, lookup):
            return rvals[i] if i < len(rvals) else None
    return if_not_found


def _index(ev, args):
    arr = args[0]
    row = int(_num(args[1]))
    col = int(_num(args[2])) if len(args) > 2 else 1
    if isinstance(arr, RangeArg):
        refs = arr.refs
        # Assume rectangular
        # Count rows/cols
        cols = set()
        rows = set()
        for r in refs:
            coord = r.split("!")[1]
            cc = "".join(ch for ch in coord if ch.isalpha())
            rr = int("".join(ch for ch in coord if ch.isdigit()))
            cols.add(cc); rows.add(rr)
        sorted_rows = sorted(rows); sorted_cols = sorted(cols, key=col_to_index)
        if row - 1 < len(sorted_rows) and col - 1 < len(sorted_cols):
            target = f"{arr.sheet}!{sorted_cols[col-1]}{sorted_rows[row-1]}"
            return ev.value_of(target)
    return None


def _match(ev, args):
    lookup = args[0]
    rng = args[1]
    if not isinstance(rng, RangeArg):
        return None
    for i, v in enumerate(rng.values()):
        if _eq(v, lookup):
            return i + 1
    return None


def _today(ev, args):
    return date.today()


def _now(ev, args):
    return datetime.utcnow()


def _date(ev, args):
    y, m, d = int(_num(args[0])), int(_num(args[1])), int(_num(args[2]))
    return date(y, m, d)


def _year(ev, args):
    v = args[0]
    if hasattr(v, "year"):
        return v.year
    return 0


def _month(ev, args):
    v = args[0]
    if hasattr(v, "month"):
        return v.month
    return 0


def _day(ev, args):
    v = args[0]
    if hasattr(v, "day"):
        return v.day
    return 0


_FUNCS: dict[str, Callable[["Evaluator", list[Any]], Any]] = {
    "SUM": _sum, "AVERAGE": _avg, "MIN": _min, "MAX": _max,
    "COUNT": _count, "COUNTA": _counta, "PRODUCT": _product,
    "IF": _if, "IFERROR": _iferror, "IFNA": _iferror,
    "AND": _and, "OR": _or, "NOT": _not,
    "ROUND": _round, "ABS": _abs,
    "SUMIF": _sumif, "SUMIFS": _sumifs,
    "COUNTIF": _countif, "COUNTIFS": _countifs,
    "AVERAGEIF": _averageif, "AVERAGEIFS": _averageifs,
    "SUMPRODUCT": _sumproduct,
    "VLOOKUP": _vlookup, "XLOOKUP": _xlookup,
    "INDEX": _index, "MATCH": _match,
    "TODAY": _today, "NOW": _now, "DATE": _date,
    "YEAR": _year, "MONTH": _month, "DAY": _day,
}
