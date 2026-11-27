# dateBar 探测记录（M0）

数据来源：`configs/local/refactor_check.yaml` 指向的 `dateBar.pkl`。  
探测产物：`version_design/design/refactor/datebar_probe.json`。

核心事实（来自 probe）：

- rows = `15,547,065`
- n_symbols = `6,090`
- idxd range = `20070104 ~ 20250114`
- dtypes 与设计一致：`idxs=object`、`idxd=int64`、OHLCV/amount/xs=`float32`、`paused/marks=int16`
- `marks` 取值数 `43`，且 `bits_ge_12_nonzero_rows = 0`

### C1

结论：价格列已处于后复权口径，后续流程不得再乘 `xs`。  
证据（抽样 `sz000630`, `idxd=20151023`）：`xs_jump=5.0537`，但 `close_jump=1.1015`（连续），而 `(close/xs)_jump=0.2180`（发生断点）。这符合“`close≈原始价×xs` 已内嵌”的预期。

### C2

结论：`idxs` 编码格式满足 `^(sh|sz|bj)\d{6}$`，当前样本无异常编码。  
证据：`datebar_probe.json.idxs_format.match_ratio = 1.0`，即全量命中正则。

### C3

结论：`is_complete=False` 的缺口主要集中在样本尾部（2024-10 之后），符合设计预期。  
口径：以 `h=40`（`future_return` 最大 horizon）计算“信号日后可用非停牌交易日是否足够”。  
结果：`incomplete_rows=3035 / 240257 (1.2632%)`；其中 `signal_date>=20241001` 的样本占缺口 `98.6820%`，尾部样本内缺口比例 `60.6890%`。

### C4

结论：`marks` 的涨跌停位序与收益跳变幅度一致，当前解码口径可用。  
证据：按 §4.4 位序抽查 `close_limit_up(bit8)` 与 `close_limit_down(bit4)` 各 10 条，日收益率均贴近 `{+10%,-10%}` 档（最大偏差分别 `0.001176`、`0.000815`），且 `bits>=12` 全零。

### C5（M4 追加：两套 datebar 规范化按值比对）

结论：`data_prep/datebar.py::load_datebar` 与 `pipeline/step1_clean.py::_clean_datebar` 对同一 `dateBar.pkl` 产出的 9 列数值**按值逐值相等**（允许 `idxd` 的 int32/int64 差异）。

验证口径：取 `data_prep.datebar.load_datebar` 输出的 9 列（`idxs`, `idxd`, `open`, `high`, `low`, `close`, `volume`, `amount`, `paused`），与 step1 落盘的 `data/processed/.../datebar.parquet` 对比。将 `idxd` 统一转换为 int64、`paused` 统一转换为 bool 后，两个 DataFrame 完全相等（`df.equals()` 返回 True）。

实测于 M4 Gate（见 `verify_report_M4.json` A4-18）：shape 相同，逐列数值差异为 0。
