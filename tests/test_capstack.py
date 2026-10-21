import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capstack.cli import main, render_diff, render_table  # noqa: E402
from capstack.engine import build, waterfall  # noqa: E402
from capstack.model import CapError, parse, parse_number, parse_percent  # noqa: E402


def holdings(table):
    return {h.name: h for h in table.holdings}


class Numbers(unittest.TestCase):
    def test_numbers_as_people_write_them(self):
        self.assertEqual(parse_number("4,000,000"), 4000000)
        self.assertEqual(parse_number("1.5M"), 1500000)
        self.assertEqual(parse_number("250k"), 250000)
        self.assertEqual(parse_number("$2b"), 2e9)
        self.assertEqual(parse_number("0.10"), 0.10)
        self.assertAlmostEqual(parse_percent("20%"), 0.2)
        self.assertAlmostEqual(parse_percent("0.2"), 0.2)
        self.assertAlmostEqual(parse_percent("20"), 0.2)

    def test_bad_lines_name_the_line(self):
        with self.assertRaises(CapError) as cm:
            parse("founder Ada 4,000,000\nsafe YC 125,000 postcap 1M\n")
        self.assertIn("line 2", str(cm.exception))


class PostMoneySafe(unittest.TestCase):
    """The YC post-money SAFE: the holder owns amount / cap of the company before the round."""

    def test_one_safe_owns_amount_over_cap(self):
        company = parse("""
            founder Ada 10,000,000
            safe YC 100,000 post-money-cap 1,000,000
            round Seed pre 9,000,000 raise 1,000,000
        """)
        t = build(company)[-1]
        h = holdings(t)
        # 10% of the pre-round capitalization: 10,000,000 / 0.9 - 10,000,000 = 1,111,111
        self.assertEqual(h["YC"].shares, 1111111)
        pre_round = h["Ada"].shares + h["YC"].shares
        self.assertAlmostEqual(h["YC"].shares / pre_round, 0.10, places=5)

    def test_two_safes_add_up(self):
        company = parse("""
            founder Ada 8,000,000
            safe A 100,000 post-money-cap 1,000,000
            safe B 500,000 post-money-cap 10,000,000
            round Seed pre 10,000,000 raise 2,000,000
        """)
        t = build(company)[-1]
        h = holdings(t)
        pre_round = sum(x.shares for x in t.holdings if x.kind != "preferred")
        self.assertAlmostEqual(h["A"].shares / pre_round, 0.10, places=4)
        self.assertAlmostEqual(h["B"].shares / pre_round, 0.05, places=4)

    def test_pool_top_up_does_not_dilute_the_safe(self):
        company = parse("""
            founder Ada 9,000,000
            pool 1,000,000
            safe YC 125,000 post-money-cap 1,875,000
            round Seed pre 12,000,000 raise 3,000,000 pool-to 10%
        """)
        t = build(company)[-1]
        h = holdings(t)
        topup = h["Unissued pool"].shares - 1000000
        new_money = sum(x.shares for x in t.holdings if x.kind == "preferred")
        capitalization = t.total() - topup - new_money
        self.assertAlmostEqual(h["YC"].shares / capitalization, 125000 / 1875000, places=4)
        self.assertAlmostEqual(h["Unissued pool"].shares / t.total(), 0.10, places=4)

    def test_discount_wins_when_it_is_the_lower_price(self):
        company = parse("""
            founder Ada 10,000,000
            safe Angel 100,000 post-money-cap 100,000,000 discount 20%
            round Seed pre 10,000,000 raise 1,000,000
        """)
        t = build(company)[-1]
        price = t.price
        self.assertAlmostEqual(holdings(t)["Angel"].price, price * 0.8, places=4)
        self.assertTrue(any("discount" in n for n in t.notes))


class ConvertibleNote(unittest.TestCase):
    def test_interest_accrues_to_the_round_date(self):
        company = parse("""
            founder Ada 10,000,000
            note Uncle 100,000 interest 10% discount 20% issued 2025-01-01
            round Seed pre 10,000,000 raise 1,000,000 date 2026-01-01
        """)
        t = build(company)[-1]
        # 365 days at 10% simple interest: $110,000 converts at an 80% price
        self.assertAlmostEqual(holdings(t)["Uncle"].invested, 110000, places=2)
        self.assertAlmostEqual(holdings(t)["Uncle"].price, t.price * 0.8, places=4)

    def test_pre_money_cap_beats_discount_when_lower(self):
        company = parse("""
            founder Ada 10,000,000
            note Uncle 100,000 cap 5,000,000 discount 20%
            round Seed pre 20,000,000 raise 5,000,000
        """)
        t = build(company)[-1]
        # cap price = 5,000,000 / 10,000,000 = $0.50; round price = $2.00 before the note dilutes it
        self.assertAlmostEqual(holdings(t)["Uncle"].price, 0.5, places=4)


