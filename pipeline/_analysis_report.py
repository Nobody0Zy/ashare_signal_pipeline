import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Dict

import pandas as pd

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover
    lgb = None

ROOT = Path(__file__).resolve().parent.parent


def _read_json(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding='utf-8'))


def _abs_path(path_str: str) -> Path:
    return (ROOT / path_str).resolve()


def _load_manifest(args: argparse.Namespace) -> Dict[str, object]:
    manifest_path: Path | None = None
    if args.manifest:
        manifest_path = _abs_path(args.manifest)
    elif args.experiment_version:
        manifest_path = ROOT / 'outputs' / args.experiment_version / 'metadata' / 'experiment_manifest.json'
    else:
        registry_path = ROOT / 'outputs' / 'experiment_registry.json'
        if registry_path.exists():
            registry = _read_json(registry_path)
            if registry:
                latest = registry[-1]
                manifest_path = _abs_path(str(latest['manifest_file']))
        if manifest_path is None:
            legacy_manifest = ROOT / 'outputs' / 'demo_v0_legacy' / 'metadata' / 'experiment_manifest.json'
            if legacy_manifest.exists():
                manifest_path = legacy_manifest

    if manifest_path is None or not manifest_path.exists():
        return {
            'experiment_version': 'legacy_root',
            'experiment': {
                'description': '未找到 manifest，回退到根目录统一版本输出布局。',
            },
            'artifact_files': {
                'prediction_file': 'outputs/legacy_root/predictions.csv',
                'train_split_file': 'data/splits/train.parquet',
                'val_split_file': 'data/splits/val.parquet',
                'test_split_file': 'data/splits/test.parquet',
                'selected_features_file': 'data/features/selected_features.txt',
                'clip_percentiles_file': 'data/features/clip_percentiles.json',
                'state_file': 'pipeline_state_full.json',
                'model_file': 'models/lgb_model.txt',
                'analysis_report_file': 'outputs/legacy_root/analysis_report.json',
            },
            '_manifest_path': None,
        }

    manifest = _read_json(manifest_path)
    manifest['_manifest_path'] = str(manifest_path.relative_to(ROOT)).replace('\\', '/')
    return manifest


def _artifact_path(manifest: Dict[str, object], key: str, *fallbacks: str) -> Path:
    artifact_files = manifest.get('artifact_files', {})
    if key in artifact_files:
        return _abs_path(str(artifact_files[key]))

    artifact_roots = manifest.get('artifact_roots', {})
    if key == 'state_file' and 'state_file' in artifact_roots:
        return _abs_path(str(artifact_roots['state_file']))
    if key == 'prediction_file' and 'output_dir' in artifact_roots:
        base = _abs_path(str(artifact_roots['output_dir']))
        for name in ('predictions.csv', 'test_predictions.csv', 'baseline_test_predictions.csv'):
            candidate = base / name
            if candidate.exists():
                return candidate
    mapping = {
        'train_split_file': ('splits_dir', 'train.parquet'),
        'val_split_file': ('splits_dir', 'val.parquet'),
        'test_split_file': ('splits_dir', 'test.parquet'),
        'selected_features_file': ('features_dir', 'selected_features.txt'),
        'clip_percentiles_file': ('features_dir', 'clip_percentiles.json'),
        'model_file': ('models_dir', 'lgb_model.txt'),
        'analysis_report_file': ('output_dir', 'analysis_report.json'),
    }
    if key in mapping:
        root_key, file_name = mapping[key]
        if root_key in artifact_roots:
            return _abs_path(str(artifact_roots[root_key])) / file_name

    for fallback in fallbacks:
        candidate = _abs_path(fallback)
        if candidate.exists() or fallback == fallbacks[-1]:
            return candidate
    raise KeyError(f'无法解析工件路径: {key}')


def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='生成实验分析 JSON，并写入对应版本目录。')
    parser.add_argument('--experiment-version', type=str, default=None)
    parser.add_argument('--manifest', type=str, default=None)
    parser.add_argument('--output', type=str, default=None)
    return parser.parse_args()


args = _build_args()
manifest = _load_manifest(args)
pred = pd.read_csv(_artifact_path(manifest, 'prediction_file', 'outputs/legacy_root/predictions.csv'))
train = pd.read_parquet(_artifact_path(manifest, 'train_split_file', 'data/splits/train.parquet'))
val = pd.read_parquet(_artifact_path(manifest, 'val_split_file', 'data/splits/val.parquet'))
test = pd.read_parquet(_artifact_path(manifest, 'test_split_file', 'data/splits/test.parquet'))
selected = [
    line.strip()
    for line in _artifact_path(manifest, 'selected_features_file', 'data/features/selected_features.txt').read_text(encoding='utf-8').splitlines()
    if line.strip()
]
clip = _read_json(_artifact_path(manifest, 'clip_percentiles_file', 'data/features/clip_percentiles.json'))
state = _read_json(_artifact_path(manifest, 'state_file', 'pipeline_state_full.json'))
model_path = _artifact_path(manifest, 'model_file', 'models/lgb_model.txt')
model = lgb.Booster(model_file=str(model_path)) if lgb is not None and model_path.exists() else None

