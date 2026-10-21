"""Turn a parsed .cap file into cap tables, one after every event, and run an exit waterfall.

Conversion math follows the documents people actually sign:

- Post-money SAFE (YC 2018 form). The holder gets amount / post_cap of the company
  capitalization immediately before the priced round. Capitalization counts every outstanding
  share, every converting SAFE and note, granted options and the unissued pool as it stood
  before the round, and excludes the new money and any pool increase made for the round.
  Several SAFEs at once make a system of equations; the fixed point is solved by iteration.
- Pre-money SAFE and convertible note. Cap price = cap / pre-money capitalization excluding the
  converting instruments themselves. Discount price = round price x (1 - discount). The holder
  converts at the lower price. Notes convert principal plus simple interest, actual/365.
- Priced round. Price = pre-money valuation / fully diluted shares before the new money, where
  fully diluted includes every converted security and the pool top-up the round requires.
  New shares = raise / price. Fractional shares are rounded down, as closing documents do.
"""
import math

from .model import CapError


class Holding:
    """One line of a cap table."""

    def __init__(self, name, kind, shares, invested=0.0, price=None, round_name=None):
        self.name = name
        self.kind = kind          # founder, option, pool, safe, note, preferred
        self.shares = shares
        self.invested = invested
        self.price = price        # price per share paid, when there is one
        self.round_name = round_name
        self.pref = 0.0           # liquidation preference multiple (preferred only)
        self.participating = False
        self.participation_cap = None
        self.seniority = 0        # higher pays out first
        self.strike = None        # options


class Table:
    """A cap table at one moment, with the shares each holder owns."""

    def __init__(self, stage, holdings, price=None, pre=None, post=None, notes=None):
        self.stage = stage
        self.holdings = holdings
        self.price = price
        self.pre = pre
        self.post = post
        self.notes = notes or []

    def total(self):
        return sum(h.shares for h in self.holdings)

    def percent(self, h):
        t = self.total()
        return h.shares / t if t else 0.0

    def to_dict(self):
        t = self.total()
        return {
            "stage": self.stage,
            "price_per_share": self.price,
            "pre_money": self.pre,
            "post_money": self.post,
            "total_shares": t,
            "holdings": [
                {"name": h.name, "kind": h.kind, "shares": h.shares,
                 "percent": (h.shares / t if t else 0.0), "invested": h.invested,
                 "price": h.price, "round": h.round_name}
                for h in self.holdings
            ],
            "notes": list(self.notes),
        }


def build(company):
    """Return the list of tables, one per stage: founding, then each priced round."""
    holdings = []
    pending = []   # SAFEs and notes waiting for the next priced round
    tables = []
    pool = None
    rank = 0
    founding_done = False
    for e in company.events:
        if e.kind == "founder":
            holdings.append(Holding(e.name, "founder", e.shares))
        elif e.kind == "pool":
            if pool is None:
                pool = Holding("Unissued pool", "pool", e.shares)
                holdings.append(pool)
            else:
                pool.shares += e.shares
        elif e.kind == "grant":
            if pool is None or pool.shares < e.shares:
                raise CapError("grant %s: the pool has %s shares, the grant needs %s"
                               % (e.name, _n(pool.shares if pool else 0), _n(e.shares)))
            pool.shares -= e.shares
            g = Holding(e.name, "option", e.shares)
            g.strike = e.strike
            holdings.append(g)
        elif e.kind in ("safe", "note"):
            pending.append(e)
        elif e.kind == "round":
            if not founding_done:
                tables.append(_founding_or_unconverted(holdings, pending, []))
                founding_done = True
            rank += 1
            if pool is None and e.pool_to:
                pool = Holding("Unissued pool", "pool", 0.0)
                holdings.append(pool)
            table = _close_round(company, e, holdings, pending, pool, rank)
            tables.append(table)
            pending = []
    if not founding_done:
        tables.append(_founding_or_unconverted(holdings, pending, []))
    elif pending:
        tables.append(_founding_or_unconverted(holdings, pending, tables))
    return tables


def _last_round_index(company):
    idx = -1
    for i, e in enumerate(company.events):
        if e.kind == "round":
            idx = i
    return idx


def _founding_or_unconverted(holdings, pending, tables):
    """A table for a company with no priced round yet (or SAFEs after the last one):
    shares as they stand, plus the unconverted instruments listed with their money in."""
    hs = [_copy(h) for h in holdings]
    notes = []
    for p in pending:
        if p.kind == "safe":
            cap = p.post_cap or p.pre_cap
            notes.append("%s: $%s on a SAFE%s, converts at the next priced round"
                         % (p.name, _n(p.amount), (" at a $%s cap" % _n(cap)) if cap else ""))
        else:
            notes.append("%s: $%s note at %s%% interest, converts at the next priced round"
                         % (p.name, _n(p.principal), _pct(p.interest)))
    stage = "Founding" if not tables else "After " + tables[-1].stage
    return Table(stage, hs, notes=notes)


