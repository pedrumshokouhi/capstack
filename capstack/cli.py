"""capstack: the command line."""
import argparse
import json
import sys

from . import __version__
from .engine import build, waterfall
from .model import CapError, parse, parse_number

EXAMPLE = """# A two-founder company with a pool, two SAFEs and a seed round.
company Example Inc

founder Ada 4,000,000
founder Ben 4,000,000
pool 1,000,000

safe YC    125,000 post-money-cap 1,875,000
safe Angel 500,000 post-money-cap 8,000,000 discount 20%

round Seed pre 12,000,000 raise 3,000,000 pool-to 10% pref 1x non-participating
  investor Sequoia 3,000,000
"""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="capstack", description="Cap tables as code: a plain-text file in, ownership out.")
    ap.add_argument("--version", action="version", version="capstack " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("table", help="the cap table after the last round (or after --at ROUND)")
    p.add_argument("file")
    p.add_argument("--at", help="stage name: Founding, or a round name")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")

    p = sub.add_parser("rounds", help="the cap table after every stage, with the conversion math")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("diff", help="how ownership changes between two .cap files")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--at", help="compare at this stage (default: the last stage of each)")

    p = sub.add_parser("exit", help="who gets what at a sale")
    p.add_argument("file")
    p.add_argument("--at", dest="values", action="append", required=True,
                   help="exit value, e.g. 50M (repeat for several)")
    p.add_argument("--stage", help="use the table after this stage (default: last)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("check", help="parse a .cap file and report problems")
    p.add_argument("file")

    sub.add_parser("example", help="print an example .cap file")

    a = ap.parse_args(argv)
    if a.cmd is None:
        ap.print_help()
        return 0
    try:
        return _run(a)
    except CapError as e:
        print("capstack: " + str(e), file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print("capstack: no such file: " + e.filename, file=sys.stderr)
        return 2


def _run(a):
    if a.cmd == "example":
        sys.stdout.write(EXAMPLE)
        return 0
    if a.cmd == "check":
        company = parse(_read(a.file))
        tables = build(company)
        print("%s: %d events, %d stages, %s shares after %s"
              % (company.name, len(company.events), len(tables), _n(tables[-1].total()), tables[-1].stage))
        return 0
    if a.cmd == "table":
        company = parse(_read(a.file))
        table = _pick(build(company), a.at)
        if a.json:
            print(json.dumps(table.to_dict(), indent=2))
        else:
            print(render_table(company.name, table))
        return 0
    if a.cmd == "rounds":
        company = parse(_read(a.file))
        tables = build(company)
        if a.json:
            print(json.dumps([t.to_dict() for t in tables], indent=2))
        else:
            for t in tables:
                print(render_table(company.name, t, with_notes=True))
                print()
        return 0
    if a.cmd == "diff":
        before = parse(_read(a.before))
        after = parse(_read(a.after))
        tb = _pick(build(before), a.at)
        ta = _pick(build(after), a.at)
        print(render_diff(a.before, a.after, tb, ta))
        return 0
    if a.cmd == "exit":
        company = parse(_read(a.file))
        table = _pick(build(company), a.stage)
        out = []
        for v in a.values:
            value = parse_number(v)
            rows = waterfall(table, value)
            if a.json:
                out.append({"exit_value": value, "stage": table.stage,
                            "payouts": [{"name": h.name, "kind": h.kind, "shares": h.shares,
                                         "invested": h.invested, "payout": p, "how": how}
                                        for h, p, how in rows]})
            else:
                print(render_waterfall(company.name, table, value, rows))
                print()
        if a.json:
            print(json.dumps(out, indent=2))
        return 0
    return 1


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _pick(tables, stage):
    if not stage:
        return tables[-1]
    for t in tables:
        if t.stage.lower() == stage.lower():
            return t
    raise CapError("no stage called %r (have: %s)" % (stage, ", ".join(t.stage for t in tables)))


# ---------- rendering ----------

def render_table(company, table, with_notes=False):
    lines = []
    title = "%s, %s" % (company, table.stage.lower() if table.stage == "Founding" else "after " + table.stage)
    lines.append(title)
    if table.price:
        lines.append("$%s pre-money, $%s post-money, $%.4f per share" % (_n(table.pre), _n(table.post), table.price))
    lines.append("")
    rows = [(_label(h, table), h.kind, h.shares, table.percent(h), h.invested, h.price) for h in table.holdings]
    w_name = max(len("Holder"), max(len(r[0]) for r in rows))
    head = "%-*s  %-9s  %14s  %7s  %14s  %10s" % (w_name, "Holder", "Kind", "Shares", "Owns", "Invested", "Per share")
    lines.append(head)
    lines.append("-" * len(head))
    for name, kind, shares, pct, invested, price in rows:
        lines.append("%-*s  %-9s  %14s  %6.2f%%  %14s  %10s"
                     % (w_name, name, kind, _n(shares), pct * 100,
                        ("$" + _n(invested)) if invested else "", ("$%.4f" % price) if price else ""))
    lines.append("-" * len(head))
    lines.append("%-*s  %-9s  %14s  %6.2f%%  %14s" % (w_name, "Total", "", _n(table.total()), 100.0,
                                                     "$" + _n(sum(h.invested for h in table.holdings))))
    if with_notes and table.notes:
        lines.append("")
        for n in table.notes:
            lines.append("  " + n)
    elif table.notes and table.stage == "Founding":
        lines.append("")
        for n in table.notes:
            lines.append("  " + n)
    return "\n".join(lines)


def render_diff(name_before, name_after, tb, ta):
    lines = ["%s -> %s  (%s -> %s)" % (name_before, name_after, tb.stage, ta.stage), ""]
    before, after = {}, {}
    for h in tb.holdings:
        before[h.name] = before.get(h.name, 0.0) + tb.percent(h)
    for h in ta.holdings:
        after[h.name] = after.get(h.name, 0.0) + ta.percent(h)
    names = list(before) + [n for n in after if n not in before]
    w = max(len("Holder"), max(len(n) for n in names))
    lines.append("  %-*s  %8s  %8s  %10s" % (w, "Holder", "Before", "After", "Change"))
    lines.append("-" * (w + 34))
    for n in names:
        b = before.get(n)
        a = after.get(n)
        if b is None:
            mark, bs, as_, ch = "+", "", "%7.2f%%" % (a * 100), "new"
        elif a is None:
            mark, bs, as_, ch = "-", "%7.2f%%" % (b * 100), "", "gone"
        else:
            d = (a - b) * 100
            mark = " " if abs(d) < 0.005 else ("+" if d > 0 else "-")
            bs, as_, ch = "%7.2f%%" % (b * 100), "%7.2f%%" % (a * 100), "%+8.2f pp" % d
        lines.append("%s %-*s  %8s  %8s  %10s" % (mark, w, n, bs, as_, ch))
    if tb.price and ta.price:
        lines.append("")
        lines.append("Price per share: $%.4f -> $%.4f    Post-money: $%s -> $%s"
                     % (tb.price, ta.price, _n(tb.post), _n(ta.post)))
    return "\n".join(lines)


def render_waterfall(company, table, value, rows):
    lines = ["%s sells for $%s (table after %s)" % (company, _n(value), table.stage), ""]
    w = max(len("Holder"), max(len(_label(h, table)) for h, _, _ in rows))
    head = "%-*s  %14s  %7s  %14s  %8s  %s" % (w, "Holder", "Invested", "Owns", "Payout", "Multiple", "How")
    lines.append(head)
    lines.append("-" * len(head))
    total = sum(p for _, p, _ in rows)
    for h, p, how in sorted(rows, key=lambda r: -r[1]):
        mult = ("%.1fx" % (p / h.invested)) if h.invested else ""
        lines.append("%-*s  %14s  %6.2f%%  %14s  %8s  %s"
                     % (w, _label(h, table), ("$" + _n(h.invested)) if h.invested else "", table.percent(h) * 100,
                        "$" + _n(p), mult, how))
    lines.append("-" * len(head))
    lines.append("%-*s  %14s  %7s  %14s" % (w, "Total", "", "", "$" + _n(total)))
    return "\n".join(lines)


def _label(h, table):
    """An investor in two rounds shows as "Sequoia (A)" so the rows are telling apart."""
    same = [x for x in table.holdings if x.name == h.name]
    if len(same) > 1 and h.round_name:
        return "%s (%s)" % (h.name, h.round_name)
    return h.name


def _n(v):
    return "{:,.0f}".format(v)