median = float(pred['y_pred'].median())
pred['score_group'] = pred['y_pred'].le(median).map({True: 'low', False: 'high'})
median_summary = pred.groupby('score_group').agg(
    sample_count=('sample_id', 'size'),
    target_mean=('target', 'mean'),
    target_win_rate=('target', lambda s: (s > 0).mean()),
    target_raw_mean=('target_raw', 'mean'),
    target_raw_win_rate=('target_raw', lambda s: (s > 0).mean()),
).reset_index()
low = median_summary.loc[median_summary['score_group'] == 'low'].iloc[0]
high = median_summary.loc[median_summary['score_group'] == 'high'].iloc[0]
median_uplift = {
    'target_mean_diff': float(high['target_mean'] - low['target_mean']),
    'target_mean_rel': float((high['target_mean'] - low['target_mean']) / abs(low['target_mean'])) if low['target_mean'] != 0 else None,
    'target_win_diff': float(high['target_win_rate'] - low['target_win_rate']),
    'target_win_rel': float((high['target_win_rate'] - low['target_win_rate']) / low['target_win_rate']) if low['target_win_rate'] != 0 else None,
    'target_raw_mean_diff': float(high['target_raw_mean'] - low['target_raw_mean']),
    'target_raw_mean_rel': float((high['target_raw_mean'] - low['target_raw_mean']) / abs(low['target_raw_mean'])) if low['target_raw_mean'] != 0 else None,
    'target_raw_win_diff': float(high['target_raw_win_rate'] - low['target_raw_win_rate']),
    'target_raw_win_rel': float((high['target_raw_win_rate'] - low['target_raw_win_rate']) / low['target_raw_win_rate']) if low['target_raw_win_rate'] != 0 else None,
}

base_target_mean = float(pred['target'].mean())
base_target_win = float((pred['target'] > 0).mean())
base_raw_mean = float(pred['target_raw'].mean())
base_raw_win = float((pred['target_raw'] > 0).mean())
threshold_rows = []
for q in [0.5, 0.6, 0.7, 0.8, 0.9]:
    thresh = float(pred['y_pred'].quantile(q))
    keep = pred[pred['y_pred'] >= thresh].copy()
    threshold_rows.append({
        'quantile': q,
        'threshold': thresh,
        'kept_samples': int(len(keep)),
        'kept_ratio': float(len(keep) / len(pred)),
        'target_mean': float(keep['target'].mean()),
        'target_win_rate': float((keep['target'] > 0).mean()),
        'target_raw_mean': float(keep['target_raw'].mean()),
        'target_raw_win_rate': float((keep['target_raw'] > 0).mean()),
        'target_mean_vs_all': float(keep['target'].mean() - base_target_mean),
        'target_win_vs_all': float((keep['target'] > 0).mean() - base_target_win),
        'target_raw_mean_vs_all': float(keep['target_raw'].mean() - base_raw_mean),
        'target_raw_win_vs_all': float((keep['target_raw'] > 0).mean() - base_raw_win),
    })
threshold_df = pd.DataFrame(threshold_rows)

source = Counter()
stat = Counter()
for name in selected:
    parts = name.split('__')
    source[parts[0]] += 1
    stat['__'.join(parts[1:]) if len(parts) > 1 else parts[0]] += 1
feature_cols = [c for c in train.columns if c not in ['sample_id', 'signal_date', 'target', 'target_raw']]
manual = [c for c in feature_cols if c not in selected]

importance_pairs = []
if model is not None:
    importance_pairs = sorted(
        zip(model.feature_name(), model.feature_importance(importance_type='gain')),
        key=lambda x: x[1],
        reverse=True,
    )

report = {
    'experiment_version': manifest.get('experiment_version', 'unknown'),
    'experiment': manifest.get('experiment', {}),
    'manifest_path': manifest.get('_manifest_path'),
    'median': median,
    'median_summary': median_summary.to_dict(orient='records'),
    'median_uplift': median_uplift,
    'threshold_table': threshold_df.to_dict(orient='records'),
    'overall_test': {
        'rows': int(len(pred)),
        'target_mean': base_target_mean,
        'target_win_rate': base_target_win,
        'target_raw_mean': base_raw_mean,
        'target_raw_win_rate': base_raw_win,
        'y_pred_mean': float(pred['y_pred'].mean()),
        'y_pred_std': float(pred['y_pred'].std()),
    },
    'split_ranges': {
        'train_rows': int(len(train)),
        'train_range': [int(train['signal_date'].min()), int(train['signal_date'].max())],
        'val_rows': int(len(val)),
        'val_range': [int(val['signal_date'].min()), int(val['signal_date'].max())],
        'test_rows': int(len(test)),
        'test_range': [int(test['signal_date'].min()), int(test['signal_date'].max())],
    },
    'clip_percentiles': clip,
    'feature_counts': {
        'selected_step4': len(selected),
        'final_model_features': len(feature_cols),
        'manual_features': len(manual),
    },
    'feature_source_counts': source.most_common(),
    'feature_stat_counts': stat.most_common(15),
    'manual_features': manual,
    'top_importance': [{'feature': name, 'gain': float(gain)} for name, gain in importance_pairs[:20]],
    'model_artifact': {
        'path': str(model_path.relative_to(ROOT)).replace('\\', '/'),
        'lightgbm_available': lgb is not None,
        'model_loaded': model is not None,
    },
    'step_durations': {
        step_name: {
            'status': step_state.get('status'),
            'duration_hms': step_state.get('duration_hms'),
            'duration_seconds': step_state.get('duration_seconds'),
            'timestamp': step_state.get('timestamp'),
        }
        for step_name, step_state in state.get('steps', {}).items()
    },
    'step6_metrics': state['steps']['step_6']['details'],
}
output_path = _abs_path(args.output) if args.output else _artifact_path(
    manifest,
    'analysis_report_file',
    f"outputs/{manifest.get('experiment_version', 'unknown')}/analysis_report.json",
)
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
