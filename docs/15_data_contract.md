# v1.1 数据契约（data contract）

> 适用版本: v1.1.0 及以上
> 维护规则: 新增列须同步更新本文件；不得修改现有契约列（向后兼容）
> 权威代码: `pipeline/adapters/sample_family.py`（`SAMPLES_REQUIRED_COLUMNS` / `LABEL_REQUIRED_COLUMNS`）

---

## 1. 样本骨架契约（`data/prepared/<samples_id>/samples.parquet`）

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 1 | `sample_id` | int64 | 由 `derive_sample_id(idxs, idxd)` 计算，全局唯一且严格升序 |
| 2 | `idxs` | str | 股票代码（如 `sh600000`） |
| 3 | `idxd` | int32 | 信号日（YYYYMMDD 整数，即 signal_date） |
| 4 | `open` | float32 | 信号日开盘价 |
| 5 | `high` | float32 | 信号日最高价 |
| 6 | `low` | float32 | 信号日最低价 |
| 7 | `close` | float32 | 信号日收盘价 |
| 8 | `volume` | float32 | 信号日成交量 |
| 9 | `amount` | float32 | 信号日成交额 |
| 10 | `is_paused` | bool | 信号日是否停牌 |
| 11 | `marks` | int16 | 停牌/特殊标记位 |
| 12 | `sampling_method` | str | 采样方法名（来自注册表） |
| 13 | `sampling_params_hash` | str | 采样参数哈希（8 位十六进制） |
| 14 | `datebar_fingerprint` | str | dateBar 文件指纹（用于数据漂移检测） |
| 15 | `created_at` | str | ISO8601 时间戳 |

**约束**:
- `sample_id` 全局唯一、严格升序（`kind="mergesort"`）
- 无 `target*` / `label*` 列（样本骨架与标签严格分离）
- `idxd` 值与 `idxs` 合法（符合 `universe.exclude_prefixes` 规则）

---

## 2. 标签契约（`data/labels/<samples_id>/<label_name>.parquet`）

| # | 列名 | 类型 | 说明 |
|---|------|------|------|
| 1 | `sample_id` | int64 | 与 samples.parquet 对齐（同 samples 总行数，含 NaN 行） |
| 2 | `idxs` | str | 股票代码 |
| 3 | `signal_date` | int64 | 信号日（YYYYMMDD） |
| 4 | `target` | float64 | 标签值（NaN 表示计算失败或历史不足） |
| 5 | `entry_idxd` | int32 / NaN | 实际入场日 |
| 6 | `exit_idxd` | int32 / NaN | 实际出场日 |
| 7 | `is_complete` | bool | 出场日在 dateBar 范围内（可用于训练） |
| 8 | `is_entry_blocked` | bool | 入场日停牌（若 True 则 target 为 NaN） |
| 9 | `label_name` | str | 标签名（如 `target_future_20d`） |
| 10 | `label_params_hash` | str | 标签参数哈希（8 位十六进制） |
| 11 | `samples_id` | str | 对应 samples_id |
| 12 | `datebar_fingerprint` | str | dateBar 文件指纹 |
| 13 | `created_at` | str | ISO8601 时间戳 |

**约束**:
- `rows == samples.parquet.rows`（含 NaN 行，不丢弃行）
- `target` 无 ±∞
- 目录结构: `data/labels/<samples_id>/` 下每个标签一个 parquet 文件 + `_index.json`

---

## 3. 内置采样清单

| `name` | `name_pattern`（注册表键） | 描述 |
|--------|--------------------------|------|
| `bollinger_breakout` | `bollinger_breakout` | 布林带向上突破事件 + 分年等距抽样 |
| `legacy_holding` | `legacy_holding` | 旧 holding_file 样本集复现（§4.3） |

**注意（V10/V14）**: `legacy_holding` 要求 `max_holding_days`、`max_samples`、`date_range_start/end` 全为 null。

---

## 4. 内置标签清单

| `name_pattern` | 参数键 | `horizon_of()` | 描述 |
|----------------|--------|---------------|------|
| `^target_future_(\d+)d$` | `future_return` | 正则提取的 `h`（天数） | 未来 h 日非停牌收益率 |
| `^target_trend_quality_(\d+)d$` | `trend_quality` | 正则提取的 `h` | 未来 h 日趋势质量（收益/最大回撤） |
| `^target_holding_return$` | — | None（horizon=0） | 旧 holding_file 的实际持仓收益率 |

**使用 `name_pattern` 的原因**: 一个定义可产出多个标签名（如 `target_future_5d`、`target_future_10d` 等），
用 pattern 而不是固定名称以避免代码里出现标签名字面量。

---

## 5. 扩展步骤

### 5.1 新增采样方法

1. 在 `data_prep/samples/events/` 下新建 `<name>.py`；
2. 实现 `SampleDefinition` 子类并添加 `@register_sample` 装饰器；
3. 在 `data_prep/samples/events/__init__.py` 中 `import` 新文件（触发注册）；
4. 在本文档「内置采样清单」中追加一行。

### 5.2 新增标签

1. 复制 `data_prep/labels/_template.py` → `data_prep/labels/<new_label>.py`；
2. 实现 `LabelDefinition` 子类（含 `name_pattern`、`spec_version >= 1`、`horizon_of`、`compute`）；
3. 在 `data_prep/labels/__init__.py` 中 `import` 新文件（触发注册）；
4. 在本文档「内置标签清单」中追加一行（包含 `name_pattern` 列）。

### 5.3 就绪检查

```powershell
# 旧路径
python tools/check_raw_data.py --config configs/local/refactor_check.yaml
# samples 路径（同时检查 prepared 目录）
python tools/check_raw_data.py --config configs/local/refactor_check_samples.yaml
```

退出码: 0=就绪，2=缺失，3=契约不符。
