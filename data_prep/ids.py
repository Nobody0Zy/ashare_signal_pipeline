"""sample_id 编解码、三类哈希与指纹计算。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Tuple

MARKET_CODES: dict[str, int] = {"sh": 1, "sz": 2, "bj": 3}
MARKET_PREFIXES: dict[int, str] = {1: "sh", 2: "sz", 3: "bj"}
_P14 = 10 ** 14
_P8 = 10 ** 8
_P6 = 10 ** 6


def derive_sample_id(idxs: str, idxd: int) -> int:
    """market*10^14 + code6*10^8 + idxd；与 (idxs, idxd) 双射。

    sh000001 → market=1, code6=0, id=1*10^14+0*10^8+idxd
    sz000001 → market=2, code6=0, id=2*10^14+0*10^8+idxd  （不冲突）
    """
    prefix = idxs[:2]
    market = MARKET_CODES.get(prefix)
    if market is None:
        raise ValueError(f"未知市场前缀: {prefix!r}，支持: {sorted(MARKET_CODES)}")
    code6 = int(idxs[2:])
    return int(market * _P14 + code6 * _P8 + idxd)


def parse_sample_id(sample_id: int) -> Tuple[str, int]:
    """解码 sample_id → (idxs, idxd)。"""
    idxd = int(sample_id % _P8)
    code6 = int((sample_id // _P8) % _P6)
    market = int(sample_id // _P14)
    prefix = MARKET_PREFIXES.get(market)
    if prefix is None:
        raise ValueError(f"无法解码 sample_id={sample_id}: market={market} 未知")
    return f"{prefix}{code6:06d}", idxd


def canonical_json(obj: object) -> str:
    """确定性 JSON 序列化（sort_keys=True，紧凑无空格）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def derive_datebar_fingerprint(path: str | Path) -> str:
    """计算 datebar 文件指纹。

    格式: sha256(raw_bytes)[:12] + "_" + f"{rows}_{min_idxd}_{max_idxd}_{n_symbols}"
    """
    import pandas as pd

    path = Path(path)
    # sha256 of raw bytes
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    sha = h.hexdigest()[:12]

    # Load to get stats (before any normalization)
    df = pd.read_pickle(path)
    df = df.reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    rows = len(df)
    min_idxd = int(df["idxd"].min())
    max_idxd = int(df["idxd"].max())
    n_symbols = int(df["idxs"].nunique())
    return f"{sha}_{rows}_{min_idxd}_{max_idxd}_{n_symbols}"


def derive_samples_id(method: str, params_hash: str, datebar_fingerprint: str) -> str:
    """samples_id = f"{method}_{params_hash}_{datebar_fingerprint[:8]}"。"""
    return f"{method}_{params_hash}_{datebar_fingerprint[:8]}"
