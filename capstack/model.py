"""The .cap file: a plain-text description of who owns a company and what they paid.

One instrument per line. Order matters: SAFEs and notes convert at the first priced round
written after them, grants come out of the pool declared before them. Comments start with #.

    company Acme Robotics
    founder Ada 4,000,000
    founder Ben 4,000,000
    pool 1,000,000
    safe YC 125,000 post-money-cap 1,875,000
    safe Angel 500,000 post-money-cap 8,000,000 discount 20%
    note Uncle 100,000 interest 6% cap 10,000,000 discount 20% issued 2025-01-15
    grant Dana 200,000 strike 0.10
    round Seed pre 12,000,000 raise 3,000,000 pool-to 10% pref 1x non-participating date 2026-03-01
      investor Sequoia 2,000,000
      investor Lightspeed 1,000,000
"""
import datetime
import re


class CapError(Exception):
    """A problem in a .cap file, with the line it came from."""

    def __init__(self, message, line_no=None, line=None):
        where = "line %d: " % line_no if line_no else ""
        super().__init__(where + message)
        self.line_no = line_no
        self.line = line


# ---------- numbers as people write them: 4,000,000  1.5M  250k  20%  0.10 ----------

_NUM = re.compile(r"^\$?(-?[0-9][0-9,]*(?:\.[0-9]+)?)([kKmMbB]?)$")


def parse_number(text):
    m = _NUM.match(text.strip())
    if not m:
        raise ValueError("not a number: %r" % text)
    value = float(m.group(1).replace(",", ""))
    suffix = m.group(2).lower()
    if suffix == "k":
        value *= 1e3
    elif suffix == "m":
        value *= 1e6
    elif suffix == "b":
        value *= 1e9
    return value


def parse_percent(text):
    t = text.strip()
    if t.endswith("%"):
        return parse_number(t[:-1]) / 100.0
    v = parse_number(t)
    # "20" means 20% in a percent slot; "0.2" means 20% too. Anything above 1 is a percentage.
    return v / 100.0 if v > 1 else v


def parse_multiple(text):
    t = text.strip().lower()
    if t.endswith("x"):
        t = t[:-1]
    return parse_number(t)


def parse_date(text):
    try:
        return datetime.date.fromisoformat(text.strip())
    except ValueError:
        raise ValueError("not a date (use YYYY-MM-DD): %r" % text)


# ---------- the instruments ----------

class Founder:
    kind = "founder"

    def __init__(self, name, shares):
        self.name = name
        self.shares = shares


class Pool:
    kind = "pool"

    def __init__(self, shares):
        self.shares = shares


class Grant:
    kind = "grant"

    def __init__(self, name, shares, strike=None):
        self.name = name
        self.shares = shares
        self.strike = strike


class Safe:
    """A SAFE. post_cap is a post-money valuation cap (the YC 2018 form), pre_cap the older form.
    discount is a fraction (0.2 for 20%). Either, both, or neither (an uncapped SAFE) may be set."""
    kind = "safe"

    def __init__(self, name, amount, post_cap=None, pre_cap=None, discount=None, mfn=False):
        self.name = name
        self.amount = amount
        self.post_cap = post_cap
        self.pre_cap = pre_cap
        self.discount = discount
        self.mfn = mfn


class Note:
    """A convertible note. Simple interest, actual/365, from issued to the round date.
    cap is a pre-money valuation cap. discount is a fraction."""
    kind = "note"

    def __init__(self, name, principal, interest=0.0, cap=None, discount=None, issued=None):
        self.name = name
        self.principal = principal
        self.interest = interest
        self.cap = cap
        self.discount = discount
        self.issued = issued


class Round:
    """A priced round. pre is the pre-money valuation. pool_to is the unissued pool the round
    requires after closing, as a fraction of the post-money fully diluted shares. The top-up
    comes out of the pre-money, so it dilutes existing holders and converting securities, not
    the new investors. pref is the liquidation preference multiple."""
    kind = "round"

    def __init__(self, name, pre, raise_amount, pool_to=None, pref=1.0, participating=False,
                 participation_cap=None, date=None, seniority="senior"):
        self.name = name
        self.pre = pre
        self.raise_amount = raise_amount
        self.pool_to = pool_to
        self.pref = pref
        self.participating = participating
        self.participation_cap = participation_cap
        self.date = date
        self.seniority = seniority  # "senior": ahead of earlier rounds. "pari-passu": alongside them.
        self.investors = []  # (name, amount)


class Company:
    def __init__(self):
        self.name = "Company"
        self.events = []  # founders, pool, grants, safes, notes, rounds, in file order


# ---------- the parser ----------

def _kv(tokens, allowed, line_no, line):
    """Parse trailing "key value" pairs and bare flags out of a token list."""
    out = {}
    i = 0
    while i < len(tokens):
        key = tokens[i].lower()
        spec = allowed.get(key)
        if spec is None:
            raise CapError("unknown word %r" % tokens[i], line_no, line)
        if spec == "flag":
            out[key] = True
            i += 1
            continue
        if i + 1 >= len(tokens):
            raise CapError("%s needs a value" % key, line_no, line)
        try:
            out[key] = spec(tokens[i + 1])
        except ValueError as e:
            raise CapError(str(e), line_no, line)
        i += 2
    return out