def _copy(h):
    c = Holding(h.name, h.kind, h.shares, h.invested, h.price, h.round_name)
    c.pref, c.participating, c.participation_cap, c.seniority, c.strike = (
        h.pref, h.participating, h.participation_cap, h.seniority, h.strike)
    return c


def _close_round(company, rnd, holdings, pending, pool, rank):
    """Convert pending SAFEs and notes, top up the pool, price the round, issue new shares."""
    existing = sum(h.shares for h in holdings)           # founders, options, pool as they stand
    unissued_before = pool.shares if pool else 0.0
    notes = []

    # Money each converting instrument brings, with note interest accrued to the round date.
    amounts = {}
    for p in pending:
        if p.kind == "safe":
            amounts[p.name] = p.amount
        else:
            days = 0
            if p.issued and rnd.date:
                days = max(0, (rnd.date - p.issued).days)
            elif p.issued or rnd.date:
                notes.append("%s: interest not accrued, both the note and the round need a date" % p.name)
            amounts[p.name] = p.principal * (1.0 + p.interest * days / 365.0)
            if days:
                notes.append("%s: $%s principal plus %d days of interest at %s%% = $%s"
                             % (p.name, _n(p.principal), days, _pct(p.interest), _n(amounts[p.name])))

    # Fixed-point iteration. Unknowns: converted shares per instrument, pool top-up, price.
    conv = {p.name: 0.0 for p in pending}
    topup = 0.0
    price = None
    prices = {}
    for _ in range(500):
        converted_total = sum(conv.values())
        pre_fd = existing + converted_total + topup
        price = rnd.pre / pre_fd if pre_fd else float("nan")
        new_shares = rnd.raise_amount / price if price else 0.0
        post_fd = pre_fd + new_shares
        new_topup = 0.0
        if rnd.pool_to is not None:
            # The round wants unissued_before + topup = pool_to * post_fd. Only ever a top-up.
            new_topup = max(0.0, rnd.pool_to * post_fd - unissued_before)
        new_conv = {}
        for p in pending:
            amount = amounts[p.name]
            candidates = []
            if p.kind == "safe" and p.post_cap:
                # Company capitalization excludes the new money and the pool increase.
                capitalization = existing + converted_total
                candidates.append(("post-money cap", p.post_cap / capitalization))
            if p.kind == "safe" and p.pre_cap:
                candidates.append(("pre-money cap", p.pre_cap / (existing + topup)))
            if p.kind == "note" and p.cap:
                candidates.append(("cap", p.cap / (existing + topup)))
            if p.discount:
                candidates.append(("discount", price * (1.0 - p.discount)))
            if not candidates:
                candidates.append(("round price", price))
            basis, unit = min(candidates, key=lambda c: c[1])
            prices[p.name] = (basis, unit)
            new_conv[p.name] = amount / unit if unit else 0.0
        delta = abs(new_topup - topup) + sum(abs(new_conv[k] - conv[k]) for k in conv)
        conv, topup = new_conv, new_topup
        if delta < 1e-7:
            break

    # Round down to whole shares, as the closing set does.
    for k in conv:
        conv[k] = math.floor(conv[k])
    topup = math.floor(topup)
    pre_fd = existing + sum(conv.values()) + topup
    price = rnd.pre / pre_fd
    new_shares = math.floor(rnd.raise_amount / price)

    # Mutate the running holdings: pool top-up, converted holders, new investors.
    if pool is not None and topup:
        pool.shares += topup
        notes.append("Pool topped up by %s shares to %s%% of the post-money"
                     % (_n(topup), _pct(rnd.pool_to)))
    for p in pending:
        h = Holding(p.name, p.kind, conv[p.name], amounts[p.name], amounts[p.name] / conv[p.name] if conv[p.name] else None, rnd.name)
        h.pref, h.participating, h.participation_cap, h.seniority = rnd.pref, rnd.participating, rnd.participation_cap, rank
        basis, unit = prices[p.name]
        notes.append("%s converts $%s at $%.4f per share (%s), %s shares"
                     % (p.name, _n(amounts[p.name]), unit, basis, _n(conv[p.name])))
        holdings.append(h)
    investors = list(rnd.investors)
    placed = sum(a for _, a in investors)
    if rnd.raise_amount - placed > 0.5:
        investors.append(("%s investors" % rnd.name, rnd.raise_amount - placed))
    issued = 0
    for name, amount in investors:
        shares = math.floor(amount / price)
        issued += shares
        h = Holding(name, "preferred", shares, amount, price, rnd.name)
        h.pref, h.participating, h.participation_cap, h.seniority = rnd.pref, rnd.participating, rnd.participation_cap, rank
        holdings.append(h)
    notes.insert(0, "%s: $%s pre-money, $%s raised, $%.4f per share, %s new shares"
                 % (rnd.name, _n(rnd.pre), _n(rnd.raise_amount), price, _n(issued)))
    return Table(rnd.name, [_copy(h) for h in holdings], price=price, pre=rnd.pre, post=rnd.pre + rnd.raise_amount, notes=notes)


