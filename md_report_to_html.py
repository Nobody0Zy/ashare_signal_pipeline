# -*- coding: utf-8 -*-
"""将分析报告 Markdown 转换为完全自包含的单文件 HTML（固定功能工具）。

图片以 base64 data URI 内嵌，mermaid.min.js 内联到 <script> 标签中。
只需把单个 HTML 文件发给他人，图片/流程图即可正常显示，无需附带任何其他文件。

用法:
    python md_report_to_html.py                          # 默认转换 outputs/v4/analysis_reports 下全部 *.md
    python md_report_to_html.py --reports-dir outputs/v5/analysis_reports
    python md_report_to_html.py --files 03_xxx.md 06_yyy.md
    python md_report_to_html.py --output-dir my_share/html
    python md_report_to_html.py --no-mermaid             # 不渲染 mermaid 流程图（按代码块显示）

依赖: python-markdown（pip install markdown）
"""
from __future__ import annotations

import argparse
import base64
import html
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

import markdown

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"
MERMAID_CACHE = Path(tempfile.gettempdir()) / "mermaid.min.js"

# 匹配 <img src="../<实验目录>/analysis/<文件>.png"> 或任意相对/绝对 .png 引用
IMG_RE = re.compile(r'src="([^"]+\.png)"', re.IGNORECASE)
MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.S)

CSS = """
:root { color-scheme: light; }
body { max-width: 1150px; margin: 0 auto; padding: 24px;
       font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
       line-height: 1.65; color: #1f2328; background: #ffffff; }
h1 { border-bottom: 2px solid #0969da; padding-bottom: 8px; font-size: 26px; }
h2 { border-bottom: 1px solid #d0d7de; padding-bottom: 6px; margin-top: 34px; font-size: 21px; }
h3 { margin-top: 26px; font-size: 17px; }
table { border-collapse: collapse; margin: 14px 0; font-size: 14px; }
th, td { border: 1px solid #d0d7de; padding: 6px 12px; }
th { background: #f6f8fa; font-weight: 600; white-space: nowrap; }
tr:nth-child(even) { background: #f6f8fa; }
img { max-width: 100%; height: auto; border: 1px solid #d8dee4; border-radius: 8px; margin: 6px 0; }
code { background: #eff1f3; padding: 2px 5px; border-radius: 4px; font-family: Consolas, "Courier New", monospace; font-size: 13px; }
pre { background: #f6f8fa; padding: 12px 14px; border-radius: 6px; overflow-x: auto; line-height: 1.45; }
pre code { background: none; padding: 0; font-size: 13px; }
blockquote { border-left: 4px solid #d0d7de; margin: 12px 0; padding: 2px 0 2px 14px; color: #59636e; }
.mermaid { text-align: center; margin: 16px 0; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 28px 0; }
p { margin: 10px 0; }
a { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }
.image-grid { display: grid; gap: 16px; margin: 14px 0 24px; }
.image-grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.image-card { margin: 0; padding: 12px; border: 1px solid #d0d7de; border-radius: 12px; background: #fbfcfe; box-shadow: 0 1px 2px rgba(31, 35, 40, 0.06); }
.image-card figcaption { margin-bottom: 8px; font-size: 13px; font-weight: 600; color: #57606a; }
.image-card img { display: block; width: 100%; margin: 0; cursor: zoom-in; }
.image-hint { margin-top: -8px; color: #57606a; font-size: 13px; }
.lightbox { position: fixed; inset: 0; display: none; align-items: center; justify-content: center; padding: 24px; background: rgba(15, 23, 42, 0.88); z-index: 9999; }
.lightbox.is-open { display: flex; }
.lightbox img { max-width: min(96vw, 1800px); max-height: 92vh; width: auto; height: auto; border: none; border-radius: 10px; box-shadow: 0 16px 60px rgba(0, 0, 0, 0.45); background: #fff; }
.lightbox-close { position: absolute; top: 16px; right: 18px; border: none; border-radius: 999px; padding: 10px 14px; background: rgba(255, 255, 255, 0.16); color: #fff; font-size: 22px; line-height: 1; cursor: pointer; }
.lightbox-caption { position: absolute; left: 24px; right: 24px; bottom: 18px; color: #fff; text-align: center; font-size: 14px; }
@media (max-width: 1100px) {
    .image-grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
    body { padding: 16px; }
    .image-grid-3 { grid-template-columns: 1fr; }
    .image-card { padding: 10px; }
}
"""

LIGHTBOX_SCRIPT = """
<div class="lightbox" id="report-lightbox" aria-hidden="true">
    <button class="lightbox-close" type="button" aria-label="关闭预览">×</button>
    <img alt="">
    <div class="lightbox-caption"></div>
</div>
<script>
(function () {
    const lightbox = document.getElementById("report-lightbox");
    const lightboxImg = lightbox.querySelector("img");
    const lightboxCaption = lightbox.querySelector(".lightbox-caption");
    const closeButton = lightbox.querySelector(".lightbox-close");

    function closeLightbox() {
        lightbox.classList.remove("is-open");
        lightbox.setAttribute("aria-hidden", "true");
        lightboxImg.removeAttribute("src");
        lightboxImg.alt = "";
        lightboxCaption.textContent = "";
        document.body.style.overflow = "";
    }

    function openLightbox(img) {
        lightboxImg.src = img.src;
        lightboxImg.alt = img.alt || "report image";
        lightboxCaption.textContent = img.alt || "点击空白处或按 Esc 关闭";
        lightbox.classList.add("is-open");
        lightbox.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
    }

    document.querySelectorAll(".image-card img").forEach(function (img) {
        img.addEventListener("click", function () {
            openLightbox(img);
        });
    });

    closeButton.addEventListener("click", closeLightbox);
    lightbox.addEventListener("click", function (event) {
        if (event.target === lightbox) {
            closeLightbox();
        }
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && lightbox.classList.contains("is-open")) {
            closeLightbox();
        }
    });
})();
</script>
"""