def parse(text):
    company = Company()
    current_round = None
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indented = line[0] in " \t"
        tokens = line.split()
        word = tokens[0].lower()
        try:
            if word == "company":
                company.name = " ".join(tokens[1:]) or company.name
            elif word == "founder":
                _need(tokens, 3, line_no, line, "founder NAME SHARES")
                company.events.append(Founder(tokens[1], parse_number(tokens[2])))
            elif word == "pool":
                _need(tokens, 2, line_no, line, "pool SHARES")
                company.events.append(Pool(parse_number(tokens[1])))
            elif word == "grant":
                _need(tokens, 3, line_no, line, "grant NAME SHARES [strike PRICE]")
                opts = _kv(tokens[3:], {"strike": parse_number}, line_no, line)
                company.events.append(Grant(tokens[1], parse_number(tokens[2]), opts.get("strike")))
            elif word == "safe":
                _need(tokens, 3, line_no, line, "safe NAME AMOUNT [post-money-cap V] [pre-money-cap V] [discount P] [mfn]")
                opts = _kv(tokens[3:], {"post-money-cap": parse_number, "pre-money-cap": parse_number,
                                        "cap": parse_number, "discount": parse_percent, "mfn": "flag"},
                           line_no, line)
                post_cap = opts.get("post-money-cap", opts.get("cap"))
                company.events.append(Safe(tokens[1], parse_number(tokens[2]), post_cap=post_cap,
                                           pre_cap=opts.get("pre-money-cap"), discount=opts.get("discount"),
                                           mfn=bool(opts.get("mfn"))))
            elif word == "note":
                _need(tokens, 3, line_no, line, "note NAME PRINCIPAL [interest P] [cap V] [discount P] [issued DATE]")
                opts = _kv(tokens[3:], {"interest": parse_percent, "cap": parse_number,
                                        "discount": parse_percent, "issued": parse_date}, line_no, line)
                company.events.append(Note(tokens[1], parse_number(tokens[2]), interest=opts.get("interest", 0.0),
                                           cap=opts.get("cap"), discount=opts.get("discount"),
                                           issued=opts.get("issued")))
            elif word == "round":
                _need(tokens, 2, line_no, line, "round NAME pre V raise V [pool-to P] [pref Nx] [participating|non-participating] [cap Nx] [date D] [pari-passu]")
                opts = _kv(tokens[2:], {"pre": parse_number, "raise": parse_number, "pool-to": parse_percent,
                                        "pref": parse_multiple, "participating": "flag",
                                        "non-participating": "flag", "cap": parse_multiple,
                                        "date": parse_date, "pari-passu": "flag"}, line_no, line)
                if "pre" not in opts or "raise" not in opts:
                    raise CapError("a round needs pre and raise", line_no, line)
                current_round = Round(tokens[1], opts["pre"], opts["raise"], pool_to=opts.get("pool-to"),
                                      pref=opts.get("pref", 1.0), participating=bool(opts.get("participating")),
                                      participation_cap=opts.get("cap"), date=opts.get("date"),
                                      seniority="pari-passu" if opts.get("pari-passu") else "senior")
                company.events.append(current_round)
            elif word == "investor":
                if current_round is None or not indented:
                    raise CapError("investor lines go indented under a round", line_no, line)
                _need(tokens, 3, line_no, line, "  investor NAME AMOUNT")
                current_round.investors.append((tokens[1], parse_number(tokens[2])))
            else:
                raise CapError("unknown line type %r" % tokens[0], line_no, line)
        except ValueError as e:
            raise CapError(str(e), line_no, line)
        if word not in ("investor", "round"):
            current_round = None
    _check(company)
    return company


def _need(tokens, n, line_no, line, usage):
    if len(tokens) < n:
        raise CapError("expected: " + usage, line_no, line)


def _check(company):
    names = set()
    for e in company.events:
        name = getattr(e, "name", None)
        if name is not None and e.kind in ("founder", "safe", "note", "grant") and name in names:
            raise CapError("%s appears twice" % name)
        if name is not None and e.kind in ("founder", "safe", "note", "grant"):
            names.add(name)
    for e in company.events:
        if e.kind == "round":
            total = sum(a for _, a in e.investors)
            if e.investors and total > e.raise_amount * 1.000001:
                raise CapError("round %s: investors put in %s but raise is %s"
                               % (e.name, _fmt(total), _fmt(e.raise_amount)))
            if e.pool_to is not None and not 0 <= e.pool_to < 1:
                raise CapError("round %s: pool-to must be between 0% and 100%" % e.name)
        if e.kind == "safe" and e.discount is not None and not 0 <= e.discount < 1:
            raise CapError("safe %s: discount must be between 0%% and 100%%" % e.name)
        if e.kind == "note" and e.discount is not None and not 0 <= e.discount < 1:
            raise CapError("note %s: discount must be between 0%% and 100%%" % e.name)


def _fmt(v):
    return "{:,.0f}".format(v)
