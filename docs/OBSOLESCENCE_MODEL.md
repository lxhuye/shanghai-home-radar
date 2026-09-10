# Obsolescence Risk Model

## Purpose

Obsolescence Risk measures the chance that a home becomes a less competitive residential product.
It is a separate 0–100 risk score where higher is worse. It is not `100 - Future Score`.

A core-location old apartment can therefore retain a strong Future Score while also carrying
meaningful product risk.

## Components

| Component | Weight |
| --- | ---: |
| Building aging | 18 |
| Elevator disadvantage | 12 |
| Parking deficiency | 8 |
| Layout obsolescence | 10 |
| Property-management weakness | 8 |
| Community deterioration | 8 |
| Competing supply | 12 |
| Buyer-pool shrinkage | 10 |
| Total-price mismatch | 6 |
| Product replacement | 8 |

The model derives only what structured evidence supports. Building age uses the canonical
construction year and projects mechanical age five years forward. Elevator, parking, management,
maintenance, and layout conditions remain unknown when absent. Supply, buyer pool, and community
risks consume corresponding P5 factors.

For available components:

```text
Obsolescence Risk = Σ(component risk × weight) / Σ(available weight)
```

The result is withheld below the configured 55% coverage gate. It reports component scores,
contributions, coverage, and missing components.

## Warnings

The warning engine can emit:

```text
HIGH_OBSOLESCENCE_RISK
SUPPLY_SHOCK_RISK
PLANNING_DEPENDENCY
EMPLOYMENT_ACCESS_WEAKNESS
AGING_PRODUCT
LIQUIDITY_DECAY_RISK
LOW_RENTAL_SUPPORT
LOW_FORECAST_CONFIDENCE
UNCALIBRATED_MODEL
NON_LIVE_DATA
```

Warnings are deterministic and configured. They do not become investment recommendations.

## Validation behavior

The synthetic Value Trap case combines a low apparent current price with a walk-up, aging product,
weak buyer pool, and competing supply. It produces a low Future Score and high Obsolescence Risk.

The Aging Core Location case keeps strong employment and liquidity factors while retaining medium
or high obsolescence. This confirms that location strength does not erase product weakness.
