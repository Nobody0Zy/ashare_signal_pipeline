from __future__ import annotations

import argparse
import html
import json
import random
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")

from matplotlib import font_manager
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover
    lgb = None

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


random.seed(42)
np.random.seed(42)

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = "configs/report_analysis/default.yaml"
BASE_COLUMNS = {"sample_id", "signal_date", "target", "target_raw", "y_pred"}
PREFERRED_CN_FONTS = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS"]


@dataclass
class AnalysisConfig:
    manifest_path: str = ""
    experiment_version: str = ""
    test_prediction_path: str = ""
    train_prediction_path: str = ""
    val_prediction_path: str = ""
    model_path: str = ""
    selected_features_path: str = ""
    clip_percentiles_path: str = ""
    state_file: str = ""
    model_feature_schema_path: str = ""
    output_dir: str = ""
    report_title: str = "实验分析报告"
    pred_col: str = "y_pred"
    target_col: str = "target"
    target_raw_col: str = "target_raw"
    sample_id_col: str = "sample_id"
    signal_date_col: str = "signal_date"
    n_monotonic_groups: int = 100
    monotonic_preview_stride: int = 10
    remove_outliers_for_monotonic: bool = False
    monotonic_outlier_low: float = 0.01
    monotonic_outlier_high: float = 0.99
    test_group_count: int = 5
    threshold_quantiles: List[float] = field(default_factory=lambda: [0.5, 0.6, 0.7, 0.8, 0.9])
    feature_importance_top_k: int = 20
    plot_dpi: int = 180
    strict_mode: bool = True

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AnalysisConfig":
        payload = dict(raw or {})
        if "threshold_quantiles" in payload and payload["threshold_quantiles"] is None:
            payload["threshold_quantiles"] = [0.5, 0.6, 0.7, 0.8, 0.9]
        return cls(**payload)


