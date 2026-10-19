"""管道编排层。"""

from __future__ import annotations

import time
import traceback
from typing import Callable, Dict

from .config import Paths, PipelineParams
from .state import (
    format_duration_hms,
    load_state,
    now_iso,
    require_previous_step,
    step_key,
    update_step_state,
)
from .step1_clean import compute_step1
from .step2_slices import compute_step2
from .step3_indicators import compute_step3
from .step4_features import compute_step4
from .step5_split import compute_step5
from .step6_model import compute_step6
from .utils import log_step_start


StepRunner = Callable[[Paths, str, PipelineParams], Dict[str, object]]


STEP_RUNNERS: Dict[int, StepRunner] = {
    1: compute_step1,
    2: compute_step2,
    3: compute_step3,
    4: compute_step4,
    5: compute_step5,
    6: compute_step6,
}


STEP_MESSAGES = {
    1: "清洗持仓与日线原始数据，生成标准化 processed 文件。",
    3: "基于 60 日切片计算 12 个基础技术指标。",
}


def _completed_step_matches_current_inputs(
    paths: Paths,
    params: PipelineParams,
    step: int,
    current: Dict[str, object],
) -> bool:
    if step != 4:
        return True
    details = current.get("details", {}) if isinstance(current, dict) else {}
    if not isinstance(details, dict):
        return True
    role = str(params.tsfresh.master_library_role)
    if role == "project":
        return details.get("source_master_version") == paths.resolved_master_library_version
    if role == "build":
        return details.get("built_master_version") == paths.resolved_master_library_version
    return True


def get_step_message(step: int, mode: str) -> str:
    if step in STEP_MESSAGES:
        return STEP_MESSAGES[step]
    if step == 2:
        return "随机抽取冒烟样本并生成 60 日非停牌行情切片及 non_trading_ratio。" if mode == "smoke" else "处理全部样本并生成 60 日非停牌行情切片及 non_trading_ratio。"
    if step == 4:
        return "执行 tsfresh 特征提取与冒烟特征筛选，输出 50 个最终特征。" if mode == "smoke" else "执行 tsfresh 特征提取与基于训练集的特征筛选，输出最终特征文件。"
    if step == 5:
        return "加入手工特征、创建 target_raw，并随机划分训练/验证/测试集。" if mode == "smoke" else "加入手工特征、创建 target_raw，并按时间划分训练/验证/测试集。"
    if step == 6:
        return "训练 LightGBM 回归模型并输出训练/验证/测试集预测与测试集分组统计。"
    raise KeyError(f"未知步骤: {step}")


def run_step(paths: Paths, mode: str, params: PipelineParams, step: int) -> int:
    state = load_state(paths, mode)
    started_at = now_iso()
    started_perf = time.perf_counter()
    try:
        state_file_display = str(paths.state_file.relative_to(paths.root)).replace("\\", "/")
    except ValueError:
        state_file_display = str(paths.state_file)
    try:
        require_previous_step(state, step)
        current = state.get("steps", {}).get(step_key(step), {})
        if current.get("status") == "completed" and current.get("checks_passed") is True:
            if not _completed_step_matches_current_inputs(paths, params, step, current):
                print(f"步骤 {step} 已完成，但输入签名已变化，重新执行。")
            else:
                print(f"步骤 {step} 已完成且校验通过，跳过执行。")
                return 0

        log_step_start(step, mode, get_step_message(step, mode))
        result = STEP_RUNNERS[step](paths, mode, params)
        checks = result["checks"]
        checks_passed = bool(all(checks.values()))
        details = result["details"]
        details["checks"] = checks
        duration_seconds = time.perf_counter() - started_perf
        duration_hms = format_duration_hms(duration_seconds)
        status = "completed" if checks_passed else "failed"
        update_step_state(
            paths,
            state,
            step,
            status,
            result["output_files"],
            checks_passed,
            details,
            started_at=started_at,
            finished_at=now_iso(),
            duration_seconds=duration_seconds,
            duration_hms=duration_hms,
        )
        print(f"步骤 {step} 验收结果:")
        for key, value in checks.items():
            print(f"- {key}: {'通过' if value else '失败'}")
        if not checks_passed:
            raise RuntimeError(f"步骤 {step} 自检失败")
        print(f"步骤 {step} 耗时: {duration_hms}")
        print(f"步骤 {step} 已完成并写入状态文件 {state_file_display}")
        return 0
    except Exception as exc:
        duration_seconds = time.perf_counter() - started_perf
        update_step_state(
            paths,
            state,
            step,
            "failed",
            [],
            False,
            {
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
            started_at=started_at,
            finished_at=now_iso(),
            duration_seconds=duration_seconds,
            duration_hms=format_duration_hms(duration_seconds),
        )
        print(f"步骤 {step} 执行失败: {exc}")
        print(traceback.format_exc())
        return 1