"""标签构建编排：ensure（定义级整组就绪）与内部 _materialize（§6.1）。

公共接口仅 ensure()；_materialize() 供 tools/build_labels.py 复用。
step1 缺标签时只抛错，不调用本模块（§7 M2 / §6.2 不变量 3）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd

from .ids import canonical_json
from .labels.base import LabelContext, LabelDefinition, build_label_sym_arrays, validate_label_frame
from .labels.cache import is_valid, save
from .labels.registry import resolve_label


def _compute_label_params_hash(
    label_name: str,
    definition: LabelDefinition,
    public_params: dict,
    samples_id: str,
    datebar_fingerprint: str,
    holding_fingerprint: str,
) -> str:
    """计算 label_params_hash（§3.4）。"""
    hash_input = {
        "label": label_name,
        "spec_version": definition.spec_version,
        "params": public_params,
        "samples_id": samples_id,
        "datebar_fingerprint": datebar_fingerprint,
        "holding_fingerprint": holding_fingerprint,
    }
    return hashlib.sha256(canonical_json(hash_input).encode("utf-8")).hexdigest()[:8]


def _get_public_params(params: dict) -> dict:
    """过滤掉以 _ 开头的内部键，只保留公开参数（用于哈希计算）。"""
    return {k: v for k, v in params.items() if not k.startswith("_")}


def _materialize(
    definition: LabelDefinition,
    raw_params: dict,
    config_dict: dict,
    samples: pd.DataFrame,
    ctx: LabelContext,
    samples_id: str,
    datebar_fingerprint: str,
    samples_set_hash: str,
    labels_dir: Path,
    dry_run: bool = False,
) -> List[Path]:
    """计算并缓存当前定义产出的全部标签文件。

    缓存策略：当且仅当全部产出文件都命中 (params_hash ∧ samples_set_hash) 时返回 []；
    否则整组重建（§3.4）。
    """
    # 1. 准备完整参数（可能注入内部键如 _holding_file）
    params = definition.prepare_params(config_dict, raw_params)
    holding_fp: str = str(params.get("_holding_fingerprint", ""))
    public_params = _get_public_params(params)

    # 2. 确定当前 params 下会产出的标签名
    expected_labels = definition.label_names(params)
    if not expected_labels:
        return []

    # 3. 预算每个标签的 params_hash
    label_hashes = {
        lname: _compute_label_params_hash(
            lname, definition, public_params, samples_id, datebar_fingerprint, holding_fp
        )
        for lname in expected_labels
    }

    # 4. 缓存检查：全部命中则跳过计算
    if all(
        is_valid(labels_dir, samples_id, lname, label_hashes[lname], samples_set_hash)
        for lname in expected_labels
    ):
        return []

    if dry_run:
        return []

    # 5. 调用 compute 生成标签帧
    label_frames = definition.compute(samples, ctx, params)

    # 6. 填充元数据列、验证、保存
    created_at = datetime.now(timezone.utc).isoformat()
    written: List[Path] = []
    for lname in expected_labels:
        frame = label_frames.get(lname)
        if frame is None:
            raise RuntimeError(
                f"{type(definition).__name__}.compute 未返回 {lname!r}；"
                f"实际返回键: {sorted(label_frames.keys())}"
            )

        frame = frame.copy()
        lhash = label_hashes[lname]
        frame["label_params_hash"] = lhash
        frame["samples_id"] = samples_id
        frame["datebar_fingerprint"] = datebar_fingerprint
        frame["created_at"] = created_at

        validate_label_frame(frame)

        # 按 sample_id 升序（§3.2 行契约）
        frame = frame.sort_values("sample_id", kind="mergesort").reset_index(drop=True)

        save(
            labels_dir, samples_id, lname, frame,
            params_hash=lhash,
            samples_set_hash=samples_set_hash,
            holding_fingerprint=holding_fp,
        )
        written.append(labels_dir / samples_id / f"{lname}.parquet")

    return written


def ensure(config_dict: dict, samples_id: str, label_name: str) -> List[Path]:
    """确保 label_name 所属定义的全部产出标签就绪，返回本次写入的路径列表。

    缓存命中时返回 []（不重算）；任一缺失则整组重建。
    """
    from .datebar import load_datebar

    # 解析定义
    definition, _, _ = resolve_label(label_name)

    data_cfg: dict = config_dict.get("data", {})
    prepared_root = data_cfg.get("prepared_dir", "data/prepared")
    labels_dir = Path(data_cfg.get("labels_dir", "data/labels"))

    # 读取 manifest
    manifest_path = Path(prepared_root) / samples_id / "manifest.json"
    with manifest_path.open(encoding="utf-8") as fh:
        manifest = json.load(fh)

    datebar_fingerprint: str = manifest["datebar_fingerprint"]
    samples_set_hash: str = manifest["samples_set_hash"]

    # 组装参数
    dp_cfg: dict = config_dict.get("data_prep", {})
    labels_cfg: dict = dp_cfg.get("labels", {})
    labels_params: dict = labels_cfg.get("params", {})
    drop_blocked = bool(labels_cfg.get("drop_blocked_entries", True))

    key = definition.params_key
    if key and key in labels_params:
        raw_params = dict(labels_params[key])
    else:
        raw_params = dict(definition.default_params)

    # 过滤掉已有的内部键（以 _ 开头），保留用户参数
    raw_params = {k: v for k, v in raw_params.items() if not k.startswith("_")}
    raw_params["drop_blocked_entries"] = drop_blocked

    # 快速缓存检查（避免加载 datebar）
    params_for_check = definition.prepare_params(config_dict, raw_params)
    expected_labels = definition.label_names(params_for_check)
    public_params = _get_public_params(params_for_check)
    holding_fp = str(params_for_check.get("_holding_fingerprint", ""))
    label_hashes = {
        lname: _compute_label_params_hash(
            lname, definition, public_params, samples_id, datebar_fingerprint, holding_fp
        )
        for lname in expected_labels
    }
    if all(
        is_valid(labels_dir, samples_id, lname, label_hashes[lname], samples_set_hash)
        for lname in expected_labels
    ):
        return []

    # 缓存未命中：加载 datebar 与 samples
    datebar_path = data_cfg.get("datebar_file", "")
    df, _ = load_datebar(datebar_path)
    sym_arrays = build_label_sym_arrays(df)
    ctx = LabelContext(sym_arrays)

    samples_path = Path(prepared_root) / samples_id / "samples.parquet"
    samples_df = pd.read_parquet(samples_path)

    return _materialize(
        definition, raw_params, config_dict, samples_df, ctx,
        samples_id, datebar_fingerprint, samples_set_hash, labels_dir,
    )