class PricedRound(unittest.TestCase):
    def test_price_and_new_shares(self):
        company = parse("""
            founder Ada 10,000,000
            round Seed pre 10,000,000 raise 2,500,000
              investor Fund 2,500,000
        """)
        t = build(company)[-1]
        self.assertAlmostEqual(t.price, 1.0)
        self.assertEqual(holdings(t)["Fund"].shares, 2500000)
        self.assertAlmostEqual(t.percent(holdings(t)["Fund"]), 0.2)
        self.assertEqual(t.post, 12500000)

    def test_unnamed_money_becomes_round_investors(self):
        company = parse("""
            founder Ada 10,000,000
            round Seed pre 10,000,000 raise 2,000,000
              investor Lead 1,500,000
        """)
        t = build(company)[-1]
        self.assertEqual(holdings(t)["Seed investors"].invested, 500000)

    def test_investors_cannot_exceed_the_raise(self):
        with self.assertRaises(CapError):
            parse("""
                founder Ada 10,000,000
                round Seed pre 10,000,000 raise 1,000,000
                  investor Lead 2,000,000
            """)

    def test_grant_comes_out_of_the_pool(self):
        company = parse("""
            founder Ada 9,000,000
            pool 1,000,000
            grant Dana 250,000 strike 0.10
        """)
        t = build(company)[-1]
        self.assertEqual(holdings(t)["Unissued pool"].shares, 750000)
        self.assertEqual(holdings(t)["Dana"].kind, "option")
        self.assertEqual(t.stage, "Founding")

    def test_stages_in_order(self):
        company = parse("""
            founder Ada 10,000,000
            safe S 100,000 post-money-cap 1,000,000
            round Seed pre 9,000,000 raise 1,000,000
            round A pre 30,000,000 raise 10,000,000
        """)
        self.assertEqual([t.stage for t in build(company)], ["Founding", "Seed", "A"])


class Waterfall(unittest.TestCase):
    def table(self, text):
        return build(parse(text))[-1]

    def test_non_participating_takes_preference_at_a_low_price(self):
        t = self.table("""
            founder Ada 8,000,000
            round Seed pre 8,000,000 raise 2,000,000 pref 1x non-participating
              investor Fund 2,000,000
        """)
        rows = {h.name: (p, how) for h, p, how in waterfall(t, 5000000)}
        # Fund owns 20%. As common: $1.0M. Preference: $2.0M. It takes the preference.
        self.assertAlmostEqual(rows["Fund"][0], 2000000)
        self.assertAlmostEqual(rows["Ada"][0], 3000000)
        self.assertEqual(rows["Fund"][1], "1x preference")

    def test_non_participating_converts_at_a_high_price(self):
        t = self.table("""
            founder Ada 8,000,000
            round Seed pre 8,000,000 raise 2,000,000 pref 1x non-participating
              investor Fund 2,000,000
        """)
        rows = {h.name: (p, how) for h, p, how in waterfall(t, 50000000)}
        self.assertAlmostEqual(rows["Fund"][0], 10000000)
        self.assertEqual(rows["Fund"][1], "converted to common")

    def test_participating_with_a_cap(self):
        t = self.table("""
            founder Ada 8,000,000
            round Seed pre 8,000,000 raise 2,000,000 pref 1x participating cap 3x
              investor Fund 2,000,000
        """)
        rows = {h.name: p for h, p, _ in waterfall(t, 20000000)}
        # preference $2M, then 20% of the remaining $18M = $3.6M: $5.6M, under the $6M cap
        self.assertAlmostEqual(rows["Fund"], 5600000)
        rows = {h.name: p for h, p, _ in waterfall(t, 100000000)}
        # 20% of $100M would be $20M; the cap holds it at 3x = $6M
        self.assertAlmostEqual(rows["Fund"], 6000000)
        self.assertAlmostEqual(rows["Ada"], 94000000)

    def test_later_round_is_senior_and_everything_adds_up(self):
        t = self.table("""
            founder Ada 8,000,000
            round Seed pre 8,000,000 raise 2,000,000 pref 1x non-participating
              investor Early 2,000,000
            round A pre 20,000,000 raise 10,000,000 pref 1x non-participating
              investor Late 10,000,000
        """)
        rows = {h.name: p for h, p, _ in waterfall(t, 11000000)}
        self.assertAlmostEqual(rows["Late"], 10000000)
        self.assertAlmostEqual(rows["Early"], 1000000)
        self.assertAlmostEqual(rows["Ada"], 0)
        self.assertAlmostEqual(sum(rows.values()), 11000000)

    def test_same_investor_in_two_rounds_is_paid_twice(self):
        t = self.table("""
            founder Ada 8,000,000
            round Seed pre 8,000,000 raise 2,000,000 pref 1x non-participating
              investor Fund 2,000,000
            round A pre 20,000,000 raise 10,000,000 pref 1x non-participating
              investor Fund 10,000,000
        """)
        rows = waterfall(t, 12000000)
        self.assertAlmostEqual(sum(p for _, p, _ in rows), 12000000)
        self.assertEqual(len([r for r in rows if r[0].name == "Fund"]), 2)


class Cli(unittest.TestCase):
    def test_example_round_trips_and_renders(self):
        here = os.path.dirname(__file__)
        path = os.path.join(here, "..", "examples", "acme.cap")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["check", path]), 0)
            self.assertEqual(main(["table", path, "--at", "Seed"]), 0)
            self.assertEqual(main(["exit", path, "--at", "50M", "--json"]), 0)
        self.assertIn("Acme Robotics", out.getvalue())
        self.assertIn('"exit_value": 50000000.0', out.getvalue())

    def test_diff_shows_dilution(self):
        a = build(parse("founder Ada 10,000,000\n"))[-1]
        b = build(parse("founder Ada 10,000,000\nround Seed pre 10,000,000 raise 2,500,000\n"))[-1]
        out = render_diff("a.cap", "b.cap", a, b)
        self.assertIn("Ada", out)
        self.assertIn("-20.00 pp", out)
        self.assertIn("Seed investors", out)

    def test_table_renders_percentages_that_sum(self):
        t = build(parse("founder Ada 6,000,000\nfounder Ben 4,000,000\n"))[-1]
        out = render_table("X", t)
        self.assertIn("60.00%", out)
        self.assertIn("40.00%", out)


if __name__ == "__main__":
    unittest.main()
