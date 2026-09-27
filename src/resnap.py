"""Compatibility entry point for the roof audit."""
from __future__ import annotations

import argparse

from .roof_audit import audit_roofs


def resnap(*, force: bool = False, limit: int | None = None) -> None:
    # Every previously stored `snapped_to_building` flag is untrusted, so the
    # new audit always checks every selected locality.
    audit_roofs(apply=True, max_groups=limit)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Accepted for compatibility; the full audit always runs")
    parser.add_argument("--limit", type=int, help="Maximum number of locality groups")
    args = parser.parse_args()
    resnap(force=args.force, limit=args.limit)


if __name__ == "__main__":
    main()
