# capstack

Cap tables as code. Write who owns what in a plain-text file, commit it, and let `capstack` do the math: SAFE and note conversion, pool top-ups, priced rounds, dilution diffs between two versions of the file, and the exit waterfall.

Founders model their next round in a spreadsheet nobody else can read and lawyers rebuild it from scratch at closing. A `.cap` file is twenty lines, reads like the term sheet, and sits in git next to the code. `capstack diff` shows what a bigger round costs you the way a code diff shows what a commit changed.

Python 3.9 or later, standard library only. No dependencies.

## Install

```bash
pip install .
```

Or run it in place with `python3 -m capstack`.

## A company in twenty lines

```
company Acme Robotics

founder Ada 4,000,000
founder Ben 4,000,000
pool 1,000,000
grant Dana 200,000 strike 0.10

safe YC     125,000 post-money-cap 1,875,000
safe Angel  500,000 post-money-cap 8,000,000 discount 20%
safe Friend  50,000 discount 25%

note Uncle 100,000 interest 6% cap 10,000,000 discount 20% issued 2025-01-15

round Seed pre 12,000,000 raise 3,000,000 pool-to 10% pref 1x non-participating date 2026-03-01
  investor Sequoia    2,000,000
  investor Lightspeed 1,000,000

round A pre 40,000,000 raise 10,000,000 pool-to 12% pref 1x participating cap 3x date 2027-09-01
  investor a16z 7,000,000
  investor Sequoia 3,000,000
```

That file is [examples/acme.cap](examples/acme.cap). Numbers take commas and suffixes (`4,000,000`, `1.5M`, `250k`), percentages take `%`, comments start with `#`. Order matters: a SAFE or note converts at the first priced round written after it, and a grant comes out of the pool declared before it.

## The cap table after the seed

```
$ capstack table examples/acme.cap --at Seed

Acme Robotics, after Seed
$12,000,000 pre-money, $15,000,000 post-money, $1.0771 per share

Holder         Kind               Shares     Owns        Invested   Per share
-----------------------------------------------------------------------------
Ada            founder         4,000,000   28.72%
Ben            founder         4,000,000   28.72%
Unissued pool  pool            1,392,606   10.00%
Dana           option            200,000    1.44%
YC             safe              703,216    5.05%        $125,000     $0.1778
Angel          safe              659,265    4.73%        $500,000     $0.7584
Friend         safe               61,893    0.44%         $50,000     $0.8078
Uncle          note              123,872    0.89%        $106,740     $0.8617
Sequoia        preferred       1,856,808   13.33%      $2,000,000     $1.0771
Lightspeed     preferred         928,404    6.67%      $1,000,000     $1.0771
-----------------------------------------------------------------------------
Total                         13,926,064  100.00%      $3,781,740
```

`capstack rounds` prints every stage in turn, founding first, with the conversion math under each one:

```
  Seed: $12,000,000 pre-money, $3,000,000 raised, $1.0771 per share, 2,785,212 new shares
  Uncle: $100,000 principal plus 410 days of interest at 6% = $106,740
  Pool topped up by 592,606 shares to 10% of the post-money
  YC converts $125,000 at $0.1778 per share (post-money cap), 703,216 shares
  Angel converts $500,000 at $0.7584 per share (post-money cap), 659,265 shares
  Friend converts $50,000 at $0.8078 per share (discount), 61,893 shares
  Uncle converts $106,740 at $0.8617 per share (discount), 123,872 shares
```

## What a bigger round costs

Copy the file, change the seed to $5M with a 15% pool, and diff:

```
$ capstack diff examples/acme.cap examples/acme-bigger-seed.cap --at Seed

  Holder           Before     After      Change
-----------------------------------------------
- Ada              28.72%    22.75%     -5.97 pp
- Ben              28.72%    22.75%     -5.97 pp
+ Unissued pool    10.00%    15.00%     +5.00 pp
- Dana              1.44%     1.14%     -0.30 pp
- YC                5.05%     4.01%     -1.04 pp
- Angel             4.73%     3.76%     -0.98 pp
- Friend            0.44%     0.39%     -0.05 pp
- Uncle             0.89%     0.78%     -0.10 pp
+ Sequoia          13.33%    17.65%     +4.31 pp
+ Lightspeed        6.67%    11.76%     +5.10 pp

Price per share: $1.0771 -> $0.9670    Post-money: $15,000,000 -> $17,000,000
```

Two extra million and five more points of pool cost each founder six points. The SAFE holders lose a point too, because the pool top-up comes out of the pre-money.

## Who gets what at a sale