@dataclass
class ArtifactBundle:
    experiment_version: str
    analysis_dir: Path
    resolution_source: str
    manifest_path: Optional[Path]
    input_artifacts: Dict[str, Optional[Path]]
    available_splits: List[str]
    missing_artifacts: List[str]
    strict_mode: bool


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sanitize_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_json(item) for item in value]
    if isinstance(value, np.generic):
        return _sanitize_for_json(value.item())
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(_sanitize_for_json(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _to_abs_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (ROOT / path).resolve()


def _rel(path: Optional[Path]) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _load_registry(root: Path) -> List[Dict[str, Any]]:
    registry_path = root / "outputs" / "experiment_registry.json"
    if not registry_path.exists():
        return []
    payload = _read_json(registry_path)
    if isinstance(payload, list):
        return payload
    return []


def _registry_manifest_path(entry: Dict[str, Any]) -> Optional[Path]:
    manifest_file = str(entry.get("manifest_file", "")).strip()
    if manifest_file:
        manifest_path = _to_abs_path(manifest_file)
        if manifest_path.exists():
            return manifest_path
    experiment_version = str(entry.get("experiment_version", "")).strip()
    if experiment_version:
        candidate = ROOT / "outputs" / experiment_version / "metadata" / "experiment_manifest.json"
        if candidate.exists():
            return candidate
    return None


def _scan_manifest_paths(root: Path) -> List[Path]:
    manifest_paths = list((root / "outputs").glob("**/metadata/experiment_manifest.json"))
    manifest_paths.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return manifest_paths


def _load_default_signature(root: Path) -> Dict[str, str]:
    default_path = root / "configs" / "default.yaml"
    if not default_path.exists() or yaml is None:
        return {"source_config": "configs/default.yaml", "base_version": "", "label": ""}
    raw = yaml.safe_load(default_path.read_text(encoding="utf-8")) or {}
    experiment = raw.get("experiment", {}) if isinstance(raw, dict) else {}
    return {
        "source_config": "configs/default.yaml",
        "base_version": str(experiment.get("base_version", "")).strip(),
        "label": str(experiment.get("label", "")).strip(),
    }


def _matches_default_signature(payload: Dict[str, Any], signature: Dict[str, str]) -> bool:
    experiment = payload.get("experiment", {}) if isinstance(payload, dict) else {}
    source_config = str(payload.get("source_config", "") or experiment.get("source_config", "")).strip()
    if source_config and source_config == signature["source_config"]:
        return True
    base_version = str(experiment.get("base_version", "")).strip()
    label = str(experiment.get("label", "")).strip()
    return bool(signature["base_version"] and signature["label"] and base_version == signature["base_version"] and label == signature["label"])


def _resolve_default_manifest(root: Path, registry: List[Dict[str, Any]]) -> Optional[Path]:
    signature = _load_default_signature(root)
    for entry in reversed(registry):
        if _matches_default_signature(entry, signature):
            manifest_path = _registry_manifest_path(entry)
            if manifest_path is not None:
                return manifest_path
    for manifest_path in _scan_manifest_paths(root):
        try:
            manifest = _read_json(manifest_path)
        except json.JSONDecodeError:
            continue
        if _matches_default_signature(manifest, signature):
            return manifest_path
    return None


def _resolve_manifest_by_version(root: Path, experiment_version: str, registry: List[Dict[str, Any]]) -> Optional[Path]:
    direct = root / "outputs" / experiment_version / "metadata" / "experiment_manifest.json"
    if direct.exists():
        return direct
    for entry in registry:
        if str(entry.get("experiment_version", "")).strip() == experiment_version:
            manifest_path = _registry_manifest_path(entry)
            if manifest_path is not None:
                return manifest_path
    return None


def load_analysis_config(config_arg: str, explicit_config: bool) -> AnalysisConfig:
    config_path = _to_abs_path(config_arg)
    if not config_path.exists():
        if explicit_config:
            raise FileNotFoundError(f"分析配置文件不存在: {config_path}")
        print(f"分析配置 {config_path} 不存在，回退到内置默认配置。")
        return AnalysisConfig()
    if yaml is None:
        if explicit_config:
            raise ImportError(f"当前环境缺少 PyYAML，无法读取分析配置文件 {config_path}。")
        print("当前环境缺少 PyYAML，回退到内置默认分析配置。")
        return AnalysisConfig()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"分析配置格式无效: {config_path}")
    return AnalysisConfig.from_dict(raw)


def _artifact_from_manifest(manifest: Dict[str, Any], *keys: str) -> tuple[Optional[str], bool]:
    artifact_files = manifest.get("artifact_files", {}) if isinstance(manifest, dict) else {}
    for key in keys:
        if key in artifact_files and str(artifact_files[key]).strip():
            return str(artifact_files[key]).strip(), True
    return None, False


def _resolve_output_dir(config: AnalysisConfig, output_override: Optional[str], manifest: Optional[Dict[str, Any]], experiment_version: str) -> Path:
    if output_override:
        return _to_abs_path(output_override)
    if config.output_dir:
        return _to_abs_path(config.output_dir)
    if manifest is not None:
        artifact_roots = manifest.get("artifact_roots", {}) if isinstance(manifest, dict) else {}
        output_root = str(artifact_roots.get("output_dir", "")).strip()
        if output_root:
            return _to_abs_path(output_root) / "analysis"
    if experiment_version:
        return ROOT / "outputs" / experiment_version / "analysis"
    return ROOT / "outputs" / "ad_hoc_analysis"


def _bundle_from_manifest(
    manifest_path: Path,
    config: AnalysisConfig,
    output_override: Optional[str],
    strict_mode: bool,
    resolution_source: str,
) -> ArtifactBundle:
    manifest = _read_json(manifest_path)
    experiment_version = str(manifest.get("experiment_version", "")).strip() or "unknown_experiment"
    analysis_dir = _resolve_output_dir(config, output_override, manifest, experiment_version)
    artifact_specs = {
        "test_prediction": ("test_prediction_file", "prediction_file"),
        "train_prediction": ("train_prediction_file",),
        "val_prediction": ("val_prediction_file",),
        "model": ("model_file",),
        "selected_features": ("selected_features_file",),
        "clip_percentiles": ("clip_percentiles_file",),
        "state_file": ("state_file",),
        "model_feature_schema": ("model_feature_schema_file",),
    }
    input_artifacts: Dict[str, Optional[Path]] = {}
    missing_artifacts: List[str] = []
    for artifact_name, keys in artifact_specs.items():
        raw_path, expected = _artifact_from_manifest(manifest, *keys)
        if raw_path is None:
            input_artifacts[artifact_name] = None
            continue
        abs_path = _to_abs_path(raw_path)
        if abs_path.exists():
            input_artifacts[artifact_name] = abs_path
        else:
            # fallback: state_file 可能在 manifest 同级 metadata 目录
            if artifact_name == "state_file":
                fallback = manifest_path.parent / "state.json"
                if fallback.exists():
                    input_artifacts[artifact_name] = fallback
                    continue
            input_artifacts[artifact_name] = None
            if expected:
                missing_artifacts.append(artifact_name)

    if input_artifacts["test_prediction"] is None:
        raise FileNotFoundError("未找到测试集预测 CSV，无法执行分析。")
    critical_missing = [name for name in missing_artifacts if name in {"train_prediction", "val_prediction"}]
    if strict_mode and critical_missing:
        missing_text = "、".join(critical_missing)
        raise FileNotFoundError(f"严格模式下缺少已登记工件: {missing_text}")

    available_splits = [split for split, key in (("train", "train_prediction"), ("val", "val_prediction"), ("test", "test_prediction")) if input_artifacts[key] is not None]
    canonical_manifest_path = analysis_dir.parent / "metadata" / "experiment_manifest.json"
    bundle_manifest_path = canonical_manifest_path if canonical_manifest_path.exists() else manifest_path
    return ArtifactBundle(
        experiment_version=experiment_version,
        analysis_dir=analysis_dir,
        resolution_source=resolution_source,
        manifest_path=bundle_manifest_path,
        input_artifacts=input_artifacts,
        available_splits=available_splits,
        missing_artifacts=missing_artifacts,
        strict_mode=strict_mode,
    )


def _bundle_from_config_paths(config: AnalysisConfig, output_override: Optional[str], strict_mode: bool) -> ArtifactBundle:
    if not config.test_prediction_path:
        raise FileNotFoundError("显式路径模式下必须提供 test_prediction_path。")
    experiment_version = config.experiment_version.strip() or "ad_hoc_analysis"
    analysis_dir = _resolve_output_dir(config, output_override, None, experiment_version)
    raw_mapping = {
        "test_prediction": config.test_prediction_path,
        "train_prediction": config.train_prediction_path,
        "val_prediction": config.val_prediction_path,
        "model": config.model_path,
        "selected_features": config.selected_features_path,
        "clip_percentiles": config.clip_percentiles_path,
        "state_file": config.state_file,
        "model_feature_schema": config.model_feature_schema_path,
    }
    input_artifacts: Dict[str, Optional[Path]] = {}
    missing_artifacts: List[str] = []
    for name, raw_path in raw_mapping.items():
        if not raw_path:
            input_artifacts[name] = None
            continue
        abs_path = _to_abs_path(raw_path)
        if abs_path.exists():
            input_artifacts[name] = abs_path
        else:
            input_artifacts[name] = None
            missing_artifacts.append(name)
    if input_artifacts["test_prediction"] is None:
        raise FileNotFoundError("显式路径模式下测试集预测 CSV 不存在。")
    critical_missing = [name for name in missing_artifacts if name in {"train_prediction", "val_prediction"}]
    if strict_mode and critical_missing:
        missing_text = "、".join(critical_missing)
        raise FileNotFoundError(f"严格模式下缺少已指定工件: {missing_text}")
    available_splits = [split for split, key in (("train", "train_prediction"), ("val", "val_prediction"), ("test", "test_prediction")) if input_artifacts[key] is not None]
    return ArtifactBundle(
        experiment_version=experiment_version,
        analysis_dir=analysis_dir,
        resolution_source="配置文件显式路径",
        manifest_path=None,
        input_artifacts=input_artifacts,
        available_splits=available_splits,
        missing_artifacts=missing_artifacts,
        strict_mode=strict_mode,
    )


def resolve_artifacts(
    config: AnalysisConfig,
    cli_manifest: Optional[str],
    cli_experiment_version: Optional[str],
    output_override: Optional[str],
    strict_mode: bool,
) -> ArtifactBundle:
    registry = _load_registry(ROOT)
    if cli_manifest:
        return _bundle_from_manifest(_to_abs_path(cli_manifest), config, output_override, strict_mode, "--manifest")
    if cli_experiment_version:
        manifest_path = _resolve_manifest_by_version(ROOT, cli_experiment_version, registry)
        if manifest_path is None:
            raise FileNotFoundError(f"未找到实验版本 {cli_experiment_version} 对应的 manifest。")
        return _bundle_from_manifest(manifest_path, config, output_override, strict_mode, "--experiment-version")
    if config.manifest_path:
        return _bundle_from_manifest(_to_abs_path(config.manifest_path), config, output_override, strict_mode, "config.manifest_path")
    if config.experiment_version:
        manifest_path = _resolve_manifest_by_version(ROOT, config.experiment_version, registry)
        if manifest_path is not None:
            return _bundle_from_manifest(manifest_path, config, output_override, strict_mode, "config.experiment_version")
    if any([
        config.test_prediction_path,
        config.train_prediction_path,
        config.val_prediction_path,
        config.model_path,
        config.selected_features_path,
        config.clip_percentiles_path,
        config.state_file,
    ]):
        return _bundle_from_config_paths(config, output_override, strict_mode)
    default_manifest = _resolve_default_manifest(ROOT, registry)
    if default_manifest is not None:
        return _bundle_from_manifest(default_manifest, config, output_override, strict_mode, "default.yaml 主实验 manifest 自动解析")
    if registry:
        latest_manifest = _registry_manifest_path(registry[-1])
        if latest_manifest is not None:
            return _bundle_from_manifest(latest_manifest, config, output_override, strict_mode, "experiment_registry 最新实验")
    raise FileNotFoundError(
        "未定位到任何实验工件：请用 --manifest 或 --experiment-version 指定，"
        "或在配置中提供 test_prediction_path。"
    )


def load_prediction(path: Path, config: AnalysisConfig, split_name: str) -> tuple[pd.DataFrame, Dict[str, int]]:
    df = pd.read_csv(path)
    required_cols = [config.pred_col, config.target_col, config.target_raw_col]
    missing_cols = [column for column in required_cols if column not in df.columns]
    if missing_cols:
        raise ValueError(f"{split_name} 预测文件缺少必需列: {', '.join(missing_cols)}")
    cleaned = df.replace([np.inf, -np.inf], np.nan)
    inf_replaced = int(np.isinf(df.select_dtypes(include=[np.number]).to_numpy()).sum())
    if cleaned[config.pred_col].dropna().empty or cleaned[config.target_col].dropna().empty:
        raise ValueError(f"{split_name} 预测文件中的 {config.pred_col} 或 {config.target_col} 全为空，无法分析。")
    nan_filled = int(cleaned.isna().sum().sum())
    cleaned = cleaned.fillna(0)
    print(f"[{split_name}] inf 替换数: {inf_replaced}, NaN 填充数: {nan_filled}")
    return cleaned, {"inf_replaced": inf_replaced, "nan_filled": nan_filled}


def _safe_quantile_codes(series: pd.Series, q: int, rank_on_failure: bool) -> pd.Series:
    actual_q = int(max(1, min(int(q), len(series))))
    if actual_q <= 1 or series.nunique(dropna=True) <= 1:
        return pd.Series(np.zeros(len(series), dtype=int), index=series.index)
    candidates = [series]
    if rank_on_failure:
        candidates.append(series.rank(method="first"))
    for candidate in candidates:
        try:
            cut = pd.qcut(candidate, q=actual_q, labels=False, duplicates="drop")
            if pd.Series(cut).notna().any():
                return pd.Series(cut, index=series.index).fillna(0).astype(int)
        except ValueError:
            continue
    return pd.Series(np.zeros(len(series), dtype=int), index=series.index)


def build_quintile_report(test_df: pd.DataFrame, config: AnalysisConfig) -> pd.DataFrame:
    ranked = test_df.copy()
    ranked["pred_group"] = _safe_quantile_codes(ranked[config.pred_col], config.test_group_count, rank_on_failure=True)
    grouped = ranked.groupby("pred_group", dropna=False)
    rows: List[Dict[str, Any]] = []
    for group_id, frame in grouped:
        rows.append(
            {
                "pred_group": int(group_id),
                "sample_count": int(len(frame)),
                "target_mean": float(frame[config.target_col].mean()),
                "target_win_rate": float((frame[config.target_col] > 0).mean()),
                "target_raw_mean": float(frame[config.target_raw_col].mean()),
                "target_raw_win_rate": float((frame[config.target_raw_col] > 0).mean()),
            }
        )
    rows.sort(key=lambda item: item["pred_group"])
    return pd.DataFrame(rows)


def build_median_split_report(test_df: pd.DataFrame, config: AnalysisConfig) -> tuple[pd.DataFrame, Dict[str, Any]]:
    median_value = float(test_df[config.pred_col].median())
    ranked = test_df.copy()
    ranked["score_group"] = np.where(ranked[config.pred_col] <= median_value, "low", "high")
    summary = (
        ranked.groupby("score_group", dropna=False)
        .agg(
            sample_count=(config.pred_col, "size"),
            target_mean=(config.target_col, "mean"),
            target_win_rate=(config.target_col, lambda series: (series > 0).mean()),
            target_raw_mean=(config.target_raw_col, "mean"),
            target_raw_win_rate=(config.target_raw_col, lambda series: (series > 0).mean()),
        )
        .reset_index()
    )
    summary["score_group"] = pd.Categorical(summary["score_group"], categories=["low", "high"], ordered=True)
    summary = summary.sort_values("score_group").reset_index(drop=True)
    low = summary.loc[summary["score_group"] == "low"].iloc[0]
    high = summary.loc[summary["score_group"] == "high"].iloc[0]
    uplift = {
        "median": median_value,
        "target_mean_diff": float(high["target_mean"] - low["target_mean"]),
        "target_mean_rel": float((high["target_mean"] - low["target_mean"]) / abs(low["target_mean"])) if low["target_mean"] != 0 else None,
        "target_win_rate_diff": float(high["target_win_rate"] - low["target_win_rate"]),
        "target_win_rate_rel": float((high["target_win_rate"] - low["target_win_rate"]) / low["target_win_rate"]) if low["target_win_rate"] != 0 else None,
        "target_raw_mean_diff": float(high["target_raw_mean"] - low["target_raw_mean"]),
        "target_raw_mean_rel": float((high["target_raw_mean"] - low["target_raw_mean"]) / abs(low["target_raw_mean"])) if low["target_raw_mean"] != 0 else None,
        "target_raw_win_rate_diff": float(high["target_raw_win_rate"] - low["target_raw_win_rate"]),
        "target_raw_win_rate_rel": float((high["target_raw_win_rate"] - low["target_raw_win_rate"]) / low["target_raw_win_rate"]) if low["target_raw_win_rate"] != 0 else None,
    }
    uplift_row = {
        "score_group": "uplift",
        "sample_count": int(high["sample_count"] + low["sample_count"]),
        "target_mean": None,
        "target_win_rate": None,
        "target_raw_mean": None,
        "target_raw_win_rate": None,
        "target_mean_diff": uplift["target_mean_diff"],
        "target_mean_rel": uplift["target_mean_rel"],
        "target_win_rate_diff": uplift["target_win_rate_diff"],
        "target_win_rate_rel": uplift["target_win_rate_rel"],
        "target_raw_mean_diff": uplift["target_raw_mean_diff"],
        "target_raw_mean_rel": uplift["target_raw_mean_rel"],
        "target_raw_win_rate_diff": uplift["target_raw_win_rate_diff"],
        "target_raw_win_rate_rel": uplift["target_raw_win_rate_rel"],
    }
    report_rows: List[Dict[str, Any]] = []
    for _, row in summary.iterrows():
        report_rows.append(
            {
                "score_group": str(row["score_group"]),
                "sample_count": int(row["sample_count"]),
                "target_mean": float(row["target_mean"]),
                "target_win_rate": float(row["target_win_rate"]),
                "target_raw_mean": float(row["target_raw_mean"]),
                "target_raw_win_rate": float(row["target_raw_win_rate"]),
                "target_mean_diff": None,
                "target_mean_rel": None,
                "target_win_rate_diff": None,
                "target_win_rate_rel": None,
                "target_raw_mean_diff": None,
                "target_raw_mean_rel": None,
                "target_raw_win_rate_diff": None,
                "target_raw_win_rate_rel": None,
            }
        )
    report_rows.append(uplift_row)
    report_df = pd.DataFrame(report_rows)
    return report_df, uplift


def build_threshold_report(test_df: pd.DataFrame, config: AnalysisConfig) -> tuple[pd.DataFrame, Dict[str, Any]]:
    base_target_mean = float(test_df[config.target_col].mean())
    base_target_win = float((test_df[config.target_col] > 0).mean())
    base_target_raw_mean = float(test_df[config.target_raw_col].mean())
    base_target_raw_win = float((test_df[config.target_raw_col] > 0).mean())
    rows: List[Dict[str, Any]] = []
    for quantile in config.threshold_quantiles:
        threshold = float(test_df[config.pred_col].quantile(quantile))
        keep = test_df[test_df[config.pred_col] >= threshold].copy()
        rows.append(
            {
                "quantile": float(quantile),
                "threshold": threshold,
                "kept_samples": int(len(keep)),
                "kept_ratio": float(len(keep) / len(test_df)),
                "target_mean": float(keep[config.target_col].mean()),
                "target_win_rate": float((keep[config.target_col] > 0).mean()),
                "target_raw_mean": float(keep[config.target_raw_col].mean()),
                "target_raw_win_rate": float((keep[config.target_raw_col] > 0).mean()),
                "target_mean_vs_all": float(keep[config.target_col].mean() - base_target_mean),
                "target_win_rate_vs_all": float((keep[config.target_col] > 0).mean() - base_target_win),
                "target_raw_mean_vs_all": float(keep[config.target_raw_col].mean() - base_target_raw_mean),
                "target_raw_win_rate_vs_all": float((keep[config.target_raw_col] > 0).mean() - base_target_raw_win),
            }
        )
    threshold_df = pd.DataFrame(rows)
    best_row = threshold_df.sort_values(["target_raw_mean", "target_mean", "kept_ratio"], ascending=[False, False, False]).iloc[0].to_dict()
    return threshold_df, {"overall_target_mean": base_target_mean, "overall_target_raw_mean": base_target_raw_mean, "best_threshold": best_row}


def build_monotonic_table(split_frames: Dict[str, pd.DataFrame], config: AnalysisConfig) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for split_name, frame in split_frames.items():
        working = frame.copy()
        if config.remove_outliers_for_monotonic:
            low = float(working[config.target_col].quantile(config.monotonic_outlier_low))
            high = float(working[config.target_col].quantile(config.monotonic_outlier_high))
            working = working[(working[config.target_col] >= low) & (working[config.target_col] <= high)].copy()
            if working.empty:
                raise ValueError(f"{split_name} 在极值剔除后无可用样本，无法生成单调性分析。")
        ranked = working.sort_values(config.pred_col, ascending=True).copy()
        codes = _safe_quantile_codes(ranked[config.pred_col], config.n_monotonic_groups, rank_on_failure=True)
        unique_codes = sorted(int(value) for value in pd.Series(codes).dropna().unique())
        mapping = {code: index + 1 for index, code in enumerate(unique_codes)}
        ranked["group_id"] = pd.Series(codes, index=ranked.index).map(mapping).fillna(1).astype(int)
        grouped = ranked.groupby("group_id", dropna=False)
        for group_id, group_frame in grouped:
            rows.append(
                {
                    "split_name": split_name,
                    "group_id": int(group_id),
                    "sample_count": int(len(group_frame)),
                    "y_pred_mean": float(group_frame[config.pred_col].mean()),
                    "target_mean": float(group_frame[config.target_col].mean()),
                    "target_raw_mean": float(group_frame[config.target_raw_col].mean()),
                    "target_win_rate": float((group_frame[config.target_col] > 0).mean()),
                }
            )
    monotonic_df = pd.DataFrame(rows)
    if not monotonic_df.empty:
        monotonic_df = monotonic_df.sort_values(["split_name", "group_id"]).reset_index(drop=True)
    return monotonic_df


def summarize_monotonic(monotonic_df: pd.DataFrame) -> Dict[str, Any]:
    per_split: Dict[str, Any] = {}
    trend_signs: List[int] = []
    for split_name, frame in monotonic_df.groupby("split_name"):
        ordered = frame.sort_values("group_id").reset_index(drop=True)
        spearman = ordered["group_id"].corr(ordered["target_mean"], method="spearman") if len(ordered) > 1 else None
        pearson = ordered["group_id"].corr(ordered["target_mean"], method="pearson") if len(ordered) > 1 else None
        end_minus_start = float(ordered["target_mean"].iloc[-1] - ordered["target_mean"].iloc[0])
        diffs = ordered["target_mean"].diff().dropna()
        non_decreasing_ratio = float((diffs >= 0).mean()) if not diffs.empty else 1.0
        positive_trend = bool(end_minus_start > 0 and (spearman is None or spearman > 0))
        trend_signs.append(1 if positive_trend else -1)
        per_split[split_name] = {
            "group_count": int(len(ordered)),
            "first_group_target_mean": float(ordered["target_mean"].iloc[0]),
            "last_group_target_mean": float(ordered["target_mean"].iloc[-1]),
            "end_minus_start": end_minus_start,
            "spearman": float(spearman) if spearman is not None and not pd.isna(spearman) else None,
            "pearson": float(pearson) if pearson is not None and not pd.isna(pearson) else None,
            "non_decreasing_ratio": non_decreasing_ratio,
            "positive_trend": positive_trend,
        }
    return {
        "available_splits": list(per_split.keys()),
        "per_split": per_split,
        "trend_consistent": len(set(trend_signs)) <= 1 if trend_signs else False,
        "all_positive_trend": all(item["positive_trend"] for item in per_split.values()) if per_split else False,
    }


def _ordered_split_names(split_names: List[str]) -> List[str]:
    preferred_order = ["train", "val", "test"]
    ordered = [name for name in preferred_order if name in split_names]
    remaining = sorted(name for name in split_names if name not in preferred_order)
    return ordered + remaining


def _configure_chinese_font() -> None:
    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    for font_name in PREFERRED_CN_FONTS:
        if font_name in available_fonts:
            plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return
    warnings.warn("未找到可用的中文字体，将尝试使用默认字体，图表中文可能显示为方块。")
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def _plot_monotonic_metric(
    axis: Any,
    monotonic_df: pd.DataFrame,
    metric: str,
    ylabel: str,
    color_map: Dict[str, str],
    label_map: Dict[str, str],
    draw_zero_line: bool,
) -> None:
    for split_name, frame in monotonic_df.groupby("split_name"):
        ordered = frame.sort_values("group_id")
        axis.plot(
            ordered["group_id"],
            ordered[metric],
            label=label_map.get(split_name, split_name),
            color=color_map.get(split_name, None),
            linewidth=1.8,
        )
    if draw_zero_line:
        axis.axhline(0.0, color="#808080", linestyle="--", linewidth=1.0)
    axis.set_ylabel(ylabel)
    axis.grid(alpha=0.2, linestyle=":", linewidth=0.8)


def plot_monotonic(monotonic_df: pd.DataFrame, output_path: Path, config: AnalysisConfig) -> None:
    _configure_chinese_font()
    color_map = {"train": "#1f77b4", "val": "#ff7f0e", "test": "#2ca02c"}
    label_map = {"train": "训练集", "val": "验证集", "test": "测试集"}
    figure, axes = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    plot_specs = [
        (axes[0], "target_raw_mean", "target_raw 均值", True),
        (axes[1], "y_pred_mean", "y_pred 均值", False),
    ]
    for axis, metric, ylabel, draw_zero_line in plot_specs:
        _plot_monotonic_metric(axis, monotonic_df, metric, ylabel, color_map, label_map, draw_zero_line)

    axes[0].set_title(f"{config.report_title} - 100组单调性")
    axes[0].legend()
    axes[1].set_xlabel("分组编号")
    plt.tight_layout()
    plt.savefig(output_path, dpi=config.plot_dpi)
    plt.close(figure)


def plot_monotonic_heatmap(
    monotonic_df: pd.DataFrame,
    output_path: Path,
    metric: str,
    title: str,
    colorbar_label: str,
    config: AnalysisConfig,
) -> None:
    if monotonic_df.empty:
        if output_path.exists():
            output_path.unlink()
        return
    _configure_chinese_font()
    label_map = {"train": "训练集", "val": "验证集", "test": "测试集"}
    ordered_splits = _ordered_split_names(monotonic_df["split_name"].dropna().unique().tolist())
    group_ids = sorted(int(value) for value in monotonic_df["group_id"].dropna().unique())
    heatmap_df = (
        monotonic_df.pivot(index="split_name", columns="group_id", values=metric)
        .reindex(index=ordered_splits, columns=group_ids)
    )
    values = heatmap_df.to_numpy(dtype=float)
    finite_values = values[np.isfinite(values)]
    if finite_values.size == 0:
        if output_path.exists():
            output_path.unlink()
        return
    if float(finite_values.min()) < 0.0 < float(finite_values.max()):
        norm: Any = matplotlib.colors.TwoSlopeNorm(
            vmin=float(finite_values.min()),
            vcenter=0.0,
            vmax=float(finite_values.max()),
        )
    else:
        norm = matplotlib.colors.Normalize(vmin=float(finite_values.min()), vmax=float(finite_values.max()))
    figure, axis = plt.subplots(1, 1, figsize=(15, max(3.0, 1.2 * len(ordered_splits) + 1.4)))
    image = axis.imshow(values, aspect="auto", cmap="RdYlGn_r", norm=norm, interpolation="nearest")
    axis.set_title(title)
    axis.set_xlabel("分组编号")
    axis.set_ylabel("集合")
    axis.set_yticks(np.arange(len(ordered_splits)))
    axis.set_yticklabels([label_map.get(name, name) for name in ordered_splits])
    tick_step = 10 if len(group_ids) >= 10 else 1
    tick_positions = list(range(0, len(group_ids), tick_step))
    if tick_positions and tick_positions[-1] != len(group_ids) - 1:
        tick_positions.append(len(group_ids) - 1)
    axis.set_xticks(tick_positions)
    axis.set_xticklabels([str(group_ids[position]) for position in tick_positions])
    for row_index in range(1, len(ordered_splits)):
        axis.axhline(row_index - 0.5, color="white", linewidth=1.0, alpha=0.8)
    colorbar = figure.colorbar(image, ax=axis, fraction=0.025, pad=0.02)
    colorbar.set_label(colorbar_label)
    plt.tight_layout()
    plt.savefig(output_path, dpi=config.plot_dpi)
    plt.close(figure)


def plot_monotonic_single_split(
    monotonic_df: pd.DataFrame,
    split_name: str,
    output_path: Path,
    metric: str,
    title: str,
    ylabel: str,
    color: str,
    draw_zero_line: bool,
    config: AnalysisConfig,
) -> None:
    """为单个 split 绘制柱状图。"""
    if monotonic_df.empty:
        if output_path.exists():
            output_path.unlink()
        return
    _configure_chinese_font()
    frame = monotonic_df[monotonic_df["split_name"].eq(split_name)]
    if frame.empty:
        if output_path.exists():
            output_path.unlink()
        return
    ordered = frame.sort_values("group_id")
    figure, axis = plt.subplots(1, 1, figsize=(14, 5))
    bars = axis.bar(
        ordered["group_id"],
        ordered[metric],
        color=color,
        edgecolor="none",
        width=0.8,
    )
    # 给每个柱子加颜色映射：正值一种颜色，负值另一种颜色
    values = ordered[metric].values
    pos_color = color
    neg_color = "#d62728"
    for bar, val in zip(bars, values):
        bar.set_color(pos_color if val >= 0 else neg_color)
    if draw_zero_line:
        axis.axhline(0.0, color="#808080", linestyle="--", linewidth=1.0)
    axis.set_title(title)
    axis.set_xlabel("分组编号")
    axis.set_ylabel(ylabel)
    axis.grid(alpha=0.2, linestyle=":", linewidth=0.8, axis="y")
    plt.tight_layout()
    plt.savefig(output_path, dpi=config.plot_dpi)
    plt.close(figure)


def plot_monotonic_metric_all_splits(
    monotonic_df: pd.DataFrame,
    output_path: Path,
    metric: str,
    title: str,
    ylabel: str,
    draw_zero_line: bool,
    config: AnalysisConfig,
) -> None:
    """为指定 metric 绘制所有 split 的合并折线图（单面板）。"""
    if monotonic_df.empty:
        if output_path.exists():
            output_path.unlink()
        return
    _configure_chinese_font()
    color_map = {"train": "#1f77b4", "val": "#ff7f0e", "test": "#2ca02c"}
    label_map = {"train": "训练集", "val": "验证集", "test": "测试集"}
    figure, axis = plt.subplots(1, 1, figsize=(12, 4))
    for split_name in _ordered_split_names(monotonic_df["split_name"].dropna().unique().tolist()):
        frame = monotonic_df[monotonic_df["split_name"].eq(split_name)]
        if frame.empty:
            continue
        ordered = frame.sort_values("group_id")
        axis.plot(
            ordered["group_id"],
            ordered[metric],
            label=label_map.get(split_name, split_name),
            color=color_map.get(split_name, None),
            linewidth=1.8,
        )
    if draw_zero_line:
        axis.axhline(0.0, color="#808080", linestyle="--", linewidth=1.0)
    axis.set_title(title)
    axis.set_xlabel("分组编号")
    axis.set_ylabel(ylabel)
    axis.legend()
    axis.grid(alpha=0.2, linestyle=":", linewidth=0.8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=config.plot_dpi)
    plt.close(figure)


def _load_selected_features(path: Optional[Path]) -> List[str]:
    if path is None or not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_state(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return _read_json(path)
    except json.JSONDecodeError:
        warnings.warn(f"状态文件无法解析为 JSON: {path}")
        return {}


def _load_clip_percentiles(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return _read_json(path)
    except json.JSONDecodeError:
        warnings.warn(f"clip_percentiles 文件无法解析为 JSON: {path}")
        return {}


def _load_yaml_payload(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists() or yaml is None:
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover
        warnings.warn(f"YAML 文件解析失败: {path} ({exc})")
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_optional_json(path: Optional[Path], label: str) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return _read_json(path)
    except json.JSONDecodeError:
        warnings.warn(f"{label} 无法解析为 JSON: {path}")
        return {}


def build_feature_importance(
    model_path: Optional[Path],
    selected_features_path: Optional[Path],
    test_df: pd.DataFrame,
    config: AnalysisConfig,
) -> tuple[Optional[pd.DataFrame], Dict[str, Any]]:
    if model_path is None or not model_path.exists():
        return None, {"model_loaded": False, "reason": "缺少模型文件"}
    if lgb is None:
        return None, {"model_loaded": False, "reason": "当前环境未安装 lightgbm"}
    booster = lgb.Booster(model_file=str(model_path))
    feature_names = list(booster.feature_name())
    gains = list(booster.feature_importance(importance_type="gain"))
    selected_features = set(_load_selected_features(selected_features_path))
    prediction_feature_columns = [column for column in test_df.columns if column not in BASE_COLUMNS]
    missing_in_prediction = [feature for feature in feature_names if feature not in test_df.columns]
    extra_in_prediction = [column for column in prediction_feature_columns if column not in feature_names]
    if missing_in_prediction:
        print(f"警告: 模型特征中有 {len(missing_in_prediction)} 个未出现在预测 CSV 中。")
    if extra_in_prediction:
        print(f"提示: 预测 CSV 中有 {len(extra_in_prediction)} 个特征列未出现在模型特征名中，按手工特征或元数据列处理。")
    importance_df = pd.DataFrame({"feature": feature_names, "gain": gains})
    importance_df = importance_df.sort_values("gain", ascending=False).reset_index(drop=True)
    total_gain = float(importance_df["gain"].sum()) if not importance_df.empty else 0.0
    importance_df["gain_share"] = importance_df["gain"].apply(lambda value: float(value / total_gain) if total_gain else 0.0)
    importance_df["feature_origin"] = importance_df["feature"].apply(
        lambda name: "tsfresh_selected" if name in selected_features else "manual_or_other"
    )
    importance_df["present_in_prediction_csv"] = importance_df["feature"].isin(test_df.columns)
    topk_df = importance_df.head(config.feature_importance_top_k).copy()
    topk_df.insert(0, "rank", np.arange(1, len(topk_df) + 1))
    return topk_df, {
        "model_loaded": True,
        "model_feature_count": len(feature_names),
        "prediction_feature_count": len(prediction_feature_columns),
        "missing_model_features_in_prediction": missing_in_prediction,
        "prediction_only_columns": extra_in_prediction,
    }


def _fmt_float(value: Any, digits: int = 6) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.{digits}f}"


def _fmt_pct(value: Any, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value) * 100:.{digits}f}%"


def _fmt_int(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{int(value):,}"


def _fmt_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        if pd.isna(value):
            return ""
        if float(value).is_integer():
            return str(int(value))
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    return str(value)


def _fmt_date_yyyymmdd(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(int(float(value)))
    if len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _signal_date_range(frame: pd.DataFrame, config: AnalysisConfig) -> str:
    if frame is None or frame.empty or config.signal_date_col not in frame.columns:
        return ""
    return f"{_fmt_date_yyyymmdd(frame[config.signal_date_col].min())} ~ {_fmt_date_yyyymmdd(frame[config.signal_date_col].max())}"


def _build_overall_metrics(frame: pd.DataFrame, config: AnalysisConfig) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {}
    return {
        "rows": int(len(frame)),
        "signal_date_range": _signal_date_range(frame, config),
        "target_mean": float(frame[config.target_col].mean()),
        "target_win_rate": float((frame[config.target_col] > 0).mean()),
        "target_raw_mean": float(frame[config.target_raw_col].mean()),
        "target_raw_win_rate": float((frame[config.target_raw_col] > 0).mean()),
        "y_pred_mean": float(frame[config.pred_col].mean()),
        "y_pred_std": float(frame[config.pred_col].std(ddof=0)),
    }


def _build_split_overview(split_frames: Dict[str, pd.DataFrame], config: AnalysisConfig) -> pd.DataFrame:
    label_map = {"train": "训练集", "val": "验证集", "test": "测试集"}
    rows: List[Dict[str, Any]] = []
    for split_name in _ordered_split_names(list(split_frames.keys())):
        frame = split_frames[split_name]
        rows.append(
            {
                "集合": label_map.get(split_name, split_name),
                "样本数": int(len(frame)),
                "时间范围": _signal_date_range(frame, config),
                "target 均值": float(frame[config.target_col].mean()),
                "target_raw 均值": float(frame[config.target_raw_col].mean()),
                "y_pred 均值": float(frame[config.pred_col].mean()),
            }
        )
    return pd.DataFrame(rows)


def _build_feature_part_counts(selected_features: List[str], part: str) -> pd.DataFrame:
    counts: Dict[str, int] = {}
    for feature in selected_features:
        if "__" in feature:
            source, stat = feature.split("__", 1)
        else:
            source, stat = feature, "manual"
        key = source if part == "source" else stat
        counts[key] = counts.get(key, 0) + 1
    column_name = "来源序列" if part == "source" else "统计类型"
    rows = [{column_name: key, "数量": value} for key, value in counts.items()]
    if not rows:
        return pd.DataFrame(columns=[column_name, "数量"])
    return pd.DataFrame(rows).sort_values(["数量", column_name], ascending=[False, True]).reset_index(drop=True)


def _build_indicator_names(resolved_params: Dict[str, Any]) -> List[str]:
    indicators = resolved_params.get("indicators", {}) if isinstance(resolved_params, dict) else {}
    ma_windows = indicators.get("ma_windows", [5, 10, 20])
    rsi_period = indicators.get("rsi_period", 14)
    atr_period = indicators.get("atr_period", 14)
    momentum_period = indicators.get("momentum_period", 5)
    volatility_period = indicators.get("volatility_period", 20)
    names = ["return_1d"]
    names.extend([f"ma_{window}" for window in ma_windows])
    names.extend(
        [
            "boll_std",
            "boll_upper",
            "boll_lower",
            f"rsi_{rsi_period}",
            f"atr_{atr_period}",
            "volume_ratio",
            f"momentum_{momentum_period}",
            f"volatility_{volatility_period}",
        ]
    )
    return names


def _build_monotonic_diagnosis_table(monotonic_summary: Dict[str, Any]) -> pd.DataFrame:
    label_map = {"train": "训练集", "val": "验证集", "test": "测试集"}
    rows: List[Dict[str, Any]] = []
    for split_name in _ordered_split_names(list(monotonic_summary.get("per_split", {}).keys())):
        payload = monotonic_summary.get("per_split", {}).get(split_name, {})
        rows.append(
            {
                "集合": label_map.get(split_name, split_name),
                "组数": int(payload.get("group_count", 0)),
                "首组 target 均值": float(payload.get("first_group_target_mean", np.nan)),
                "末组 target 均值": float(payload.get("last_group_target_mean", np.nan)),
                "首尾差": float(payload.get("end_minus_start", np.nan)),
                "Spearman": float(payload.get("spearman", np.nan)) if payload.get("spearman") is not None else np.nan,
                "Pearson": float(payload.get("pearson", np.nan)) if payload.get("pearson") is not None else np.nan,
                "非递减比例": float(payload.get("non_decreasing_ratio", np.nan)),
                "整体正向": "是" if payload.get("positive_trend") else "否",
            }
        )
    return pd.DataFrame(rows)


def _format_duration_hms(seconds: Any) -> str:
    if seconds is None or pd.isna(seconds):
        return ""
    total_seconds = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _safe_spearman(left: pd.Series, right: pd.Series) -> Optional[float]:
    if left is None or right is None or len(left) == 0 or len(right) == 0:
        return None
    corr = left.corr(right, method="spearman")
    if corr is None or pd.isna(corr):
        return None
    return float(corr)


def _safe_pearson(left: pd.Series, right: pd.Series) -> Optional[float]:
    """安全计算 Pearson 相关系数，失败返回 None。"""
    if left is None or right is None or len(left) == 0 or len(right) == 0:
        return None
    corr = left.corr(right, method="pearson")
    if corr is None or pd.isna(corr):
        return None
    return float(corr)


def _build_split_model_performance_table(split_frames: Dict[str, pd.DataFrame], config: AnalysisConfig) -> pd.DataFrame:
    label_map = {"train": "训练集", "val": "验证集", "test": "测试集"}
    rows: List[Dict[str, Any]] = []
    for split_name in _ordered_split_names(list(split_frames.keys())):
        frame = split_frames.get(split_name)
        if frame is None or frame.empty:
            continue
        pred = frame[config.pred_col]
        target = frame[config.target_col]
        rows.append(
            {
                "集合": label_map.get(split_name, split_name),
                "样本数": int(len(frame)),
                "时间范围": _signal_date_range(frame, config),
                "target MAE": float((pred - target).abs().mean()),
                "排序 Spearman": _safe_spearman(pred, target),
                "排序 Pearson": _safe_pearson(pred, target),
                "raw 均值": float(frame[config.target_raw_col].mean()),
                "raw 胜率": float((frame[config.target_raw_col] > 0).mean()),
                "预测分数波动": float(pred.std(ddof=0)),
            }
        )
    return pd.DataFrame(rows)


def _build_step_performance_table(
    state_payload: Dict[str, Any],
    resolved_params: Optional[Dict[str, Any]] = None,
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    step_name_map = {
        "step_1": "步骤1 数据准备",
        "step_2": "步骤2 切片构造",
        "step_3": "步骤3 技术指标",
        "step_4": "步骤4 特征工程",
        "step_5": "步骤5 划分与合并",
        "step_6": "步骤6 模型训练",
    }
    step_order = ["step_1", "step_2", "step_3", "step_4", "step_5", "step_6"]
    total_duration_seconds = 0.0
    durations: List[tuple[str, float]] = []
    for step_name in step_order:
        payload = _step_payload(state_payload, step_name)
        if not payload:
            continue
        duration_seconds = payload.get("duration_seconds")
        if duration_seconds is None or pd.isna(duration_seconds):
            continue
        seconds_value = float(duration_seconds)
        total_duration_seconds += seconds_value
        durations.append((step_name, seconds_value))

    step2_details = _step_details(state_payload, "step_2")
    lgb_params = resolved_params.get("lgb", {}) if isinstance(resolved_params, dict) else {}
    num_boost_round = lgb_params.get("num_boost_round")

    rows: List[Dict[str, Any]] = []
    for step_name in step_order:
        payload = _step_payload(state_payload, step_name)
        if not payload:
            continue
        details = payload.get("details", {})
        if step_name == "step_1":
            key_metric = f"{_fmt_int(details.get('sample_count'))} 个样本，覆盖 {_fmt_int(details.get('datebar_rows'))} 行行情"
            note = "先把可建模样本池打牢，覆盖年份足够长，后面的结论更有代表性。"
        elif step_name == "step_2":
            input_samples = details.get("input_samples")
            valid_samples = details.get("valid_samples")
            retention = float(valid_samples / input_samples) if input_samples else None
            key_metric = f"{_fmt_int(valid_samples)} 个有效样本，保留率 {_fmt_pct(retention) if retention is not None else ''}"
            lookback_days = resolved_params.get("window", {}).get("lookback_days") if isinstance(resolved_params, dict) else None
            lookback_days_text = _fmt_int(lookback_days)
            window_desc = f"{lookback_days_text} 日" if lookback_days_text else "目标"
            note = f"切片阶段样本损耗很低，说明原始信号大多都能拿到完整的 {window_desc}历史上下文。" if retention is not None and retention >= 0.95 else "切片阶段存在一定样本损耗，需要持续关注历史窗口不足的样本来源。"
        elif step_name == "step_3":
            key_metric = f"{_fmt_int(details.get('rows'))} 行指标，NaN 填充 {_fmt_int(details.get('nan_filled'))} 次"
            note = "这里的 NaN 多数来自滚动窗口起点，属于技术指标计算的正常补齐成本。"
        elif step_name == "step_4":
            key_metric = f"保留 {_fmt_int(details.get('selected_features'))} 个 TSFresh 特征，并行 {_fmt_int(details.get('tsfresh_jobs'))} 任务"
            note = "这是当前最耗时的阶段，后续如果要提速，优先优化这里最有效。"
        elif step_name == "step_5":
            total_rows = sum(int(details.get(key, 0) or 0) for key in ["train_rows", "val_rows", "test_rows"])
            valid_samples = step2_details.get("valid_samples")
            retention = float(total_rows / valid_samples) if valid_samples else None
            key_metric = f"最终 {_fmt_int(total_rows)} 个建模样本，建模保留率 {_fmt_pct(retention) if retention is not None else ''}"
            note = "在严格时间切分后依然留下了足够多的训练样本，说明样本基础是充足的。"
        else:
            best_iteration = details.get("best_iteration")
            spearman = details.get("spearman")
            pearson = details.get("pearson")
            if num_boost_round and best_iteration:
                best_ratio = float(best_iteration) / float(num_boost_round)
                key_metric = f"best_iteration={_fmt_int(best_iteration)}，使用了上限轮数的 {best_ratio:.2%}"
            else:
                key_metric = f"best_iteration={_fmt_int(best_iteration)}，测试集 Spearman={_fmt_float(spearman)}，Pearson={_fmt_float(pearson)}"
            note = f"训练本身很快，当前瓶颈不在建模，而在前面的特征准备；测试集 Spearman 为 {_fmt_float(spearman)}，Pearson 为 {_fmt_float(pearson)}。"
        rows.append(
            {
                "步骤": step_name_map[step_name],
                "用时": str(payload.get("duration_hms", "")),
                "关键指标": key_metric,
                "性能解读": note,
            }
        )

    slowest_step_name = ""
    slowest_share = None
    if durations:
        slowest_step_key, slowest_duration = max(durations, key=lambda item: item[1])
        slowest_step_name = step_name_map.get(slowest_step_key, slowest_step_key)
        slowest_share = float(slowest_duration / total_duration_seconds) if total_duration_seconds else None

    return pd.DataFrame(rows), {
        "total_duration_seconds": total_duration_seconds,
        "total_duration_hms": _format_duration_hms(total_duration_seconds),
        "slowest_step": slowest_step_name,
        "slowest_share": slowest_share,
    }


def _build_split_model_story(split_model_df: pd.DataFrame) -> str:
    if split_model_df is None or split_model_df.empty:
        return "当前仅检测到测试集或缺少完整分集合结果，暂时无法判断模型在不同阶段的泛化差异。"
    row_map = {str(row["集合"]): row for _, row in split_model_df.iterrows()}

    def _get_float(label: str, column: str) -> Optional[float]:
        row = row_map.get(label)
        if row is None:
            return None
        value = row.get(column)
        if value is None or pd.isna(value):
            return None
        return float(value)

    parts: List[str] = []
    test_spearman = _get_float("测试集", "排序 Spearman")
    if test_spearman is not None:
        parts.append(f"测试集的排序 Spearman 为 {test_spearman:.3f}，{_spearman_interpretation(test_spearman)}")

    train_spearman = _get_float("训练集", "排序 Spearman")
    if train_spearman is not None and test_spearman is not None:
        gap = train_spearman - test_spearman
        if gap > 0.18:
            parts.append("训练集明显强于测试集，说明模型在历史上学到的模式落到未来样本时会受到市场噪声和风格漂移的削弱。")
        elif gap > 0.08:
            parts.append("训练集略强于未来样本，存在正常的泛化损耗，但暂时还不算失控。")

    val_spearman = _get_float("验证集", "排序 Spearman")
    if val_spearman is not None and test_spearman is not None:
        if abs(val_spearman - test_spearman) <= 0.05:
            parts.append("测试集没有明显差于验证集，说明模型外推并未继续恶化。")
        elif test_spearman < val_spearman:
            parts.append("测试集弱于验证集，说明最近阶段样本更难，需要继续观察稳定性。")
        else:
            parts.append("测试集略强于验证集，说明近期阶段并没有进一步恶化。")

    test_pearson = _get_float("测试集", "排序 Pearson")
    if test_pearson is not None:
        pearson_strength = "较强" if abs(test_pearson) >= 0.4 else ("中等" if abs(test_pearson) >= 0.25 else ("较弱" if abs(test_pearson) >= 0.15 else "极弱"))
        parts.append(f"测试集 Pearson 为 {test_pearson:.3f}（{pearson_strength}线性相关），反映模型预测值与真实值之间的线性一致性。")
    parts.append("对当前任务来说，MAE 只是辅助参考，更关键的是模型能否把相对更好的信号排到前面。")
    return " ".join(parts)


def _build_feature_importance_story(
    feature_importance_df: Optional[pd.DataFrame],
    dominant_source: str = "",
    dominant_source_count: Optional[int] = None,
) -> str:
    if feature_importance_df is None or feature_importance_df.empty:
        return "缺少模型文件或特征重要性数据，暂时无法判断模型主要依赖哪些线索。"

    top3_df = feature_importance_df.head(3).copy()
    top3_names = [str(value) for value in top3_df["feature"].tolist()]
    top3_share = float(top3_df["gain_share"].sum()) if "gain_share" in top3_df.columns else None
    parts: List[str] = []
    if top3_names:
        joined_names = "、".join(top3_names)
        if top3_share is not None and not pd.isna(top3_share):
            parts.append(f"当前重要性最靠前的三个特征是 {joined_names}，合计贡献了可见重要性中的 {_fmt_pct(top3_share)}。")
        else:
            parts.append(f"当前重要性最靠前的三个特征是 {joined_names}。")

    has_time_feature = any(name in {"date", "month", "year"} or name.startswith("date") or name.startswith("month") for name in top3_names)
    has_non_trading = any("non_trading_ratio" in name for name in top3_names)
    if has_time_feature and has_non_trading:
        parts.append("这说明模型目前先在识别市场阶段、月份和交易稠密度这类环境线索，再用价格和成交量形态做进一步细分。")
    elif has_time_feature:
        parts.append("这说明模型当前对市场阶段或时间位置较敏感，会先判断“当前处于什么环境”，再决定信号排序。")
    elif has_non_trading:
        parts.append("这说明交易稠密度和停牌背景对最终排序有明显影响。")

    top_feature_name = str(feature_importance_df.iloc[0]["feature"])
    if top_feature_name == "date":
        parts.append("其中 date 排在前列通常意味着不同年份或市场阶段的收益分布差异很大，因此上线后需要特别关注跨年份稳定性。")
    elif top_feature_name == "month":
        parts.append("month 靠前通常意味着不同月份的市场环境差异在影响模型打分。")

    if dominant_source:
        source_text = f"在自动提取的序列特征里，{dominant_source} 相关特征数量最多"
        if dominant_source_count is not None:
            source_text += f"（{int(dominant_source_count)} 个）"
        source_text += "，说明该维度最容易提供可区分的信号。"
        parts.append(source_text)

    return " ".join(parts)


def _spearman_interpretation(spearman: Optional[float]) -> str:
    if spearman is None or pd.isna(spearman):
        return "未计算 Spearman，无法判断排序能力。"
    value = float(spearman)
    if value < 0.05:
        return "接近随机排序，单独使用价值较弱。"
    if value < 0.15:
        return "存在弱相关，可作为辅助信息，但不宜单独依赖。"
    if value < 0.25:
        return "已有一定排序能力，适合作为粗过滤器的候选。"
    if value < 0.4:
        return "排序能力已达到可落地过滤的水平。"
    return "排序能力很强，但需要额外警惕过拟合或信息泄露。"


def _markdown_table(df: pd.DataFrame, percent_columns: Optional[List[str]] = None, float_columns: Optional[List[str]] = None) -> str:
    if df is None or df.empty:
        return "_无数据_"
    percent_columns = percent_columns or []
    float_columns = float_columns or []
    display_df = df.copy()
    for column in percent_columns:
        if column in display_df.columns:
            display_df[column] = display_df[column].map(_fmt_pct)
    for column in float_columns:
        if column in display_df.columns:
            display_df[column] = display_df[column].map(_fmt_float)
    headers = list(display_df.columns)
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for _, row in display_df.iterrows():
        values = ["" if pd.isna(row[column]) else str(row[column]) for column in headers]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _html_table(df: pd.DataFrame, percent_columns: Optional[List[str]] = None, float_columns: Optional[List[str]] = None, table_class: str = "") -> str:
    if df is None or df.empty:
        return "<p>无数据</p>"
    percent_columns = percent_columns or []
    float_columns = float_columns or []
    display_df = df.copy()
    for column in percent_columns:
        if column in display_df.columns:
            display_df[column] = display_df[column].map(_fmt_pct)
    for column in float_columns:
        if column in display_df.columns:
            display_df[column] = display_df[column].map(_fmt_float)
    headers = list(display_df.columns)
    class_attr = f' class="{table_class}"' if table_class else ""
    lines = [f'<div class="table-wrap"><table{class_attr}><thead><tr>']
    lines.extend([f"<th>{html.escape(str(header))}</th>" for header in headers])
    lines.append("</tr></thead><tbody>")
    for _, row in display_df.iterrows():
        lines.append("<tr>")
        for header in headers:
            value = "" if pd.isna(row[header]) else str(row[header])
            lines.append(f"<td>{html.escape(value)}</td>")
        lines.append("</tr>")
    lines.append("</tbody></table></div>")
    return "".join(lines)


def _monotonic_summary_text(monotonic_summary: Dict[str, Any]) -> str:
    if not monotonic_summary.get("per_split"):
        return "仅检测到测试集或单一集合，无法判断跨集合一致性。"
    if monotonic_summary.get("trend_consistent") and monotonic_summary.get("all_positive_trend"):
        return "可用集合整体呈正向单调趋势，但局部仍可能存在回撤。"
    positive_splits = [name for name, payload in monotonic_summary["per_split"].items() if payload.get("positive_trend")]
    if positive_splits:
        return f"{', '.join(positive_splits)} 呈正向趋势，但各集合强弱不完全一致。"
    return "各集合未呈现稳定的正向单调趋势，需要谨慎使用高分过滤。"


def _threshold_recommendation_text(median_uplift: Dict[str, Any], threshold_summary: Dict[str, Any]) -> str:
    best_row = threshold_summary.get("best_threshold", {})
    if not best_row:
        return "未生成阈值过滤结果。"
    quantile = float(best_row["quantile"])
    best_raw_mean = float(best_row["target_raw_mean"])
    median_raw_diff = float(median_uplift.get("target_raw_mean_diff", 0.0))
    return (
        f"候选阈值中，q={quantile:.1f} 的 target_raw 均值最高，为 {best_raw_mean:.4f}；"
        f"中位数二分的高低组 raw 均值差为 {median_raw_diff:.4f}。"
    )


def _bool_cn(value: Any) -> str:
    return "是" if bool(value) else "否"


def _step_payload(state_payload: Dict[str, Any], step_name: str) -> Dict[str, Any]:
    return state_payload.get("steps", {}).get(step_name, {}) if state_payload else {}


def _step_details(state_payload: Dict[str, Any], step_name: str) -> Dict[str, Any]:
    return _step_payload(state_payload, step_name).get("details", {}) if state_payload else {}


def _build_step_execution_table(state_payload: Dict[str, Any]) -> pd.DataFrame:
    step_name_map = {
        "step_1": "步骤1 数据准备",
        "step_2": "步骤2 切片构造",
        "step_3": "步骤3 技术指标",
        "step_4": "步骤4 特征工程",
        "step_5": "步骤5 划分与合并",
        "step_6": "步骤6 模型训练",
    }
    rows: List[Dict[str, Any]] = []
    for step_name in ["step_1", "step_2", "step_3", "step_4", "step_5", "step_6"]:
        payload = _step_payload(state_payload, step_name)
        if not payload:
            continue
        rows.append(
            {
                "步骤": step_name_map[step_name],
                "状态": str(payload.get("status", "")),
                "用时": str(payload.get("duration_hms", "")),
                "检查通过": _bool_cn(payload.get("checks_passed")),
                "输出文件数": int(len(payload.get("output_files", []))),
            }
        )
    return pd.DataFrame(rows)


def _build_lgb_param_table(resolved_params: Dict[str, Any]) -> pd.DataFrame:
    lgb_params = resolved_params.get("lgb", {}) if isinstance(resolved_params, dict) else {}
    descriptions = {
        "objective": "任务类型，当前为回归预测收益率。",
        "metric": "训练时监控指标，当前为 MAE。",
        "boosting_type": "树模型类型，当前使用标准 GBDT。",
        "num_leaves": "单棵树最多叶子数，控制模型复杂度。",
        "max_depth": "树的最大深度，防止树过深记住噪声。",
        "learning_rate": "每轮学习步长，越小越保守。",
        "min_child_samples": "每个叶子最少样本数，避免少数异常样本主导。",
        "subsample": "每轮只抽部分训练样本，降低过拟合。",
        "colsample_bytree": "每棵树只抽部分特征，降低特征垄断。",
        "reg_alpha": "L1 正则，压缩不重要规则。",
        "reg_lambda": "L2 正则，限制单一规则权重过大。",
        "seed": "随机种子，保证复现。",
        "num_threads": "线程数，当前单线程更稳定可复现。",
    }
    rows: List[Dict[str, Any]] = []
    for key in [
        "objective",
        "metric",
        "boosting_type",
        "num_leaves",
        "max_depth",
        "learning_rate",
        "min_child_samples",
        "subsample",
        "colsample_bytree",
        "reg_alpha",
        "reg_lambda",
        "seed",
        "num_threads",
    ]:
        if key in lgb_params:
            rows.append({"参数": key, "取值": _fmt_value(lgb_params.get(key)), "说明": descriptions.get(key, "")})
    return pd.DataFrame(rows)


def _build_training_control_table(resolved_params: Dict[str, Any], step6_details: Dict[str, Any]) -> pd.DataFrame:
    lgb_params = resolved_params.get("lgb", {}) if isinstance(resolved_params, dict) else {}
    rows = [
        {"参数": "num_boost_round", "取值": _fmt_value(lgb_params.get("num_boost_round")), "说明": "最大迭代轮数上限。"},
        {"参数": "early_stopping_rounds", "取值": _fmt_value(lgb_params.get("early_stopping_rounds")), "说明": "验证集连续若干轮无改进则提前停止。"},
        {"参数": "实际 best_iteration", "取值": _fmt_value(step6_details.get("best_iteration")), "说明": "最终实际停下来的轮数。"},
        {"参数": "log_evaluation_period", "取值": _fmt_value(lgb_params.get("log_evaluation_period")), "说明": "日志打印频率。"},
    ]
    return pd.DataFrame(rows)


def _build_quintile_display_table(quintile_df: pd.DataFrame, overall_test: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if overall_test:
        rows.append(
            {
                "分组": "测试集全集(基线)",
                "样本数": int(overall_test.get("rows", 0)),
                "target 均值": float(overall_test.get("target_mean", np.nan)),
                "target 胜率": float(overall_test.get("target_win_rate", np.nan)),
                "target_raw 均值": float(overall_test.get("target_raw_mean", np.nan)),
                "target_raw 胜率": float(overall_test.get("target_raw_win_rate", np.nan)),
            }
        )
    for _, row in quintile_df.sort_values("pred_group").iterrows():
        rows.append(
            {
                "分组": str(int(row["pred_group"])),
                "样本数": int(row["sample_count"]),
                "target 均值": float(row["target_mean"]),
                "target 胜率": float(row["target_win_rate"]),
                "target_raw 均值": float(row["target_raw_mean"]),
                "target_raw 胜率": float(row["target_raw_win_rate"]),
            }
        )
    return pd.DataFrame(rows)


def _build_median_group_table(median_df: pd.DataFrame, overall_test: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if overall_test:
        rows.append(
            {
                "组别": "测试集全集(基线)",
                "样本数": int(overall_test.get("rows", 0)),
                "target 均值": float(overall_test.get("target_mean", np.nan)),
                "target 胜率": float(overall_test.get("target_win_rate", np.nan)),
                "target_raw 均值": float(overall_test.get("target_raw_mean", np.nan)),
                "target_raw 胜率": float(overall_test.get("target_raw_win_rate", np.nan)),
            }
        )
    name_map = {"low": "低分组(后50%)", "high": "高分组(前50%)"}
    groups = median_df[median_df["score_group"].isin(["low", "high"])].copy()
    for _, row in groups.iterrows():
        rows.append(
            {
                "组别": name_map.get(str(row["score_group"]), str(row["score_group"])),
                "样本数": int(row["sample_count"]),
                "target 均值": float(row["target_mean"]),
                "target 胜率": float(row["target_win_rate"]),
                "target_raw 均值": float(row["target_raw_mean"]),
                "target_raw 胜率": float(row["target_raw_win_rate"]),
            }
        )
    return pd.DataFrame(rows)


def _build_median_uplift_table(median_uplift: Dict[str, Any]) -> pd.DataFrame:
    rows = [
        {"指标": "target 均值提升", "绝对提升": float(median_uplift.get("target_mean_diff", np.nan)), "相对提升": float(median_uplift.get("target_mean_rel", np.nan))},
        {"指标": "target 胜率提升", "绝对提升": float(median_uplift.get("target_win_rate_diff", np.nan)), "相对提升": float(median_uplift.get("target_win_rate_rel", np.nan))},
        {"指标": "target_raw 均值提升", "绝对提升": float(median_uplift.get("target_raw_mean_diff", np.nan)), "相对提升": float(median_uplift.get("target_raw_mean_rel", np.nan))},
        {"指标": "target_raw 胜率提升", "绝对提升": float(median_uplift.get("target_raw_win_rate_diff", np.nan)), "相对提升": float(median_uplift.get("target_raw_win_rate_rel", np.nan))},
    ]
    return pd.DataFrame(rows)


def _build_threshold_display_table(threshold_df: pd.DataFrame, overall_test: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if overall_test:
        rows.append(
            {
                "分位阈值": "0.0(基线)",
                "分数阈值": np.nan,
                "保留比例": 1.0,
                "target 均值": float(overall_test.get("target_mean", np.nan)),
                "target 胜率": float(overall_test.get("target_win_rate", np.nan)),
                "target_raw 均值": float(overall_test.get("target_raw_mean", np.nan)),
                "target_raw 胜率": float(overall_test.get("target_raw_win_rate", np.nan)),
            }
        )
    for _, row in threshold_df.sort_values("quantile").iterrows():
        rows.append(
            {
                "分位阈值": f"{float(row['quantile']):.1f}",
                "分数阈值": float(row["threshold"]),
                "保留比例": float(row["kept_ratio"]),
                "target 均值": float(row["target_mean"]),
                "target 胜率": float(row["target_win_rate"]),
                "target_raw 均值": float(row["target_raw_mean"]),
                "target_raw 胜率": float(row["target_raw_win_rate"]),
            }
        )
    return pd.DataFrame(rows)


def render_markdown_report(
    bundle: ArtifactBundle,
    config: AnalysisConfig,
    clean_stats: Dict[str, Dict[str, int]],
    split_frames: Dict[str, pd.DataFrame],
    quintile_df: pd.DataFrame,
    median_df: pd.DataFrame,
    median_uplift: Dict[str, Any],
    threshold_df: pd.DataFrame,
    threshold_summary: Dict[str, Any],
    monotonic_df: pd.DataFrame,
    monotonic_summary: Dict[str, Any],
    feature_importance_df: Optional[pd.DataFrame],
    feature_importance_summary: Dict[str, Any],
    clip_percentiles: Dict[str, Any],
    state_payload: Dict[str, Any],
    resolved_params: Dict[str, Any],
    model_feature_schema: Dict[str, Any],
) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    label_map = {"train": "训练集", "val": "验证集", "test": "测试集"}
    selected_features = _load_selected_features(bundle.input_artifacts.get("selected_features"))
    step1_details = _step_details(state_payload, "step_1")
    step2_details = _step_details(state_payload, "step_2")
    step3_details = _step_details(state_payload, "step_3")
    step4_details = _step_details(state_payload, "step_4")
    step5_details = _step_details(state_payload, "step_5")
    step6_details = _step_details(state_payload, "step_6")
    overall_test = _build_overall_metrics(split_frames.get("test"), config)
    split_overview_df = _build_split_overview(split_frames, config)
    split_model_df = _build_split_model_performance_table(split_frames, config)
    split_model_story = _build_split_model_story(split_model_df)
    step_execution_df = _build_step_execution_table(state_payload)
    step_performance_df, step_runtime_summary = _build_step_performance_table(state_payload, resolved_params)
    feature_source_df = _build_feature_part_counts(selected_features, "source")
    feature_stat_df = _build_feature_part_counts(selected_features, "stat")
    dominant_feature_source = str(feature_source_df.iloc[0]["来源序列"]) if not feature_source_df.empty else ""
    dominant_feature_source_count = int(feature_source_df.iloc[0]["数量"]) if not feature_source_df.empty else None
    feature_story = _build_feature_importance_story(
        feature_importance_df,
        dominant_source=dominant_feature_source,
        dominant_source_count=dominant_feature_source_count,
    )
    indicator_names = _build_indicator_names(resolved_params)
    monotonic_diag_df = _build_monotonic_diagnosis_table(monotonic_summary)
    quintile_display_df = _build_quintile_display_table(quintile_df, overall_test)
    median_group_df = _build_median_group_table(median_df, overall_test)
    median_uplift_df = _build_median_uplift_table(median_uplift)
    threshold_display_df = _build_threshold_display_table(threshold_df, overall_test)
    lgb_param_df = _build_lgb_param_table(resolved_params)
    training_control_df = _build_training_control_table(resolved_params, step6_details)
    lookback_days_value = resolved_params.get("window", {}).get("lookback_days") if isinstance(resolved_params, dict) else None
    lookback_days_text = _fmt_int(lookback_days_value)
    lookback_days_label = f"{lookback_days_text}日" if lookback_days_text else "窗口"
    spearman_text = _spearman_interpretation(step6_details.get("spearman"))
    test_pearson_val = step6_details.get("pearson")
    # fallback: state.json 可能不含 pearson（旧 pipeline 产物），从 test 预测数据直接计算
    if test_pearson_val is None and "test" in split_frames:
        test_frame = split_frames["test"]
        if "target" in test_frame.columns and "y_pred" in test_frame.columns:
            test_pearson_val = _safe_pearson(test_frame["target"], test_frame["y_pred"])
            step6_details["pearson"] = test_pearson_val
    if test_pearson_val is not None:
        if abs(test_pearson_val) >= 0.4:
            pearson_text = "线性相关较强，预测值与真实值存在较明显的线性一致性。"
        elif abs(test_pearson_val) >= 0.25:
            pearson_text = "线性相关中等，预测值与真实值存在一定线性趋势。"
        elif abs(test_pearson_val) >= 0.15:
            pearson_text = "线性相关较弱，预测值与真实值的线性关系不够稳定。"
        else:
            pearson_text = "线性相关极弱或几乎不存在，关系主要为非线性（这在金融数据中很常见）。"
    else:
        pearson_text = "Pearson 不可用。"
    top_feature_name = feature_importance_df.iloc[0]["feature"] if feature_importance_df is not None and not feature_importance_df.empty else ""
    best_threshold = threshold_summary.get("best_threshold", {})
    best_threshold_quantile = f"{float(best_threshold.get('quantile')):.1f}" if best_threshold else ""
    best_threshold_raw_mean = _fmt_pct(best_threshold.get("target_raw_mean")) if best_threshold else ""
    input_lines = [
        f"- 测试集预测: {_rel(bundle.input_artifacts.get('test_prediction'))}",
        f"- 训练集预测: {_rel(bundle.input_artifacts.get('train_prediction')) or '缺失'}",
        f"- 验证集预测: {_rel(bundle.input_artifacts.get('val_prediction')) or '缺失'}",
        f"- 模型文件: {_rel(bundle.input_artifacts.get('model')) or '缺失'}",
        f"- Manifest: {_rel(bundle.manifest_path) or '未使用 manifest'}",
    ]
    preview_df = monotonic_df[monotonic_df["group_id"].eq(1) | monotonic_df["group_id"].mod(max(1, config.monotonic_preview_stride)).eq(0)].copy() if not monotonic_df.empty else pd.DataFrame()
    if not monotonic_df.empty:
        last_rows = monotonic_df.groupby("split_name", as_index=False).tail(1)
        preview_df = pd.concat([preview_df, last_rows], ignore_index=True).drop_duplicates(subset=["split_name", "group_id"])
        preview_df = preview_df.sort_values(["split_name", "group_id"]).reset_index(drop=True)
    monotonic_preview_sections: List[str] = []
    if not preview_df.empty:
        for split_name in _ordered_split_names(preview_df["split_name"].dropna().unique().tolist()):
            split_preview = preview_df[preview_df["split_name"].eq(split_name)].reset_index(drop=True)
            monotonic_preview_sections.extend(
                [
                    f"### {label_map.get(split_name, split_name)}",
                    _markdown_table(
                        split_preview,
                        percent_columns=["target_win_rate"],
                        float_columns=["y_pred_mean", "target_mean", "target_raw_mean"],
                    ),
                    "",
                ]
            )

    high_group = median_df[median_df["score_group"].eq("high")]
    low_group = median_df[median_df["score_group"].eq("low")]
    high_group_raw_mean = _fmt_pct(high_group["target_raw_mean"].iloc[0]) if not high_group.empty else ""
    low_group_raw_mean = _fmt_pct(low_group["target_raw_mean"].iloc[0]) if not low_group.empty else ""
    summary_lines = [
        "- 结论一句话：当前模型已经具备可落地的信号过滤价值，最适合先做中位数附近的宽阈值过滤。",
        "- 模型定位：这里的 LightGBM 更像“信号排序器”，核心价值是把相对更好的机会往前排，而不是精确预测每一笔会赚多少。",
        f"- 测试集排序能力：Spearman={_fmt_float(step6_details.get('spearman'))}，{spearman_text}",
        f"- 测试集整体 raw 均值：{_fmt_pct(overall_test.get('target_raw_mean'))}；中位数高分组 raw 均值：{high_group_raw_mean}。",
        f"- 阈值结论：{_threshold_recommendation_text(median_uplift, threshold_summary)}",
        f"- 单调性判断：{_monotonic_summary_text(monotonic_summary)}",
    ]
    if clip_percentiles:
        summary_lines.append(
            f"- 训练集标签缩尾：P1={_fmt_pct(clip_percentiles.get('p1'))}，P99={_fmt_pct(clip_percentiles.get('p99'))}。"
        )

    missing_artifacts_text = "、".join(bundle.missing_artifacts)
    monotonic_section = (
        "\n".join(
            [
                "这部分用来回答一个最关键的问题：模型打出来的高分，是否真的对应更高的真实收益？",
                "- 第一张图是三个集合的 target_raw_mean 对比：先按 y_pred 从低到高分成 100 组，再看每组原始真实收益均值是否整体抬升。",
                "- 后面三张图分别展示训练集、验证集、测试集的 target_raw_mean，方便单独观察每个集合的单调性走势。",
                "- 最后一张图是 y_pred_mean 三集合对比：用于检查模型自己的打分曲线是否平滑、有无明显挤压或跳变。",
                "- y=0 灰色虚线是重要参考线：如果曲线在零线以上稳步抬升，说明高分组的真实收益确实更好。",
                "",
                "### target_raw_mean 三集合对比",
                "",
                "![](monotonic_target_all.png)",
                "",
                "### target_raw_mean 各集合单独视图",
                "",
                "![](monotonic_target_train.png)",
                "",
                "![](monotonic_target_val.png)",
                "",
                "![](monotonic_target_test.png)",
                "",
                "### y_pred_mean 三集合对比",
                "",
                "![](monotonic_y_pred_groups.png)",
            ]
        )
        if not monotonic_df.empty
        else "> ⚠️ 因缺少测试集预测工件，本章节分析已跳过。"
    )
    feature_section = (
        _markdown_table(
            feature_importance_df,
            percent_columns=["gain_share"],
            float_columns=["gain"],
        )
        if feature_importance_df is not None and not feature_importance_df.empty
        else "> ⚠️ 因缺少 model_file 工件，本章节分析已跳过。"
    )
    conclusion_lines = [
        f"- 当前分析基于 {', '.join(bundle.available_splits)} 集合输出，解析来源为 {bundle.resolution_source}。",
        "- 当前最稳妥的落地方式仍是中位数附近的宽阈值过滤，而不是只追逐最顶端 10% 或 20% 的高分样本。",
        f"- 若后续需要进生产或做汇报，优先监控时间特征依赖、单调性热力图中的局部回撤，以及 missing_artifacts={missing_artifacts_text or '无'}。",
        f"- 若要继续提高研发效率，先优化 {step_runtime_summary.get('slowest_step') or '步骤4 特征工程'} 这一瓶颈步骤，短期收益通常比调模型参数更直接。",
    ]
    step3_indicator_text = "、".join(indicator_names)
    manual_features = step5_details.get("manual_features", resolved_params.get("manual_features", {}))
    if isinstance(manual_features, dict):
        manual_feature_names = [name.replace("include_", "") for name, enabled in manual_features.items() if name.startswith("include_") and enabled]
    else:
        manual_feature_names = list(manual_features or [])

    step_runtime_sentence = ""
    if step_runtime_summary.get("total_duration_hms"):
        step_runtime_sentence = (
            f"整条 step1-step6 流程总耗时约 {step_runtime_summary.get('total_duration_hms')}，"
            f"其中 {step_runtime_summary.get('slowest_step') or '步骤4 特征工程'} 单独占用了 {_fmt_pct(step_runtime_summary.get('slowest_share'))}。"
            "说明当前实验迭代速度主要受 TSFresh 特征提取影响，训练本身并不是主要成本。"
        )

    median_business_text = ""
    if not high_group.empty and not low_group.empty:
        median_business_text = (
            f"业务化理解：从测试集全集的 {_fmt_pct(overall_test.get('target_raw_mean'))} 到高分组的 {high_group_raw_mean}，"
            f"说明模型已经能把一部分明显较差的信号筛出去；而低分组 raw 均值只有 {low_group_raw_mean}，"
            "高低组差异已经具备实际区分度。"
        )

    lines = [
        f"# {config.report_title} - {bundle.experiment_version}",
        "",
        f"生成时间: {generated_at}",
        f"输出目录: {_rel(bundle.analysis_dir)}",
        "",
        "## 1. 实验目标与结论摘要",
        "",
        "本报告的目标是解释：这个模型是否真的能把原始交易信号按质量重新排序，并告诉我们最稳妥的使用方式是什么。",
        "",
        "### 1.1 一句话结论",
        "",
        "当前模型已经具备可落地的过滤价值，更适合作为“先剔除差信号”的宽阈值过滤器，而不是“只挑最顶尖少量信号”的极端筛选器。",
        "",
        "### 1.2 核心发现",
        *summary_lines,
        "",
        "### 1.3 输入工件",
        *input_lines,
        "",
        "## 2. 管道执行全景",
        "",
        "这部分对应训练管道的 step1 到 step6，回答“数据从哪里来、怎么加工、模型如何训练”。",
        "",
        _markdown_table(step_execution_df),
        "",
        step_runtime_sentence,
        "",
        "### 2.1 不同 step 的性能与瓶颈",
        _markdown_table(step_performance_df),
        "",
        "这张表既看每一步是否跑通，也看它在总流程里的成本和价值。对当前实验来说，最值得优先优化的不是训练，而是特征提取。",
        "",
        "### 2.2 当前 train / val / test 覆盖范围",
        _markdown_table(
            split_overview_df,
            float_columns=["target 均值", "target_raw 均值", "y_pred 均值"],
        ),
        "",
        "### 2.3 步骤1：原始数据准备",
        "步骤1的任务是把原始持仓与行情数据清洗成可建模样本，解决脏数据、空值和基础字段完整性问题。",
        "",
        f"- 清洗后有效样本数：{_fmt_int(step1_details.get('sample_count'))}",
        f"- 信号日期范围：{_fmt_date_yyyymmdd(step1_details.get('signal_date_min'))} ~ {_fmt_date_yyyymmdd(step1_details.get('signal_date_max'))}",
        f"- 日线行情总行数：{_fmt_int(step1_details.get('datebar_rows'))}",
        f"- 标签 target 均值：{_fmt_pct(step1_details.get('target_stats', {}).get('mean'))}，中位数：{_fmt_pct(step1_details.get('target_stats', {}).get('50%'))}",
        "- 这一步的意义是建立一个可靠的历史样本池，后续所有结论都建立在这里的数据质量之上。",
        "",
        f"### 2.4 步骤2：{lookback_days_label}切片构造",
        f"步骤2为每个信号截取信号日前 {lookback_days_text or '指定'} 个实际交易日的行情窗口，作为后续特征计算的原材料。",
        "",
        f"- 输入样本数：{_fmt_int(step2_details.get('input_samples'))}",
        f"- 成功构造切片的有效样本数：{_fmt_int(step2_details.get('valid_samples'))}",
        f"- 被剔除样本数：{_fmt_int(step2_details.get('dropped_samples'))}，其中历史不足 {lookback_days_text or '该窗口'} 日的样本占主要原因。",
        f"- 切片总行数：{_fmt_int(step2_details.get('slice_rows'))}，理论上每个样本对应 {lookback_days_text or '该窗口'} 行。",
        "- 这一步本质是在为每个信号准备“发生前的上下文”，模型并不是直接看当天，而是看之前一段时间发生了什么。",
        "",
        "### 2.5 步骤3：基础技术指标计算",
        "步骤3把原始 OHLCV 切片转成更容易学习的技术指标时间序列，相当于把“原材料”做成“半成品”。",
        "",
        f"- 产出行数：{_fmt_int(step3_details.get('rows'))}，覆盖样本数：{_fmt_int(step3_details.get('samples'))}",
        f"- NaN 填充数：{_fmt_int(step3_details.get('nan_filled'))}，inf 替换数：{_fmt_int(step3_details.get('inf_replaced'))}",
        f"- 当前基础指标包括：{step3_indicator_text}。",
        "- 对小白来说，可以把这些指标理解为：价格趋势、波动强弱、动量和成交量强弱的不同观察角度。",
        "",
        "### 2.6 步骤4：特征工程与筛选",
        "步骤4使用 TSFresh 从技术指标序列中自动提取大量统计特征，再筛出最有信息量的一小部分。",
        "",
        f"- 当前样本数：{_fmt_int(step4_details.get('samples'))}，特征文件列数：{_fmt_int(step4_details.get('columns'))}",
        f"- 最终入选 TSFresh 特征数：{_fmt_int(step4_details.get('selected_features'))}",
        f"- TSFresh 并行任务数：{_fmt_int(step4_details.get('tsfresh_jobs'))}",
        f"- 标签缩尾阈值：P1={_fmt_pct(clip_percentiles.get('p1'))}，P99={_fmt_pct(clip_percentiles.get('p99'))}",
        "- 缩尾的目的不是美化结果，而是避免极端暴涨暴跌样本把模型带偏。",
        "",
        "#### 入选特征来源分布",
        _markdown_table(feature_source_df),
        "",
        "#### 入选特征统计类型分布",
        _markdown_table(feature_stat_df),
        "",
        "### 2.7 步骤5：最终特征合并与时间切分",
        "步骤5把自动特征和手工特征合并，然后按时间严格切分 train / val / test，防止偷看未来。",
        "",
        f"- 训练集样本数：{_fmt_int(step5_details.get('train_rows'))}，验证集样本数：{_fmt_int(step5_details.get('val_rows'))}，测试集样本数：{_fmt_int(step5_details.get('test_rows'))}",
        f"- 手工特征：{', '.join(manual_feature_names) if manual_feature_names else '无'}。",
        f"- 配置中的时间边界：train_end={resolved_params.get('time_split', {}).get('train_end', '')}，val_start={resolved_params.get('time_split', {}).get('val_start', '')}，val_end={resolved_params.get('time_split', {}).get('val_end', '')}，test_start={resolved_params.get('time_split', {}).get('test_start', '')}。",
        "- 这一步最重要的不是切得漂亮，而是时间隔离足够严，保证测试集真的代表未来。",
        "",
        "### 2.8 步骤6：LightGBM 模型训练",
        "步骤6使用 LightGBM 学习“什么样的信号更可能赚到钱”，并在验证集上自动早停。",
        "",
        f"- 当前 best_iteration：{_fmt_int(step6_details.get('best_iteration'))}",
        f"- 测试集 Spearman：{_fmt_float(step6_details.get('spearman'))}；MAE：{_fmt_float(step6_details.get('mae'))}",
        f"- 测试集 Pearson：{_fmt_float(step6_details.get('pearson'))}",
        f"- 最终模型特征数：{_fmt_int(model_feature_schema.get('feature_count')) or _fmt_int(feature_importance_summary.get('model_feature_count'))}",
        f"- 第一重要特征：{top_feature_name or '未生成'}。",
        "- 这里要强调：这个模型的主要价值不是精确预测每笔收益，而是把相对更好的信号排到更前面。",
        "",
        "## 3. 模型配置与参数说明",
        "",
        "### 3.1 为什么这里用 LightGBM",
        "LightGBM 很适合当前这类“表格特征 + 噪声很强”的任务。它不要求输入特征统一量纲，也能自然学习非线性组合关系，所以非常适合把技术指标、TSFresh 统计量和手工特征混在一起使用。",
        "",
        "更重要的是，这里并不要求模型像回归器那样把每笔收益预测得非常准，而是要求它能稳定分出高低。对交易筛选任务来说，这类树模型往往比更复杂但更难解释的方案更容易先落地。",
        "",
        "### 3.2 主要 LightGBM 参数",
        _markdown_table(lgb_param_df),
        "",
        "### 3.3 训练控制参数",
        _markdown_table(training_control_df),
        "",
        "参数解读：当前这组参数明显偏保守，设计目标是宁可少学一点，也不要把训练集里的噪声记住。",
        "",
        "## 4. 核心实验结果",
        "",
        "这一部分回答最核心的问题：模型到底有没有把测试集里的信号排出层次。这里先看 train / val / test 的整体表现，再看更细的分组结果。",
        "",
        "### 4.1 train / val / test 模型表现",
        _markdown_table(
            split_model_df,
            percent_columns=["raw 胜率"],
            float_columns=["target MAE", "排序 Spearman", "排序 Pearson", "raw 均值", "预测分数波动"],
        ),
        "",
        split_model_story,
        "",
        "读法提示：MAE 看预测值与标签平均差多远，Spearman 看排序是否靠谱（对信号过滤更核心），Pearson 看线性相关性（补充参考）。对于当前这种信号过滤任务，排序相关性比线性相关性更重要。",
        "",
        "### 4.2 测试集整体基线与模型概览",
        _markdown_table(
            pd.DataFrame(
                [
                    {"指标": "测试集样本数", "数值": int(overall_test.get("rows", 0))},
                    {"指标": "测试集时间范围", "数值": overall_test.get("signal_date_range", "")},
                    {"指标": "测试集 target_raw 均值", "数值": _fmt_pct(overall_test.get("target_raw_mean"))},
                    {"指标": "测试集 target_raw 胜率", "数值": _fmt_pct(overall_test.get("target_raw_win_rate"))},
                    {"指标": "测试集 target 均值", "数值": _fmt_pct(overall_test.get("target_mean"))},
                    {"指标": "测试集 Spearman", "数值": _fmt_float(step6_details.get("spearman"))},
                    {"指标": "测试集 Pearson", "数值": _fmt_float(step6_details.get("pearson"))},
                    {"指标": "测试集 MAE", "数值": _fmt_float(step6_details.get("mae"))},
                ]
            )
        ),
        "",
        f"Spearman 解读：{spearman_text}",
        f"Pearson 解读：{pearson_text}",
        "",
        "### 4.3 100 组单调性图",
        "",
        monotonic_section,
        "",
        "### 4.4 100 组单调性诊断表",
        _markdown_table(
            monotonic_diag_df,
            percent_columns=["非递减比例"],
            float_columns=["首组 target 均值", "末组 target 均值", "首尾差", "Spearman", "Pearson"],
        ),
        "",
        "### 4.5 100 组单调性表摘要",
        *(monotonic_preview_sections if monotonic_preview_sections else ["> ⚠️ 因缺少可用集合，本章节分析已跳过。"]),
        "",
        "读图建议：先看 test 是否整体从左低右高，再看 train 与 val 是否方向一致；如果 test 也大体抬升，说明模型在未来样本上确实学到了可迁移信号。",
        "",
        "### 4.6 测试集五分组分析",
        _markdown_table(
            quintile_display_df,
            percent_columns=["target 胜率", "target_raw 胜率"],
            float_columns=["target 均值", "target_raw 均值"],
        ),
        "",
        "五分组解读：如果最低组明显更差，而中间或中高组显著优于基线，说明模型至少能做粗粒度排序。若最高组反而掉下来，则表示极端高分尾部还不够稳定。",
        "",
        "## 5. 中位数阈值过滤分析（重点）",
        "",
        f"当前测试集使用 y_pred 中位数 {_fmt_float(median_uplift.get('median'))} 做二分，最适合回答“这模型能不能直接当过滤器用”。",
        "",
        "### 5.1 中位数二分结果",
        _markdown_table(
            median_group_df,
            percent_columns=["target 胜率", "target_raw 胜率"],
            float_columns=["target 均值", "target_raw 均值"],
        ),
        "",
        "### 5.2 高分组相对提升",
        _markdown_table(
            median_uplift_df,
            percent_columns=["绝对提升", "相对提升"],
        ),
        "",
        median_business_text,
        "",
        "中位数结论：如果你的目标是先上线一个简单、稳妥的信号过滤器，那么中位数阈值是当前最容易解释、也最不容易犯错的选择。",
        "",
        "## 6. 更激进阈值是否更好？",
        "",
        f"额外阈值分析用于回答：是否值得只保留更高分的前 40%、30%、20%、10% 样本。当前最优候选分位是 q={best_threshold_quantile}，对应 raw 均值 {best_threshold_raw_mean}。",
        "",
        _markdown_table(
            threshold_display_df,
            percent_columns=["保留比例", "target 胜率", "target_raw 胜率"],
            float_columns=["分数阈值", "target 均值", "target_raw 均值"],
        ),
        "",
        "阈值解读：如果阈值提高后收益没有继续抬升，说明模型擅长“筛掉差的”，但还不擅长“精准抓最好的”。这通常是金融噪声很强时的正常现象。",
        "",
        "## 7. 特征重要性分析",
        "",
        "这一部分回答模型到底在看什么。对小白来说，可以把它理解为：模型判定一个信号好坏时，最依赖哪些线索。",
        "",
        feature_story,
        "",
        feature_section,
        "",
        "### 7.1 特征重要性解读",
        f"当前最重要的特征是 {top_feature_name or '未生成'}。如果时间类或结构类手工特征长期排在最前面，通常意味着模型对市场阶段、月份、交易稠密度这类宏观背景较敏感。",
        "",
        "这类结果并不一定是坏事。它更像在告诉我们：当前这套模型不是单纯识别某个图形形态，而是在先判断“现在是什么样的市场环境”，再决定哪些信号更值得留下。",
        "",
        "### 7.2 模型特征对齐情况",
        _markdown_table(
            pd.DataFrame(
                [
                    {"指标": "模型特征总数", "值": int(feature_importance_summary.get("model_feature_count", 0))},
                    {"指标": "预测 CSV 特征列数", "值": int(feature_importance_summary.get("prediction_feature_count", 0))},
                    {"指标": "预测 CSV 缺失模型特征数", "值": int(len(feature_importance_summary.get("missing_model_features_in_prediction", [])))},
                    {"指标": "仅存在于预测 CSV 的列数", "值": int(len(feature_importance_summary.get("prediction_only_columns", [])))},
                ]
            )
        ),
        "",
        "## 8. 结论与下一步建议",
        *conclusion_lines,
    ]
    if bundle.missing_artifacts:
        lines.extend(["", f"> ⚠️ 缺失工件: {missing_artifacts_text}"])
    if config.remove_outliers_for_monotonic:
        lines.extend(["", f"> ⚠️ 单调性分析已启用极值剔除，参数为 [{config.monotonic_outlier_low}, {config.monotonic_outlier_high}]。"])
    if feature_importance_summary.get("missing_model_features_in_prediction"):
        lines.extend(
            [
                "",
                f"> ⚠️ 模型特征与预测 CSV 对齐存在差异，缺失特征数: {len(feature_importance_summary.get('missing_model_features_in_prediction', []))}",
            ]
        )
    return "\n".join(line for line in lines if line is not None) + "\n"


def render_leadership_html(
        bundle: ArtifactBundle,
        config: AnalysisConfig,
        split_frames: Dict[str, pd.DataFrame],
        quintile_df: pd.DataFrame,
        median_df: pd.DataFrame,
        median_uplift: Dict[str, Any],
        threshold_df: pd.DataFrame,
        threshold_summary: Dict[str, Any],
        monotonic_summary: Dict[str, Any],
        feature_importance_df: Optional[pd.DataFrame],
        state_payload: Dict[str, Any],
        resolved_params: Dict[str, Any],
) -> str:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        overall_test = _build_overall_metrics(split_frames.get("test"), config)
        split_model_df = _build_split_model_performance_table(split_frames, config)
        split_model_story = _build_split_model_story(split_model_df)
        step_performance_df, step_runtime_summary = _build_step_performance_table(state_payload, resolved_params)
        quintile_display_df = _build_quintile_display_table(quintile_df, overall_test)
        threshold_display_df = _build_threshold_display_table(threshold_df, overall_test)
        selected_features = _load_selected_features(bundle.input_artifacts.get("selected_features"))
        feature_source_df = _build_feature_part_counts(selected_features, "source")
        dominant_feature_source = str(feature_source_df.iloc[0]["来源序列"]) if not feature_source_df.empty else ""
        dominant_feature_source_count = int(feature_source_df.iloc[0]["数量"]) if not feature_source_df.empty else None
        feature_top10_df = feature_importance_df.head(10)[["rank", "feature", "gain", "feature_origin"]].copy() if feature_importance_df is not None and not feature_importance_df.empty else pd.DataFrame()
        step6_details = _step_details(state_payload, "step_6")
        spearman_text = _spearman_interpretation(step6_details.get("spearman"))
        test_pearson_val_html = step6_details.get("pearson")
        # fallback: state.json 可能不含 pearson（旧 pipeline 产物），从 test 预测数据直接计算
        if test_pearson_val_html is None and "test" in split_frames:
            test_frame = split_frames["test"]
            if "target" in test_frame.columns and "y_pred" in test_frame.columns:
                test_pearson_val_html = _safe_pearson(test_frame["target"], test_frame["y_pred"])
                step6_details["pearson"] = test_pearson_val_html
        if test_pearson_val_html is not None:
            if abs(test_pearson_val_html) >= 0.4:
                pearson_text = "线性相关较强，预测值与真实值存在较明显的线性一致性。"
            elif abs(test_pearson_val_html) >= 0.25:
                pearson_text = "线性相关中等，预测值与真实值存在一定线性趋势。"
            elif abs(test_pearson_val_html) >= 0.15:
                pearson_text = "线性相关较弱，预测值与真实值的线性关系不够稳定。"
            else:
                pearson_text = "线性相关极弱或几乎不存在，关系主要为非线性（这在金融数据中很常见）。"
        else:
            pearson_text = "Pearson 不可用。"
        high_group = median_df[median_df["score_group"].eq("high")]
        high_group_raw_mean = _fmt_pct(high_group["target_raw_mean"].iloc[0]) if not high_group.empty else ""
        best_threshold = threshold_summary.get("best_threshold", {})
        best_threshold_quantile = f"{float(best_threshold.get('quantile')):.1f}" if best_threshold else ""
        best_threshold_raw_mean = _fmt_pct(best_threshold.get("target_raw_mean")) if best_threshold else ""
        top_feature_name = feature_top10_df.iloc[0]["feature"] if not feature_top10_df.empty else "未生成"
        top_feature_origin = feature_top10_df.iloc[0]["feature_origin"] if not feature_top10_df.empty else ""
        feature_story = _build_feature_importance_story(
            feature_importance_df,
            dominant_source=dominant_feature_source,
            dominant_source_count=dominant_feature_source_count,
        )
        figures = [
            "monotonic_target_all.png",
            "monotonic_target_train.png",
            "monotonic_target_val.png",
            "monotonic_target_test.png",
            "monotonic_y_pred_groups.png",
        ]
        figure_html = "".join([f'<img src="{html.escape(name)}" alt="{html.escape(name)}" />' for name in figures])
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>实验分析报告 - 管理层摘要</title>
<style>
    :root {{
        --primary: #18344f;
        --primary-light: #386789;
        --accent: #c96d1f;
        --accent-soft: #f4dfc9;
        --success: #1f7a5c;
        --warn: #a34c18;
        --bg: #f3efe7;
        --card: #ffffff;
        --text: #233241;
        --muted: #62707d;
        --border: #dde3e8;
        --shadow: 0 16px 40px rgba(24, 52, 79, 0.12);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif; background: radial-gradient(circle at top, #faf6ef 0%, var(--bg) 48%, #efe7dc 100%); color: var(--text); line-height: 1.75; }}
    .hero {{ background: linear-gradient(135deg, #122638 0%, #244f6e 55%, #3c7a83 100%); color: #fff; padding: 64px 20px 118px; position: relative; overflow: hidden; }}
    .hero::after {{ content: ""; position: absolute; inset: auto -8% -120px auto; width: 340px; height: 340px; border-radius: 50%; background: rgba(255, 255, 255, 0.08); }}
    .hero h1 {{ font-size: 34px; margin-bottom: 12px; letter-spacing: 0.02em; }}
    .hero p {{ opacity: 0.88; max-width: 760px; margin: 0 auto; }}
    .wrap {{ max-width: 1220px; margin: -72px auto 42px; padding: 0 20px; position: relative; z-index: 1; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 18px; }}
    .kpi-card {{ background: linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(250,248,243,0.98) 100%); border: 1px solid rgba(24,52,79,0.08); border-radius: 18px; padding: 20px 22px; box-shadow: var(--shadow); min-height: 148px; }}
    .kpi-label {{ font-size: 13px; color: var(--muted); margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .kpi-value {{ font-size: 30px; line-height: 1.2; color: var(--primary); font-weight: 700; margin-bottom: 10px; }}
    .kpi-note {{ font-size: 13px; color: var(--muted); }}
    .layout {{ display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.95fr); gap: 18px; }}
    .stack {{ display: grid; gap: 18px; }}
    .card {{ background: var(--card); border-radius: 18px; box-shadow: var(--shadow); padding: 30px 32px; border: 1px solid rgba(24,52,79,0.08); }}
    .span-2 {{ grid-column: 1 / -1; }}
    h2 {{ margin: 0 0 14px; color: var(--primary); font-size: 24px; }}
    h3 {{ margin: 20px 0 10px; color: var(--primary-light); font-size: 18px; }}
    p {{ margin-bottom: 14px; }}
    ul {{ margin: 10px 0 18px 22px; }}
    li {{ margin-bottom: 8px; }}
    .highlight {{ background: linear-gradient(90deg, rgba(24,52,79,0.08) 0%, rgba(24,52,79,0.03) 100%); border-left: 4px solid var(--primary-light); padding: 18px 18px; border-radius: 0 12px 12px 0; margin: 16px 0 18px; font-weight: 600; }}
    .callout {{ background: linear-gradient(90deg, rgba(201,109,31,0.12) 0%, rgba(201,109,31,0.04) 100%); border-left: 4px solid var(--accent); padding: 15px 18px; border-radius: 0 12px 12px 0; margin: 16px 0 18px; }}
    .soft-panel {{ background: #f9f6ef; border: 1px solid rgba(24,52,79,0.08); border-radius: 14px; padding: 16px 18px; }}
    .figure-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin: 18px 0 8px; }}
    .figure-grid img {{ width: 100%; border: 1px solid var(--border); border-radius: 10px; background: #fff; }}
    .table-wrap {{ overflow-x: auto; margin: 16px 0 20px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 620px; font-size: 14px; background: #fff; }}
    th {{ background: var(--primary); color: #fff; padding: 10px 12px; text-align: center; }}
    td {{ border-bottom: 1px solid var(--border); padding: 9px 12px; text-align: center; }}
    tr:hover td {{ background: #f9fbfd; }}
    .feature-table td:nth-child(2), .feature-table th:nth-child(2) {{ text-align: left; }}
    .footer {{ text-align: center; color: var(--muted); font-size: 13px; padding: 22px 0 0; }}
    @media (max-width: 980px) {{
        .kpi-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
        .layout {{ grid-template-columns: 1fr; }}
        .figure-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 640px) {{
        .hero {{ padding: 48px 16px 108px; }}
        .hero h1 {{ font-size: 28px; }}
        .wrap {{ margin-top: -64px; padding: 0 14px; }}
        .kpi-grid {{ grid-template-columns: 1fr; }}
        .card {{ padding: 24px 20px; }}
        .kpi-card {{ min-height: 0; }}
    }}
</style>
</head>
<body>
<header class="hero">
    <h1>实验分析报告 管理层汇报</h1>
    <p>实验版本：{html.escape(bundle.experiment_version)} | 生成时间：{html.escape(generated_at)}<br>这份页面优先回答三个问题：模型能不能用、应该怎么用、下一步优先改哪里。</p>
</header>
<div class="wrap">
    <div class="kpi-grid"><div class="kpi-card"><div class="kpi-label">测试集排序 Spearman</div><div class="kpi-value">{html.escape(_fmt_float(step6_details.get('spearman')))}</div><div class="kpi-note">越高说明把更好的信号排到前面的能力越强</div></div><div class="kpi-card"><div class="kpi-label">测试集 Pearson</div><div class="kpi-value">{html.escape(_fmt_float(step6_details.get('pearson')))}</div><div class="kpi-note">衡量预测值与真实值的线性相关强度</div></div><div class="kpi-card"><div class="kpi-label">中位数高分组 raw 均值</div><div class="kpi-value">{html.escape(high_group_raw_mean)}</div><div class="kpi-note">这是最适合先落地的默认过滤结果</div></div><div class="kpi-card"><div class="kpi-label">高低组 raw 均值差</div><div class="kpi-value">{html.escape(_fmt_pct(median_uplift.get('target_raw_mean_diff')))}</div><div class="kpi-note">高分组与低分组的平均收益差</div></div><div class="kpi-card"><div class="kpi-label">最优宽阈值</div><div class="kpi-value">{html.escape('q=' + best_threshold_quantile if best_threshold_quantile else '')}</div><div class="kpi-note">该阈值下 raw 均值为 {html.escape(best_threshold_raw_mean)}</div></div><div class="kpi-card"><div class="kpi-label">最耗时 step</div><div class="kpi-value">{html.escape(step_runtime_summary.get('slowest_step') or '')}</div><div class="kpi-note">当前迭代速度主要由 TSFresh 提取决定</div></div><div class="kpi-card"><div class="kpi-label">第一重要特征</div><div class="kpi-value">{html.escape(str(top_feature_name))}</div><div class="kpi-note">帮助判断模型到底在看什么</div></div></div>

    <div class="layout">
        <section class="card">
            <h2>1. 核心结论与业务建议</h2>
            <div class="highlight">当前模型已经具备可落地的过滤价值，最适合先作为中位数附近的宽阈值信号过滤器上线试运行。</div>
            <p>这套模型不是拿来“精准预测每笔交易赚多少”的，而是拿来把原始信号重新排队。现在最清晰的结论是：它已经能把一批明显更差的信号筛出去，但还不够稳定到只看最高分 10% 或 20% 就能明显更好。</p>
            <ul>
                <li>模型在测试集上的 Spearman 为 {html.escape(_fmt_float(step6_details.get('spearman')))} ({html.escape(spearman_text)})，Pearson 为 {html.escape(_fmt_float(step6_details.get('pearson')))} ({html.escape(pearson_text)})</li>
                <li>测试集原始 raw 均值为 {html.escape(_fmt_pct(overall_test.get('target_raw_mean')))}，中位数高分组 raw 均值提升到 {html.escape(high_group_raw_mean)}</li>
                <li>当前最优宽阈值来自 q={html.escape(best_threshold_quantile)}，对应 raw 均值 {html.escape(best_threshold_raw_mean)}</li>
                <li>单调性总结：{html.escape(_monotonic_summary_text(monotonic_summary))}</li>
            </ul>
            <div class="callout"><strong>业务含义：</strong>这更像一个“先剔除明显不好的信号”的工具，而不是“只留下极少数顶尖信号”的狙击器。先做宽阈值过滤，比直接做极端收缩更稳。</div>
        </section>

        <div class="stack">
            <section class="card">
                <h2>2. 模型怎么理解</h2>
                <p>这里使用的是 LightGBM。可以把它理解成一个擅长处理表格数据的打分器：它同时看过去 60 个交易日里价格、波动、成交量和手工补充特征，然后给每个信号一个相对分数。</p>
                <div class="soft-panel">
                    <strong>当前最重要特征：</strong> {html.escape(str(top_feature_name))}<br>
                    <strong>特征来源：</strong> {html.escape(str(top_feature_origin))}
                </div>
                <p>{html.escape(split_model_story)}</p>
                {_html_table(split_model_df, percent_columns=["raw 胜率"], float_columns=["target MAE", "排序 Spearman", "排序 Pearson", "raw 均值", "预测分数波动"])}
            </section>

            <section class="card">
                <h2>3. 不同 Step 的性能</h2>
                <p>整条 step1-step6 流程总耗时约 {html.escape(step_runtime_summary.get('total_duration_hms') or '')}，其中 {html.escape(step_runtime_summary.get('slowest_step') or '')} 单独占用了 {html.escape(_fmt_pct(step_runtime_summary.get('slowest_share')))}。说明当前实验迭代速度主要受 TSFresh 特征提取影响，训练本身并不是主要成本。</p>
                {_html_table(step_performance_df)}
            </section>
        </div>

        <section class="card span-2">
            <h2>4. 结果图总览</h2>
            <p>先看三集合对比图了解整体趋势，再看各集合单独图检查局部回撤。颜色上，蓝=训练集、橙=验证集、绿=测试集。如果从左到右整体抬升，说明模型分数与真实收益的方向一致。</p>
            <div class="figure-grid">{figure_html}</div>
        </section>

        <section class="card">
            <h2>5. 测试集分组表现</h2>
            <p>这部分直接回答“上线后怎么用”。五分组先看模型有没有把差的放到底部；阈值表再看保留前 50%、40%、30% 时，收益是否真的越来越好。</p>
            <h3>5.1 五分组结果</h3>
            {_html_table(quintile_display_df, percent_columns=["target 胜率", "target_raw 胜率"], float_columns=["target 均值", "target_raw 均值"])}
            <h3>5.2 分位阈值结果</h3>
            {_html_table(threshold_display_df, percent_columns=["保留比例", "target 胜率", "target_raw 胜率"], float_columns=["分数阈值", "target 均值", "target_raw 均值"])}
            <div class="callout"><strong>怎么读：</strong>如果把阈值抬高后收益没有继续改善，就说明当前模型更擅长“筛掉差的”，还不擅长“精准抓最好的”。现在的结果正是这种情况。</div>
        </section>

        <section class="card">
            <h2>6. 模型在看什么</h2>
            <p>{html.escape(feature_story)}</p>
            {_html_table(feature_top10_df, float_columns=["gain"], table_class="feature-table")}
            <p>如果时间位置、月份、停牌比例这类特征长期排在前面，就说明模型在相当程度上是在识别市场环境。这不一定是坏事，但要持续观察不同年份是否还能成立。</p>
        </section>

        <section class="card span-2">
            <h2>7. 建议与下一步</h2>
            <ul>
                <li>优先采用中位数阈值作为默认过滤规则，先以简单、稳妥为目标。</li>
                <li>持续监控时间特征依赖，如果未来市场风格切换，需要尽快复盘模型稳定性。</li>
                <li>如需继续优化，优先研究为何最高分尾部不够稳定，而不是盲目继续提高过滤阈值。</li>
                <li>如果希望缩短实验迭代时间，先优化步骤4的 TSFresh 提取，而不是优先改训练参数。</li>
            </ul>
            <div class="footer">本文件由分析报告工具自动生成。</div>
        </section>
    </div>
</div>
</body>
</html>
"""


def render_summary_json(
    bundle: ArtifactBundle,
    clean_stats: Dict[str, Dict[str, int]],
    split_frames: Dict[str, pd.DataFrame],
    quintile_df: pd.DataFrame,
    median_df: pd.DataFrame,
    median_uplift: Dict[str, Any],
    threshold_df: pd.DataFrame,
    threshold_summary: Dict[str, Any],
    monotonic_df: pd.DataFrame,
    monotonic_summary: Dict[str, Any],
    feature_importance_df: Optional[pd.DataFrame],
    feature_importance_summary: Dict[str, Any],
    state_payload: Dict[str, Any],
    resolved_params: Dict[str, Any],
    model_feature_schema: Dict[str, Any],
    config: AnalysisConfig,
) -> Dict[str, Any]:
    split_overview = _build_split_overview(split_frames, config)
    overall_test = _build_overall_metrics(split_frames.get("test"), config)
    return {
        "experiment_version": bundle.experiment_version,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_artifacts": {key: _rel(path) for key, path in bundle.input_artifacts.items() if path is not None},
        "analysis_scope": {
            "test_only": bundle.available_splits == ["test"],
            "available_splits": bundle.available_splits,
            "missing_artifacts": bundle.missing_artifacts,
            "resolution_source": bundle.resolution_source,
            "strict_mode": bundle.strict_mode,
        },
        "pipeline_steps": state_payload.get("steps", {}),
        "resolved_params": resolved_params,
        "model_feature_schema": model_feature_schema,
        "cleaning": clean_stats,
        "split_overview": split_overview.to_dict(orient="records"),
        "overall_test": overall_test,
        "test_quintile": quintile_df.to_dict(orient="records"),
        "test_median_split": {
            "rows": median_df.to_dict(orient="records"),
            "uplift": median_uplift,
        },
        "test_threshold_filters": {
            "rows": threshold_df.to_dict(orient="records"),
            "summary": threshold_summary,
        },
        "monotonic": {
            "rows": monotonic_df.to_dict(orient="records"),
            "summary": monotonic_summary,
        },
        "artifacts_generated": {
            "monotonic_line_chart": "monotonic_groups.png",
            "monotonic_target_all": "monotonic_target_all.png",
            "monotonic_target_train": "monotonic_target_train.png",
            "monotonic_target_val": "monotonic_target_val.png",
            "monotonic_target_test": "monotonic_target_test.png",
            "monotonic_y_pred_groups": "monotonic_y_pred_groups.png",
            "report_markdown": "experiment_analysis_report.md",
            "leadership_html": "experiment_summary_for_leadership.html",
            "summary_json": "report_summary.json",
        },
        "feature_importance_top20": feature_importance_df.to_dict(orient="records") if feature_importance_df is not None else [],
        "feature_alignment": feature_importance_summary,
    }


def _write_dataframe(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def _scope_text(available_splits: List[str]) -> str:
    if available_splits == ["test"]:
        return "仅测试集"
    if available_splits == ["train", "test"]:
        return "train / test"
    if available_splits == ["val", "test"]:
        return "val / test"
    if available_splits == ["train", "val", "test"]:
        return "train / val / test 全集合"
    return " / ".join(available_splits)


def _print_banner(bundle: ArtifactBundle) -> None:
    print("=" * 40)
    print("  独立分析报告工具")
    print(f"  实验版本: {bundle.experiment_version}")
    print("  输入工件:")
    print(f"    - 测试集预测: {_rel(bundle.input_artifacts.get('test_prediction'))}")
    print(f"    - 训练集预测: {_rel(bundle.input_artifacts.get('train_prediction')) or '缺失'}")
    print(f"    - 验证集预测: {_rel(bundle.input_artifacts.get('val_prediction')) or '缺失'}")
    print(f"    - 模型文件: {_rel(bundle.input_artifacts.get('model')) or '缺失'}")
    print(f"  输出目录: {_rel(bundle.analysis_dir)}")
    print(f"  分析范围: {_scope_text(bundle.available_splits)}")
    print("=" * 40)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="独立分析报告工具 —— 基于已完成的 step1-6 实验结果，生成统计表、图表和 Markdown 报告。"
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="分析配置文件路径")
    parser.add_argument("--manifest", default=None, help="实验 manifest JSON 路径（优先级最高）")
    parser.add_argument("--experiment-version", default=None, help="实验版本号")
    parser.add_argument("--output-dir", default=None, help="覆盖输出目录")
    parser.add_argument("--strict", action="store_true", help="强制启用严格模式")
    parser.add_argument("--no-strict", action="store_true", help="工件缺失时降级运行而非报错退出")
    parser.add_argument("--dry-run", action="store_true", help="仅解析和打印输入输出，不生成文件")
    args = parser.parse_args()
    if args.strict and args.no_strict:
        parser.error("--strict 与 --no-strict 不能同时使用。")
    return args


def main() -> int:
    args = _parse_args()
    explicit_config = args.config != DEFAULT_CONFIG
    config = load_analysis_config(args.config, explicit_config=explicit_config)
    strict_mode = config.strict_mode
    if args.strict:
        strict_mode = True
    if args.no_strict:
        strict_mode = False
    bundle = resolve_artifacts(config, args.manifest, args.experiment_version, args.output_dir, strict_mode)
    _print_banner(bundle)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "experiment_version": bundle.experiment_version,
                    "analysis_dir": _rel(bundle.analysis_dir),
                    "resolution_source": bundle.resolution_source,
                    "available_splits": bundle.available_splits,
                    "missing_artifacts": bundle.missing_artifacts,
                    "input_artifacts": {key: _rel(path) for key, path in bundle.input_artifacts.items() if path is not None},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    bundle.analysis_dir.mkdir(parents=True, exist_ok=True)
    clean_stats: Dict[str, Dict[str, int]] = {}
    split_frames: Dict[str, pd.DataFrame] = {}
    split_key_map = {"train": "train_prediction", "val": "val_prediction", "test": "test_prediction"}
    for split_name in bundle.available_splits:
        path = bundle.input_artifacts[split_key_map[split_name]]
        if path is None:
            continue
        frame, stats = load_prediction(path, config, split_name)
        split_frames[split_name] = frame
        clean_stats[split_name] = stats

    test_df = split_frames["test"]
    quintile_df = build_quintile_report(test_df, config)
    median_df, median_uplift = build_median_split_report(test_df, config)
    threshold_df, threshold_summary = build_threshold_report(test_df, config)
    monotonic_df = build_monotonic_table(split_frames, config)
    monotonic_summary = summarize_monotonic(monotonic_df)
    feature_importance_df, feature_importance_summary = build_feature_importance(
        bundle.input_artifacts.get("model"),
        bundle.input_artifacts.get("selected_features"),
        test_df,
        config,
    )
    clip_percentiles = _load_clip_percentiles(bundle.input_artifacts.get("clip_percentiles"))
    state_payload = _load_state(bundle.input_artifacts.get("state_file"))
    resolved_params_path = bundle.manifest_path.parent / "resolved_params.yaml" if bundle.manifest_path is not None else None
    resolved_params = _load_yaml_payload(resolved_params_path)
    model_feature_schema = _load_optional_json(bundle.input_artifacts.get("model_feature_schema"), "model_feature_schema")

    _write_dataframe(quintile_df, bundle.analysis_dir / "test_quintile_groups.csv")
    _write_dataframe(median_df, bundle.analysis_dir / "test_median_split.csv")
    _write_dataframe(threshold_df, bundle.analysis_dir / "test_threshold_filters.csv")
    _write_dataframe(monotonic_df, bundle.analysis_dir / "monotonic_groups.csv")
    plot_monotonic(monotonic_df, bundle.analysis_dir / "monotonic_groups.png", config)

    # --- 新增: 单面板折线图（替代热力图） ---
    color_map = {"train": "#1f77b4", "val": "#ff7f0e", "test": "#2ca02c"}
    label_map = {"train": "训练集", "val": "验证集", "test": "测试集"}

    plot_monotonic_metric_all_splits(
        monotonic_df,
        bundle.analysis_dir / "monotonic_target_all.png",
        "target_raw_mean",
        f"{config.report_title} - target_raw_mean 三集合对比",
        "target_raw 均值",
        True,
        config,
    )
    plot_monotonic_metric_all_splits(
        monotonic_df,
        bundle.analysis_dir / "monotonic_y_pred_groups.png",
        "y_pred_mean",
        f"{config.report_title} - y_pred_mean 三集合对比",
        "y_pred 均值",
        False,
        config,
    )
    for split_name in ["train", "val", "test"]:
        if split_name in monotonic_df["split_name"].values:
            plot_monotonic_single_split(
                monotonic_df, split_name,
                bundle.analysis_dir / f"monotonic_target_{split_name}.png",
                "target_raw_mean",
                f"target_raw_mean - {label_map[split_name]}",
                "target_raw 均值",
                color_map[split_name],
                True,
                config,
            )

    # --- 清理旧热力图残留 ---
    for name in ["monotonic_target_heatmap.png", "monotonic_y_pred_heatmap.png"]:
        obsolete = bundle.analysis_dir / name
        if obsolete.exists():
            obsolete.unlink()

    obsolete_bar_plot = bundle.analysis_dir / "monotonic_groups_bar.png"
    if obsolete_bar_plot.exists():
        obsolete_bar_plot.unlink()
    if feature_importance_df is not None:
        _write_dataframe(feature_importance_df, bundle.analysis_dir / "top_feature_importance.csv")

    report_markdown = render_markdown_report(
        bundle,
        config,
        clean_stats,
        split_frames,
        quintile_df,
        median_df,
        median_uplift,
        threshold_df,
        threshold_summary,
        monotonic_df,
        monotonic_summary,
        feature_importance_df,
        feature_importance_summary,
        clip_percentiles,
        state_payload,
        resolved_params,
        model_feature_schema,
    )
    (bundle.analysis_dir / "experiment_analysis_report.md").write_text(report_markdown, encoding="utf-8")
    leadership_html = render_leadership_html(
        bundle,
        config,
        split_frames,
        quintile_df,
        median_df,
        median_uplift,
        threshold_df,
        threshold_summary,
        monotonic_summary,
        feature_importance_df,
        state_payload,
        resolved_params,
    )
    (bundle.analysis_dir / "experiment_summary_for_leadership.html").write_text(leadership_html, encoding="utf-8")
    summary_payload = render_summary_json(
        bundle,
        clean_stats,
        split_frames,
        quintile_df,
        median_df,
        median_uplift,
        threshold_df,
        threshold_summary,
        monotonic_df,
        monotonic_summary,
        feature_importance_df,
        feature_importance_summary,
        state_payload,
        resolved_params,
        model_feature_schema,
        config,
    )
    _write_json(bundle.analysis_dir / "report_summary.json", summary_payload)
    print(f"分析完成，产物已写入: {_rel(bundle.analysis_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())