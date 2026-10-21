"""first_round_closeout 共享常量与辅助函数。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import config as pipeline_config

PipelineParams = pipeline_config.PipelineParams
yaml = pipeline_config.yaml

CLOSEOUT_CONFIG_PATH = ROOT / "tools" / "frozen_experiments.yaml"


def _load_closeout_config() -> dict:
    if not CLOSEOUT_CONFIG_PATH.exists():
        raise FileNotFoundError("缺少 tools/frozen_experiments.yaml，请由谱系自行维护。")
    if yaml is None:
        raise ImportError("PyYAML 未安装，无法读取 tools/frozen_experiments.yaml。")
    with CLOSEOUT_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("tools/frozen_experiments.yaml 必须是对象结构。")
    return payload


_CLOSEOUT_CONFIG = _load_closeout_config()

BASE_VERSION = str(_CLOSEOUT_CONFIG.get("base_version", "")).strip()
FAILED_RESOLVED_PLACEHOLDER = "not_available_from_failed_run"
ABORTED_RESOLVED_PLACEHOLDER = "not_available_from_aborted_run"
ABORTED_STATE_PLACEHOLDER = "not_available_from_aborted_run"

MASTER_LIBRARY_DOCS = tuple(
    str(item) for item in (_CLOSEOUT_CONFIG.get("master_library_docs") or [])
)
_PROTECTED_PATHS = dict(_CLOSEOUT_CONFIG.get("protected_paths") or {})

CAPSULE_REQUIRED_PREFIXES = (
    "- experiment_version:",
    "- base_version:",
    "- label:",
    "- source_config:",
    "- resolved_params:",
    "- canonical_status:",
    "- rerun:",
    "- data_prerequisites:",
    "- conclusion_summary:",
)


@dataclass(frozen=True)
class FrozenExperiment:
    experiment_version: str
    status: str
    config_path: str
    max_step: int
    summary_lines: tuple[str, ...]

    @property
    def config_stem(self) -> str:
        return Path(self.config_path).stem


FROZEN_EXPERIMENTS = tuple(
    FrozenExperiment(
        experiment_version=str(item["experiment_version"]),
        status=str(item["status"]),
        config_path=str(item["config_path"]),
        max_step=int(item["max_step"]),
        summary_lines=tuple(str(line) for line in (item.get("summary_lines") or [])),
    )
    for item in (_CLOSEOUT_CONFIG.get("experiments") or [])
)

FROZEN_EXPERIMENTS_BY_ID = {spec.experiment_version: spec for spec in FROZEN_EXPERIMENTS}


def normalize_rel_path(value: str | Path) -> str:
    return str(value).replace("\\", "/").lstrip("./")


def relative_to_root(path: Path) -> str:
    try:
        return normalize_rel_path(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return normalize_rel_path(path)


def ensure_yaml_available() -> None:
    if yaml is None:
        raise ImportError("PyYAML 未安装，无法读取 YAML 文件。")


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{relative_to_root(path)} 不是对象 JSON。")
    return payload


def read_yaml_dict(path: Path) -> dict:
    ensure_yaml_available()
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{relative_to_root(path)} 不是对象 YAML。")
    return payload


def get_spec(experiment_version: str) -> FrozenExperiment:
    try:
        return FROZEN_EXPERIMENTS_BY_ID[experiment_version]
    except KeyError as exc:
        raise KeyError(f"未知 experiment_version: {experiment_version}") from exc


def load_config_metadata(config_path: str) -> dict[str, str]:
    payload = read_yaml_dict(ROOT / config_path)
    experiment = payload.get("experiment", {}) if isinstance(payload.get("experiment"), dict) else {}
    tsfresh = payload.get("tsfresh", {}) if isinstance(payload.get("tsfresh"), dict) else {}
    return {
        "label": str(experiment.get("label", "")).strip(),
        "base_version": str(experiment.get("base_version", BASE_VERSION)).strip() or BASE_VERSION,
        "master_library_role": str(tsfresh.get("master_library_role", "off")).strip() or "off",
    }


def load_experiment_index() -> dict:
    return read_yaml_dict(ROOT / "configs" / "experiment_index.yaml")


def is_config_registered(config_path: str) -> bool:
    normalized = normalize_rel_path(config_path)
    payload = load_experiment_index()
    for entry in payload.get("experiments", []):
        if isinstance(entry, dict) and normalize_rel_path(entry.get("config", "")) == normalized:
            return True
    return False


def build_rerun_commands(spec: FrozenExperiment) -> list[str]:
    """生成平台中立的复跑命令。

    不写死解释器绝对路径：capsule 会被带到其他机器上使用，
    硬编码 ``C:/.../python.exe`` 会让回放命令在别处失效。
    统一用 ``python``（PATH 上的解释器），并在 capsule 中附注替换方式。
    """
    config_path = normalize_rel_path(spec.config_path)
    state_file = f"pipeline_state_full_closeout_{spec.config_stem}.json"
    return [
        (
            f"python run_experiment_pipeline.py --mode full --step {step} "
            f"--config {config_path} --state-file {state_file}"
        )
        for step in range(1, spec.max_step + 1)
    ]


def require_base_version() -> str:
    if not BASE_VERSION:
        raise ValueError(
            "tools/frozen_experiments.yaml 的 base_version 未设置；"
            "请填入样本族名（如 boll / lowvol）后再使用收尾工具。"
        )
    return BASE_VERSION


def metadata_dir(spec: FrozenExperiment) -> Path:
    """定位实验的 metadata 目录。

    支持两种 experiment_version 写法：
    - 仅 run_id：``<base_version>/outputs`` 下按 ``base_version/<run_id>`` 或
      ``base_version/<version_subdir>/<run_id>`` 查找
    - 相对路径：``<version_subdir>/<run_id>`` 或 ``<base_version>/<version_subdir>/<run_id>``

    三段式目录（base_version / version_subdir / run_id）是当前标准，
    旧的两段式（base_version / run_id）保持兼容。
    """
    base = require_base_version()
    output_root = ROOT / "outputs"
    raw = str(spec.experiment_version).strip().strip("/")
    run_id = raw.split("/")[-1]

    candidates = [
        output_root / base / raw / "metadata",
        output_root / raw / "metadata",
        output_root / base / run_id / "metadata",
        output_root / run_id / "metadata",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    # 兜底：在 base_version 下递归查找 run_id（覆盖 version_subdir 任意深度）
    for match in output_root.glob(f"{base}/**/{run_id}/metadata"):
        if match.is_dir():
            return match

    # 都不存在：返回最可能的路径，由调用方的 exists() 判断给出明确报错
    return candidates[0]


def full_state_file(spec: FrozenExperiment) -> Path | None:
    directory = metadata_dir(spec)
    if not directory.exists():
        return None
    matches = sorted(directory.glob("pipeline_state_full*.json"))
    return matches[0] if matches else None


def status_note(status: str) -> str:
    if status == "canonical":
        return (
            "Frozen run has formal outputs/models/metadata; replay must use a fresh state-file "
            "and may rebuild heavy artifacts after prune."
        )
    if status == "failed":
        return (
            "User-approved exception: this failed run preserves only pipeline_state metadata, "
            "so this capsule uses pipeline_state + config fallback; replay must use a fresh state-file "
            "and should not target the historical failed output directory."
        )
    return (
        "Data-only residue only; there is no formal outputs/models metadata. Replay must start "
        "from source_config with a fresh state-file, and resolved_params are intentionally "
        "unavailable from the aborted run."
    )


def collect_capsule_fields(
    spec: FrozenExperiment,
    *,
    allow_missing_metadata_fallback: bool = False,
) -> dict[str, object]:
    config_metadata = load_config_metadata(spec.config_path)
    label = config_metadata["label"]
    base_version = config_metadata["base_version"]
    master_role = config_metadata["master_library_role"]
    source_config_origin = normalize_rel_path(spec.config_path)
    source_config_field = source_config_origin
    resolved_params_field = (
        ABORTED_RESOLVED_PLACEHOLDER if spec.status == "aborted" else FAILED_RESOLVED_PLACEHOLDER
    )
    state_path = full_state_file(spec)
    state_field = (
        relative_to_root(state_path)
        if state_path is not None
        else (ABORTED_STATE_PLACEHOLDER if spec.status == "aborted" else FAILED_RESOLVED_PLACEHOLDER)
    )
    master_version = master_library_version() if master_role != "off" else ""

    manifest_path = metadata_dir(spec) / "experiment_manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        experiment = manifest.get("experiment", {}) if isinstance(manifest.get("experiment"), dict) else {}
        artifact_files = manifest.get("artifact_files", {}) if isinstance(manifest.get("artifact_files"), dict) else {}
        master_library = manifest.get("master_library", {}) if isinstance(manifest.get("master_library"), dict) else {}
        label = str(experiment.get("label", label)).strip() or label
        source_config_copy = str(manifest.get("source_config_copy", "")).strip()
        source_config_original = str(manifest.get("source_config", source_config_origin)).strip() or source_config_origin
        if source_config_copy:
            source_config_field = (
                f"{normalize_rel_path(source_config_copy)} "
                f"(origin: {normalize_rel_path(source_config_original)})"
            )
        else:
            source_config_field = normalize_rel_path(source_config_original)
        resolved_params = str(manifest.get("resolved_params_file", "")).strip()
        if resolved_params:
            resolved_params_field = normalize_rel_path(resolved_params)
        state_from_manifest = str(artifact_files.get("state_file", "")).strip()
        if state_from_manifest:
            state_field = normalize_rel_path(state_from_manifest)
        master_version = str(master_library.get("master_version", master_version)).strip() or master_version
    elif spec.status == "failed" and allow_missing_metadata_fallback and state_path is not None:
        pass
    elif spec.status != "aborted":
        raise FileNotFoundError(
            "缺少 metadata manifest："
            f"{relative_to_root(manifest_path)}"
        )

    master_dir = f"data/tsfresh_master/{master_version}" if master_role != "off" else "not_required"
    data_prerequisites = [
        "raw_data: `data/raw/**` must remain intact.",
        f"source_config_origin: `{source_config_origin}`.",
        "experiment_index_registration: `configs/experiment_index.yaml` must keep the corresponding entry.",
        f"pipeline_state: `{state_field}`.",
    ]
    if master_role != "off":
        data_prerequisites.append(
            f"master_library_dependency: `{master_dir}` (frozen lineage uses master version `{master_version}`)."
        )
        data_prerequisites.append("master_library_rebuild_docs:")
        for doc in MASTER_LIBRARY_DOCS:
            data_prerequisites.append(f"  - `{doc}`.")
    data_prerequisites.append(f"status_note: {status_note(spec.status)}")

    return {
        "experiment_version": spec.experiment_version,
        "base_version": base_version,
        "label": label,
        "source_config": source_config_field,
        "resolved_params": resolved_params_field,
        "canonical_status": spec.status,
        "rerun_commands": build_rerun_commands(spec),
        "data_prerequisites": data_prerequisites,
        "conclusion_summary": list(spec.summary_lines),
    }


def render_capsule_text(
    spec: FrozenExperiment,
    *,
    allow_missing_metadata_fallback: bool = False,
) -> str:
    fields = collect_capsule_fields(
        spec,
        allow_missing_metadata_fallback=allow_missing_metadata_fallback,
    )
    lines = [
        f"# Replay Capsule - {fields['experiment_version']}",
        "",
        f"- experiment_version: `{fields['experiment_version']}`",
        f"- base_version: `{fields['base_version']}`",
        f"- label: `{fields['label']}`",
        f"- source_config: `{fields['source_config']}`",
        f"- resolved_params: `{fields['resolved_params']}`",
        f"- canonical_status: `{fields['canonical_status']}`",
        "- rerun:",
        "```text",
        *fields["rerun_commands"],
        "```",
        (
            "  > 命令中的 `python` 指当前环境可用的解释器；"
            "若不在 PATH，替换为你的解释器绝对路径后执行。"
        ),
        "- data_prerequisites:",
    ]
    for item in fields["data_prerequisites"]:
        if item.startswith("  - "):
            lines.append(f"  {item}")
        else:
            lines.append(f"  - {item}")
    lines.append("- conclusion_summary:")
    for item in fields["conclusion_summary"]:
        lines.append(f"  - {item}")
    return "\n".join(lines) + "\n"


def write_capsule(
    spec: FrozenExperiment,
    output_dir: Path,
    *,
    allow_missing_metadata_fallback: bool = False,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    capsule_path = output_dir / f"{spec.experiment_version}.md"
    capsule_path.write_text(
        render_capsule_text(
            spec,
            allow_missing_metadata_fallback=allow_missing_metadata_fallback,
        ),
        encoding="utf-8",
    )
    return capsule_path


def validate_capsule_file(capsule_path: Path) -> list[str]:
    if not capsule_path.exists():
        return ["capsule file missing"]
    lines = capsule_path.read_text(encoding="utf-8").splitlines()
    missing = [
        prefix
        for prefix in CAPSULE_REQUIRED_PREFIXES
        if not any(line.startswith(prefix) for line in lines)
    ]
    return missing


def directory_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())


def prediction_prune_targets() -> list[Path]:
    if not BASE_VERSION:
        return []
    output_root = ROOT / "outputs" / BASE_VERSION
    targets = [
        path
        for path in output_root.rglob("*")
        if path.is_file() and path.name in {"train_predictions.csv", "val_predictions.csv"}
    ]
    return sorted(targets, key=lambda item: relative_to_root(item))


def master_library_version() -> str:
    explicit = str(_CLOSEOUT_CONFIG.get("master_library_version", "")).strip()
    if explicit:
        return explicit
    for spec in FROZEN_EXPERIMENTS:
        manifest = metadata_dir(spec) / "experiment_manifest.json"
        if manifest.exists():
            data = read_json(manifest)
            version = str((data.get("master_library") or {}).get("master_version", "")).strip()
            if version:
                return version
    raise ValueError("无法确定母库版本：请在 tools/frozen_experiments.yaml 设置 master_library_version")


def master_library_dir() -> Path:
    return ROOT / "data" / "tsfresh_master" / master_library_version()


def protected_paths() -> dict[str, Path]:
    mapping = {name: ROOT / rel for name, rel in _PROTECTED_PATHS.items()}
    mapping.setdefault("raw_data", ROOT / "data" / "raw")
    return mapping


def raw_data_paths() -> tuple[Path, Path]:
    return (
        ROOT / "data" / "raw" / "holding_stats_all.parquet",
        ROOT / "data" / "raw" / "dateBar.pkl",
    )


def master_docs_exist() -> bool:
    return all((ROOT / doc).exists() for doc in MASTER_LIBRARY_DOCS)


def validate_config_loadable(config_path: str) -> bool:
    PipelineParams.from_yaml(ROOT / config_path)
    return True


def iter_whitelist() -> Iterable[FrozenExperiment]:
    return FROZEN_EXPERIMENTS