```
$ capstack exit examples/acme.cap --at 60M

Acme Robotics sells for $60,000,000 (table after A)

Holder                Invested     Owns          Payout  Multiple  How
----------------------------------------------------------------------
a16z                $7,000,000   14.00%     $14,954,543      2.1x  1x preference, participating (capped 3x)
Ada                              21.70%     $12,330,633            common
Ben                              21.70%     $12,330,633            common
Sequoia (A)         $3,000,000    6.00%      $6,409,090      2.1x  1x preference, participating (capped 3x)
Sequoia (Seed)      $2,000,000   10.07%      $5,723,904      2.9x  converted to common
Lightspeed          $1,000,000    5.04%      $2,861,952      2.9x  converted to common
YC                    $125,000    3.82%      $2,167,775     17.3x  converted to common
Angel                 $500,000    3.58%      $2,032,289      4.1x  converted to common
Dana                              1.09%        $616,532            common
Uncle                 $106,740    0.67%        $381,855      3.6x  converted to common
Friend                 $50,000    0.34%        $190,795      3.8x  converted to common
----------------------------------------------------------------------
Total                                       $60,000,000
```

Pass `--at` more than once to see several prices. Non-participating preferred take the larger of their preference and their share as common, and the engine re-decides every holder's choice until nobody wants to switch. Participating preferred take the preference and then their pro rata, up to the cap. Later rounds are paid first unless a round is marked `pari-passu`.

## Commands

| Command | What it does |
|---|---|
| `capstack table FILE [--at STAGE] [--json]` | The cap table after the last round, or after `Founding` or a named round |
| `capstack rounds FILE [--json]` | Every stage with the conversion math |
| `capstack diff BEFORE AFTER [--at STAGE]` | Ownership change per holder in percentage points |
| `capstack exit FILE --at VALUE [--at VALUE ...] [--stage S] [--json]` | The waterfall at one or more exit values |
| `capstack check FILE` | Parse the file and report problems with the line number |
| `capstack example` | Print a starter file |

## The file

| Line | Fields |
|---|---|
| `company NAME` | |
| `founder NAME SHARES` | |
| `pool SHARES` | Unissued option pool. Repeat to add to it. |
| `grant NAME SHARES [strike PRICE]` | Options out of the pool |
| `safe NAME AMOUNT [post-money-cap V] [pre-money-cap V] [discount P] [mfn]` | No cap and no discount means an uncapped SAFE that converts at the round price |
| `note NAME PRINCIPAL [interest P] [cap V] [discount P] [issued DATE]` | `cap` is a pre-money cap. Interest is simple, actual/365, from `issued` to the round `date` |
| `round NAME pre V raise V [pool-to P] [pref Nx] [participating\|non-participating] [cap Nx] [date DATE] [pari-passu]` | `pool-to` is the unissued pool after closing, as a share of the post-money |
| `  investor NAME AMOUNT` | Indented under a round. Money not named goes to `ROUND investors` |

## The math, and where it comes from

- **Post-money SAFE** (the YC form since 2018). The holder owns `amount / cap` of the company capitalization immediately before the round. Capitalization counts every outstanding share, every converting SAFE and note, granted options and the pool as it stood, and excludes the new money and the pool increase made for the round. Several SAFEs at once form a system of equations; the engine solves the fixed point by iteration to one part in ten million. In the example, YC's $125,000 at a $1,875,000 cap is 6.67% of 10,548,246 shares: 703,216.
- **Pre-money SAFE and convertible note.** Cap price is `cap / pre-money capitalization` without the converting instruments. Discount price is the round price times `1 - discount`. The holder converts at the lower of the two.
- **Priced round.** Price is `pre-money / fully diluted shares before the new money`, where fully diluted includes every converted security and the pool top-up the round asks for. New shares are `raise / price`. Fractions are rounded down, as the closing set does.
- **Exit.** The unissued pool gets nothing. Options are paid as common and the strike is ignored, which overstates option holders slightly at low prices.

Every number in this README comes from running the commands shown on the files in `examples/`.

## Tests

```bash
python3 -m unittest discover -s tests
```

Twenty-one tests cover the number parser, single and multiple post-money SAFEs, discounts beating caps, note interest, pool top-ups that do not dilute the SAFE, pricing a round, and the waterfall with preferences, participation caps, seniority and an investor who appears in two rounds.

## Limits

One class of common stock. No vesting schedules, no pro rata rights, no warrants, no down-round anti-dilution, no MFN resolution (the flag parses and is ignored). Dates are only used for note interest. The engine models the documents; it is not legal advice, and the closing set wins.

## License

MIT.
