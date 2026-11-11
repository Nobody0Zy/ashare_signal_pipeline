"""CLI：构建 samples.parquet + manifest.json。

用法：
    python tools/build_samples.py --config configs/local/refactor_check.yaml [--dry-run] [--json]

--json 时输出单行 JSON 含 samples_id（供 PowerShell ConvertFrom-Json 使用）。
禁止在此直接调用 yaml.safe_load；必须经 load_params 再转 dict。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保仓库根在 sys.path（允许从 tools/ 或仓库根运行）
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_config_dict(config_path: str) -> dict:
    """使用 pipeline.config.load_params 加载配置并转为 dict（不直接 yaml.safe_load）。"""
    from dataclasses import asdict
    from pipeline.config import load_params

    params = load_params(config_path)
    return asdict(params)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 samples.parquet + manifest.json")
    parser.add_argument("--config", required=True, help="YAML 配置路径")
    parser.add_argument("--dry-run", action="store_true", help="仅推导 samples_id，不落盘")
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="将 samples_id 以单行 JSON 输出")
    args = parser.parse_args()

    try:
        config_dict = _load_config_dict(args.config)
    except Exception as exc:
        print(f"[build_samples] 配置加载失败: {exc}", file=sys.stderr)
        return 1

    try:
        from data_prep.build_samples import build
        samples_id = build(config_dict, dry_run=args.dry_run)
    except Exception as exc:
        print(f"[build_samples] 构建失败: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    if args.output_json:
        print(json.dumps({"samples_id": samples_id}, ensure_ascii=False))
    else:
        print(f"samples_id: {samples_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
