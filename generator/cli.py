"""CLI: python -m generator build --seed 42 --tier golden --out test_data"""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import GenConfig
from .pipeline import build, write


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="generator", description="HFB synthetic test-data generator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="generate and write a dataset")
    b.add_argument("--seed", type=int, default=42)
    b.add_argument("--tier", choices=["golden", "volume", "all"], default="all")
    b.add_argument("--customers", type=int, default=None, help="override number of customers (volume tier)")
    b.add_argument("--out", type=Path, default=Path("test_data"))

    args = parser.parse_args(argv)

    if args.cmd == "build":
        cfg = GenConfig(seed=args.seed)
        if args.customers is not None:
            cfg.n_customers = args.customers
        ds = build(cfg, include_volume=args.tier in ("all", "volume"))
        paths = write(ds, args.out, tier=args.tier)
        print(f"seed={args.seed} tier={args.tier}")
        print(f"  world: {len(ds.locations)} locations x {len(ds.vehicle_classes)} classes, "
              f"{len(ds.rate_cards)} rate-cards")
        print(f"  catalogues: {len(ds.protection_products)} protection, {len(ds.extras)} extras, "
              f"{len(ds.policies)} policies, {len(ds.rate_plans)} rate-plans")
        print(f"  golden scenarios: {len(ds.scenarios)}")
        if args.tier in ("all", "volume"):
            print(f"  volume: {len(ds.customers)} customers, {len(ds.companies)} companies, "
                  f"{len(ds.bookings)} bookings, {len(ds.invoices)} invoices, {len(ds.events)} events")
        print(f"  wrote {len(paths)} files under {args.out}/")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
