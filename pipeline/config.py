"""管道配置、路径定义和默认参数。"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Dict, List, Optional, Type

import shutil

import numpy as np

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _apply_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)


_apply_seed(42)


def _today_token() -> str:
    return datetime.now().strftime("%Y%m%d")


def _sanitize_token(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip()).strip("_")
    return cleaned or "na"


def _normalize_rel_path(value: str) -> str:
    return str(value).replace("\\", "/").lstrip("./")


def _resolve_state_file_path(root: Path, state_file: str, experiment_version: str) -> Path:
    state_path = Path(state_file)
    if state_path.is_absolute():
        return state_path
    if state_path.parent == Path("."):
        return root / "outputs" / experiment_version / "metadata" / state_path.name
    return root / state_path


def mode_suffix(mode: str) -> str:
    return "_smoke" if str(mode).strip().lower() == "smoke" else ""


def prediction_artifact_paths(paths: "Paths", mode: str) -> Dict[str, Path]:
    suffix = mode_suffix(mode)
    return {
        "train": paths.output_dir / f"train_predictions{suffix}.csv",
        "val": paths.output_dir / f"val_predictions{suffix}.csv",
        "test": paths.output_dir / f"predictions{suffix}.csv",
    }


def feature_schema_artifact_path(paths: "Paths", mode: str) -> Path:
    return paths.metadata_dir / f"model_feature_schema{mode_suffix(mode)}.json"


def _resolve_raw_path(root: Path, value: str) -> Path:
    """原始数据路径：绝对路径原样使用，相对路径相对仓库根目录。"""
    raw = Path(str(value).strip())
    return raw if raw.is_absolute() else root / raw


def _master_version_hash(params: "PipelineParams") -> str:
    payload = json.dumps(
        {
            "fc_profile": "full",
            "lookback_days": params.window.lookback_days,
            "lookback_periods": sorted(params.indicators.lookback_periods),
            "lookback_columns": sorted(params.indicators.lookback_columns),
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]

def resolve_master_library_version(params: "PipelineParams") -> str:
    explicit_version = str(params.tsfresh.master_library_version).strip()
    if explicit_version:
        return _sanitize_token(explicit_version)
    return _master_version_hash(params)


@dataclass(frozen=True)
class Paths:
    root: Path
    experiment_version: str
    raw_holding: Path
    raw_datebar: Path
    processed_dir: Path
    slices_dir: Path
    ts_features_dir: Path
    tsfresh_chunks_dir: Path
    tsfresh_master_root_dir: Path
    features_dir: Path
    splits_dir: Path
    models_dir: Path
    output_dir: Path
    output_root_dir: Path
    metadata_dir: Path
    resolved_params_file: Path
    source_config_copy_file: Path
    manifest_file: Path
    registry_file: Path
    state_file: Path
    resolved_master_library_version: str
    master_library_role: str


def build_paths(root: Path, state_file: str, params: "PipelineParams") -> Paths:
    experiment_version = params.build_experiment_version()
    master_library_version = resolve_master_library_version(params)
    output_root_dir = root / "outputs"
    output_dir = output_root_dir / experiment_version
    metadata_dir = output_dir / "metadata"
    return Paths(
        root=root,
        experiment_version=experiment_version,
        raw_holding=_resolve_raw_path(root, params.data.holding_file),
        raw_datebar=_resolve_raw_path(root, params.data.datebar_file),
        processed_dir=root / "data" / "processed" / experiment_version,
        slices_dir=root / "data" / "slices" / experiment_version,
        ts_features_dir=root / "data" / "ts_features" / experiment_version,
        tsfresh_chunks_dir=root / "data" / "tsfresh_chunks" / experiment_version,
        tsfresh_master_root_dir=root / "data" / "tsfresh_master",
        features_dir=root / "data" / "features" / experiment_version,
        splits_dir=root / "data" / "splits" / experiment_version,
        models_dir=root / "models" / experiment_version,
        output_dir=output_dir,
        output_root_dir=output_root_dir,
        metadata_dir=metadata_dir,
        resolved_params_file=metadata_dir / "resolved_params.yaml",
        source_config_copy_file=metadata_dir / "source_config.yaml",
        manifest_file=metadata_dir / "experiment_manifest.json",
        registry_file=output_root_dir / "experiment_registry.json",
        state_file=_resolve_state_file_path(root, state_file, experiment_version),
        resolved_master_library_version=master_library_version,
        master_library_role=str(params.tsfresh.master_library_role),
    )


def resolve_raw_data_paths(
    root: Path,
    params: "PipelineParams",
    data_dir: Optional[Path] = None,
) -> Dict[str, Path]:
    """解析 holding / datebar 的实际路径。

    `data_dir` 为空时使用配置中的路径（相对仓库根或绝对路径）；
    传入 `data_dir` 时只取配置里的文件名，落到该外部目录。
    """
    if data_dir is None:
        return {
            "holding": _resolve_raw_path(root, params.data.holding_file),
            "datebar": _resolve_raw_path(root, params.data.datebar_file),
        }
    base = Path(data_dir)
    return {
        "holding": base / Path(params.data.holding_file).name,
        "datebar": base / Path(params.data.datebar_file).name,
    }


def ensure_directories(paths: Paths) -> None:
    for directory in (
        paths.processed_dir,
        paths.slices_dir,
        paths.ts_features_dir,
        paths.tsfresh_chunks_dir,
        paths.tsfresh_master_root_dir,
        paths.features_dir,
        paths.splits_dir,
        paths.models_dir,
        paths.output_root_dir,
        paths.output_dir,
        paths.metadata_dir,
        paths.state_file.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)


@dataclass
class RawDataParams:
    """原始输入文件位置；相对仓库根目录，也可写绝对路径。

    新建谱系时如数据命名或落点不同，只改这里，不要改内核代码。
    文件缺失不阻塞脚手架与配置生成，只阻塞 step1 起的实际运行。
    """

    holding_file: str = "data/raw/holding_stats_all.parquet"
    datebar_file: str = "data/raw/dateBar.pkl"


@dataclass
class WindowParams:
    lookback_days: int = 100
    chunk_size_slices: int = 5000


@dataclass
class SampleFilterParams:
    max_holding_days: Optional[int] = None  # None = 不过滤
    tolerance: float = 1.0


@dataclass
class TsfreshParams:
    chunk_size_tsfresh: int = 0       # 0 = 不分块，一次提取
    tsfresh_n_jobs: int = 0           # 0 = 使用全部 CPU 核心
    fc_profile: str = "compact"       # "compact" | "medium" | "broad" | "full"
    master_library_role: str = "off"  # "off" | "build" | "project"
    master_library_version: str = ""


@dataclass
class TimeSplitParams:
    train_end: int = 20201130
    val_start: int = 20210429
    val_end: int = 20221231
    test_start: int = 20230605
    buffer_trading_days: int = 100
    enforce_buffer: bool = True


@dataclass
class MonitoringParams:
    enabled: bool = False
    level: str = "light"


@dataclass
class IndicatorParams:
    profile: str = "base"
    rsi_period: int = 14
    atr_period: int = 14
    ma_windows: List[int] = field(default_factory=lambda: [5, 10, 20])
    boll_period: int = 20
    boll_std_mult: float = 2.0
    volume_ma_period: int = 20
    momentum_period: int = 5
    volatility_period: int = 20
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    kdj_period: int = 9
    kdj_k_period: int = 3
    kdj_d_period: int = 3

    # V4.1 新增
    skip_indicators: bool = False
    lookback_periods: List[int] = field(default_factory=list)
    lookback_columns: List[str] = field(
        default_factory=lambda: ["open", "high", "low", "close", "volume"]
    )


@dataclass
class FeatureSelectionParams:
    max_features: Optional[int] = None      # None = 不限制，由 corr_threshold 去重自然收束
    top_candidates: Optional[int] = None    # None = 全部候选进入去重
    corr_threshold: float = 0.95
    target_clip_low: float = 0.01
    target_clip_high: float = 0.99

    # V4.1 新增
    selection_mode: str = "correlation"  # "correlation" | "none"


@dataclass
class ManualFeatureParams:
    include_date: bool = True
    include_year: bool = True
    include_month: bool = True
    include_non_trading_ratio: bool = True
    non_trading_ratio_fill_value: float = 0.0
    include_signal_date_selection_score: bool = True
    selection_score_fill_value: float = 0.0


@dataclass
class ExperimentParams:
    version_subdir: str = ""
    base_version: str = "v0"
    run_date: str = "auto"
    label: str = ""
    include_param_hash: bool = True
    param_hash_length: int = 8
    auto_eval_summary: bool = False

    def resolve_run_date(self) -> str:
        raw_value = str(self.run_date).strip()
        if not raw_value or raw_value.lower() == "auto":
            return _today_token()
        return raw_value


@dataclass
class SplitParams:
    smoke_train_ratio: float = 0.70
    smoke_val_ratio: float = 0.15


@dataclass
class LightGBMParams:
    objective: str = "regression"
    metric: str = "mae"
    boosting_type: str = "gbdt"
    num_leaves: int = 31
    max_depth: int = 7
    learning_rate: float = 0.05
    min_child_samples: int = 100
    subsample: float = 0.6
    colsample_bytree: float = 0.5
    reg_alpha: float = 1.0
    reg_lambda: float = 5.0
    seed: int = 42
    num_threads: int = 1
    verbose: int = -1
    num_boost_round: int = 3000
    early_stopping_rounds: int = 100
    log_evaluation_period: int = 100
    lgb_profile: str = "default"

    def to_lgb_params(self) -> Dict[str, object]:
        return {
            "objective": self.objective,
            "metric": self.metric,
            "boosting_type": self.boosting_type,
            "num_leaves": self.num_leaves,
            "max_depth": self.max_depth,
            "learning_rate": self.learning_rate,
            "min_child_samples": self.min_child_samples,
            "subsample": self.subsample,
            "colsample_bytree": self.colsample_bytree,
            "reg_alpha": self.reg_alpha,
            "reg_lambda": self.reg_lambda,
            "random_state": self.seed,
            "seed": self.seed,
            "verbose": self.verbose,
            "num_threads": self.num_threads,
        }

    def resolve_profile(self) -> Dict[str, object]:
        """根据 lgb_profile 返回覆写参数字典。default 返回空（使用显式字段值）。"""
        if self.lgb_profile == "default":
            return {}

        profiles = {
            "high_dim": {
                "num_leaves": 63,
                "max_depth": 8,
                "min_child_samples": 200,
                "colsample_bytree": 0.6,
                "subsample": 0.7,
                "learning_rate": 0.03,
                "num_boost_round": 5000,
                "reg_alpha": 2.0,
                "reg_lambda": 2.0,
            },
            "high_dim_aggressive": {
                "num_leaves": 127,
                "max_depth": 10,
                "min_child_samples": 300,
                "colsample_bytree": 0.5,
                "subsample": 0.8,
                "learning_rate": 0.03,
                "num_boost_round": 5000,
                "reg_alpha": 3.0,
                "reg_lambda": 3.0,
            },
        }
        if self.lgb_profile not in profiles:
            raise ValueError(
                f"未知 lgb_profile: {self.lgb_profile}，可选: default|high_dim|high_dim_aggressive"
            )
        return profiles[self.lgb_profile]


@dataclass
class EvalParams:
    pred_group_q: int = 5


@dataclass
class PipelineParams:
    seed: int = 42
    smoke_size: int = 5000
    data: RawDataParams = field(default_factory=RawDataParams)
    window: WindowParams = field(default_factory=WindowParams)
    sample_filter: SampleFilterParams = field(default_factory=SampleFilterParams)
    tsfresh: TsfreshParams = field(default_factory=TsfreshParams)
    time_split: TimeSplitParams = field(default_factory=TimeSplitParams)
    monitoring: MonitoringParams = field(default_factory=MonitoringParams)
    indicators: IndicatorParams = field(default_factory=IndicatorParams)
    feature_selection: FeatureSelectionParams = field(default_factory=FeatureSelectionParams)
    manual_features: ManualFeatureParams = field(default_factory=ManualFeatureParams)
    split: SplitParams = field(default_factory=SplitParams)
    lgb: LightGBMParams = field(default_factory=LightGBMParams)
    eval: EvalParams = field(default_factory=EvalParams)
    experiment: ExperimentParams = field(default_factory=ExperimentParams)

    @classmethod
    def from_dict(cls, raw: Dict[str, object]) -> "PipelineParams":
        nested: Dict[str, Type[object]] = {
            "data": RawDataParams,
            "window": WindowParams,
            "sample_filter": SampleFilterParams,
            "tsfresh": TsfreshParams,
            "time_split": TimeSplitParams,
            "monitoring": MonitoringParams,
            "indicators": IndicatorParams,
            "feature_selection": FeatureSelectionParams,
            "manual_features": ManualFeatureParams,
            "split": SplitParams,
            "lgb": LightGBMParams,
            "eval": EvalParams,
            "experiment": ExperimentParams,
        }
        # 兜底：兼容尚未更新的旧配置文件
        if isinstance(raw, dict):
            # ① window → tsfresh 迁移（旧 YAML 的 tsfresh 参数仍在 window 段）
            window_raw = raw.get("window", {})
            tsfresh_raw = raw.get("tsfresh", {})
            if isinstance(window_raw, dict) and isinstance(tsfresh_raw, dict):
                for _migrate_key in ("chunk_size_tsfresh", "tsfresh_n_jobs"):
                    if _migrate_key in window_raw and _migrate_key not in tsfresh_raw:
                        tsfresh_raw[_migrate_key] = window_raw.pop(_migrate_key)
                if tsfresh_raw:
                    raw["tsfresh"] = tsfresh_raw
            # ② 删除 indicators 中的死字段（旧 YAML 含 boll_percent_b_period）
            indicators_raw = raw.get("indicators", {})
            if isinstance(indicators_raw, dict) and "boll_percent_b_period" in indicators_raw:
                indicators_raw.pop("boll_percent_b_period")
        kwargs: Dict[str, object] = {}
        for key, dtype in nested.items():
            value = raw.get(key, {}) if isinstance(raw, dict) else {}
            kwargs[key] = dtype(**value) if isinstance(value, dict) else dtype()
        for key in ["seed", "smoke_size"]:
            if isinstance(raw, dict) and key in raw:
                kwargs[key] = raw[key]
        _validate_params(kwargs)
        return cls(**kwargs)

    @classmethod
    def from_yaml(cls, path: Path) -> "PipelineParams":
        if yaml is None:
            raise ImportError("PyYAML 未安装")
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        return cls.from_dict(raw)

    def to_yaml(self, path: Path) -> None:
        if yaml is None:
            raise ImportError("PyYAML 未安装")
        with path.open("w", encoding="utf-8") as handle:
            yaml.dump(asdict(self), handle, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def version_payload(self) -> Dict[str, object]:
        payload = asdict(self)
        payload.pop("experiment", None)
        # 原始数据位置属于环境事实，不参与版本哈希；其取值记录在 resolved_params.yaml
        payload.pop("data", None)
        tsfresh_payload = payload.get("tsfresh")
        if isinstance(tsfresh_payload, dict):
            tsfresh_payload.pop("master_library_role", None)
            tsfresh_payload.pop("master_library_version", None)
        return payload

    def build_experiment_version(self) -> str:
        parts = [
            _sanitize_token(self.experiment.base_version),
            _sanitize_token(self.experiment.resolve_run_date()),
        ]
        label = str(self.experiment.label).strip()
        if label:
            parts.append(_sanitize_token(label))
        if self.experiment.include_param_hash:
            hash_length = max(4, min(16, int(self.experiment.param_hash_length)))
            payload_json = json.dumps(
                self.version_payload(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:hash_length]
            parts.append(digest)
        version = "_".join(parts)
        base = _sanitize_token(self.experiment.base_version)
        subdir = str(self.experiment.version_subdir).strip().rstrip("/")
        if subdir:
            version = f"{base}/{subdir}/{version}"
        else:
            version = f"{base}/{version}"
        return version


def _validate_params(kwargs: Dict[str, object]) -> None:
    """V4.1 新增：参数合法性校验，在 YAML 加载阶段即报错。"""
    window = kwargs.get("window")
    data = kwargs.get("data")
    time_split = kwargs.get("time_split")
    tsfresh = kwargs.get("tsfresh")
    monitoring = kwargs.get("monitoring")
    indicators = kwargs.get("indicators")
    lgb = kwargs.get("lgb")

    if isinstance(window, WindowParams) and isinstance(time_split, TimeSplitParams):
        if time_split.enforce_buffer and time_split.buffer_trading_days < window.lookback_days:
            raise ValueError(
                f"buffer_trading_days ({time_split.buffer_trading_days}) 必须 >= lookback_days ({window.lookback_days})"
            )

    if isinstance(data, RawDataParams):
        for field_name in ("holding_file", "datebar_file"):
            if not str(getattr(data, field_name)).strip():
                raise ValueError(f"data.{field_name} 不能为空")

    if isinstance(tsfresh, TsfreshParams):
        valid_fc_profiles = {"compact", "medium", "broad", "full"}
        if tsfresh.fc_profile not in valid_fc_profiles:
            raise ValueError(
                f"fc_profile={tsfresh.fc_profile} 非法，仅允许 {sorted(valid_fc_profiles)}"
            )
        valid_master_roles = {"off", "build", "project"}
        if tsfresh.master_library_role not in valid_master_roles:
            raise ValueError(
                "tsfresh.master_library_role="
                f"{tsfresh.master_library_role} 非法，仅允许 {sorted(valid_master_roles)}"
            )
        if tsfresh.master_library_role == "build" and tsfresh.fc_profile != "full":
            raise ValueError("master_library_role=build 仅允许与 fc_profile=full 搭配")

    if isinstance(monitoring, MonitoringParams):
        valid_monitoring_levels = {"light", "full"}
        if monitoring.level not in valid_monitoring_levels:
            raise ValueError(
                f"monitoring.level={monitoring.level} 非法，仅允许 {sorted(valid_monitoring_levels)}"
            )

    if isinstance(lgb, LightGBMParams):
        valid_lgb_profiles = {"default", "high_dim", "high_dim_aggressive"}
        if lgb.lgb_profile not in valid_lgb_profiles:
            raise ValueError(
                f"lgb_profile={lgb.lgb_profile} 非法，仅允许 {sorted(valid_lgb_profiles)}"
            )

    if isinstance(indicators, IndicatorParams):
        if indicators.skip_indicators:
            if indicators.lookback_periods:
                if isinstance(window, WindowParams) and max(indicators.lookback_periods) > window.lookback_days:
                    raise ValueError(
                        f"max(lookback_periods) ({max(indicators.lookback_periods)}) 必须 <= lookback_days ({window.lookback_days})"
                    )
            valid_cols = {"open", "high", "low", "close", "volume"}
            invalid_cols = set(indicators.lookback_columns) - valid_cols
            if invalid_cols:
                raise ValueError(
                    f"lookback_columns 包含非法列名: {sorted(invalid_cols)}，仅允许 {sorted(valid_cols)}"
                )
            if indicators.profile == "v3_enriched":
                raise ValueError("skip_indicators=true 与 profile=v3_enriched 互斥")


def load_params(yaml_path: str) -> PipelineParams:
    path = Path(yaml_path)
    if not path.exists():
        print(f"参数文件 {path} 不存在，回退到内置默认参数。")
        params = PipelineParams()
    elif yaml is None:
        raise ImportError(
            f"当前环境缺少 PyYAML，无法读取参数文件 {path}。"
            "请先安装 PyYAML，确保本次实验确实使用指定 YAML 配置。"
        )
    else:
        params = PipelineParams.from_yaml(path)
    _apply_seed(params.seed)
    return params


def _resolve_source_config_path(root: Path, source_config: str) -> Path:
    source_path = Path(source_config)
    if source_path.is_absolute():
        return source_path
    return (root / source_config).resolve()


def _default_experiment_description(params: PipelineParams) -> str:
    label = str(params.experiment.label).strip() or "default"
    return (
        f"自动生成实验说明：base_version={params.experiment.base_version}, "
        f"label={label}, base_version 仅表示实验谱系，不用于单项功能或参数调整, "
        f"include_date={params.manual_features.include_date}, "
        f"include_year={params.manual_features.include_year}, "
        f"include_month={params.manual_features.include_month}."
    )


def _build_artifact_files(paths: Paths, mode: str) -> Dict[str, str]:
    prediction_paths = prediction_artifact_paths(paths, mode)
    suffix = mode_suffix(mode)
    artifact_files: Dict[str, str] = {
        "state_file": _relative_if_possible(paths.root, paths.state_file),
        "prediction_file": _relative_if_possible(paths.root, prediction_paths["test"]),
        "test_prediction_file": _relative_if_possible(paths.root, prediction_paths["test"]),
        "train_prediction_file": _relative_if_possible(paths.root, prediction_paths["train"]),
        "val_prediction_file": _relative_if_possible(paths.root, prediction_paths["val"]),
        "model_file": _relative_if_possible(paths.root, paths.models_dir / f"lgb_model{suffix}.txt"),
        "train_split_file": _relative_if_possible(paths.root, paths.splits_dir / f"train{suffix}.parquet"),
        "val_split_file": _relative_if_possible(paths.root, paths.splits_dir / f"val{suffix}.parquet"),
        "test_split_file": _relative_if_possible(paths.root, paths.splits_dir / f"test{suffix}.parquet"),
        "selected_features_file": _relative_if_possible(paths.root, paths.features_dir / f"selected_features{suffix}.txt"),
        "model_feature_schema_file": _relative_if_possible(paths.root, feature_schema_artifact_path(paths, mode)),
        "analysis_report_file": _relative_if_possible(paths.root, paths.output_dir / "analysis_report.json"),
    }
    if paths.master_library_role != "off":
        master_dir = paths.tsfresh_master_root_dir / paths.resolved_master_library_version
        artifact_files.update(
            {
                "tsfresh_master_version": paths.resolved_master_library_version,
                "tsfresh_master_dir": _relative_if_possible(paths.root, master_dir),
                "tsfresh_master_chunks_dir": _relative_if_possible(paths.root, master_dir / "chunks"),
                "tsfresh_master_schema_file": _relative_if_possible(paths.root, master_dir / "schema.json"),
                "tsfresh_master_zero_variance_file": _relative_if_possible(
                    paths.root,
                    master_dir / "zero_variance_columns.json",
                ),
            }
        )
    if mode != "smoke":
        artifact_files["clip_percentiles_file"] = _relative_if_possible(
            paths.root,
            paths.features_dir / "clip_percentiles.json",
        )
    return artifact_files


def _merge_artifact_files_by_mode(
    existing_manifest: Dict[str, object] | None,
    current_artifact_files: Dict[str, str],
    mode: str,
) -> tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
    artifact_files_by_mode: Dict[str, Dict[str, str]] = {}
    if isinstance(existing_manifest, dict):
        existing_by_mode = existing_manifest.get("artifact_files_by_mode", {})
        if isinstance(existing_by_mode, dict):
            artifact_files_by_mode = {
                str(key): value for key, value in existing_by_mode.items() if isinstance(value, dict)
            }
    artifact_files_by_mode[mode] = current_artifact_files

    canonical_artifacts = current_artifact_files
    if mode == "smoke" and "full" in artifact_files_by_mode:
        canonical_artifacts = artifact_files_by_mode["full"]
    return canonical_artifacts, artifact_files_by_mode


def _resolve_experiment_metadata(
    paths: Paths,
    params: PipelineParams,
    source_config: str,
) -> Dict[str, object]:
    catalog_name = ""
    description = ""
    expected_base_version = ""
    source_config_token = _normalize_rel_path(source_config)

    if yaml is not None:
        experiment_index_path = paths.root / "configs" / "experiment_index.yaml"
        if experiment_index_path.exists():
            with experiment_index_path.open("r", encoding="utf-8") as handle:
                raw_index = yaml.safe_load(handle) or {}
            matched_entry: Dict[str, object] | None = None
            signature_entry: Dict[str, object] | None = None
            for entry in raw_index.get("experiments", []):
                if not isinstance(entry, dict):
                    continue
                entry_config = _normalize_rel_path(entry.get("config", ""))
                same_config = entry_config and entry_config == source_config_token
                same_signature = (
                    str(entry.get("expected_base_version", "")) == str(params.experiment.base_version)
                    and str(entry.get("label", "")) == str(params.experiment.label)
                )
                if same_config:
                    matched_entry = entry
                    break
                if same_signature and signature_entry is None:
                    signature_entry = entry
            matched_entry = matched_entry or signature_entry
            if matched_entry is not None:
                catalog_name = str(matched_entry.get("name", ""))
                description = str(matched_entry.get("description", "")).strip()
                expected_base_version = str(matched_entry.get("expected_base_version", ""))

    return {
        "catalog_name": catalog_name,
        "description": description or _default_experiment_description(params),
        "base_version": str(params.experiment.base_version),
        "run_date": params.experiment.resolve_run_date(),
        "label": str(params.experiment.label),
        "source_config": source_config,
        "source_config_resolved": _relative_if_possible(paths.root, _resolve_source_config_path(paths.root, source_config)),
        "expected_base_version": expected_base_version or str(params.experiment.base_version),
    }


def _relative_if_possible(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def save_resolved_params(paths: Paths, params: PipelineParams, source_config: str, mode: str) -> None:
    payload = asdict(params)
    source_path = _resolve_source_config_path(paths.root, source_config)
    experiment_metadata = _resolve_experiment_metadata(paths, params, source_config)
    if source_path.exists():
        shutil.copyfile(source_path, paths.source_config_copy_file)
    payload["resolved"] = {
        "experiment_version": paths.experiment_version,
        "experiment": experiment_metadata,
        "source_config": source_config,
        "source_config_copy": _relative_if_possible(paths.root, paths.source_config_copy_file),
        "artifact_roots": {
            "processed_dir": _relative_if_possible(paths.root, paths.processed_dir),
            "slices_dir": _relative_if_possible(paths.root, paths.slices_dir),
            "ts_features_dir": _relative_if_possible(paths.root, paths.ts_features_dir),
            "tsfresh_chunks_dir": _relative_if_possible(paths.root, paths.tsfresh_chunks_dir),
            "tsfresh_master_root_dir": _relative_if_possible(paths.root, paths.tsfresh_master_root_dir),
            "features_dir": _relative_if_possible(paths.root, paths.features_dir),
            "splits_dir": _relative_if_possible(paths.root, paths.splits_dir),
            "models_dir": _relative_if_possible(paths.root, paths.models_dir),
            "output_dir": _relative_if_possible(paths.root, paths.output_dir),
            "metadata_dir": _relative_if_possible(paths.root, paths.metadata_dir),
            "state_file": _relative_if_possible(paths.root, paths.state_file),
        },
    }
    with paths.resolved_params_file.open("w", encoding="utf-8") as handle:
        if yaml is None:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        else:
            yaml.dump(payload, handle, allow_unicode=True, default_flow_style=False, sort_keys=False)

    existing_manifest: Dict[str, object] | None = None
    if paths.manifest_file.exists():
        with paths.manifest_file.open("r", encoding="utf-8") as handle:
            existing_manifest = json.load(handle)

    current_artifact_files = _build_artifact_files(paths, mode)
    canonical_artifact_files, artifact_files_by_mode = _merge_artifact_files_by_mode(
        existing_manifest,
        current_artifact_files,
        mode,
    )

    manifest = {
        "experiment_version": paths.experiment_version,
        "latest_run_mode": mode,
        "experiment": experiment_metadata,
        "source_config": source_config,
        "source_config_copy": _relative_if_possible(paths.root, paths.source_config_copy_file),
        "resolved_params_file": _relative_if_possible(paths.root, paths.resolved_params_file),
        "artifact_roots": payload["resolved"]["artifact_roots"],
        "artifact_files": canonical_artifact_files,
        "artifact_files_by_mode": artifact_files_by_mode,
    }
    if paths.master_library_role != "off":
        master_dir = paths.tsfresh_master_root_dir / paths.resolved_master_library_version
        manifest["master_library"] = {
            "role": paths.master_library_role,
            "master_version": paths.resolved_master_library_version,
            "dir": _relative_if_possible(paths.root, master_dir),
            "chunks_dir": _relative_if_possible(paths.root, master_dir / "chunks"),
            "schema_file": _relative_if_possible(paths.root, master_dir / "schema.json"),
            "zero_variance_columns_file": _relative_if_possible(
                paths.root,
                master_dir / "zero_variance_columns.json",
            ),
        }
    with paths.manifest_file.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    registry: List[Dict[str, object]] = []
    if paths.registry_file.exists():
        with paths.registry_file.open("r", encoding="utf-8") as handle:
            registry = json.load(handle)

    registry = [
        entry for entry in registry
        if entry.get("experiment_version") != paths.experiment_version
    ]
    registry.append(
        {
            "experiment_version": paths.experiment_version,
            "experiment": experiment_metadata,
            "source_config": source_config,
            "source_config_copy": manifest["source_config_copy"],
            "resolved_params_file": manifest["resolved_params_file"],
            "manifest_file": _relative_if_possible(paths.root, paths.manifest_file),
            "artifact_roots": manifest["artifact_roots"],
            "artifact_files": manifest["artifact_files"],
            "artifact_files_by_mode": manifest["artifact_files_by_mode"],
            "master_library": manifest.get("master_library", {}),
            "latest_run_mode": mode,
        }
    )
    registry.sort(key=lambda entry: entry["experiment_version"])
    with paths.registry_file.open("w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)