def embed_images(text: str, reports_dir: Path) -> str:
    """将 md 中引用的图片以 base64 内嵌进 HTML，返回替换后的文本。

    相同源文件只编码一次，重复引用复用 data URI，避免文件膨胀。
    """
    cache: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        rel = m.group(1)
        src = (reports_dir / rel).resolve()
        if not src.exists():
            print(f"  [WARN] 源图片不存在: {rel}")
            return m.group(0)
        if src not in cache:
            cache[src] = base64.b64encode(src.read_bytes()).decode("ascii")
        return f'src="data:image/png;base64,{cache[src]}"'

    out = IMG_RE.sub(repl, text)
    total = sum(len(v) for v in cache.values())
    print(f"  内嵌图片 {len(cache)} 个唯一源 (base64 共 {total / 1024 / 1024:.1f} MB)")
    return out


def extract_mermaid(text: str) -> tuple[str, list[str]]:
    """提取 ```mermaid 代码块为占位符，返回 (text, mermaid_parts)。"""
    parts: list[str] = []

    def repl(m: re.Match) -> str:
        parts.append(html.escape(m.group(1).strip()))
        return f"@@MERMAID_{len(parts) - 1}@@"

    return MERMAID_RE.sub(repl, text), parts


def restore_mermaid(body_html: str, parts: list[str]) -> str:
    for i, content in enumerate(parts):
        body_html = body_html.replace(
            f"@@MERMAID_{i}@@", f'<div class="mermaid">{content}</div>'
        )
    return body_html


def get_mermaid_js() -> str | None:
    """返回 mermaid.min.js 内容；优先用系统缓存，否则下载。失败返回 None。"""
    if MERMAID_CACHE.exists():
        return MERMAID_CACHE.read_text(encoding="utf-8")
    try:
        print("  下载 mermaid.min.js ...")
        urllib.request.urlretrieve(MERMAID_CDN, MERMAID_CACHE)
        size = MERMAID_CACHE.stat().st_size
        print(f"  下载完成，缓存至 {MERMAID_CACHE} ({size / 1024 / 1024:.1f} MB)")
        return MERMAID_CACHE.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] mermaid.js 下载失败: {e}；流程图将以代码块形式显示")
        return None


def build_page(title: str, body_html: str, mermaid_js: str | None) -> str:
    mermaid_script = ""
    if mermaid_js:
        mermaid_script = (
            "<script>\n" f"{mermaid_js}\n" "</script>\n"
            '<script>mermaid.initialize({startOnLoad:true, theme:"default"});</script>'
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
{body_html}
{LIGHTBOX_SCRIPT}
{mermaid_script}
</body>
</html>
"""


def convert_one(md_path: Path, reports_dir: Path, output_dir: Path, enable_mermaid: bool) -> None:
    text = md_path.read_text(encoding="utf-8")

    m = re.match(r"^#\s+(.+?)\s*$", text, re.M)
    title = m.group(1).strip() if m else md_path.stem

    print(f"==> 处理 {md_path.name}")
    text = embed_images(text, reports_dir)
    text, mermaid_parts = extract_mermaid(text)
    if mermaid_parts:
        print(f"  提取 mermaid 流程图 {len(mermaid_parts)} 个")

    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    body = restore_mermaid(body, mermaid_parts)

    mermaid_js = None
    if enable_mermaid and mermaid_parts:
        mermaid_js = get_mermaid_js()

    html_path = output_dir / (md_path.stem + ".html")
    html_path.write_text(build_page(title, body, mermaid_js), encoding="utf-8")
    print(f"  -> {html_path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="将分析报告 Markdown 转换为完全自包含的单文件 HTML（图片 base64 内嵌 + mermaid 内联）。",
    )
    p.add_argument(
        "--reports-dir",
        default=os.path.join("outputs", "v4", "analysis_reports"),
        help="md 报告所在目录（默认 outputs/v4/analysis_reports）",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="html 输出目录（默认 <reports-dir>/html）",
    )
    p.add_argument(
        "--files",
        nargs="+",
        default=None,
        help="指定要转换的 md 文件（可多个，按文件名）；默认转换目录下全部 *.md",
    )
    p.add_argument(
        "--no-mermaid",
        action="store_true",
        help="不内联 mermaid，流程图以代码块显示",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    reports_dir = Path(args.reports_dir).resolve()
    if not reports_dir.is_dir():
        print(f"[ERROR] 报告目录不存在: {reports_dir}")
        return 1
    output_dir = Path(args.output_dir).resolve() if args.output_dir else reports_dir / "html"
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.files:
        md_files = [reports_dir / f for f in args.files]
        missing = [f for f in md_files if not f.exists()]
        if missing:
            print(f"[ERROR] 以下文件不存在: {[str(f) for f in missing]}")
            return 1
    else:
        md_files = sorted(reports_dir.glob("*.md"))
        if not md_files:
            print(f"[ERROR] 目录下没有 .md 文件: {reports_dir}")
            return 1

    for md_path in md_files:
        convert_one(md_path, reports_dir, output_dir, enable_mermaid=not args.no_mermaid)

    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
