"""新样本数据 → step1/step2 原始契约的最小适配器壳。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

HOLDING_REQUIRED_COLUMNS = (
    "idxs",
    "signal_date",
    "holding_return",
    "holding_trade_days",
    "is_closed",
    "entry_execution_price",
)

DATEBAR_REQUIRED_COLUMNS = (
    "idxs",
    "idxd",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "paused",
)


@dataclass(frozen=True)
class RawArtifactContract:
    holding_output_name: str = "holding_stats_all.parquet"
    datebar_output_name: str = "dateBar.pkl"

    def to_dict(self) -> dict[str, object]:
        return {
            "holding_output_name": self.holding_output_name,
            "datebar_output_name": self.datebar_output_name,
            "holding_required_columns": list(HOLDING_REQUIRED_COLUMNS),
            "datebar_required_columns": list(DATEBAR_REQUIRED_COLUMNS),
        }


def missing_columns(columns: Sequence[str], required: Sequence[str]) -> list[str]:
    observed = {str(column) for column in columns}
    return [column for column in required if column not in observed]


def validate_column_contract(holding_columns: Sequence[str], datebar_columns: Sequence[str]) -> None:
    missing_holding = missing_columns(holding_columns, HOLDING_REQUIRED_COLUMNS)
    if missing_holding:
        raise ValueError(f"holding 输入缺少必要列: {missing_holding}")
    normalized_datebar = [str(column).lower() for column in datebar_columns]
    missing_datebar = missing_columns(normalized_datebar, DATEBAR_REQUIRED_COLUMNS)
    if missing_datebar:
        raise ValueError(f"datebar 输入缺少必要列: {missing_datebar}")


def materialize_raw_bundle(holding_frame, datebar_frame, destination: Path) -> dict[str, Path]:
    validate_column_contract(list(holding_frame.columns), list(datebar_frame.columns))
    destination.mkdir(parents=True, exist_ok=True)
    holding_path = destination / RawArtifactContract().holding_output_name
    datebar_path = destination / RawArtifactContract().datebar_output_name
    holding_frame.to_parquet(holding_path, index=False)
    datebar_frame.to_pickle(datebar_path)
    return {
        "holding": holding_path,
        "datebar": datebar_path,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="展示新样本数据适配到 step1 原始输入契约所需的最小字段集合。",
    )
    parser.add_argument(
        "--show-contract",
        action="store_true",
        help="打印 holding/datebar 所需列名与目标文件名。",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.show_contract:
        print(json.dumps(RawArtifactContract().to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
