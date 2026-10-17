#!/usr/bin/env python3
"""CLI status for MMX 200 battery tray."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend import EstimateStore, Mmx200Backend  # noqa: E402

CONFIG = os.path.expanduser("~/.config/mmx200-battery")


def main() -> None:
    hw = Mmx200Backend().read()
    est = EstimateStore(os.path.join(CONFIG, "estimate"), 40.0)
    print(
        json.dumps(
            {**hw.__dict__, "estimate": int(round(est.percentage))},
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
