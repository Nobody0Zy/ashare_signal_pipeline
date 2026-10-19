"""步骤6：训练 LightGBM 并输出预测结果。"""

from __future__ import annotations

import json
from typing import Dict, List

import lightgbm as lgb
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import mean_absolute_error

from .config import Paths, PipelineParams, feature_schema_artifact_path, prediction_artifact_paths
from .monitoring import monitor_step
from .utils import file_suffix


def _compute_group_metrics(pred_df: pd.DataFrame, pred_group_q: int) -> List[Dict[str, object]]:
    ranked = pred_df.copy()
    ranked["pred_group"] = pd.qcut(ranked["y_pred"], q=pred_group_q, labels=False, duplicates="drop")
    grouped = ranked.groupby("pred_group", dropna=False)
    rows: List[Dict[str, object]] = []
    for group, frame in grouped:
        rows.append(
            {
                "pred_group": int(group) if pd.notna(group) else -1,
                "count": int(len(frame)),
                "target_mean": float(frame["target"].mean()),
                "target_win_rate": float((frame["target"] > 0).mean()),
                "target_raw_mean": float(frame["target_raw"].mean()),
                "target_raw_win_rate": float((frame["target_raw"] > 0).mean()),
            }
        )
    return rows


def _build_prediction_frame(frame: pd.DataFrame, predictions, feature_cols: List[str]) -> pd.DataFrame:
    pred_df = frame[["sample_id", "signal_date", "target", "target_raw"] + feature_cols].copy()
    pred_df["y_pred"] = predictions
    return pred_df


def _write_feature_schema(paths: Paths, mode: str, feature_cols: List[str]) -> str:
    schema_path = feature_schema_artifact_path(paths, mode)
    payload = {
        "feature_columns": feature_cols,
        "feature_count": len(feature_cols),
        "required_base_columns": ["sample_id", "signal_date", "target", "target_raw", "y_pred"],
    }
    schema_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(schema_path.relative_to(paths.root)).replace("\\", "/")


def compute_step6(paths: Paths, mode: str, params: PipelineParams) -> Dict[str, object]:
    suffix = file_suffix(mode)
    train_path = paths.splits_dir / f"train{suffix}.parquet"
    val_path = paths.splits_dir / f"val{suffix}.parquet"
    test_path = paths.splits_dir / f"test{suffix}.parquet"
    train_df = pd.read_parquet(train_path)
    val_df = pd.read_parquet(val_path)
    test_df = pd.read_parquet(test_path)

    exclude_cols = ["sample_id", "signal_date", "target", "target_raw"]
    feature_cols = sorted([column for column in train_df.columns if column not in exclude_cols])
    X_train = train_df[feature_cols]
    y_train = train_df["target"]
    X_val = val_df[feature_cols]
    y_val = val_df["target"]
    X_test = test_df[feature_cols]
    y_test = test_df["target"]

    with monitor_step(paths, params, 6, mode, "dataset_train_predict"):
        train_data = lgb.Dataset(X_train, label=y_train)
        val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
        lgb_params = params.lgb.to_lgb_params()
        profile_overrides = params.lgb.resolve_profile()

        if mode == "smoke" and params.lgb.lgb_profile != "default":
            print(f"步骤6 冒烟模式: lgb_profile 从 {params.lgb.lgb_profile} 降级为 default (p>>n 风险)")
            profile_overrides = {}

        if profile_overrides:
            num_boost_round = profile_overrides.pop("num_boost_round", params.lgb.num_boost_round)
            lgb_params.update(profile_overrides)
            print(f"步骤6 LGB profile={params.lgb.lgb_profile} 覆写参数: {list(profile_overrides.keys())}")
        else:
            num_boost_round = params.lgb.num_boost_round

        model = lgb.train(
            lgb_params,
            train_data,
            num_boost_round=num_boost_round,
            valid_sets=[val_data],
            callbacks=[
                lgb.early_stopping(stopping_rounds=params.lgb.early_stopping_rounds),
                lgb.log_evaluation(period=params.lgb.log_evaluation_period),
            ],
        )

        best_iteration = int(model.best_iteration)
        train_pred = model.predict(X_train[feature_cols], num_iteration=best_iteration)
        val_pred = model.predict(X_val[feature_cols], num_iteration=best_iteration)
        test_pred = model.predict(X_test[feature_cols], num_iteration=best_iteration)
        mae = float(mean_absolute_error(y_test, test_pred))
        spearman = float(spearmanr(y_test, test_pred).statistic)
        pearson = float(pearsonr(y_test, test_pred).statistic)
        train_pred_df = _build_prediction_frame(train_df, train_pred, feature_cols)
        val_pred_df = _build_prediction_frame(val_df, val_pred, feature_cols)
        test_pred_df = _build_prediction_frame(test_df, test_pred, feature_cols)
        group_metrics = _compute_group_metrics(test_pred_df, params.eval.pred_group_q)
        print("步骤6分组测试统计:")
        print(pd.DataFrame(group_metrics).to_string(index=False))

        model_path = paths.models_dir / f"lgb_model{suffix}.txt"
        prediction_paths = prediction_artifact_paths(paths, mode)
        model.save_model(str(model_path))
        train_pred_df.to_csv(prediction_paths["train"], index=False)
        val_pred_df.to_csv(prediction_paths["val"], index=False)
        test_pred_df.to_csv(prediction_paths["test"], index=False)
        feature_schema_rel = _write_feature_schema(paths, mode, feature_cols)

    checks = {
        "model_exists": model_path.exists(),
        "train_prediction_exists": prediction_paths["train"].exists(),
        "val_prediction_exists": prediction_paths["val"].exists(),
        "test_prediction_exists": prediction_paths["test"].exists(),
        "test_prediction_rows_match": int(len(test_pred_df)) == int(len(test_df)),
        "train_prediction_rows_match": int(len(train_pred_df)) == int(len(train_df)),
        "val_prediction_rows_match": int(len(val_pred_df)) == int(len(val_df)),
        "feature_schema_exists": (paths.root / feature_schema_rel).exists(),
        "feature_count_positive": len(feature_cols) > 0,
    }
    return {
        "output_files": [
            str(model_path.relative_to(paths.root)).replace("\\", "/"),
            str(prediction_paths["train"].relative_to(paths.root)).replace("\\", "/"),
            str(prediction_paths["val"].relative_to(paths.root)).replace("\\", "/"),
            str(prediction_paths["test"].relative_to(paths.root)).replace("\\", "/"),
            feature_schema_rel,
        ],
        "checks": checks,
        "details": {
            "best_iteration": best_iteration,
            "mae": mae,
            "spearman": spearman,
            "pearson": pearson,
            "train_rows": int(len(train_df)),
            "val_rows": int(len(val_df)),
            "test_rows": int(len(test_df)),
            "feature_columns": feature_cols,
            "group_metrics": group_metrics,
        },
    }