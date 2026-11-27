"""标签缓存：is_valid / load / save（§3.4 缓存策略）。

_index.json 结构（每个 samples_id 目录一个）：
{
  "<label_name>": {
    "params_hash":        "8位hex",
    "rows":               int,
    "non_null_rows":      int,
    "coverage":           float,
    "created_at":         "ISO8601",
    "holding_fingerprint":"",
    "samples_set_hash":   "16位hex"  ← 额外字段，用于缓存有效性判断
  }
}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

INDEX_FILE = "_index.json"


def _read_index(index_path: Path) -> dict:
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_index(index_path: Path, data: dict) -> None:
    index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_valid(
    labels_dir: Path,
    samples_id: str,
    label_name: str,
    params_hash: str,
    samples_set_hash: str,
) -> bool:
    """返回 True 当且仅当缓存文件存在且 params_hash + samples_set_hash 均匹配。"""
    parquet_path = labels_dir / samples_id / f"{label_name}.parquet"
    if not parquet_path.exists():
        return False
    index = _read_index(labels_dir / samples_id / INDEX_FILE)
    entry = index.get(label_name, {})
    if entry.get("params_hash") != params_hash:
        return False
    if entry.get("samples_set_hash") != samples_set_hash:
        return False
    return True


def load(labels_dir: Path, samples_id: str, label_name: str) -> pd.DataFrame:
    """从缓存加载标签 DataFrame。"""
    path = labels_dir / samples_id / f"{label_name}.parquet"
    return pd.read_parquet(path)


def save(
    labels_dir: Path,
    samples_id: str,
    label_name: str,
    frame: pd.DataFrame,
    params_hash: str,
    samples_set_hash: str,
    holding_fingerprint: str = "",
) -> None:
    """保存标签 DataFrame 并更新 _index.json。"""
    out_dir = labels_dir / samples_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 写 parquet
    parquet_path = out_dir / f"{label_name}.parquet"
    frame.to_parquet(parquet_path, index=False)

    # 更新 _index.json
    index_path = out_dir / INDEX_FILE
    index = _read_index(index_path)

    rows = len(frame)
    non_null_rows = int(frame["target"].notna().sum())
    coverage = non_null_rows / rows if rows > 0 else 0.0

    index[label_name] = {
        "params_hash": params_hash,
        "rows": rows,
        "non_null_rows": non_null_rows,
        "coverage": coverage,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "holding_fingerprint": holding_fingerprint,
        "samples_set_hash": samples_set_hash,
    }
    _write_index(index_path, index)
