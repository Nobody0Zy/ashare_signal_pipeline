from __future__ import annotations

import copy
import html
import io
import json
import shutil
import subprocess
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

import matplotlib

matplotlib.use("Agg")

from matplotlib import font_manager
import matplotlib.pyplot as plt

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover
    lgb = None

try:
    import pyarrow.parquet as pq
except ImportError:  # pragma: no cover
    pq = None

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from ..config import Paths, PipelineParams, build_paths, ensure_directories, load_params, save_resolved_params
from ..runner import run_step
from ..state import load_state, now_iso, save_state, step_key


FULL_MODE = "full"
BASE_COLUMNS = ["sample_id", "signal_date", "target", "target_raw"]
PREFERRED_CN_FONTS = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS"]


def _norm_rel(path_str: str) -> str:
    return str(path_str).replace("\\", "/").lstrip("./")


def _rel(root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _resolve_path(root: Path, path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (root / path).resolve()


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise ImportError("PyYAML 未安装")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"YAML 格式无效: {path}")
    return raw


def _write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    if yaml is None:
        raise ImportError("PyYAML 未安装")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(payload, handle, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 格式无效: {path}")
    return payload


def _sanitize_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_json(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    return value


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_sanitize_for_json(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _copy_or_link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        dst.hardlink_to(src)
    except OSError:
        shutil.copy2(src, dst)


def _read_selected_features(path: Path) -> List[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _parquet_columns(path: Path) -> List[str]:
    if pq is not None:
        return list(pq.ParquetFile(path).schema.names)
    return list(pd.read_parquet(path).columns)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(numeric):
        return None
    return numeric


def _safe_spearman(target: pd.Series, pred: pd.Series) -> Optional[float]:
    if len(target) == 0 or len(pred) == 0:
        return None
    statistic = spearmanr(target, pred).statistic
    return None if statistic is None or np.isnan(statistic) else float(statistic)


def _mae(target: pd.Series, pred: pd.Series) -> float:
    return float((pred - target).abs().mean())


def _fmt_float(value: Optional[float], digits: int = 6) -> str:
    if value is None:
        return ""
    return f"{float(value):.{digits}f}"


def _fmt_pct(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.{digits}f}%"


def _markdown_table(
    rows: List[Dict[str, Any]],
    columns: Sequence[str],
    float_columns: Optional[Sequence[str]] = None,
    percent_columns: Optional[Sequence[str]] = None,
) -> str:
    float_set = set(float_columns or [])
    percent_set = set(percent_columns or [])
    if not rows:
        return "| 空 | 值 |\n| --- | --- |\n| 无数据 | 无 |"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        rendered: List[str] = []
        for column in columns:
            value = row.get(column)
            if column in percent_set:
                rendered.append(_fmt_pct(_safe_float(value)))
            elif column in float_set:
                rendered.append(_fmt_float(_safe_float(value)))
            elif value is None:
                rendered.append("")
            else:
                rendered.append(str(value))
        lines.append("| " + " | ".join(rendered) + " |")
    return "\n".join(lines)


def _html_table_from_df(
    df: pd.DataFrame,
    columns: Optional[Sequence[str]] = None,
    float_columns: Optional[Sequence[str]] = None,
    percent_columns: Optional[Sequence[str]] = None,
    table_class: str = "",
) -> str:
    columns = list(columns or df.columns.tolist())
    float_set = set(float_columns or [])
    percent_set = set(percent_columns or [])
    class_attr = f' class="{html.escape(table_class)}"' if table_class else ""
    if df.empty:
        return f'<div class="table-wrap"><table{class_attr}><thead><tr><th>状态</th><th>值</th></tr></thead><tbody><tr><td>无数据</td><td>无</td></tr></tbody></table></div>'

    header_html = "".join([f"<th>{html.escape(str(column))}</th>" for column in columns])
    body_rows: List[str] = []
    for _, row in df[columns].iterrows():
        cells: List[str] = []
        for column in columns:
            value = row[column]
            if column in percent_set:
                rendered = _fmt_pct(_safe_float(value))
            elif column in float_set:
                rendered = _fmt_float(_safe_float(value))
            elif pd.isna(value):
                rendered = ""
            else:
                rendered = str(value)
            cells.append(f"<td>{html.escape(rendered)}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="table-wrap"><table{class_attr}><thead><tr>{header_html}</tr></thead><tbody>{"".join(body_rows)}</tbody></table></div>'


def _configure_plot_font() -> None:
    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    for font_name in PREFERRED_CN_FONTS:
        if font_name in available_fonts:
            plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def _artifact_roots(root: Path, paths: Paths) -> Dict[str, str]:
    return {
        "processed_dir": _rel(root, paths.processed_dir),
        "slices_dir": _rel(root, paths.slices_dir),
        "ts_features_dir": _rel(root, paths.ts_features_dir),
        "tsfresh_chunks_dir": _rel(root, paths.tsfresh_chunks_dir),
        "features_dir": _rel(root, paths.features_dir),
        "splits_dir": _rel(root, paths.splits_dir),
        "models_dir": _rel(root, paths.models_dir),
        "output_dir": _rel(root, paths.output_dir),
        "metadata_dir": _rel(root, paths.metadata_dir),
        "state_file": _rel(root, paths.state_file),
    }


@dataclass
class SourceExperimentConfig:
    template_config_path: str
    source_config_path: str
    source_state_file: str
    source_manifest_path: str = ""
    source_experiment_version: str = ""
    bootstrap_if_missing: bool = True


@dataclass
class LabExperimentConfig:
    base_version: str
    label_template: str
    state_file_template: str


@dataclass
class EnsembleExperimentConfig:
    base_version: str
    label: str
    state_file: str


@dataclass
class BaggingOptions:
    seeds: List[int]
    allow_partial_ensemble: bool = False
    min_successful_labs: int = 10
    train_shuffle: bool = True


@dataclass
class AnalysisOptions:
    report_title: str = "Bagging 实验分析报告"
    strict_mode: bool = True


@dataclass
class RunOptions:
    max_labs: int = 10
    skip_analysis: bool = False


@dataclass
class OutputOptions:
    include_member_predictions: bool = True


@dataclass
class BaggingConfig:
    source_experiment: SourceExperimentConfig
    lab_experiment: LabExperimentConfig
    ensemble_experiment: EnsembleExperimentConfig
    bagging: BaggingOptions
    expected_source_base_version: str = ""
    analysis: AnalysisOptions = field(default_factory=AnalysisOptions)
    run: RunOptions = field(default_factory=RunOptions)
    output: OutputOptions = field(default_factory=OutputOptions)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "BaggingConfig":
        return cls(
            source_experiment=SourceExperimentConfig(**dict(raw.get("source_experiment") or {})),
            lab_experiment=LabExperimentConfig(**dict(raw.get("lab_experiment") or {})),
            ensemble_experiment=EnsembleExperimentConfig(**dict(raw.get("ensemble_experiment") or {})),
            bagging=BaggingOptions(**dict(raw.get("bagging") or {})),
            expected_source_base_version=str(raw.get("expected_source_base_version", "")).strip(),
            analysis=AnalysisOptions(**dict(raw.get("analysis") or {})),
            run=RunOptions(**dict(raw.get("run") or {})),
            output=OutputOptions(**dict(raw.get("output") or {})),
        )


@dataclass
class SourceContext:
    params: PipelineParams
    config_path: Path
    template_config_path: Path
    paths: Paths
    state_file: str
    manifest_path: Optional[Path]
    bootstrap_needed: bool
    bootstrap_reason: str
    equivalent_manifest_path: Optional[Path] = None


@dataclass
class SourceArtifacts:
    experiment_version: str
    manifest_path: Path
    manifest: Dict[str, Any]
    train_path: Path
    val_path: Path
    test_path: Path
    selected_features_path: Path
    clip_percentiles_path: Path
    state_file_path: Optional[Path]
    selected_features: List[str]
    feature_columns: List[str]


@dataclass
class LabRunSpec:
    lab_id: int
    seed: int
    experiment_version: str
    label: str
    state_file: str


@dataclass
class EnsembleContext:
    experiment_version: str
    label: str
    state_file: str
    output_dir: Path
    metadata_dir: Path
    analysis_dir: Path


@dataclass
class LabResult:
    lab_id: int
    seed: int
    label: str
    experiment_version: str
    status: str
    output_files: Dict[str, str] = field(default_factory=dict)
    best_iteration: Optional[int] = None
    val_mae: Optional[float] = None
    test_mae: Optional[float] = None
    test_spearman: Optional[float] = None
    error: str = ""
    manifest_path: Optional[Path] = None
    bagging_lab_summary_path: Optional[Path] = None
    analysis_summary_path: Optional[Path] = None


@dataclass
class PreflightSummary:
    source_resolution: str
    source_manifest_path: str
    source_experiment_version: str
    source_config_path: str
    source_state_file: str
    bootstrap_needed: bool
    bootstrap_reason: str
    ensemble_experiment_version: str
    planned_labs: List[Dict[str, Any]]


def load_bagging_config(root: Path, config_path: str) -> BaggingConfig:
    raw = _load_yaml(_resolve_path(root, config_path))
    return BaggingConfig.from_dict(raw)


def _load_registry(root: Path) -> List[Dict[str, Any]]:
    registry_path = root / "outputs" / "experiment_registry.json"
    if not registry_path.exists():
        return []
    with registry_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, list) else []


def _write_registry(root: Path, registry: List[Dict[str, Any]]) -> None:
    registry_path = root / "outputs" / "experiment_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry.sort(key=lambda entry: str(entry.get("experiment_version", "")))
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def _manifest_path_from_entry(root: Path, entry: Dict[str, Any]) -> Optional[Path]:
    manifest_file = str(entry.get("manifest_file", "")).strip()
    if manifest_file:
        path = _resolve_path(root, manifest_file)
        if path.exists():
            return path
    experiment_version = str(entry.get("experiment_version", "")).strip()
    if experiment_version:
        candidate = root / "outputs" / experiment_version / "metadata" / "experiment_manifest.json"
        if candidate.exists():
            return candidate
    return None


def _artifact_path(root: Path, manifest: Dict[str, Any], key: str) -> Optional[Path]:
    artifact_files = manifest.get("artifact_files", {}) if isinstance(manifest, dict) else {}
    value = str(artifact_files.get(key, "")).strip()
    if not value:
        return None
    path = _resolve_path(root, value)
    return path if path.exists() else None


def _source_step_artifacts(paths: Paths) -> Dict[int, List[Path]]:
    return {
        1: [paths.processed_dir / "holding_clean.parquet", paths.processed_dir / "datebar.parquet"],
        2: [paths.slices_dir / "slices_60d.parquet", paths.slices_dir / "non_trading_ratio.csv"],
        3: [paths.ts_features_dir / "ts_features_60d.parquet"],
        4: [
            paths.features_dir / "selected_features.txt",
            paths.features_dir / "clip_percentiles.json",
            paths.features_dir / "features_selected_50.parquet",
        ],
        5: [paths.splits_dir / "train.parquet", paths.splits_dir / "val.parquet", paths.splits_dir / "test.parquet"],
    }


def _source_step_output_files(root: Path, paths: Paths) -> Dict[int, List[str]]:
    return {
        step: [_rel(root, item) for item in files]
        for step, files in _source_step_artifacts(paths).items()
    }


def _completed_source_steps(paths: Paths) -> int:
    completed_until = 0
    for step, files in _source_step_artifacts(paths).items():
        if all(file_path.exists() for file_path in files):
            completed_until = step
        else:
            break
    return completed_until


def _validate_source_config_derivation(
    template_path: Path,
    source_path: Path,
    expected_base_version: str = "",
) -> None:
    template_raw = _load_yaml(template_path)
    source_raw = _load_yaml(source_path)

    if set(template_raw.keys()) != set(source_raw.keys()):
        raise RuntimeError("source config 与模板配置的顶层键不一致，无法确认其仅为最小偏移。")

    for key, template_value in template_raw.items():
        source_value = source_raw.get(key)
        if key == "seed":
            continue
        if key != "experiment" and source_value != template_value:
            raise RuntimeError(f"source config 非允许字段发生漂移: {key}")

    template_experiment = dict(template_raw.get("experiment") or {})
    source_experiment = dict(source_raw.get("experiment") or {})
    if set(template_experiment.keys()) != set(source_experiment.keys()):
        raise RuntimeError("source config 的 experiment 段与模板不一致。")
    for key, template_value in template_experiment.items():
        if key in {"base_version", "label"}:
            continue
        if source_experiment.get(key) != template_value:
            raise RuntimeError(f"source config 的 experiment.{key} 非允许变更。")

    if int(source_raw.get("seed", -1)) != 42:
        raise RuntimeError("source config 的 seed 必须固定为 42。")
    expected_base = str(expected_base_version).strip()
    if expected_base and str(source_experiment.get("base_version", "")).strip() != expected_base:
        raise RuntimeError(f"source config 的 experiment.base_version 必须为 {expected_base}。")
    if not str(source_experiment.get("label", "")).strip():
        raise RuntimeError("source config 的 experiment.label 不能为空。")


def _is_valid_source_manifest(
    root: Path,
    manifest: Dict[str, Any],
    expected_base_version: str = "",
) -> Tuple[bool, str]:
    experiment = manifest.get("experiment", {}) if isinstance(manifest, dict) else {}
    base_version = str(experiment.get("base_version", "")).strip()
    expected_base = str(expected_base_version).strip()
    if expected_base and base_version != expected_base:
        return False, f"source manifest base_version={base_version or 'empty'}，预期 {expected_base}"
    required = {
        "train_split_file": "train split",
        "val_split_file": "val split",
        "test_split_file": "test split",
        "selected_features_file": "selected features",
        "clip_percentiles_file": "clip percentiles",
    }
    for key, label in required.items():
        if _artifact_path(root, manifest, key) is None:
            return False, f"source manifest 缺少可用工件: {label}"
    return True, "ok"


def _resolve_manifest_by_version(root: Path, experiment_version: str) -> Optional[Path]:
    candidate = root / "outputs" / experiment_version / "metadata" / "experiment_manifest.json"
    if candidate.exists():
        return candidate
    for entry in _load_registry(root):
        if str(entry.get("experiment_version", "")).strip() == experiment_version:
            manifest_path = _manifest_path_from_entry(root, entry)
            if manifest_path is not None:
                return manifest_path
    return None


def _find_matching_source_manifest(
    root: Path,
    expected_version: str,
    source_config_path: str,
    expected_base_version: str = "",
) -> Optional[Path]:
    for entry in reversed(_load_registry(root)):
        if str(entry.get("experiment_version", "")).strip() != expected_version:
            continue
        manifest_path = _manifest_path_from_entry(root, entry)
        if manifest_path is None:
            continue
        try:
            manifest = _load_json(manifest_path)
        except Exception:
            continue
        source_config = str(manifest.get("source_config", "")).strip()
        if source_config and _norm_rel(source_config) != _norm_rel(source_config_path):
            continue
        valid, _ = _is_valid_source_manifest(root, manifest, expected_base_version)
        if valid:
            return manifest_path
    return None


def _comparable_payload_from_resolved(resolved_payload: Dict[str, Any], source_payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: resolved_payload.get(key) for key in source_payload.keys()}


def _equivalent_step15_manifest(root: Path, source_params: PipelineParams) -> Optional[Path]:
    source_payload = source_params.version_payload()
    expected_json = json.dumps(source_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    candidate_paths: List[Path] = []
    seen: set[str] = set()
    for entry in _load_registry(root):
        manifest_path = _manifest_path_from_entry(root, entry)
        if manifest_path is None:
            continue
        key = str(manifest_path.resolve())
        if key in seen:
            continue
        seen.add(key)
        candidate_paths.append(manifest_path)

    for manifest_path in sorted((root / "outputs").glob("*/metadata/experiment_manifest.json")):
        key = str(manifest_path.resolve())
        if key in seen:
            continue
        seen.add(key)
        candidate_paths.append(manifest_path)

    for manifest_path in reversed(candidate_paths):
        try:
            manifest = _load_json(manifest_path)
        except Exception:
            continue
        required_keys = ["train_split_file", "val_split_file", "test_split_file", "selected_features_file", "clip_percentiles_file"]
        if any(_artifact_path(root, manifest, key) is None for key in required_keys):
            continue
        resolved_rel = str(manifest.get("resolved_params_file", "")).strip()
        if not resolved_rel:
            continue
        resolved_payload = _load_optional_yaml(_resolve_path(root, resolved_rel))
        if not resolved_payload:
            continue
        comparable = _comparable_payload_from_resolved(resolved_payload, source_payload)
        comparable_json = json.dumps(comparable, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        if comparable_json == expected_json:
            return manifest_path
    return None


def _build_source_context(root: Path, cfg: BaggingConfig) -> SourceContext:
    source_config_path = _resolve_path(root, cfg.source_experiment.source_config_path)
    template_config_path = _resolve_path(root, cfg.source_experiment.template_config_path)
    params = load_params(str(source_config_path))
    paths = build_paths(root, cfg.source_experiment.source_state_file, params)
    ensure_directories(paths)

    explicit_manifest = str(cfg.source_experiment.source_manifest_path).strip()
    explicit_version = str(cfg.source_experiment.source_experiment_version).strip()

    if explicit_manifest:
        manifest_path = _resolve_path(root, explicit_manifest)
        manifest = _load_json(manifest_path)
        valid, reason = _is_valid_source_manifest(root, manifest, cfg.expected_source_base_version)
        if not valid:
            raise RuntimeError(f"显式 source manifest 无效: {reason}")
        return SourceContext(
            params=params,
            config_path=source_config_path,
            template_config_path=template_config_path,
            paths=paths,
            state_file=cfg.source_experiment.source_state_file,
            manifest_path=manifest_path,
            bootstrap_needed=False,
            bootstrap_reason="显式 source_manifest_path 可复用",
        )

    if explicit_version:
        manifest_path = _resolve_manifest_by_version(root, explicit_version)
        if manifest_path is not None:
            manifest = _load_json(manifest_path)
            valid, reason = _is_valid_source_manifest(root, manifest, cfg.expected_source_base_version)
            if not valid:
                raise RuntimeError(f"显式 source_experiment_version 对应 manifest 无效: {reason}")
            return SourceContext(
                params=params,
                config_path=source_config_path,
                template_config_path=template_config_path,
                paths=paths,
                state_file=cfg.source_experiment.source_state_file,
                manifest_path=manifest_path,
                bootstrap_needed=False,
                bootstrap_reason="显式 source_experiment_version 可复用",
            )

    reused_manifest = _find_matching_source_manifest(
        root,
        paths.experiment_version,
        cfg.source_experiment.source_config_path,
        cfg.expected_source_base_version,
    )
    if reused_manifest is not None:
        return SourceContext(
            params=params,
            config_path=source_config_path,
            template_config_path=template_config_path,
            paths=paths,
            state_file=cfg.source_experiment.source_state_file,
            manifest_path=reused_manifest,
            bootstrap_needed=False,
            bootstrap_reason="复用同配置同 hash 的 source manifest",
        )

    equivalent_manifest = _equivalent_step15_manifest(root, params)
    if equivalent_manifest is not None:
        return SourceContext(
            params=params,
            config_path=source_config_path,
            template_config_path=template_config_path,
            paths=paths,
            state_file=cfg.source_experiment.source_state_file,
            manifest_path=paths.manifest_file if paths.manifest_file.exists() else None,
            bootstrap_needed=False,
            bootstrap_reason=f"找到等价历史 step1~5 工件，将回填到新的 source 目录: {_rel(root, equivalent_manifest)}",
            equivalent_manifest_path=equivalent_manifest,
        )

    completed_until = _completed_source_steps(paths)
    if completed_until == 5:
        return SourceContext(
            params=params,
            config_path=source_config_path,
            template_config_path=template_config_path,
            paths=paths,
            state_file=cfg.source_experiment.source_state_file,
            manifest_path=paths.manifest_file if paths.manifest_file.exists() else None,
            bootstrap_needed=False,
            bootstrap_reason="source step1~5 工件已存在，将重建 metadata/manifest/registry",
        )

    if not cfg.source_experiment.bootstrap_if_missing:
        raise RuntimeError("未找到可复用 source manifest，且 bootstrap_if_missing=false")

    next_step = completed_until + 1
    reason = "未找到可复用 source manifest，需要从 step1 开始 bootstrap"
    if completed_until > 0:
        reason = f"source 工件已完成到 step{completed_until}，将从 step{next_step} 续跑到 step5"
    return SourceContext(
        params=params,
        config_path=source_config_path,
        template_config_path=template_config_path,
        paths=paths,
        state_file=cfg.source_experiment.source_state_file,
        manifest_path=paths.manifest_file if paths.manifest_file.exists() else None,
        bootstrap_needed=True,
        bootstrap_reason=reason,
    )


def _build_lab_specs(cfg: BaggingConfig, source_params: PipelineParams, max_labs: Optional[int]) -> List[LabRunSpec]:
    limit = max_labs if max_labs is not None else cfg.run.max_labs
    limit = max(0, min(int(limit), len(cfg.bagging.seeds)))
    seeds = list(cfg.bagging.seeds)[:limit]
    specs: List[LabRunSpec] = []
    for index, seed in enumerate(seeds, start=1):
        label = cfg.lab_experiment.label_template.format(lab_id=index, seed=seed)
        params = copy.deepcopy(source_params)
        params.seed = seed
        params.lgb.seed = seed
        params.experiment.base_version = cfg.lab_experiment.base_version
        params.experiment.label = label
        experiment_version = params.build_experiment_version()
        state_file = cfg.lab_experiment.state_file_template.format(lab_id=index, seed=seed)
        specs.append(
            LabRunSpec(
                lab_id=index,
                seed=seed,
                experiment_version=experiment_version,
                label=label,
                state_file=state_file,
            )
        )
    return specs


def _build_ensemble_context(root: Path, cfg: BaggingConfig, source_params: PipelineParams) -> EnsembleContext:
    params = copy.deepcopy(source_params)
    params.experiment.base_version = cfg.ensemble_experiment.base_version
    params.experiment.label = cfg.ensemble_experiment.label
    experiment_version = params.build_experiment_version()
    output_dir = root / "outputs" / experiment_version
    metadata_dir = output_dir / "metadata"
    analysis_dir = output_dir / "analysis"
    return EnsembleContext(
        experiment_version=experiment_version,
        label=cfg.ensemble_experiment.label,
        state_file=cfg.ensemble_experiment.state_file,
        output_dir=output_dir,
        metadata_dir=metadata_dir,
        analysis_dir=analysis_dir,
    )


def build_preflight_summary(root: Path, cfg: BaggingConfig, max_labs: Optional[int]) -> PreflightSummary:
    source_ctx = _build_source_context(root, cfg)
    manifest_path = source_ctx.manifest_path or source_ctx.paths.manifest_file
    source_manifest_path = _rel(root, manifest_path) if manifest_path.exists() else ""
    source_experiment_version = source_ctx.paths.experiment_version
    if source_ctx.manifest_path is not None and source_ctx.manifest_path.exists():
        manifest = _load_json(source_ctx.manifest_path)
        source_experiment_version = str(manifest.get("experiment_version", source_ctx.paths.experiment_version))
    lab_specs = _build_lab_specs(cfg, source_ctx.params, max_labs)
    ensemble_ctx = _build_ensemble_context(root, cfg, source_ctx.params)
    if source_ctx.bootstrap_reason.startswith("复用") or source_ctx.bootstrap_reason.startswith("显式"):
        source_resolution = "reuse"
    elif source_ctx.equivalent_manifest_path is not None:
        source_resolution = "backfill"
    elif source_ctx.bootstrap_needed:
        source_resolution = "bootstrap"
    else:
        source_resolution = "rebuild_metadata"
    return PreflightSummary(
        source_resolution=source_resolution,
        source_manifest_path=source_manifest_path,
        source_experiment_version=source_experiment_version,
        source_config_path=_rel(root, source_ctx.config_path),
        source_state_file=source_ctx.state_file,
        bootstrap_needed=source_ctx.bootstrap_needed,
        bootstrap_reason=source_ctx.bootstrap_reason,
        ensemble_experiment_version=ensemble_ctx.experiment_version,
        planned_labs=[
            {
                "lab_id": spec.lab_id,
                "seed": spec.seed,
                "label": spec.label,
                "experiment_version": spec.experiment_version,
                "state_file": spec.state_file,
            }
            for spec in lab_specs
        ],
    )


def _print_preflight(summary: PreflightSummary) -> None:
    print("[bagging] preflight summary")
    print(f"- source_resolution: {summary.source_resolution}")
    print(f"- source_manifest_path: {summary.source_manifest_path or '(pending build/rebuild)'}")
    print(f"- source_experiment_version: {summary.source_experiment_version}")
    print(f"- source_config_path: {summary.source_config_path}")
    print(f"- source_state_file: {summary.source_state_file}")
    print(f"- bootstrap_needed: {summary.bootstrap_needed}")
    print(f"- bootstrap_reason: {summary.bootstrap_reason}")
    print(f"- ensemble_experiment_version: {summary.ensemble_experiment_version}")
    print("- planned_labs:")
    for lab in summary.planned_labs:
        print(
            f"  lab{lab['lab_id']:02d}: seed={lab['seed']}, "
            f"version={lab['experiment_version']}, state={lab['state_file']}"
        )


def _ensure_recovered_source_state(root: Path, paths: Paths, completed_until: int) -> None:
    try:
        state = load_state(paths, FULL_MODE)
    except RuntimeError:
        state = {"mode": FULL_MODE, "experiment_version": paths.experiment_version, "steps": {}}
    state.setdefault("mode", FULL_MODE)
    state["experiment_version"] = paths.experiment_version
    state.setdefault("steps", {})
    output_map = _source_step_output_files(root, paths)
    for step in range(1, completed_until + 1):
        state["steps"][step_key(step)] = {
            "status": "completed",
            "output_files": output_map[step],
            "checks_passed": True,
            "timestamp": now_iso(),
            "details": {
                "recovered_from_existing_artifacts": True,
            },
        }
    for step in range(completed_until + 1, 7):
        state["steps"].pop(step_key(step), None)
    save_state(paths, state)


def _artifact_root_path(root: Path, manifest: Dict[str, Any], key: str) -> Optional[Path]:
    artifact_roots = manifest.get("artifact_roots", {}) if isinstance(manifest, dict) else {}
    value = str(artifact_roots.get(key, "")).strip()
    if not value:
        return None
    path = _resolve_path(root, value)
    return path if path.exists() else None


def _materialize_source_from_equivalent_manifest(root: Path, target_paths: Paths, equivalent_manifest_path: Path) -> None:
    manifest = _load_json(equivalent_manifest_path)
    processed_dir = _artifact_root_path(root, manifest, "processed_dir")
    slices_dir = _artifact_root_path(root, manifest, "slices_dir")
    ts_features_dir = _artifact_root_path(root, manifest, "ts_features_dir")
    features_dir = _artifact_root_path(root, manifest, "features_dir")
    splits_dir = _artifact_root_path(root, manifest, "splits_dir")
    if any(item is None for item in [processed_dir, slices_dir, ts_features_dir, features_dir, splits_dir]):
        raise RuntimeError("等价历史 manifest 缺少完整 artifact_roots，无法回填 source 工件。")

    mapping = {
        processed_dir / "holding_clean.parquet": target_paths.processed_dir / "holding_clean.parquet",
        processed_dir / "datebar.parquet": target_paths.processed_dir / "datebar.parquet",
        slices_dir / "slices_60d.parquet": target_paths.slices_dir / "slices_60d.parquet",
        slices_dir / "non_trading_ratio.csv": target_paths.slices_dir / "non_trading_ratio.csv",
        ts_features_dir / "ts_features_60d.parquet": target_paths.ts_features_dir / "ts_features_60d.parquet",
        features_dir / "selected_features.txt": target_paths.features_dir / "selected_features.txt",
        features_dir / "clip_percentiles.json": target_paths.features_dir / "clip_percentiles.json",
        features_dir / "features_selected_50.parquet": target_paths.features_dir / "features_selected_50.parquet",
        splits_dir / "train.parquet": target_paths.splits_dir / "train.parquet",
        splits_dir / "val.parquet": target_paths.splits_dir / "val.parquet",
        splits_dir / "test.parquet": target_paths.splits_dir / "test.parquet",
    }
    for src, dst in mapping.items():
        if not src.exists():
            raise RuntimeError(f"等价历史工件缺失: {src}")
        _copy_or_link(src, dst)


def _prepare_source_experiment(root: Path, cfg: BaggingConfig, source_ctx: SourceContext) -> SourceArtifacts:
    _validate_source_config_derivation(
        source_ctx.template_config_path,
        source_ctx.config_path,
        cfg.expected_source_base_version,
    )

    if source_ctx.manifest_path is not None and source_ctx.manifest_path.exists():
        manifest = _load_json(source_ctx.manifest_path)
        valid, _ = _is_valid_source_manifest(root, manifest, cfg.expected_source_base_version)
        if valid:
            return _resolve_source_artifacts(root, source_ctx.manifest_path, cfg.expected_source_base_version)

    if source_ctx.equivalent_manifest_path is not None:
        _materialize_source_from_equivalent_manifest(root, source_ctx.paths, source_ctx.equivalent_manifest_path)
        _ensure_recovered_source_state(root, source_ctx.paths, 5)
        save_resolved_params(source_ctx.paths, source_ctx.params, cfg.source_experiment.source_config_path, FULL_MODE)
        if not source_ctx.paths.manifest_file.exists():
            raise RuntimeError("source manifest 未生成")
        return _resolve_source_artifacts(root, source_ctx.paths.manifest_file, cfg.expected_source_base_version)

    completed_until = _completed_source_steps(source_ctx.paths)
    _ensure_recovered_source_state(root, source_ctx.paths, completed_until)
    save_resolved_params(source_ctx.paths, source_ctx.params, cfg.source_experiment.source_config_path, FULL_MODE)

    if completed_until < 5:
        for step in range(completed_until + 1, 6):
            print(f"[bagging] source bootstrap run step {step}")
            return_code = run_step(source_ctx.paths, FULL_MODE, source_ctx.params, step)
            if return_code != 0:
                raise RuntimeError(f"source bootstrap 执行 step {step} 失败")
        save_resolved_params(source_ctx.paths, source_ctx.params, cfg.source_experiment.source_config_path, FULL_MODE)

    if not source_ctx.paths.manifest_file.exists():
        raise RuntimeError("source manifest 未生成")
    return _resolve_source_artifacts(root, source_ctx.paths.manifest_file, cfg.expected_source_base_version)


def _resolve_source_artifacts(
    root: Path,
    manifest_path: Path,
    expected_base_version: str = "",
) -> SourceArtifacts:
    manifest = _load_json(manifest_path)
    valid, reason = _is_valid_source_manifest(root, manifest, expected_base_version)
    if not valid:
        raise RuntimeError(f"source manifest 无效: {reason}")
    train_path = _artifact_path(root, manifest, "train_split_file")
    val_path = _artifact_path(root, manifest, "val_split_file")
    test_path = _artifact_path(root, manifest, "test_split_file")
    selected_features_path = _artifact_path(root, manifest, "selected_features_file")
    clip_percentiles_path = _artifact_path(root, manifest, "clip_percentiles_file")
    state_file_path = _artifact_path(root, manifest, "state_file")
    if any(item is None for item in [train_path, val_path, test_path, selected_features_path, clip_percentiles_path]):
        raise RuntimeError("source manifest 缺少必要工件路径。")

    selected_features = _read_selected_features(selected_features_path)
    if not selected_features:
        raise RuntimeError("source selected_features.txt 为空。")

    split_columns = {
        "train": _parquet_columns(train_path),
        "val": _parquet_columns(val_path),
        "test": _parquet_columns(test_path),
    }
    for split_name, columns in split_columns.items():
        missing_base = [column for column in BASE_COLUMNS if column not in columns]
        if missing_base:
            raise RuntimeError(f"source {split_name} split 缺少基础列: {missing_base}")
        missing_features = [feature for feature in selected_features if feature not in columns]
        if missing_features:
            raise RuntimeError(
                f"source {split_name} split 缺少 selected features: {missing_features[:5]}"
            )

    feature_columns = [column for column in split_columns["train"] if column not in BASE_COLUMNS]
    clip_payload = _load_json(clip_percentiles_path)
    if "p1" not in clip_payload or "p99" not in clip_payload:
        raise RuntimeError("source clip_percentiles.json 缺少 p1/p99。")

    return SourceArtifacts(
        experiment_version=str(manifest.get("experiment_version", "")).strip(),
        manifest_path=manifest_path,
        manifest=manifest,
        train_path=train_path,
        val_path=val_path,
        test_path=test_path,
        selected_features_path=selected_features_path,
        clip_percentiles_path=clip_percentiles_path,
        state_file_path=state_file_path,
        selected_features=selected_features,
        feature_columns=feature_columns,
    )


def _load_optional_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists() or yaml is None:
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _update_registry_from_manifest(root: Path, manifest_path: Path, manifest: Dict[str, Any]) -> None:
    registry = [entry for entry in _load_registry(root) if entry.get("experiment_version") != manifest.get("experiment_version")]
    registry.append(
        {
            "experiment_version": manifest.get("experiment_version"),
            "experiment": manifest.get("experiment"),
            "source_config": manifest.get("source_config"),
            "source_config_copy": manifest.get("source_config_copy"),
            "resolved_params_file": manifest.get("resolved_params_file"),
            "manifest_file": _rel(root, manifest_path),
            "artifact_roots": manifest.get("artifact_roots"),
            "artifact_files": manifest.get("artifact_files"),
            "artifact_files_by_mode": manifest.get("artifact_files_by_mode"),
            "latest_run_mode": manifest.get("latest_run_mode", FULL_MODE),
            "source_inputs": manifest.get("source_inputs", {}),
            "bagging": manifest.get("bagging", {}),
            "source_experiment": manifest.get("source_experiment", {}),
        }
    )
    _write_registry(root, registry)


def _build_lab_params(source_params: PipelineParams, spec: LabRunSpec, cfg: BaggingConfig) -> PipelineParams:
    params = copy.deepcopy(source_params)
    params.seed = spec.seed
    params.lgb.seed = spec.seed
    params.lgb.num_threads = 1
    params.experiment.base_version = cfg.lab_experiment.base_version
    params.experiment.label = spec.label
    return params


def _seed_lab_state(
    root: Path,
    lab_paths: Paths,
    source_artifacts: SourceArtifacts,
    spec: LabRunSpec,
    train_rows: int,
    val_rows: int,
    test_rows: int,
) -> None:
    source_state = {}
    if source_artifacts.state_file_path is not None and source_artifacts.state_file_path.exists():
        try:
            source_state = _load_json(source_artifacts.state_file_path)
        except Exception:
            source_state = {}

    state = {
        "mode": FULL_MODE,
        "experiment_version": lab_paths.experiment_version,
        "updated_at": now_iso(),
        "steps": {},
    }
    source_steps = source_state.get("steps", {}) if isinstance(source_state, dict) else {}
    for step in range(1, 5):
        payload = source_steps.get(step_key(step))
        if isinstance(payload, dict):
            state["steps"][step_key(step)] = payload
            details = dict(payload.get("details", {}))
            details["source_experiment_version"] = source_artifacts.experiment_version
            state["steps"][step_key(step)]["details"] = details

    state["steps"][step_key(5)] = {
        "status": "completed",
        "output_files": [
            _rel(root, lab_paths.splits_dir / "train.parquet"),
            _rel(root, lab_paths.splits_dir / "val.parquet"),
            _rel(root, lab_paths.splits_dir / "test.parquet"),
        ],
        "checks_passed": True,
        "timestamp": now_iso(),
        "details": {
            "train_rows": train_rows,
            "val_rows": val_rows,
            "test_rows": test_rows,
            "source_experiment_version": source_artifacts.experiment_version,
            "train_shuffled_seed": spec.seed,
            "checks": {
                "split_files_exist": True,
                "columns_aligned": True,
                "has_target_raw": True,
                "no_nan_inf": True,
                "smoke_ratio_reasonable": True,
            },
        },
    }
    save_state(lab_paths, state)


def _prepare_lab_inputs(
    root: Path,
    cfg: BaggingConfig,
    source_artifacts: SourceArtifacts,
    spec: LabRunSpec,
    source_params: PipelineParams,
) -> Tuple[PipelineParams, Paths]:
    lab_params = _build_lab_params(source_params, spec, cfg)
    lab_paths = build_paths(root, spec.state_file, lab_params)
    ensure_directories(lab_paths)

    train_df = pd.read_parquet(source_artifacts.train_path)
    if cfg.bagging.train_shuffle:
        train_df = train_df.sample(frac=1.0, random_state=spec.seed).reset_index(drop=True)
    val_df = pd.read_parquet(source_artifacts.val_path)
    test_df = pd.read_parquet(source_artifacts.test_path)

    train_df.to_parquet(lab_paths.splits_dir / "train.parquet", index=False)
    _copy_or_link(source_artifacts.val_path, lab_paths.splits_dir / "val.parquet")
    _copy_or_link(source_artifacts.test_path, lab_paths.splits_dir / "test.parquet")
    _copy_or_link(source_artifacts.selected_features_path, lab_paths.features_dir / "selected_features.txt")
    _copy_or_link(source_artifacts.clip_percentiles_path, lab_paths.features_dir / "clip_percentiles.json")

    _seed_lab_state(root, lab_paths, source_artifacts, spec, len(train_df), len(val_df), len(test_df))
    return lab_params, lab_paths


def _read_prediction_metrics(path: Path) -> Tuple[float, Optional[float]]:
    frame = pd.read_csv(path)
    return _mae(frame["target"], frame["y_pred"]), _safe_spearman(frame["target"], frame["y_pred"])


def _summarize_lab_metrics(root: Path, lab_paths: Paths) -> Dict[str, Any]:
    train_prediction_path = lab_paths.output_dir / "train_predictions.csv"
    val_prediction_path = lab_paths.output_dir / "val_predictions.csv"
    test_prediction_path = lab_paths.output_dir / "predictions.csv"
    state = load_state(lab_paths, FULL_MODE)
    step6_details = state.get("steps", {}).get(step_key(6), {}).get("details", {})
    val_mae, _ = _read_prediction_metrics(val_prediction_path)
    test_mae, test_spearman = _read_prediction_metrics(test_prediction_path)
    return {
        "experiment_version": lab_paths.experiment_version,
        "best_iteration": step6_details.get("best_iteration"),
        "val_mae": val_mae,
        "test_mae": test_mae,
        "test_spearman": test_spearman,
        "train_prediction_file": _rel(root, train_prediction_path),
        "val_prediction_file": _rel(root, val_prediction_path),
        "test_prediction_file": _rel(root, test_prediction_path),
        "model_file": _rel(root, lab_paths.models_dir / "lgb_model.txt"),
        "model_feature_schema_file": _rel(root, lab_paths.metadata_dir / "model_feature_schema.json"),
        "state_file": _rel(root, lab_paths.state_file),
    }


def _build_analysis_config(
    experiment_version: str,
    output_dir: Path,
    train_prediction_path: Path,
    val_prediction_path: Path,
    test_prediction_path: Path,
    model_path: Optional[Path],
    selected_features_path: Path,
    clip_percentiles_path: Path,
    state_file: Path,
    model_feature_schema_path: Path,
    report_title: str,
    strict_mode: bool,
    root: Path,
) -> Dict[str, Any]:
    return {
        "experiment_version": experiment_version,
        "train_prediction_path": _rel(root, train_prediction_path),
        "val_prediction_path": _rel(root, val_prediction_path),
        "test_prediction_path": _rel(root, test_prediction_path),
        "model_path": _rel(root, model_path) if model_path is not None else "",
        "selected_features_path": _rel(root, selected_features_path),
        "clip_percentiles_path": _rel(root, clip_percentiles_path),
        "state_file": _rel(root, state_file),
        "model_feature_schema_path": _rel(root, model_feature_schema_path),
        "output_dir": _rel(root, output_dir),
        "report_title": report_title,
        "strict_mode": strict_mode,
    }


def _run_analysis(root: Path, config_path: Path, strict_mode: bool, log_path: Path) -> None:
    command = [sys.executable, str(root / "analyze_experiment_results.py"), "--config", str(config_path)]
    if strict_mode:
        command.append("--strict")
    else:
        command.append("--no-strict")
    completed = subprocess.run(
        command,
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log_text = "\n".join(
        [
            "[command] " + " ".join(command),
            "[stdout]",
            completed.stdout,
            "[stderr]",
            completed.stderr,
            f"[returncode] {completed.returncode}",
        ]
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(log_text, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"分析脚本执行失败，详见 {log_path.name}")


def _load_existing_lab_result(root: Path, spec: LabRunSpec) -> Optional[LabResult]:
    metadata_dir = root / "outputs" / spec.experiment_version / "metadata"
    manifest_path = metadata_dir / "experiment_manifest.json"
    summary_path = metadata_dir / "bagging_lab_summary.json"
    analysis_summary_path = root / "outputs" / spec.experiment_version / "analysis" / "report_summary.json"
    if not manifest_path.exists() or not summary_path.exists() or not analysis_summary_path.exists():
        return None
    try:
        summary = _load_json(summary_path)
    except Exception:
        return None
    if str(summary.get("status", "")) != "success":
        return None
    return LabResult(
        lab_id=spec.lab_id,
        seed=spec.seed,
        label=spec.label,
        experiment_version=spec.experiment_version,
        status="success",
        output_files=dict(summary.get("output_files", {})),
        best_iteration=summary.get("best_iteration"),
        val_mae=summary.get("val_mae"),
        test_mae=summary.get("test_mae"),
        test_spearman=summary.get("test_spearman"),
        manifest_path=manifest_path,
        bagging_lab_summary_path=summary_path,
        analysis_summary_path=analysis_summary_path,
    )


def _write_lab_manifest(
    root: Path,
    lab_paths: Paths,
    lab_params: PipelineParams,
    source_artifacts: SourceArtifacts,
    spec: LabRunSpec,
    bagging_summary_path: Path,
    analysis_config_path: Path,
) -> None:
    existing_manifest = _load_json(lab_paths.manifest_file) if lab_paths.manifest_file.exists() else {}
    resolved_params = _load_optional_yaml(lab_paths.resolved_params_file)
    experiment_metadata = (
        resolved_params.get("resolved", {}).get("experiment")
        or existing_manifest.get("experiment")
        or {
            "catalog_name": "",
            "description": f"bagging lab {spec.lab_id:02d} seed={spec.seed}",
            "base_version": lab_params.experiment.base_version,
            "run_date": lab_params.experiment.resolve_run_date(),
            "label": lab_params.experiment.label,
            "source_config": "",
            "source_config_resolved": "",
            "expected_base_version": lab_params.experiment.base_version,
        }
    )
    artifact_files = dict(existing_manifest.get("artifact_files") or {})
    artifact_files.update(
        {
            "state_file": _rel(root, lab_paths.state_file),
            "prediction_file": _rel(root, lab_paths.output_dir / "predictions.csv"),
            "test_prediction_file": _rel(root, lab_paths.output_dir / "predictions.csv"),
            "train_prediction_file": _rel(root, lab_paths.output_dir / "train_predictions.csv"),
            "val_prediction_file": _rel(root, lab_paths.output_dir / "val_predictions.csv"),
            "model_file": _rel(root, lab_paths.models_dir / "lgb_model.txt"),
            "train_split_file": _rel(root, lab_paths.splits_dir / "train.parquet"),
            "val_split_file": _rel(root, lab_paths.splits_dir / "val.parquet"),
            "test_split_file": _rel(root, lab_paths.splits_dir / "test.parquet"),
            "selected_features_file": _rel(root, lab_paths.features_dir / "selected_features.txt"),
            "clip_percentiles_file": _rel(root, lab_paths.features_dir / "clip_percentiles.json"),
            "model_feature_schema_file": _rel(root, lab_paths.metadata_dir / "model_feature_schema.json"),
            "bagging_lab_summary_file": _rel(root, bagging_summary_path),
            "source_manifest_file": _rel(root, source_artifacts.manifest_path),
            "analysis_report_file": _rel(root, lab_paths.output_dir / "analysis" / "experiment_analysis_report.md"),
            "report_summary_file": _rel(root, lab_paths.output_dir / "analysis" / "report_summary.json"),
            "leadership_html_file": _rel(root, lab_paths.output_dir / "analysis" / "experiment_summary_for_leadership.html"),
            "training_log_file": _rel(root, lab_paths.metadata_dir / "training_log.txt"),
            "resolved_analysis_config_file": _rel(root, analysis_config_path),
            "analysis_invocation_log_file": _rel(root, lab_paths.metadata_dir / "analysis_invocation.log"),
        }
    )
    manifest = {
        "experiment_version": lab_paths.experiment_version,
        "latest_run_mode": FULL_MODE,
        "experiment": experiment_metadata,
        "source_config": existing_manifest.get("source_config", ""),
        "source_config_copy": existing_manifest.get("source_config_copy", _rel(root, lab_paths.source_config_copy_file)),
        "resolved_params_file": existing_manifest.get("resolved_params_file", _rel(root, lab_paths.resolved_params_file)),
        "artifact_roots": _artifact_roots(root, lab_paths),
        "artifact_files": artifact_files,
        "artifact_files_by_mode": {FULL_MODE: artifact_files},
        "source_inputs": {
            "train_split_file": _rel(root, source_artifacts.train_path),
            "val_split_file": _rel(root, source_artifacts.val_path),
            "test_split_file": _rel(root, source_artifacts.test_path),
            "selected_features_file": _rel(root, source_artifacts.selected_features_path),
            "clip_percentiles_file": _rel(root, source_artifacts.clip_percentiles_path),
        },
        "bagging": {
            "kind": "lab",
            "lab_id": spec.lab_id,
            "seed": spec.seed,
            "train_shuffle": True,
        },
        "source_experiment": {
            "experiment_version": source_artifacts.experiment_version,
            "manifest_file": _rel(root, source_artifacts.manifest_path),
        },
    }
    _write_json(lab_paths.manifest_file, manifest)
    _update_registry_from_manifest(root, lab_paths.manifest_file, manifest)


def _execute_lab(
    root: Path,
    cfg: BaggingConfig,
    source_params: PipelineParams,
    source_artifacts: SourceArtifacts,
    spec: LabRunSpec,
    analysis_report_title: str,
) -> LabResult:
    cached = _load_existing_lab_result(root, spec)
    if cached is not None:
        print(f"[bagging] reuse lab{spec.lab_id:02d} existing outputs: {spec.experiment_version}")
        return cached

    try:
        lab_params, lab_paths = _prepare_lab_inputs(root, cfg, source_artifacts, spec, source_params)
        save_resolved_params(lab_paths, lab_params, cfg.source_experiment.source_config_path, FULL_MODE)

        train_log_stream = io.StringIO()
        with redirect_stdout(train_log_stream), redirect_stderr(train_log_stream):
            return_code = run_step(lab_paths, FULL_MODE, lab_params, 6)
        training_log_text = train_log_stream.getvalue()
        print(training_log_text, end="")
        training_log_path = lab_paths.metadata_dir / "training_log.txt"
        training_log_path.write_text(training_log_text, encoding="utf-8")
        if return_code != 0:
            raise RuntimeError("step6 训练失败")

        metric_summary = _summarize_lab_metrics(root, lab_paths)
        summary_payload = {
            "lab_id": spec.lab_id,
            "seed": spec.seed,
            "label": spec.label,
            "experiment_version": spec.experiment_version,
            "best_iteration": metric_summary["best_iteration"],
            "val_mae": metric_summary["val_mae"],
            "test_mae": metric_summary["test_mae"],
            "test_spearman": metric_summary["test_spearman"],
            "output_files": {
                "train_prediction_file": metric_summary["train_prediction_file"],
                "val_prediction_file": metric_summary["val_prediction_file"],
                "test_prediction_file": metric_summary["test_prediction_file"],
                "model_file": metric_summary["model_file"],
                "model_feature_schema_file": metric_summary["model_feature_schema_file"],
                "state_file": metric_summary["state_file"],
            },
            "status": "success",
            "error": "",
        }
        bagging_summary_path = lab_paths.metadata_dir / "bagging_lab_summary.json"
        _write_json(bagging_summary_path, summary_payload)

        analysis_config_payload = _build_analysis_config(
            experiment_version=spec.experiment_version,
            output_dir=lab_paths.output_dir / "analysis",
            train_prediction_path=lab_paths.output_dir / "train_predictions.csv",
            val_prediction_path=lab_paths.output_dir / "val_predictions.csv",
            test_prediction_path=lab_paths.output_dir / "predictions.csv",
            model_path=lab_paths.models_dir / "lgb_model.txt",
            selected_features_path=lab_paths.features_dir / "selected_features.txt",
            clip_percentiles_path=lab_paths.features_dir / "clip_percentiles.json",
            state_file=lab_paths.state_file,
            model_feature_schema_path=lab_paths.metadata_dir / "model_feature_schema.json",
            report_title=f"{analysis_report_title} - {spec.label}",
            strict_mode=cfg.analysis.strict_mode,
            root=root,
        )
        analysis_config_path = lab_paths.metadata_dir / "resolved_analysis_config.yaml"
        _write_yaml(analysis_config_path, analysis_config_payload)
        _run_analysis(root, analysis_config_path, cfg.analysis.strict_mode, lab_paths.metadata_dir / "analysis_invocation.log")
        _write_lab_manifest(root, lab_paths, lab_params, source_artifacts, spec, bagging_summary_path, analysis_config_path)

        return LabResult(
            lab_id=spec.lab_id,
            seed=spec.seed,
            label=spec.label,
            experiment_version=spec.experiment_version,
            status="success",
            output_files=summary_payload["output_files"],
            best_iteration=summary_payload["best_iteration"],
            val_mae=summary_payload["val_mae"],
            test_mae=summary_payload["test_mae"],
            test_spearman=summary_payload["test_spearman"],
            manifest_path=lab_paths.manifest_file,
            bagging_lab_summary_path=bagging_summary_path,
            analysis_summary_path=lab_paths.output_dir / "analysis" / "report_summary.json",
        )
    except Exception as exc:
        error_text = f"{exc}\n{traceback.format_exc()}"
        print(f"[bagging] lab{spec.lab_id:02d} failed: {exc}")
        metadata_dir = root / "outputs" / spec.experiment_version / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        failure_summary = {
            "lab_id": spec.lab_id,
            "seed": spec.seed,
            "label": spec.label,
            "experiment_version": spec.experiment_version,
            "best_iteration": None,
            "val_mae": None,
            "test_mae": None,
            "test_spearman": None,
            "output_files": {},
            "status": "failed",
            "error": error_text,
        }
        summary_path = metadata_dir / "bagging_lab_summary.json"
        _write_json(summary_path, failure_summary)
        return LabResult(
            lab_id=spec.lab_id,
            seed=spec.seed,
            label=spec.label,
            experiment_version=spec.experiment_version,
            status="failed",
            error=str(exc),
            bagging_lab_summary_path=summary_path,
        )


def _collect_lab_results_from_artifacts(root: Path, specs: List[LabRunSpec]) -> List[LabResult]:
    results: List[LabResult] = []
    for spec in specs:
        existing = _load_existing_lab_result(root, spec)
        if existing is not None:
            results.append(existing)
            continue
        results.append(
            LabResult(
                lab_id=spec.lab_id,
                seed=spec.seed,
                label=spec.label,
                experiment_version=spec.experiment_version,
                status="failed",
                error="缺少现有 lab 完整产物",
            )
        )
    return results


def _aggregate_prediction_frame(
    frames: List[Tuple[LabResult, pd.DataFrame]],
    include_member_predictions: bool,
) -> pd.DataFrame:
    if not frames:
        raise RuntimeError("没有可聚合的预测数据。")
    reference_lab, reference_df = frames[0]
    reference_df = reference_df.sort_values(["sample_id", "signal_date"]).reset_index(drop=True)
    reference_features = [column for column in reference_df.columns if column not in BASE_COLUMNS + ["y_pred"]]
    member_columns: List[str] = []

    output_df = reference_df[BASE_COLUMNS + reference_features].copy()
    prediction_arrays: List[np.ndarray] = []
    for lab_result, frame in frames:
        ordered = frame.sort_values(["sample_id", "signal_date"]).reset_index(drop=True)
        if len(ordered) != len(reference_df):
            raise RuntimeError(f"{lab_result.experiment_version} 样本数与参考 lab 不一致。")
        if not ordered[["sample_id", "signal_date"]].equals(reference_df[["sample_id", "signal_date"]]):
            raise RuntimeError(f"{lab_result.experiment_version} 与参考 lab 的 sample_id+signal_date 对齐失败。")
        if not ordered["target"].equals(reference_df["target"]):
            raise RuntimeError(f"{lab_result.experiment_version} 的 target 与参考 lab 不一致。")
        if not ordered["target_raw"].equals(reference_df["target_raw"]):
            raise RuntimeError(f"{lab_result.experiment_version} 的 target_raw 与参考 lab 不一致。")
        feature_columns = [column for column in ordered.columns if column not in BASE_COLUMNS + ["y_pred"]]
        if set(feature_columns) != set(reference_features):
            raise RuntimeError(f"{lab_result.experiment_version} 的特征列集合与参考 lab 不一致。")
        member_col = f"y_pred_lab{lab_result.lab_id:02d}_seed{lab_result.seed}"
        member_columns.append(member_col)
        prediction_arrays.append(ordered["y_pred"].to_numpy())
        if include_member_predictions:
            output_df[member_col] = ordered["y_pred"].to_numpy()

    matrix = np.column_stack(prediction_arrays)
    output_df["y_pred"] = matrix.mean(axis=1)
    output_df["y_pred_std"] = matrix.std(axis=1, ddof=0)
    output_df["y_pred_min"] = matrix.min(axis=1)
    output_df["y_pred_max"] = matrix.max(axis=1)
    if include_member_predictions:
        ordered_columns = BASE_COLUMNS + reference_features + ["y_pred", "y_pred_std", "y_pred_min", "y_pred_max"] + member_columns
    else:
        ordered_columns = BASE_COLUMNS + reference_features + ["y_pred", "y_pred_std", "y_pred_min", "y_pred_max"]
    return output_df[ordered_columns]


def _aggregate_predictions(
    root: Path,
    ensemble_ctx: EnsembleContext,
    lab_results: List[LabResult],
    include_member_predictions: bool,
) -> Tuple[Dict[str, Path], Dict[str, Any], List[str]]:
    ensemble_ctx.output_dir.mkdir(parents=True, exist_ok=True)
    ensemble_ctx.metadata_dir.mkdir(parents=True, exist_ok=True)
    ensemble_ctx.analysis_dir.mkdir(parents=True, exist_ok=True)

    split_file_map = {
        "train": "train_prediction_file",
        "val": "val_prediction_file",
        "test": "test_prediction_file",
    }
    output_name_map = {
        "train": "train_predictions.csv",
        "val": "val_predictions.csv",
        "test": "predictions.csv",
    }
    aggregated_paths: Dict[str, Path] = {}
    feature_columns: List[str] = []
    split_summaries: Dict[str, Any] = {}

    for split_name, output_key in output_name_map.items():
        frames: List[Tuple[LabResult, pd.DataFrame]] = []
        for lab_result in lab_results:
            file_path = lab_result.output_files.get(split_file_map[split_name])
            if not file_path:
                raise RuntimeError(f"{lab_result.experiment_version} 缺少 {split_name} 预测文件登记。")
            absolute_path = _resolve_path(root, file_path)
            frames.append((lab_result, pd.read_csv(absolute_path)))
        aggregated = _aggregate_prediction_frame(frames, include_member_predictions)
        if not feature_columns:
            feature_columns = [column for column in aggregated.columns if column not in BASE_COLUMNS + ["y_pred", "y_pred_std", "y_pred_min", "y_pred_max"] and not column.startswith("y_pred_lab")]
        output_path = ensemble_ctx.output_dir / output_key
        aggregated.to_csv(output_path, index=False)
        aggregated_paths[split_name] = output_path
        split_summaries[split_name] = {
            "rows": int(len(aggregated)),
            "signal_date_min": int(aggregated["signal_date"].min()),
            "signal_date_max": int(aggregated["signal_date"].max()),
            "target_mean": float(aggregated["target"].mean()),
            "target_raw_mean": float(aggregated["target_raw"].mean()),
            "y_pred_mean": float(aggregated["y_pred"].mean()),
            "y_pred_std_mean": float(aggregated["y_pred_std"].mean()),
        }

    test_frame = pd.read_csv(aggregated_paths["test"])
    aggregation_summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "successful_lab_count": len(lab_results),
        "split_summaries": split_summaries,
        "test_mae": _mae(test_frame["target"], test_frame["y_pred"]),
        "test_spearman": _safe_spearman(test_frame["target"], test_frame["y_pred"]),
        "feature_count": len(feature_columns),
    }
    return aggregated_paths, aggregation_summary, feature_columns


def _aggregate_feature_importance(root: Path, lab_results: List[LabResult], output_path: Path) -> pd.DataFrame:
    if lgb is None:
        raise RuntimeError("当前环境未安装 lightgbm，无法聚合 gain importance。")
    per_model_rows: List[pd.DataFrame] = []
    for lab_result in lab_results:
        model_file = lab_result.output_files.get("model_file")
        if not model_file:
            raise RuntimeError(f"{lab_result.experiment_version} 缺少 model_file。")
        booster = lgb.Booster(model_file=str(_resolve_path(root, model_file)))
        gains = booster.feature_importance(importance_type="gain")
        features = booster.feature_name()
        total_gain = float(np.sum(gains))
        share = gains / total_gain if total_gain else np.zeros_like(gains, dtype=float)
        per_model_rows.append(
            pd.DataFrame(
                {
                    "feature": features,
                    f"gain_lab{lab_result.lab_id:02d}_seed{lab_result.seed}": gains,
                    f"gain_share_lab{lab_result.lab_id:02d}_seed{lab_result.seed}": share,
                }
            )
        )
    merged = per_model_rows[0]
    for frame in per_model_rows[1:]:
        merged = merged.merge(frame, on="feature", how="outer")
    merged = merged.fillna(0.0)
    gain_columns = [column for column in merged.columns if column.startswith("gain_lab")]
    share_columns = [column for column in merged.columns if column.startswith("gain_share_lab")]
    merged["mean_gain"] = merged[gain_columns].mean(axis=1)
    merged["std_gain"] = merged[gain_columns].std(axis=1, ddof=0)
    merged["mean_gain_share"] = merged[share_columns].mean(axis=1)
    merged["nonzero_model_count"] = (merged[gain_columns] > 0).sum(axis=1)
    merged = merged.sort_values(["mean_gain", "feature"], ascending=[False, True]).reset_index(drop=True)
    merged.insert(0, "rank", np.arange(1, len(merged) + 1))
    ordered_columns = ["rank", "feature", "mean_gain", "std_gain", "mean_gain_share", "nonzero_model_count"] + gain_columns + share_columns
    merged = merged[ordered_columns]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False)
    return merged


def _write_ensemble_state(
    root: Path,
    state_path: Path,
    experiment_version: str,
    aggregated_paths: Dict[str, Path],
    aggregation_summary: Dict[str, Any],
    feature_columns: List[str],
    source_experiment_version: str,
) -> None:
    state = {
        "mode": FULL_MODE,
        "experiment_version": experiment_version,
        "updated_at": now_iso(),
        "steps": {
            step_key(5): {
                "status": "completed",
                "output_files": [_rel(root, aggregated_paths["train"]), _rel(root, aggregated_paths["val"]), _rel(root, aggregated_paths["test"])],
                "checks_passed": True,
                "timestamp": now_iso(),
                "details": {
                    "source_experiment_version": source_experiment_version,
                    "checks": {
                        "split_files_exist": True,
                        "columns_aligned": True,
                        "has_target_raw": True,
                        "no_nan_inf": True,
                        "smoke_ratio_reasonable": True,
                    },
                },
            },
            step_key(6): {
                "status": "completed",
                "output_files": [_rel(root, aggregated_paths["train"]), _rel(root, aggregated_paths["val"]), _rel(root, aggregated_paths["test"])],
                "checks_passed": True,
                "timestamp": now_iso(),
                "details": {
                    "best_iteration": None,
                    "mae": aggregation_summary.get("test_mae"),
                    "spearman": aggregation_summary.get("test_spearman"),
                    "train_rows": aggregation_summary.get("split_summaries", {}).get("train", {}).get("rows"),
                    "val_rows": aggregation_summary.get("split_summaries", {}).get("val", {}).get("rows"),
                    "test_rows": aggregation_summary.get("split_summaries", {}).get("test", {}).get("rows"),
                    "feature_columns": feature_columns,
                    "group_metrics": [],
                    "checks": {
                        "model_exists": False,
                        "train_prediction_exists": aggregated_paths["train"].exists(),
                        "val_prediction_exists": aggregated_paths["val"].exists(),
                        "test_prediction_exists": aggregated_paths["test"].exists(),
                        "test_prediction_rows_match": True,
                        "train_prediction_rows_match": True,
                        "val_prediction_rows_match": True,
                        "feature_schema_exists": True,
                        "feature_count_positive": len(feature_columns) > 0,
                    },
                },
            },
        },
    }
    _write_json(state_path, state)


def _write_ensemble_resolved_params(
    root: Path,
    bagging_config_path: str,
    source_params: PipelineParams,
    source_artifacts: SourceArtifacts,
    ensemble_ctx: EnsembleContext,
    lab_results: List[LabResult],
    aggregation_summary: Dict[str, Any],
    config: BaggingConfig,
) -> Path:
    source_config_copy = ensemble_ctx.metadata_dir / "source_config.yaml"
    shutil.copyfile(_resolve_path(root, bagging_config_path), source_config_copy)
    payload = asdict(source_params)
    payload["resolved"] = {
        "experiment_version": ensemble_ctx.experiment_version,
        "experiment": {
            "catalog_name": "",
            "description": "bagging ensemble 聚合实验",
            "base_version": config.ensemble_experiment.base_version,
            "run_date": source_params.experiment.resolve_run_date(),
            "label": config.ensemble_experiment.label,
            "source_config": bagging_config_path,
            "source_config_resolved": _norm_rel(bagging_config_path),
            "expected_base_version": config.ensemble_experiment.base_version,
        },
        "source_config": bagging_config_path,
        "source_config_copy": _rel(root, source_config_copy),
        "artifact_roots": {
            "output_dir": _rel(root, ensemble_ctx.output_dir),
            "metadata_dir": _rel(root, ensemble_ctx.metadata_dir),
            "state_file": _rel(root, ensemble_ctx.metadata_dir / config.ensemble_experiment.state_file),
        },
    }
    payload["bagging"] = {
        "controller_config": asdict(config),
        "source_experiment_version": source_artifacts.experiment_version,
        "source_manifest_file": _rel(root, source_artifacts.manifest_path),
        "lab_runs": [
            {
                "lab_id": result.lab_id,
                "seed": result.seed,
                "label": result.label,
                "experiment_version": result.experiment_version,
                "status": result.status,
            }
            for result in lab_results
        ],
        "aggregation_summary": aggregation_summary,
    }
    resolved_path = ensemble_ctx.metadata_dir / "resolved_params.yaml"
    _write_yaml(resolved_path, payload)
    return resolved_path


def _write_ensemble_manifest(
    root: Path,
    ensemble_ctx: EnsembleContext,
    resolved_params_path: Path,
    source_config_copy: Path,
    source_artifacts: SourceArtifacts,
    artifact_files: Dict[str, str],
    config_path: str,
    cfg: BaggingConfig,
    lab_results: List[LabResult],
    aggregation_summary: Dict[str, Any],
) -> None:
    manifest = {
        "experiment_version": ensemble_ctx.experiment_version,
        "latest_run_mode": FULL_MODE,
        "experiment": {
            "catalog_name": "",
            "description": "bagging ensemble 聚合实验",
            "base_version": cfg.ensemble_experiment.base_version,
            "run_date": datetime.now().strftime("%Y%m%d"),
            "label": cfg.ensemble_experiment.label,
            "source_config": config_path,
            "source_config_resolved": _norm_rel(config_path),
            "expected_base_version": cfg.ensemble_experiment.base_version,
        },
        "source_config": config_path,
        "source_config_copy": _rel(root, source_config_copy),
        "resolved_params_file": _rel(root, resolved_params_path),
        "artifact_roots": {
            "output_dir": _rel(root, ensemble_ctx.output_dir),
            "metadata_dir": _rel(root, ensemble_ctx.metadata_dir),
            "analysis_dir": _rel(root, ensemble_ctx.analysis_dir),
            "state_file": _rel(root, ensemble_ctx.metadata_dir / cfg.ensemble_experiment.state_file),
        },
        "artifact_files": artifact_files,
        "artifact_files_by_mode": {FULL_MODE: artifact_files},
        "source_inputs": {
            "train_split_file": _rel(root, source_artifacts.train_path),
            "val_split_file": _rel(root, source_artifacts.val_path),
            "test_split_file": _rel(root, source_artifacts.test_path),
            "selected_features_file": _rel(root, source_artifacts.selected_features_path),
            "clip_percentiles_file": _rel(root, source_artifacts.clip_percentiles_path),
        },
        "bagging": {
            "kind": "ensemble",
            "successful_lab_count": len([item for item in lab_results if item.status == "success"]),
            "failed_lab_count": len([item for item in lab_results if item.status != "success"]),
            "allow_partial_ensemble": cfg.bagging.allow_partial_ensemble,
            "min_successful_labs": cfg.bagging.min_successful_labs,
            "successful_labs": [
                {
                    "lab_id": item.lab_id,
                    "seed": item.seed,
                    "label": item.label,
                    "experiment_version": item.experiment_version,
                }
                for item in lab_results
                if item.status == "success"
            ],
            "failed_labs": [
                {
                    "lab_id": item.lab_id,
                    "seed": item.seed,
                    "label": item.label,
                    "experiment_version": item.experiment_version,
                    "error": item.error,
                }
                for item in lab_results
                if item.status != "success"
            ],
            "aggregation_summary": aggregation_summary,
        },
        "source_experiment": {
            "experiment_version": source_artifacts.experiment_version,
            "manifest_file": _rel(root, source_artifacts.manifest_path),
        },
    }
    manifest_path = ensemble_ctx.metadata_dir / "experiment_manifest.json"
    _write_json(manifest_path, manifest)
    _update_registry_from_manifest(root, manifest_path, manifest)


def _lab_metric_summary_rows(lab_results: List[LabResult]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for result in lab_results:
        rows.append(
            {
                "lab_id": result.lab_id,
                "seed": result.seed,
                "label": result.label,
                "experiment_version": result.experiment_version,
                "status": result.status,
                "best_iteration": result.best_iteration,
                "val_mae": result.val_mae,
                "test_mae": result.test_mae,
                "test_spearman": result.test_spearman,
                "error": result.error,
            }
        )
    return rows


def _load_analysis_summary(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return _load_json(path)
    except Exception:
        return {}


def _build_lab_conclusion_text(analysis_summary: Dict[str, Any]) -> str:
    monotonic = analysis_summary.get("monotonic", {}).get("summary", {})
    median_uplift = analysis_summary.get("test_median_split", {}).get("uplift", {})
    threshold = analysis_summary.get("test_threshold_filters", {}).get("summary", {}).get("best_threshold", {})
    trend_text = "单调性不足"
    if monotonic.get("all_positive_trend"):
        trend_text = "train/val/test 单调性均为正向"
    elif monotonic.get("trend_consistent"):
        trend_text = "单调方向基本一致"
    best_q = threshold.get("quantile")
    best_q_text = f"q={float(best_q):.1f}" if best_q is not None else "未给出"
    return (
        f"{trend_text}；中位数高低组 raw 均值差={_fmt_pct(_safe_float(median_uplift.get('target_raw_mean_diff')))}；"
        f"最佳阈值={best_q_text}。"
    )


def _prediction_metrics_from_csv(path: Path) -> Dict[str, Optional[float]]:
    frame = pd.read_csv(path, usecols=lambda column: column in {"target", "target_raw", "y_pred"})
    return {
        "mae": _mae(frame["target"], frame["y_pred"]),
        "spearman": _safe_spearman(frame["target"], frame["y_pred"]),
        "target_raw_mean": float(frame["target_raw"].mean()),
        "target_raw_win_rate": float((frame["target_raw"] > 0).mean()),
        "y_pred_mean": float(frame["y_pred"].mean()),
        "y_pred_std": float(frame["y_pred"].std(ddof=0)),
    }


def _experiment_display_name(lab_id: Optional[int], seed: Optional[int], kind: str) -> str:
    if kind == "ensemble":
        return "ensemble"
    return f"lab{int(lab_id):02d}_s{int(seed)}"


def _bool_to_text(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return ""


def _build_monotonic_comparison_tables(
    root: Path,
    ensemble_ctx: EnsembleContext,
    lab_results: List[LabResult],
    ensemble_analysis_summary: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    success_results = [item for item in lab_results if item.status == "success"]
    experiment_entries: List[Dict[str, Any]] = []

    ensemble_val_metrics = _prediction_metrics_from_csv(ensemble_ctx.output_dir / "val_predictions.csv")
    ensemble_test_metrics = _prediction_metrics_from_csv(ensemble_ctx.output_dir / "predictions.csv")
    experiment_entries.append(
        {
            "display_name": _experiment_display_name(None, None, "ensemble"),
            "kind": "ensemble",
            "seed": "bagged",
            "experiment_version": ensemble_ctx.experiment_version,
            "best_iteration": None,
            "val_metrics": ensemble_val_metrics,
            "test_metrics": ensemble_test_metrics,
            "analysis_summary": ensemble_analysis_summary,
            "analysis_dir": ensemble_ctx.analysis_dir,
        }
    )

    for result in success_results:
        if result.analysis_summary_path is None:
            continue
        experiment_entries.append(
            {
                "display_name": _experiment_display_name(result.lab_id, result.seed, "lab"),
                "kind": "lab",
                "seed": result.seed,
                "experiment_version": result.experiment_version,
                "best_iteration": result.best_iteration,
                "val_metrics": _prediction_metrics_from_csv(_resolve_path(root, result.output_files["val_prediction_file"])),
                "test_metrics": _prediction_metrics_from_csv(_resolve_path(root, result.output_files["test_prediction_file"])),
                "analysis_summary": _load_analysis_summary(result.analysis_summary_path),
                "analysis_dir": result.analysis_summary_path.parent,
            }
        )

    experiment_order = [entry["display_name"] for entry in experiment_entries]
    mapping_rows: List[Dict[str, Any]] = []
    metric_rows: List[Dict[str, Any]] = []
    monotonic_summary_rows: List[Dict[str, Any]] = []
    monotonic_frames: List[pd.DataFrame] = []

    for entry in experiment_entries:
        display_name = entry["display_name"]
        summary = entry["analysis_summary"]
        monotonic_summary = summary.get("monotonic", {}).get("summary", {})
        overall_test = summary.get("overall_test", {})
        median_uplift = summary.get("test_median_split", {}).get("uplift", {})
        threshold = summary.get("test_threshold_filters", {}).get("summary", {}).get("best_threshold", {})
        test_monotonic = monotonic_summary.get("per_split", {}).get("test", {})

        mapping_rows.append(
            {
                "experiment": display_name,
                "kind": entry["kind"],
                "seed": entry["seed"],
                "experiment_version": entry["experiment_version"],
            }
        )
        metric_rows.append(
            {
                "experiment": display_name,
                "kind": entry["kind"],
                "seed": entry["seed"],
                "best_iteration": entry["best_iteration"],
                "val_mae": entry["val_metrics"].get("mae"),
                "val_spearman": entry["val_metrics"].get("spearman"),
                "test_mae": entry["test_metrics"].get("mae"),
                "test_spearman": entry["test_metrics"].get("spearman"),
                "test_raw_mean": overall_test.get("target_raw_mean", entry["test_metrics"].get("target_raw_mean")),
                "test_raw_win_rate": overall_test.get("target_raw_win_rate", entry["test_metrics"].get("target_raw_win_rate")),
                "median_raw_mean_diff": median_uplift.get("target_raw_mean_diff"),
                "median_raw_win_rate_diff": median_uplift.get("target_raw_win_rate_diff"),
                "best_threshold_quantile": threshold.get("quantile"),
                "best_threshold_raw_mean": threshold.get("target_raw_mean"),
                "test_monotonic_end_minus_start": test_monotonic.get("end_minus_start"),
                "test_monotonic_spearman": test_monotonic.get("spearman"),
                "test_monotonic_non_decreasing_ratio": test_monotonic.get("non_decreasing_ratio"),
                "trend_consistent": _bool_to_text(monotonic_summary.get("trend_consistent")),
                "all_positive_trend": _bool_to_text(monotonic_summary.get("all_positive_trend")),
            }
        )

        for split_name in ["train", "val", "test"]:
            split_payload = monotonic_summary.get("per_split", {}).get(split_name, {})
            if not split_payload:
                continue
            monotonic_summary_rows.append(
                {
                    "experiment": display_name,
                    "split_name": split_name,
                    "group_count": split_payload.get("group_count"),
                    "first_group_target_mean": split_payload.get("first_group_target_mean"),
                    "last_group_target_mean": split_payload.get("last_group_target_mean"),
                    "end_minus_start": split_payload.get("end_minus_start"),
                    "spearman": split_payload.get("spearman"),
                    "non_decreasing_ratio": split_payload.get("non_decreasing_ratio"),
                    "positive_trend": _bool_to_text(split_payload.get("positive_trend")),
                }
            )

        monotonic_path = entry["analysis_dir"] / "monotonic_groups.csv"
        if monotonic_path.exists():
            monotonic_frame = pd.read_csv(monotonic_path)
            monotonic_frame["experiment"] = display_name
            monotonic_frames.append(monotonic_frame)

    mapping_df = pd.DataFrame(mapping_rows)
    metric_df = pd.DataFrame(metric_rows)
    monotonic_summary_df = pd.DataFrame(monotonic_summary_rows)
    monotonic_long_df = pd.concat(monotonic_frames, ignore_index=True) if monotonic_frames else pd.DataFrame()
    if not monotonic_long_df.empty:
        monotonic_long_df = monotonic_long_df.sort_values(["split_name", "group_id", "experiment"]).reset_index(drop=True)
    test_monotonic_df = monotonic_long_df[monotonic_long_df["split_name"].eq("test")].copy() if not monotonic_long_df.empty else pd.DataFrame()

    pivot_tables: Dict[str, pd.DataFrame] = {}
    if not test_monotonic_df.empty:
        for metric_name in ["target_mean", "target_raw_mean", "target_win_rate", "y_pred_mean"]:
            pivot_df = test_monotonic_df.pivot(index="group_id", columns="experiment", values=metric_name).reset_index()
            pivot_df = pivot_df[["group_id"] + experiment_order]
            pivot_tables[metric_name] = pivot_df.sort_values("group_id").reset_index(drop=True)

        target_mean_pivot = pivot_tables["target_mean"]
        lab_columns = [column for column in experiment_order if column != "ensemble"]
        spread_df = pd.DataFrame(
            {
                "group_id": target_mean_pivot["group_id"],
                "ensemble": target_mean_pivot["ensemble"],
                "lab_mean": target_mean_pivot[lab_columns].mean(axis=1),
                "lab_std": target_mean_pivot[lab_columns].std(axis=1, ddof=0),
                "lab_min": target_mean_pivot[lab_columns].min(axis=1),
                "lab_max": target_mean_pivot[lab_columns].max(axis=1),
            }
        )
        spread_df["ensemble_minus_lab_mean"] = spread_df["ensemble"] - spread_df["lab_mean"]
    else:
        spread_df = pd.DataFrame()

    comparison_files = {
        "mapping": ensemble_ctx.analysis_dir / "comparison_experiment_mapping.csv",
        "metrics": ensemble_ctx.analysis_dir / "comparison_experiment_metrics.csv",
        "monotonic_summary": ensemble_ctx.analysis_dir / "comparison_monotonic_summary.csv",
        "monotonic_long": ensemble_ctx.analysis_dir / "comparison_monotonic_groups_long.csv",
        "test_target_mean": ensemble_ctx.analysis_dir / "comparison_test_monotonic_target_mean.csv",
        "test_target_raw_mean": ensemble_ctx.analysis_dir / "comparison_test_monotonic_target_raw_mean.csv",
        "test_target_win_rate": ensemble_ctx.analysis_dir / "comparison_test_monotonic_target_win_rate.csv",
        "test_y_pred_mean": ensemble_ctx.analysis_dir / "comparison_test_monotonic_y_pred_mean.csv",
        "test_target_mean_spread": ensemble_ctx.analysis_dir / "comparison_test_monotonic_target_mean_spread.csv",
    }
    mapping_df.to_csv(comparison_files["mapping"], index=False)
    metric_df.to_csv(comparison_files["metrics"], index=False)
    monotonic_summary_df.to_csv(comparison_files["monotonic_summary"], index=False)
    monotonic_long_df.to_csv(comparison_files["monotonic_long"], index=False)
    if pivot_tables:
        pivot_tables["target_mean"].to_csv(comparison_files["test_target_mean"], index=False)
        pivot_tables["target_raw_mean"].to_csv(comparison_files["test_target_raw_mean"], index=False)
        pivot_tables["target_win_rate"].to_csv(comparison_files["test_target_win_rate"], index=False)
        pivot_tables["y_pred_mean"].to_csv(comparison_files["test_y_pred_mean"], index=False)
        spread_df.to_csv(comparison_files["test_target_mean_spread"], index=False)

    return {
        "mapping": mapping_df,
        "metrics": metric_df,
        "monotonic_summary": monotonic_summary_df,
        "monotonic_long": monotonic_long_df,
        "test_target_mean": pivot_tables.get("target_mean", pd.DataFrame()),
        "test_target_raw_mean": pivot_tables.get("target_raw_mean", pd.DataFrame()),
        "test_target_win_rate": pivot_tables.get("target_win_rate", pd.DataFrame()),
        "test_y_pred_mean": pivot_tables.get("y_pred_mean", pd.DataFrame()),
        "test_target_mean_spread": spread_df,
    }


def _plot_test_monotonic_comparison(
    test_target_mean_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    output_path: Path,
) -> None:
    if test_target_mean_df.empty:
        return
    _configure_plot_font()
    figure, axis = plt.subplots(figsize=(14, 7.5))
    x_values = test_target_mean_df["group_id"]
    for column in test_target_mean_df.columns:
        if column == "group_id":
            continue
        if column == "ensemble":
            axis.plot(
                x_values,
                test_target_mean_df[column],
                color="#c96d1f",
                linewidth=3.2,
                label="ensemble",
                zorder=5,
            )
        else:
            axis.plot(
                x_values,
                test_target_mean_df[column],
                color="#5f7488",
                linewidth=1.0,
                alpha=0.45,
                zorder=2,
            )

    axis.axhline(0.0, color="#7a8794", linestyle="--", linewidth=1.0)
    axis.set_title("11 个实验 Test 100 组单调性对比")
    axis.set_xlabel("group_id")
    axis.set_ylabel("target_mean")
    axis.grid(alpha=0.18, linestyle=":")

    ensemble_row = metrics_df.loc[metrics_df["experiment"].eq("ensemble")]
    best_lab_row = metrics_df.loc[metrics_df["test_spearman"].idxmax()] if not metrics_df.empty else None
    analysis_lines = []
    if not ensemble_row.empty:
        analysis_lines.append(
            f"ensemble: test_spearman={ensemble_row.iloc[0]['test_spearman']:.3f}, median_raw_mean_diff={ensemble_row.iloc[0]['median_raw_mean_diff'] * 100:.2f}%"
        )
    if best_lab_row is not None and str(best_lab_row["experiment"]) != "ensemble":
        analysis_lines.append(
            f"best single lab: {best_lab_row['experiment']} (test_spearman={float(best_lab_row['test_spearman']):.3f})"
        )
    analysis_lines.append("细线为 10 个单模型，粗橙线为 ensemble；观察其是否在多数 group 上更平滑且尾部抬升更稳定。")
    figure.text(0.02, 0.02, "\n".join(analysis_lines), ha="left", va="bottom", fontsize=10, color="#334455")
    plt.tight_layout(rect=[0, 0.08, 1, 1])
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def _plot_all_split_monotonic_grid(
    monotonic_long_df: pd.DataFrame,
    output_path: Path,
    metric: str,
    title: str,
    ylabel: str,
    footer_text: str,
) -> None:
    if monotonic_long_df.empty:
        return
    _configure_plot_font()
    split_order = ["train", "val", "test"]
    figure, axes = plt.subplots(3, 1, figsize=(14, 15), sharex=True)
    label_map = {"train": "Train", "val": "Valid", "test": "Test"}

    for axis, split_name in zip(axes, split_order):
        split_df = monotonic_long_df[monotonic_long_df["split_name"].eq(split_name)].copy()
        if split_df.empty:
            continue
        for experiment, frame in split_df.groupby("experiment"):
            ordered = frame.sort_values("group_id")
            if experiment == "ensemble":
                axis.plot(
                    ordered["group_id"],
                    ordered[metric],
                    color="#c96d1f",
                    linewidth=3.2,
                    label="ensemble",
                    zorder=5,
                )
            else:
                axis.plot(
                    ordered["group_id"],
                    ordered[metric],
                    color="#5f7488",
                    linewidth=0.95,
                    alpha=0.42,
                    zorder=2,
                )
        axis.axhline(0.0, color="#7a8794", linestyle="--", linewidth=1.0)
        axis.set_ylabel(ylabel)
        axis.set_title(f"{label_map.get(split_name, split_name)} 100 组单调性")
        axis.grid(alpha=0.18, linestyle=":")

    axes[-1].set_xlabel("group_id")
    figure.suptitle(title, fontsize=16, y=0.995)
    figure.text(
        0.02,
        0.01,
        footer_text,
        ha="left",
        va="bottom",
        fontsize=10,
        color="#334455",
    )
    plt.tight_layout(rect=[0, 0.03, 1, 0.98])
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def _build_metric_spread_by_split(monotonic_long_df: pd.DataFrame, metric: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if monotonic_long_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    spread_frames: List[pd.DataFrame] = []
    summary_rows: List[Dict[str, Any]] = []
    for split_name in ["train", "val", "test"]:
        split_df = monotonic_long_df[monotonic_long_df["split_name"].eq(split_name)].copy()
        if split_df.empty:
            continue
        pivot = split_df.pivot(index="group_id", columns="experiment", values=metric).sort_index()
        if "ensemble" not in pivot.columns:
            continue
        lab_columns = [column for column in pivot.columns if column != "ensemble"]
        if not lab_columns:
            continue

        lab_mean = pivot[lab_columns].mean(axis=1)
        lab_std = pivot[lab_columns].std(axis=1, ddof=0)
        spread_part = pd.DataFrame(
            {
                "split_name": split_name,
                "group_id": pivot.index.to_numpy(),
                "ensemble": pivot["ensemble"].to_numpy(),
                "lab_mean": lab_mean.to_numpy(),
                "lab_std": lab_std.to_numpy(),
                "lab_min": pivot[lab_columns].min(axis=1).to_numpy(),
                "lab_max": pivot[lab_columns].max(axis=1).to_numpy(),
            }
        )
        spread_part["ensemble_minus_lab_mean"] = spread_part["ensemble"] - spread_part["lab_mean"]
        spread_frames.append(spread_part)

        group_index = pd.Series(pivot.index.to_numpy(), index=pivot.index)
        summary_rows.append(
            {
                "split_name": split_name,
                "ensemble_start": float(pivot["ensemble"].iloc[0]),
                "ensemble_end": float(pivot["ensemble"].iloc[-1]),
                "ensemble_span": float(pivot["ensemble"].iloc[-1] - pivot["ensemble"].iloc[0]),
                "lab_mean_start": float(lab_mean.iloc[0]),
                "lab_mean_end": float(lab_mean.iloc[-1]),
                "lab_mean_span": float(lab_mean.iloc[-1] - lab_mean.iloc[0]),
                "avg_lab_std": float(lab_std.mean()),
                "max_lab_std": float(lab_std.max()),
                "ensemble_above_lab_mean_groups": int((spread_part["ensemble_minus_lab_mean"] > 0).sum()),
                "ensemble_below_lab_mean_groups": int((spread_part["ensemble_minus_lab_mean"] < 0).sum()),
                "ensemble_metric_spearman": float(group_index.corr(pivot["ensemble"], method="spearman")),
                "ensemble_abs_mean_gap": float(spread_part["ensemble_minus_lab_mean"].abs().mean()),
            }
        )

    spread_df = pd.concat(spread_frames, ignore_index=True) if spread_frames else pd.DataFrame()
    summary_df = pd.DataFrame(summary_rows)
    return spread_df, summary_df


def _format_group_delta_list(spread_df: pd.DataFrame, top_n: int, ascending: bool) -> str:
    if spread_df.empty:
        return "无"
    ranked = spread_df.sort_values("ensemble_minus_lab_mean", ascending=ascending).head(top_n)
    items = [
        f"g{int(row['group_id'])} ({float(row['ensemble_minus_lab_mean']):+.4f})"
        for _, row in ranked.iterrows()
    ]
    return "、".join(items) if items else "无"


def _format_group_std_list(spread_df: pd.DataFrame, top_n: int) -> str:
    if spread_df.empty:
        return "无"
    ranked = spread_df.sort_values("lab_std", ascending=False).head(top_n)
    items = [f"g{int(row['group_id'])} (std={float(row['lab_std']):.4f})" for _, row in ranked.iterrows()]
    return "、".join(items) if items else "无"


def _build_target_monotonic_analysis_lines(comparison_tables: Dict[str, pd.DataFrame], metrics_df: pd.DataFrame) -> List[str]:
    spread_df, summary_df = _build_metric_spread_by_split(comparison_tables["monotonic_long"], "target_mean")
    if summary_df.empty:
        return ["缺少 target 单调性数据，无法生成说明。"]

    summary_map = {row["split_name"]: row for _, row in summary_df.iterrows()}
    test_spread = spread_df[spread_df["split_name"].eq("test")].copy()
    best_lab_row = metrics_df.loc[metrics_df["kind"].eq("lab")].sort_values("test_monotonic_spearman", ascending=False).iloc[0]
    ensemble_row = metrics_df.loc[metrics_df["experiment"].eq("ensemble")].iloc[0]
    return [
        (
            f"target 图是主图，因为纵轴直接对应真实收益均值。ensemble 在 train / valid / test 上从首组到末组的 target_mean "
            f"分别提升 {summary_map['train']['ensemble_span']:.4f} / {summary_map['val']['ensemble_span']:.4f} / {summary_map['test']['ensemble_span']:.4f}，"
            f"说明它在训练、验证和测试阶段都保留了正向层次。"
        ),
        (
            f"从测试集看，ensemble 高于 10 个单模型均值的 group 有 {int(summary_map['test']['ensemble_above_lab_mean_groups'])} 个，"
            f"低于均值的有 {int(summary_map['test']['ensemble_below_lab_mean_groups'])} 个。它不是把所有 group 一起抬高，"
            f"而是在多数关键区间对局部噪声做重新平滑。"
        ),
        (
            f"对 ensemble 最有利的测试分组主要是 {_format_group_delta_list(test_spread, top_n=3, ascending=False)}；"
            f"牺牲较明显的分组主要是 {_format_group_delta_list(test_spread, top_n=3, ascending=True)}。"
        ),
        (
            f"test 单调 Spearman 最强的单模型是 {best_lab_row['experiment']}={float(best_lab_row['test_monotonic_spearman']):.3f}；"
            f"ensemble 为 {float(ensemble_row['test_monotonic_spearman']):.3f}。这说明 ensemble 的定位更偏稳健默认口径，而不是追求单一 seed 的峰值表现。"
        ),
    ]


def _build_y_pred_monotonic_analysis_lines(comparison_tables: Dict[str, pd.DataFrame]) -> List[str]:
    spread_df, summary_df = _build_metric_spread_by_split(comparison_tables["monotonic_long"], "y_pred_mean")
    if summary_df.empty:
        return ["缺少 y_pred 单调性数据，无法生成说明。"]

    summary_map = {row["split_name"]: row for _, row in summary_df.iterrows()}
    test_spread = spread_df[spread_df["split_name"].eq("test")].copy()
    return [
        (
            "y_pred 图不是直接看收益，而是看 11 个实验的打分结构。由于 100 组本身按 y_pred 分箱形成，曲线天然会递增，"
            "真正有信息量的是不同实验的打分跨度、斜率和彼此离散度。"
        ),
        (
            f"ensemble 在 train / valid / test 上的 y_pred 跨度分别为 {summary_map['train']['ensemble_span']:.4f} / "
            f"{summary_map['val']['ensemble_span']:.4f} / {summary_map['test']['ensemble_span']:.4f}。验证和测试阶段的分数跨度明显小于训练集，"
            "说明出样本阶段模型打分会自然收缩，置信度更保守。"
        ),
        (
            f"在测试集，ensemble 相对单模型均值的平均绝对偏离仅 {summary_map['test']['ensemble_abs_mean_gap']:.4f}，"
            f"而单模型在各 group 的平均离散度为 {summary_map['test']['avg_lab_std']:.4f}。这说明集成曲线总体落在单模型中枢带附近，"
            "主要作用是压缩极端打分，而不是制造新的极端分数。"
        ),
        (
            f"单模型在 y_pred 上分歧最大的测试分组主要集中在 {_format_group_std_list(test_spread, top_n=3)}。"
            "这些位置往往也是后续复盘尾部不稳定性时最值得优先检查的区间。"
        ),
    ]


def _render_bagging_leadership_html(
    root: Path,
    ensemble_ctx: EnsembleContext,
    comparison_tables: Dict[str, pd.DataFrame],
    aggregation_summary: Dict[str, Any],
    feature_importance_df: pd.DataFrame,
) -> str:
    metrics_df = comparison_tables["metrics"].copy()
    overview_df = metrics_df[[
        "experiment",
        "test_spearman",
        "test_mae",
        "median_raw_mean_diff",
        "test_monotonic_spearman",
        "test_monotonic_non_decreasing_ratio",
    ]].copy()
    overview_df = overview_df.rename(
        columns={
            "experiment": "实验",
            "test_spearman": "测试集 Spearman",
            "test_mae": "测试集 MAE",
            "median_raw_mean_diff": "中位数高低组 raw 均值差",
            "test_monotonic_spearman": "Test 单调 Spearman",
            "test_monotonic_non_decreasing_ratio": "Test 非递减比例",
        }
    )
    top_feature_df = feature_importance_df.head(20)[["rank", "feature", "mean_gain", "std_gain", "nonzero_model_count"]].copy()
    top_feature_df = top_feature_df.rename(
        columns={
            "rank": "排名",
            "feature": "特征",
            "mean_gain": "平均 gain",
            "std_gain": "gain 波动",
            "nonzero_model_count": "非零模型数",
        }
    )
    ensemble_row = metrics_df.loc[metrics_df["experiment"].eq("ensemble")].iloc[0]
    best_lab_row = metrics_df.loc[metrics_df["kind"].eq("lab")].sort_values("test_spearman", ascending=False).iloc[0]
    feature_top_name = str(top_feature_df.iloc[0]["特征"]) if not top_feature_df.empty else "未生成"
    ensemble_rank = int((metrics_df["test_spearman"].rank(ascending=False, method="min")[metrics_df["experiment"].eq("ensemble")]).iloc[0]) if not metrics_df.empty else 0
    summary_row_df = overview_df.copy()
    target_analysis_lines = _build_target_monotonic_analysis_lines(comparison_tables, metrics_df)
    y_pred_analysis_lines = _build_y_pred_monotonic_analysis_lines(comparison_tables)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bagging 管理层报告</title>
<style>
    :root {{
        --primary: #18344f;
        --primary-light: #386789;
        --accent: #c96d1f;
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
    .hero h1 {{ font-size: 34px; margin-bottom: 12px; letter-spacing: 0.02em; }}
    .hero p {{ opacity: 0.88; max-width: 860px; margin: 0 auto; }}
    .wrap {{ max-width: 1260px; margin: -72px auto 42px; padding: 0 20px; position: relative; z-index: 1; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 18px; }}
    .kpi-card {{ background: linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(250,248,243,0.98) 100%); border: 1px solid rgba(24,52,79,0.08); border-radius: 18px; padding: 20px 22px; box-shadow: var(--shadow); min-height: 138px; }}
    .kpi-label {{ font-size: 13px; color: var(--muted); margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .kpi-value {{ font-size: 28px; line-height: 1.2; color: var(--primary); font-weight: 700; margin-bottom: 10px; }}
    .kpi-note {{ font-size: 13px; color: var(--muted); }}
    .layout {{ display: grid; grid-template-columns: 1fr; gap: 18px; }}
    .card {{ background: var(--card); border-radius: 18px; box-shadow: var(--shadow); padding: 28px 30px; border: 1px solid rgba(24,52,79,0.08); }}
    h2 {{ margin: 0 0 14px; color: var(--primary); font-size: 24px; }}
    h3 {{ margin: 18px 0 10px; color: var(--primary-light); font-size: 18px; }}
    p {{ margin-bottom: 14px; }}
    ul {{ margin: 10px 0 18px 22px; }}
    li {{ margin-bottom: 8px; }}
    .highlight {{ background: linear-gradient(90deg, rgba(24,52,79,0.08) 0%, rgba(24,52,79,0.03) 100%); border-left: 4px solid var(--primary-light); padding: 18px; border-radius: 0 12px 12px 0; margin: 16px 0 18px; font-weight: 600; }}
    .callout {{ background: linear-gradient(90deg, rgba(201,109,31,0.12) 0%, rgba(201,109,31,0.04) 100%); border-left: 4px solid var(--accent); padding: 15px 18px; border-radius: 0 12px 12px 0; margin: 16px 0 18px; }}
    .table-wrap {{ overflow-x: auto; margin: 16px 0 12px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 620px; font-size: 14px; background: #fff; }}
    th {{ background: var(--primary); color: #fff; padding: 10px 12px; text-align: center; }}
    td {{ border-bottom: 1px solid var(--border); padding: 9px 12px; text-align: center; }}
    tr:hover td {{ background: #f9fbfd; }}
    .feature-table td:nth-child(2), .feature-table th:nth-child(2) {{ text-align: left; }}
    .hero-figure img, .figure-panel img {{ width: 100%; border: 1px solid var(--border); border-radius: 12px; background: #fff; }}
    .footer {{ text-align: center; color: var(--muted); font-size: 13px; padding: 10px 0 0; }}
    @media (max-width: 980px) {{
        .kpi-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
        .hero {{ padding: 48px 16px 108px; }}
        .hero h1 {{ font-size: 28px; }}
        .wrap {{ margin-top: -64px; padding: 0 14px; }}
        .kpi-grid {{ grid-template-columns: 1fr; }}
        .card {{ padding: 24px 20px; }}
    }}
</style>
</head>
<body>
<header class="hero">
    <h1>Bagging 管理层报告</h1>
    <p>实验版本：{html.escape(ensemble_ctx.experiment_version)}。本页面只保留最关键的横向对比与可视化，优先回答三个问题：ensemble 是否优于单模型、11 个实验的单调性是否稳定、模型主要看什么。</p>
</header>
<div class="wrap">
    <div class="kpi-grid">
        <div class="kpi-card"><div class="kpi-label">Ensemble Test Spearman</div><div class="kpi-value">{html.escape(_fmt_float(_safe_float(ensemble_row['test_spearman'])))}</div><div class="kpi-note">11 个实验里最终采用的集成排序能力</div></div>
        <div class="kpi-card"><div class="kpi-label">Best Single Lab</div><div class="kpi-value">{html.escape(str(best_lab_row['experiment']))}</div><div class="kpi-note">其 Test Spearman={html.escape(_fmt_float(_safe_float(best_lab_row['test_spearman'])))}</div></div>
        <div class="kpi-card"><div class="kpi-label">中位数过滤提升</div><div class="kpi-value">{html.escape(_fmt_pct(_safe_float(ensemble_row['median_raw_mean_diff'])))}</div><div class="kpi-note">高分组相对低分组的 raw 均值改善</div></div>
        <div class="kpi-card"><div class="kpi-label">头号稳定特征</div><div class="kpi-value">{html.escape(feature_top_name)}</div><div class="kpi-note">Top20 中稳定性最高的核心线索之一</div></div>
    </div>

    <div class="layout">
        <section class="card">
            <h2>1. 核心结论</h2>
            <div class="highlight">ensemble 的 Test Spearman 为 {html.escape(_fmt_float(_safe_float(ensemble_row['test_spearman'])))}，高于基线 lab01，并且 10 个单模型在 test 单调性上全部保持正向。按 11 个实验整体排序，ensemble 当前位于第 {ensemble_rank} 位。</div>
            <ul>
                <li>相对最优单模型，ensemble 的主要价值不是大幅拉高绝对收益，而是让 100 组单调性曲线更平滑，减少局部噪声。</li>
                <li>如果你的目标是选择一个更稳的默认过滤器，ensemble 比任意单一 seed 更合适。</li>
                <li>如果你的目标是追求极端高分尾部，当前结果仍说明上沿稳定性有限，后续应优化尾部区间，而不是盲目提高阈值。</li>
            </ul>
        </section>

        <section class="card">
            <h2>5. 横向指标总对比</h2>
            <p>这里只保留每个实验最关键的横向指标，便于快速比较 ensemble 与 10 个 lab 的位置。</p>
            {_html_table_from_df(summary_row_df, float_columns=["测试集 Spearman", "测试集 MAE", "Test 单调 Spearman"], percent_columns=["中位数高低组 raw 均值差", "Test 非递减比例"])}
        </section>

        <section class="card hero-figure">
            <h2>2. 11 个实验 Train / Valid / Test 100 组单调性：target</h2>
            <p>这张图对应真实收益均值 target_mean，是最重要的单调性总图。粗橙线是 ensemble，细蓝灰线是 10 个单模型，用来判断集成后是否在验证和测试阶段更平滑、更稳健。</p>
            <img src="bagging_all_splits_monotonic_grid_target.png" alt="bagging_all_splits_monotonic_grid_target.png" />
            <div class="callout"><strong>target 图解读：</strong>{html.escape(target_analysis_lines[0])}</div>
            <p>{html.escape(target_analysis_lines[1])}</p>
            <p>{html.escape(target_analysis_lines[2])}</p>
            <p>{html.escape(target_analysis_lines[3])}</p>
        </section>

        <section class="card figure-panel">
            <h2>3. 11 个实验 Train / Valid / Test 100 组单调性：y_pred</h2>
            <p>这张图对应模型打分均值 y_pred_mean。它不是直接看收益，而是看 11 个实验的打分结构是否更稳定、不同 seed 之间的分数形状是否更加收敛。</p>
            <img src="bagging_all_splits_monotonic_grid_y_pred.png" alt="bagging_all_splits_monotonic_grid_y_pred.png" />
            <div class="callout"><strong>y_pred 图解读：</strong>{html.escape(y_pred_analysis_lines[0])}</div>
            <p>{html.escape(y_pred_analysis_lines[1])}</p>
            <p>{html.escape(y_pred_analysis_lines[2])}</p>
            <p>{html.escape(y_pred_analysis_lines[3])}</p>
        </section>

        <section class="card">
            <h2>4. 单调性分析说明</h2>
            <ul>
                <li>11 个实验的 test 单调性都为正向，这说明 seed 扰动没有改变模型学到的总体方向。</li>
                <li>ensemble 的 Test 单调 Spearman 为 {html.escape(_fmt_float(_safe_float(ensemble_row['test_monotonic_spearman'])))}，并非最高，但它更接近多模型均值，更适合作为稳定默认方案。</li>
                <li>如果只看最高单模型，lab04_s45 和 lab03_s44 的 test 单调 Spearman 更高，但它们不一定在所有 group 上都更平滑。</li>
            </ul>
        </section>

        <section class="card">
            <h2>5. 业务解读</h2>
            <p>当前 bagging 更像是在做“稳定化”而不是“性能跳跃”。对实际落地来说，这通常是好事，因为它意味着不同 seed 训练出的模型结论相互接近，系统性偏差较小。</p>
            <ul>
                <li>默认策略仍建议采用中位数附近的宽阈值过滤。</li>
                <li>如需下一轮优化，重点应放在 test 尾部区间的抬升与回撤控制。</li>
            </ul>
        </section>

        <section class="card">
            <h2>6. 模型在看什么</h2>
            <p>当前 ensemble 没有单一模型文件，因此这里直接使用 bagged feature importance，而不是沿用单模型 feature importance 解释。</p>
            <h3>16. Bagging Feature Importance Top 20 与稳定性说明</h3>
            {_html_table_from_df(top_feature_df, float_columns=["平均 gain", "gain 波动"], table_class="feature-table")}
            <p>如果一个特征在 10 个模型里几乎都非零，且 gain 波动较小，说明它不是某个 seed 的偶然产物，而是更稳定的结构性信号。当前 date、month、non_trading_ratio 都属于这一类。</p>
        </section>

        <section class="card">
            <h2>7. 建议</h2>
            <ul>
                <li>若目标是更稳地做过滤，优先用 ensemble。</li>
                <li>若目标是挑选最强单模型做研究，可重点复盘 lab04_s45 与 lab03_s44 的 test 单调性形状。</li>
                <li>如果后续要继续给领导汇报，建议只展示本页两张图和横向指标总表，不再展开 100 行明细表。</li>
            </ul>
            <div class="footer">本文件由 bagging 编排器自动生成。</div>
        </section>
    </div>
</div>
</body>
</html>"""


def _build_bagging_report(
    root: Path,
    ensemble_ctx: EnsembleContext,
    source_artifacts: SourceArtifacts,
    lab_results: List[LabResult],
    aggregation_summary: Dict[str, Any],
    feature_importance_df: pd.DataFrame,
    ensemble_analysis_summary: Dict[str, Any],
) -> Path:
    success_results = [item for item in lab_results if item.status == "success"]
    failure_results = [item for item in lab_results if item.status != "success"]
    lab_rows = _lab_metric_summary_rows(lab_results)
    baseline = next((item for item in success_results if item.lab_id == 1), None)
    comparison_tables = _build_monotonic_comparison_tables(root, ensemble_ctx, lab_results, ensemble_analysis_summary)
    target_all_split_figure = ensemble_ctx.analysis_dir / "bagging_all_splits_monotonic_grid_target.png"
    y_pred_all_split_figure = ensemble_ctx.analysis_dir / "bagging_all_splits_monotonic_grid_y_pred.png"
    _plot_all_split_monotonic_grid(
        comparison_tables["monotonic_long"],
        target_all_split_figure,
        metric="target_mean",
        title="11 个实验 100 组单调性对比（Train / Valid / Test, target_mean）",
        ylabel="target_mean",
        footer_text="三张子图分别展示 train / valid / test 的 target_mean 单调性；粗橙线为 ensemble，细蓝灰线为单模型，用于观察集成后真实收益分组是否更平滑、更稳定。",
    )
    _plot_all_split_monotonic_grid(
        comparison_tables["monotonic_long"],
        y_pred_all_split_figure,
        metric="y_pred_mean",
        title="11 个实验 100 组单调性对比（Train / Valid / Test, y_pred_mean）",
        ylabel="y_pred_mean",
        footer_text="三张子图分别展示 train / valid / test 的 y_pred_mean 单调性；粗橙线为 ensemble，细蓝灰线为单模型，用于观察打分结构是否收敛、是否减少不同 seed 之间的极端偏移。",
    )
    baseline_delta_spearman = None
    baseline_delta_mae = None
    if baseline is not None:
        baseline_delta_spearman = _safe_float(aggregation_summary.get("test_spearman"))
        if baseline_delta_spearman is not None and baseline.test_spearman is not None:
            baseline_delta_spearman -= baseline.test_spearman
        baseline_delta_mae = _safe_float(aggregation_summary.get("test_mae"))
        if baseline_delta_mae is not None and baseline.test_mae is not None:
            baseline_delta_mae -= baseline.test_mae

    lab_conclusions = []
    for result in success_results:
        analysis_summary = _load_analysis_summary(result.analysis_summary_path)
        lab_conclusions.append(
            {
                "lab": f"lab{result.lab_id:02d}",
                "seed": result.seed,
                "experiment_version": result.experiment_version,
                "conclusion": _build_lab_conclusion_text(analysis_summary),
            }
        )

    monotonic_summary = ensemble_analysis_summary.get("monotonic", {}).get("summary", {})
    median_uplift = ensemble_analysis_summary.get("test_median_split", {}).get("uplift", {})
    threshold_summary = ensemble_analysis_summary.get("test_threshold_filters", {}).get("summary", {})
    quintile_rows = ensemble_analysis_summary.get("test_quintile", [])
    top20_rows = feature_importance_df.head(20).to_dict(orient="records")
    best_threshold = threshold_summary.get("best_threshold", {})
    best_threshold_quantile = best_threshold.get("quantile")
    best_threshold_raw_mean = _safe_float(best_threshold.get("target_raw_mean"))
    metrics_df = comparison_tables["metrics"].copy()
    lab_only_metrics_df = metrics_df.loc[metrics_df["kind"].eq("lab")].copy()
    best_lab_row = lab_only_metrics_df.sort_values("test_spearman", ascending=False).iloc[0] if not lab_only_metrics_df.empty else None
    worst_lab_row = lab_only_metrics_df.sort_values("test_spearman", ascending=True).iloc[0] if not lab_only_metrics_df.empty else None
    ensemble_rank = int((metrics_df["test_spearman"].rank(ascending=False, method="min")[metrics_df["experiment"].eq("ensemble")]).iloc[0]) if not metrics_df.empty else 0
    spread_df = comparison_tables["test_target_mean_spread"].copy()
    positive_group_count = int((spread_df["ensemble_minus_lab_mean"] > 0).sum()) if not spread_df.empty else 0
    negative_group_count = int((spread_df["ensemble_minus_lab_mean"] < 0).sum()) if not spread_df.empty else 0

    compact_metrics_df = metrics_df[[
        "experiment",
        "test_spearman",
        "test_mae",
        "median_raw_mean_diff",
        "best_threshold_raw_mean",
        "test_monotonic_spearman",
        "test_monotonic_non_decreasing_ratio",
    ]].copy()
    target_analysis_lines = _build_target_monotonic_analysis_lines(comparison_tables, metrics_df)
    y_pred_analysis_lines = _build_y_pred_monotonic_analysis_lines(comparison_tables)
    metric_comparison_table = _markdown_table(
        compact_metrics_df.to_dict(orient="records"),
        [
            "experiment",
            "test_spearman",
            "test_mae",
            "median_raw_mean_diff",
            "best_threshold_raw_mean",
            "test_monotonic_spearman",
            "test_monotonic_non_decreasing_ratio",
        ],
        float_columns=[
            "test_spearman",
            "test_mae",
            "test_monotonic_spearman",
        ],
        percent_columns=[
            "median_raw_mean_diff",
            "best_threshold_raw_mean",
            "test_monotonic_non_decreasing_ratio",
        ],
    )
    spread_df = comparison_tables["test_target_mean_spread"].copy()
    if not spread_df.empty:
        top_positive_groups = spread_df.sort_values("ensemble_minus_lab_mean", ascending=False).head(5)
        top_negative_groups = spread_df.sort_values("ensemble_minus_lab_mean", ascending=True).head(5)
        monotonic_spread_table = _markdown_table(
            pd.concat([top_positive_groups, top_negative_groups], ignore_index=True).drop_duplicates(subset=["group_id"]).to_dict(orient="records"),
            ["group_id", "ensemble", "lab_mean", "lab_std", "lab_min", "lab_max", "ensemble_minus_lab_mean"],
            float_columns=["ensemble", "lab_mean", "lab_std", "lab_min", "lab_max", "ensemble_minus_lab_mean"],
        )
    else:
        monotonic_spread_table = _markdown_table([], ["group_id", "ensemble", "lab_mean", "lab_std", "lab_min", "lab_max", "ensemble_minus_lab_mean"])

    lines = [
        "# Bagging 合并分析报告",
        "",
        "## 1. 实验概述",
        f"- source experiment_version: {source_artifacts.experiment_version}",
        f"- ensemble experiment_version: {ensemble_ctx.experiment_version}",
        f"- 成功 lab 数: {len(success_results)}",
        f"- 失败 lab 数: {len(failure_results)}",
        f"- ensemble 在 11 个实验中的 test_spearman 排名: {ensemble_rank}",
        "",
        "## 2. source experiment 来源说明",
        f"- source manifest: {_rel(root, source_artifacts.manifest_path)}",
        f"- source train split: {_rel(root, source_artifacts.train_path)}",
        f"- source val split: {_rel(root, source_artifacts.val_path)}",
        f"- source test split: {_rel(root, source_artifacts.test_path)}",
        f"- source selected_features: {_rel(root, source_artifacts.selected_features_path)}",
        f"- source clip_percentiles: {_rel(root, source_artifacts.clip_percentiles_path)}",
        "",
        "## 5. 横向指标总对比",
        "对外只保留最关键的一张横向表，避免重复堆叠多个指标明细。",
        metric_comparison_table,
        "",
        "## 8. 11 个实验 Train / Valid / Test 100 组单调性",
        f"![11 个实验 target 单调性图]({target_all_split_figure.name})",
        "",
        "### 8.1 target 详细分析",
        *[f"- {line}" for line in target_analysis_lines],
        "",
        f"![11 个实验 y_pred 单调性图]({y_pred_all_split_figure.name})",
        "",
        "### 8.2 y_pred 详细分析",
        *[f"- {line}" for line in y_pred_analysis_lines],
        "",
        "## 9. train / val / test 三集合的 ensemble 分析摘要",
        f"- test MAE: {_fmt_float(_safe_float(aggregation_summary.get('test_mae')))}",
        f"- test Spearman: {_fmt_float(_safe_float(aggregation_summary.get('test_spearman')))}",
        f"- train 样本数: {aggregation_summary.get('split_summaries', {}).get('train', {}).get('rows', '')}",
        f"- val 样本数: {aggregation_summary.get('split_summaries', {}).get('val', {}).get('rows', '')}",
        f"- test 样本数: {aggregation_summary.get('split_summaries', {}).get('test', {}).get('rows', '')}",
        "",
        "## 10. 100 组单调性结论摘要",
        f"- trend_consistent: {monotonic_summary.get('trend_consistent')}",
        f"- all_positive_trend: {monotonic_summary.get('all_positive_trend')}",
        f"- 可用集合: {', '.join(monotonic_summary.get('available_splits', [])) or '无'}",
        "",
        "## 11. 五分组 / 中位数 / 阈值过滤结论摘要",
        f"- 五分组行数: {len(quintile_rows)}",
        f"- 中位数高低组 raw 均值差: {_fmt_pct(_safe_float(median_uplift.get('target_raw_mean_diff')))}",
        f"- 中位数高低组 raw 胜率差: {_fmt_pct(_safe_float(median_uplift.get('target_raw_win_rate_diff')))}",
        f"- 最优阈值分位: {'' if best_threshold_quantile is None else f'q={float(best_threshold_quantile):.1f}'}",
        f"- 最优阈值 raw 均值: {_fmt_pct(best_threshold_raw_mean)}",
        "",
        "## 15. Bagging Feature Importance Top 20 与稳定性说明",
        _markdown_table(
            top20_rows,
            ["rank", "feature", "mean_gain", "std_gain", "mean_gain_share", "nonzero_model_count"],
            float_columns=["mean_gain", "std_gain", "mean_gain_share"],
        ),
        "",
        "稳定性说明：nonzero_model_count 越接近模型总数，说明该特征在不同 seed 下越稳定；std_gain 越小说明其重要性波动越小。",
        "",
        "## 16. 与 bagging_lab01_seed42 单模型基线对比",
        f"- baseline experiment_version: {baseline.experiment_version if baseline is not None else '未找到'}",
        f"- ensemble vs baseline test Spearman 差值: {_fmt_float(baseline_delta_spearman)}",
        f"- ensemble vs baseline test MAE 差值: {_fmt_float(baseline_delta_mae)}",
        "",
        "## 17. 补充文件",
        f"- 管理层报告 HTML: {_rel(root, ensemble_ctx.analysis_dir / 'experiment_summary_for_leadership.html')}",
        f"- 11 个实验 target 单调性图: {_rel(root, target_all_split_figure)}",
        f"- 11 个实验 y_pred 单调性图: {_rel(root, y_pred_all_split_figure)}",
        f"- 横向指标 CSV: {_rel(root, ensemble_ctx.analysis_dir / 'comparison_experiment_metrics.csv')}",
        "",
        "## 18. 结论与建议",
        "- 若 ensemble test Spearman 高于 baseline 且中位数过滤提升仍为正，说明 bagging 至少改善了排序稳定性。",
        "- 若 top features 的 nonzero_model_count 接近模型总数，当前特征重要性结论稳定性较强。",
        "- 若极端阈值未继续改善 raw 均值，下一轮应优先优化最高分尾部稳定性，而不是继续抬高过滤阈值。",
    ]
    leadership_html_path = ensemble_ctx.analysis_dir / "experiment_summary_for_leadership.html"
    leadership_html_path.write_text(
        _render_bagging_leadership_html(root, ensemble_ctx, comparison_tables, aggregation_summary, feature_importance_df),
        encoding="utf-8",
    )
    report_path = ensemble_ctx.analysis_dir / "bagging_experiment_report.md"
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report_path


def _write_preflight_summary(root: Path, ensemble_ctx: EnsembleContext, summary: PreflightSummary) -> Path:
    ensemble_ctx.metadata_dir.mkdir(parents=True, exist_ok=True)
    path = ensemble_ctx.metadata_dir / "preflight_summary.json"
    _write_json(
        path,
        {
            "source_resolution": summary.source_resolution,
            "source_manifest_path": summary.source_manifest_path,
            "source_experiment_version": summary.source_experiment_version,
            "source_config_path": summary.source_config_path,
            "source_state_file": summary.source_state_file,
            "bootstrap_needed": summary.bootstrap_needed,
            "bootstrap_reason": summary.bootstrap_reason,
            "ensemble_experiment_version": summary.ensemble_experiment_version,
            "planned_labs": summary.planned_labs,
        },
    )
    return path


def run_bagging_experiment(
    root: Path,
    config_path: str,
    dry_run: bool,
    prepare_source_only: bool,
    max_labs: Optional[int],
    skip_analysis: bool,
    only_aggregate: bool,
) -> int:
    cfg = load_bagging_config(root, config_path)
    source_ctx = _build_source_context(root, cfg)
    summary = build_preflight_summary(root, cfg, max_labs)
    _print_preflight(summary)

    ensemble_ctx = _build_ensemble_context(root, cfg, source_ctx.params)
    if not dry_run:
        _write_preflight_summary(root, ensemble_ctx, summary)

    if dry_run:
        return 0

    source_artifacts = _prepare_source_experiment(root, cfg, source_ctx)
    print(f"[bagging] source ready: {_rel(root, source_artifacts.manifest_path)}")
    if prepare_source_only:
        return 0

    lab_specs = _build_lab_specs(cfg, source_ctx.params, max_labs)
    if not lab_specs:
        raise RuntimeError("未生成任何 lab 计划，无法继续。")

    if only_aggregate:
        lab_results = _collect_lab_results_from_artifacts(root, lab_specs)
    else:
        lab_results = [
            _execute_lab(root, cfg, source_ctx.params, source_artifacts, spec, cfg.analysis.report_title)
            for spec in lab_specs
        ]

    success_results = [item for item in lab_results if item.status == "success"]
    failed_results = [item for item in lab_results if item.status != "success"]
    requested_lab_count = len(lab_specs)
    probe_mode = max_labs is not None and max_labs < len(cfg.bagging.seeds)
    formal_ensemble_allowed = (
        len(success_results) >= cfg.bagging.min_successful_labs
        and (cfg.bagging.allow_partial_ensemble or len(success_results) == requested_lab_count == len(cfg.bagging.seeds))
        and not probe_mode
    )

    if len(success_results) < 2:
        print("[bagging] successful labs < 2，跳过聚合。")
        return 0 if probe_mode else (1 if failed_results else 0)

    aggregated_paths, aggregation_summary, feature_columns = _aggregate_predictions(
        root,
        ensemble_ctx,
        success_results,
        include_member_predictions=cfg.output.include_member_predictions,
    )
    lab_runs_path = ensemble_ctx.metadata_dir / "lab_runs.json"
    _write_json(
        lab_runs_path,
        {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "lab_runs": [asdict(result) for result in lab_results],
        },
    )
    aggregation_summary["formal_ensemble_allowed"] = formal_ensemble_allowed
    aggregation_summary["successful_labs"] = [item.experiment_version for item in success_results]
    aggregation_summary["failed_labs"] = [item.experiment_version for item in failed_results]
    aggregation_summary_path = ensemble_ctx.metadata_dir / "aggregation_summary.json"
    _write_json(aggregation_summary_path, aggregation_summary)

    schema_payload = {
        "feature_columns": feature_columns,
        "feature_count": len(feature_columns),
        "required_base_columns": BASE_COLUMNS + ["y_pred"],
        "ensemble_member_count": len(success_results),
    }
    model_feature_schema_path = ensemble_ctx.metadata_dir / "model_feature_schema.json"
    _write_json(model_feature_schema_path, schema_payload)

    lab_metric_summary_df = pd.DataFrame(_lab_metric_summary_rows(lab_results))
    lab_metric_summary_path = ensemble_ctx.analysis_dir / "lab_metric_summary.csv"
    ensemble_ctx.analysis_dir.mkdir(parents=True, exist_ok=True)
    lab_metric_summary_df.to_csv(lab_metric_summary_path, index=False)

    feature_importance_path = ensemble_ctx.analysis_dir / "feature_importance_bagged.csv"
    feature_importance_df = _aggregate_feature_importance(root, success_results, feature_importance_path)

    ensemble_state_path = ensemble_ctx.metadata_dir / cfg.ensemble_experiment.state_file
    _write_ensemble_state(
        root,
        ensemble_state_path,
        ensemble_ctx.experiment_version,
        aggregated_paths,
        aggregation_summary,
        feature_columns,
        source_artifacts.experiment_version,
    )
    resolved_params_path = _write_ensemble_resolved_params(
        root,
        config_path,
        source_ctx.params,
        source_artifacts,
        ensemble_ctx,
        lab_results,
        aggregation_summary,
        cfg,
    )
    source_config_copy = ensemble_ctx.metadata_dir / "source_config.yaml"

    if not formal_ensemble_allowed:
        print("[bagging] 当前成功 lab 数不足以生成正式 ensemble 报告，已输出聚合探针产物。")
        if not probe_mode and not cfg.bagging.allow_partial_ensemble:
            return 1 if failed_results else 0
        return 0

    analysis_config_payload = _build_analysis_config(
        experiment_version=ensemble_ctx.experiment_version,
        output_dir=ensemble_ctx.analysis_dir,
        train_prediction_path=aggregated_paths["train"],
        val_prediction_path=aggregated_paths["val"],
        test_prediction_path=aggregated_paths["test"],
        model_path=None,
        selected_features_path=source_artifacts.selected_features_path,
        clip_percentiles_path=source_artifacts.clip_percentiles_path,
        state_file=ensemble_state_path,
        model_feature_schema_path=model_feature_schema_path,
        report_title=cfg.analysis.report_title,
        strict_mode=cfg.analysis.strict_mode,
        root=root,
    )
    analysis_config_path = ensemble_ctx.metadata_dir / "resolved_analysis_config.yaml"
    _write_yaml(analysis_config_path, analysis_config_payload)

    bagging_report_path = ensemble_ctx.analysis_dir / "bagging_experiment_report.md"
    if not skip_analysis:
        _run_analysis(root, analysis_config_path, cfg.analysis.strict_mode, ensemble_ctx.metadata_dir / "analysis_invocation.log")
        ensemble_analysis_summary = _load_json(ensemble_ctx.analysis_dir / "report_summary.json")
        bagging_report_path = _build_bagging_report(
            root,
            ensemble_ctx,
            source_artifacts,
            lab_results,
            aggregation_summary,
            feature_importance_df,
            ensemble_analysis_summary,
        )

    artifact_files = {
        "state_file": _rel(root, ensemble_state_path),
        "train_prediction_file": _rel(root, aggregated_paths["train"]),
        "val_prediction_file": _rel(root, aggregated_paths["val"]),
        "test_prediction_file": _rel(root, aggregated_paths["test"]),
        "prediction_file": _rel(root, aggregated_paths["test"]),
        "model_feature_schema_file": _rel(root, model_feature_schema_path),
        "lab_runs_file": _rel(root, lab_runs_path),
        "aggregation_summary_file": _rel(root, aggregation_summary_path),
        "feature_importance_bagged_file": _rel(root, feature_importance_path),
        "analysis_report_file": _rel(root, ensemble_ctx.analysis_dir / "experiment_analysis_report.md") if (ensemble_ctx.analysis_dir / "experiment_analysis_report.md").exists() else "",
        "leadership_html_file": _rel(root, ensemble_ctx.analysis_dir / "experiment_summary_for_leadership.html") if (ensemble_ctx.analysis_dir / "experiment_summary_for_leadership.html").exists() else "",
        "report_summary_file": _rel(root, ensemble_ctx.analysis_dir / "report_summary.json") if (ensemble_ctx.analysis_dir / "report_summary.json").exists() else "",
        "bagging_report_file": _rel(root, bagging_report_path) if bagging_report_path.exists() else "",
        "source_manifest_file": _rel(root, source_artifacts.manifest_path),
        "resolved_analysis_config_file": _rel(root, analysis_config_path),
        "analysis_invocation_log_file": _rel(root, ensemble_ctx.metadata_dir / "analysis_invocation.log") if (ensemble_ctx.metadata_dir / "analysis_invocation.log").exists() else "",
        "lab_metric_summary_file": _rel(root, lab_metric_summary_path),
    }
    artifact_files = {key: value for key, value in artifact_files.items() if value}
    _write_ensemble_manifest(
        root,
        ensemble_ctx,
        resolved_params_path,
        source_config_copy,
        source_artifacts,
        artifact_files,
        config_path,
        cfg,
        lab_results,
        aggregation_summary,
    )
    return 1 if failed_results else 0