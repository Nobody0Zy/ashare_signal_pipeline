"""模型评估即时摘要 — 排序单调性 + 泄露检测。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover
    lgb = None


def _safe_quantile_codes(series: pd.Series, q: int) -> pd.Series:
    actual_q = int(max(1, min(int(q), len(series))))
    if actual_q <= 1 or series.nunique(dropna=True) <= 1:
        return pd.Series(np.zeros(len(series), dtype=int), index=series.index)
    candidates = [series, series.rank(method="first")]
    for candidate in candidates:
        try:
            cut = pd.qcut(candidate, q=actual_q, labels=False, duplicates="drop")
            if pd.Series(cut).notna().any():
                return pd.Series(cut, index=series.index).fillna(0).astype(int)
        except ValueError:
            continue
    return pd.Series(np.zeros(len(series), dtype=int), index=series.index)


def _prediction_candidates(root: Path, experiment_version: str, split: str) -> list[Path]:
    output_dir = root / "outputs" / experiment_version
    if split == "train":
        names = ["train_predictions.csv", "train_predictions_smoke.csv"]
    elif split == "val":
        names = ["val_predictions.csv", "val_predictions_smoke.csv"]
    else:
        names = ["predictions.csv", "predictions_smoke.csv"]
    return [output_dir / name for name in names]


def _load_prediction_frame(root: Path, experiment_version: str, split: str) -> pd.DataFrame:
    for candidate in _prediction_candidates(root, experiment_version, split):
        if candidate.exists():
            frame = pd.read_csv(candidate)
            required = ["y_pred", "target", "target_raw"]
            missing = [column for column in required if column not in frame.columns]
            if missing:
                raise ValueError(f"预测文件缺少必需列: {missing}")
            frame = frame.replace([np.inf, -np.inf], np.nan).fillna(0)
            return frame
    raise FileNotFoundError(f"未找到 {experiment_version} 的 {split} 预测文件")


def _build_monotonic_from_prediction(prediction: pd.DataFrame, split: str) -> pd.DataFrame:
    ranked = prediction.sort_values("y_pred", ascending=True).copy()
    codes = _safe_quantile_codes(ranked["y_pred"], 100)
    unique_codes = sorted(int(value) for value in pd.Series(codes).dropna().unique())
    mapping = {code: index + 1 for index, code in enumerate(unique_codes)}
    ranked["group_id"] = pd.Series(codes, index=ranked.index).map(mapping).fillna(1).astype(int)
    grouped = ranked.groupby("group_id", dropna=False)
    rows = []
    for group_id, frame in grouped:
        rows.append(
            {
                "split_name": split,
                "group_id": int(group_id),
                "sample_count": int(len(frame)),
                "y_pred_mean": float(frame["y_pred"].mean()),
                "target_mean": float(frame["target"].mean()),
                "target_raw_mean": float(frame["target_raw"].mean()),
                "target_win_rate": float((frame["target"] > 0).mean()),
            }
        )
    return pd.DataFrame(rows).sort_values("group_id").reset_index(drop=True)


def _load_split_monotonic(root: Path, experiment_version: str, split: str) -> pd.DataFrame:
    monotonic_path = root / "outputs" / experiment_version / "analysis" / "monotonic_groups.csv"
    if monotonic_path.exists():
        frame = pd.read_csv(monotonic_path)
        subset = frame.loc[frame["split_name"].eq(split)].copy()
        if not subset.empty:
            return subset.sort_values("group_id").reset_index(drop=True)
    prediction = _load_prediction_frame(root, experiment_version, split)
    return _build_monotonic_from_prediction(prediction, split)


def _build_importance_table(model_path: Path) -> pd.DataFrame:
    if lgb is None:
        raise ImportError("当前环境未安装 lightgbm，无法生成 eval_summary")
    booster = lgb.Booster(model_file=str(model_path))
    feature_names = list(booster.feature_name())
    gains = list(booster.feature_importance(importance_type="gain"))
    importance = pd.DataFrame({"feature": feature_names, "gain": gains})
    total_gain = float(importance["gain"].sum()) if not importance.empty else 0.0
    importance["gain_share"] = importance["gain"].apply(lambda value: float(value / total_gain) if total_gain else 0.0)
    importance = importance.sort_values(["gain", "feature"], ascending=[False, True]).reset_index(drop=True)
    importance["rank"] = np.arange(1, len(importance) + 1)
    return importance


def _feature_rank(importance: pd.DataFrame, feature: str) -> Optional[int]:
    matched = importance.loc[importance["feature"].eq(feature), "rank"]
    if matched.empty:
        return None
    return int(matched.iloc[0])


def generate_eval_summary(
    root: Path,
    experiment_version: str,
    split: str = "test",
) -> Dict[str, object]:
    """生成实验评估即时摘要。

    包含排序单调性指标（spearman / peak_group / tail_drop / mean_81_100_raw）
    和日历特征泄露检测（date / month / year 的 gain_share）。
    由 run_experiment_pipeline.py 在 step6 完成后自动调用（需配置 auto_eval_summary: true）。
    """
    analysis_dir = root / "outputs" / experiment_version / "analysis"
    model_path = root / "models" / experiment_version / "lgb_model.txt"
    if not model_path.exists():
        smoke_model_path = root / "models" / experiment_version / "lgb_model_smoke.txt"
        if smoke_model_path.exists():
            model_path = smoke_model_path
    if not model_path.exists():
        raise FileNotFoundError(f"模型文件不存在: {model_path}")

    monotonic = _load_split_monotonic(root, experiment_version, split)
    if monotonic.empty:
        raise ValueError(f"{experiment_version} 的 split={split} 单调性结果为空")

    _ = analysis_dir / "top_feature_importance.csv"
    importance = _build_importance_table(model_path)
    prediction = _load_prediction_frame(root, experiment_version, split)

    max_group_id = int(monotonic["group_id"].max())
    last_group = monotonic.loc[monotonic["group_id"].eq(max_group_id)].iloc[-1]
    max_target_raw = float(monotonic["target_raw_mean"].max())
    peak_group = int(monotonic.loc[monotonic["target_raw_mean"].eq(max_target_raw), "group_id"].max())
    tail_start = 81 if max_group_id >= 100 else max(1, max_group_id - 19)
    tail_frame = monotonic.loc[monotonic["group_id"].between(tail_start, max_group_id)].copy()
    calendar_features = ["date", "year", "month"]
    calendar_gain_share = float(
        importance.loc[importance["feature"].isin(calendar_features), "gain_share"].sum()
    )
    spearman = prediction["target"].corr(prediction["y_pred"], method="spearman")
    pearson = prediction["target"].corr(prediction["y_pred"], method="pearson")

    return {
        "experiment_version": experiment_version,
        "split": split,
        "peak_group": peak_group,
        "tail_drop": float(max_target_raw - float(last_group["target_raw_mean"])),
        "mean_81_100_raw": float(tail_frame["target_raw_mean"].mean()),
        "last_group_target_raw_mean": float(last_group["target_raw_mean"]),
        "year_rank": _feature_rank(importance, "year"),
        "month_rank": _feature_rank(importance, "month"),
        "date_rank": _feature_rank(importance, "date"),
        "calendar_gain_share": calendar_gain_share,
        "spearman": None if pd.isna(spearman) else float(spearman),
        "pearson": None if pd.isna(pearson) else float(pearson),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
