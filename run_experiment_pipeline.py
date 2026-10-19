import argparse
import json
from pathlib import Path

from pipeline.config import build_paths, ensure_directories, load_params, save_resolved_params
from pipeline.runner import run_step


def main() -> int:
    parser = argparse.ArgumentParser(description="A股布林带信号增强实验管道")
    parser.add_argument("--mode", choices=["smoke", "full"], required=True)
    parser.add_argument("--step", type=int, choices=range(1, 7), required=True)
    parser.add_argument("--smoke", type=int, default=None)
    parser.add_argument("--config", type=str, default="configs/default.yaml")
    parser.add_argument("--state-file", type=str, required=True)
    args = parser.parse_args()

    params = load_params(args.config)
    if args.smoke is not None:
        params.smoke_size = args.smoke
    root = Path.cwd()
    paths = build_paths(root, args.state_file, params)
    ensure_directories(paths)
    save_resolved_params(paths, params, args.config, args.mode)
    return_code = run_step(paths, args.mode, params, args.step)
    if return_code == 0 and args.step == 6 and params.experiment.auto_eval_summary:
        from pipeline.eval_summary import generate_eval_summary

        summary = generate_eval_summary(root, paths.experiment_version, split="test")
        summary_path = root / "outputs" / paths.experiment_version / "analysis" / "eval_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[eval_summary] 已写入 {summary_path}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())