# ---------- exit waterfall ----------

def waterfall(table, exit_value):
    """Distribute exit_value across the holders of a table.

    Preferred holders are paid their preference in seniority order (later rounds first unless
    pari-passu). Non-participating preferred take the larger of the preference and converting to
    common. Participating preferred take the preference and then share pro rata, up to the cap.
    The unissued pool gets nothing. Options are treated as common and the strike is ignored.
    Returns a list of (holding, payout, how)."""
    holders = [h for h in table.holdings if h.kind != "pool" and h.shares > 0]
    remaining = float(exit_value)
    preferred = [h for h in holders if h.kind in ("preferred", "safe", "note") and h.pref]

    # Decide which non-participating preferred convert. Iterate: a conversion changes the
    # common pool size, which can change another holder's decision.
    converting = set()
    for _ in range(20):
        changed = False
        for h in preferred:
            if h.participating:
                continue
            as_pref = _pay_preferences(holders, preferred, converting, remaining)[0].get(id(h), 0.0)
            as_common = _pay_preferences(holders, preferred, converting | {id(h)}, remaining)[1].get(id(h), 0.0)
            want = as_common > as_pref + 1e-6
            if want and id(h) not in converting:
                converting.add(id(h))
                changed = True
            elif not want and id(h) in converting:
                converting.discard(id(h))
                changed = True
        if not changed:
            break

    pref_pay, common_pay = _pay_preferences(holders, preferred, converting, remaining)
    rows = []
    for h in holders:
        p = pref_pay.get(id(h), 0.0) + common_pay.get(id(h), 0.0)
        if id(h) in converting:
            label = "converted to common"
        elif h.pref and h.participating:
            label = "%gx preference, participating" % h.pref + (" (capped %gx)" % h.participation_cap if h.participation_cap else "")
        elif h.pref:
            label = "%gx preference" % h.pref
        else:
            label = "common"
        rows.append((h, p, label))
    return rows


def _pay_preferences(holders, preferred, converting, total):
    """Return (preference payouts, common payouts) given which preferred convert."""
    remaining = total
    pref_pay = {}
    # Seniority: higher rank first; pari-passu rounds share a rank with the one before them.
    ranks = sorted({h.seniority for h in preferred if id(h) not in converting}, reverse=True)
    for r in ranks:
        group = [h for h in preferred if h.seniority == r and id(h) not in converting]
        owed = {id(h): h.invested * h.pref for h in group}
        total_owed = sum(owed.values())
        pay = min(remaining, total_owed)
        for h in group:
            pref_pay[id(h)] = pay * (owed[id(h)] / total_owed) if total_owed else 0.0
        remaining -= pay
    # Common: everyone who did not take a preference, plus participating preferred, pro rata.
    participants = [h for h in holders if id(h) not in pref_pay or h.participating]
    common_pay = {}
    capped = set()
    for _ in range(10):
        shares = sum(h.shares for h in participants if id(h) not in capped)
        pool = remaining - sum(common_pay[k] for k in capped)
        changed = False
        for h in participants:
            if id(h) in capped:
                continue
            common_pay[id(h)] = pool * h.shares / shares if shares else 0.0
            if h.participating and h.participation_cap:
                cap_total = h.invested * h.participation_cap
                if pref_pay.get(id(h), 0.0) + common_pay[id(h)] > cap_total:
                    common_pay[id(h)] = max(0.0, cap_total - pref_pay.get(id(h), 0.0))
                    capped.add(id(h))
                    changed = True
        if not changed:
            break
    return pref_pay, common_pay


def _n(v):
    return "{:,.0f}".format(v)


def _pct(v):
    return ("%.2f" % (v * 100)).rstrip("0").rstrip(".")
