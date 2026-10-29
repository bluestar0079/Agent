# 03 效果验证

## 3.1 两类证据，边界不同

| 证据 | 用什么跑 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| `tests/` 29 个测试 | `MockProvider` / `ScriptedProvider` | 流程、状态机、护栏、幂等、预算是否按设计工作 | 模型能否做出正确决策 |
| `examples/run-0*` 9 组记录 | `qwen3.8-flash` 真实模型 | 模型在真实工具返回下能否调整动作 | 结果的可重复性（温度非 0，每次略有差异） |

下面分别列出。

## 3.2 测试结果

```bash
$ "D:/Anaconda/python.exe" -m pytest -q
.............................                                            [100%]
29 passed in 0.16s
```

逐条用例的完整输出见 [examples/pytest-output.txt](../examples/pytest-output.txt)。

| 测试文件 | 覆盖 | 用例数 |
|---|---|---|
| `test_tools.py` | 时段占用、R2 判定、草稿幂等、越权守门、参数错误可自愈、补查、故障注入 | 8 |
| `test_agent_decision.py` | 九类验收点的端到端行为 + 两条路径必须不同 | 10 |
| `test_guardrails.py` | 无发布能力、schema 无法表达 published、失败不虚报、步数与工具调用上限 | 5 |
| `test_schema.py` | finish 载荷校验（缺 reason、非法状态、required/suggested 分离） | 6 |

关键断言举例：

```python
# 19:00 空闲 ≠ 合规：R2 必须阻断
assert any(r["rule_id"] == "R2" and r["level"] == "block" for r in obs["risks"])

# 模型跳过 check_rules 直接存非法时段，工具层必须拦住
assert obs["error"]["code"] == "rule_violation"

# 两条路径的工具序列必须不同
assert tool_sequence(normal) != tool_sequence(conflict)
```

## 3.3 九组真实模型运行结果

全部由 `qwen3.8-flash` 产生，`--provider qwen`，预算 14 步 / 12 次工具调用。

| 记录 | 场景 | 状态 | 步数 | 关键结果 |
|---|---|---|---|---|
| [run-01-normal](../examples/run-01-normal) | 正常排期 | `completed` | 6 | 18:00 空闲 → 草稿 `D-F54BD617`，未改时段 |
| [run-02-conflict](../examples/run-02-conflict) | 时段冲突 | `pending_confirmation` | 7 | 18:00 被占、19:00 违规 → 建议 20:00，待确认 |
| [run-03-missing-meta](../examples/run-03-missing-meta) | 信息缺失 | `blocked` | 3 | 补查到"医疗健康" → R1 阻断，无草稿 |
| [run-04-rule-risk](../examples/run-04-rule-risk) | 规则风险 | `blocked` | 2 | R1 阻断，未查时段、未存草稿 |
| [run-05-hard-requirement](../examples/run-05-hard-requirement) | 硬性要求 | `pending_confirmation` | 7 | 要求 19:00 仍保留，建议 20:00，待确认非空 |
| [run-06-duplicate](../examples/run-06-duplicate) | 重复提交 | `completed` | 5 | 未调 `save_draft`，草稿数仍为 1 |
| [run-07-save-fail](../examples/run-07-save-fail) | 保存失败（可重试） | `completed` | 8 | 失败 → 重试 → 参数不全 → 自我修正 → 成功 |
| [run-08-save-fail-persistent](../examples/run-08-save-fail-persistent) | 保存失败（不可重试） | `incomplete` | 6 | 明确失败，`draft.saved=false` |
| [run-09-step-limit](../examples/run-09-step-limit) | 步数上限 | `step_limit` | 2 | 触顶即停，返回未完成原因 |

### 两条执行路径的对比（题目硬性要求）

| | run-01-normal | run-02-conflict |
|---|---|---|
| 用户输入 | 完全相同（M01，要求 18:00） | 完全相同（M01，要求 18:00） |
| 账号已有排期 | 无 | 17:30、18:00 |
| 工具序列 | get_asset → check_rules → query_slots → check_rules → find_existing_draft → save_draft | get_asset → check_rules → query_slots → **check_rules(18:00) 失败** → **check_rules(20:00)** → find_existing_draft → save_draft |
| 时段结果 | 18:00，未变更 | 20:00，变更并待确认 |
| 步数 | 6 | 7 |

**输入相同、数据不同、模型的路径也不同**——这正是要证明的东西。

### 与题目参考实例的差异（特此说明）

题目参考实例写的是「18:00 被占用、19:00 可用 → 建议 19:00」。本实现里 19:00 **同样空闲，但不合规**：
R2 要求同账号两次发布间隔 ≥120 分钟，而账号 A07 在 18:00 已有排期，19:00 只隔 60 分钟。
所以建议时段是 20:00（差额刚好 120 分钟）。

这是有意设计，不是实现偏差。若 19:00 直接可用，模型只需「看到占用 → 取下一个空闲时段」，
代码里两个分支就能覆盖，看不出决策。加上 R2 之后「空闲」与「合规」分离，模型必须先校验 19:00
拿到阻断结果，才能推出 20:00——`run-02` 的 trace 第 4、5 步就是这个过程。

差异来自规则参数而非硬编码：把 `data/rules.json` 的 `R2.min_gap_minutes` 从 `120` 改成 `60`，
`check_rules(M01, A07, 19:00)` 立刻不再阻断，结果就回到参考实例的形态。可自行验证：

```bash
# 把 data/rules.json 中 R2.min_gap_minutes 改为 60 后
"$PY" -m src.cli run --scenario data/scenarios/conflict_m01.json --provider mock --out runs/gap60
```

其余验收点（正常、信息缺失、规则风险、重复提交、保存失败、步数上限）与题目描述一致，无偏差。

### 一个值得单独看的细节

`run-02` 第 5 步，模型在观察了 `check_rules(18:00) → R2 block` 之后，直接去校验 `20:00`，
跳过了空闲的 `19:00`。它的决策依据写的是：

> "20:00 仅 R5 需确认，非阻断；19:00 距 X02 仅 60 分钟必违反 R2，故 20:00 为最早合规时段。"

这是模型自己从 18:00 的阻断结果推出 19:00 也不合规，不是代码里的分支。

## 3.4 失败与限制（如实记录）

### ① 模型在 `run-07` 漏填 `suggested_slot`

`run-07` 的草稿实际保存在 18:00，但 finish 载荷里 `suggested_slot` 是 `null`。
任务结论（草稿已存、未发布）没受影响，但报告字段不完整。
**这是模型的输出稳定性问题，不是流程问题。** 可选的改法是把它设为 schema 必填并回灌校验错误让它重填，
本次未做，列入下一步。

### ② 第一次跑 `run-07` 时任务失败

第一次运行（已覆盖）时，模型重试 `save_draft` 漏传了 `slot`，工具只返回
`TypeError: missing 1 required positional argument: 'slot'`，模型无法据此修正，最终 `incomplete`。
据此改了两处：`dispatch()` 在调用前显式校验必填参数，错误信息里带上必需参数列表和 `retryable=true`。
重跑后模型在下一步补全参数并成功。这个修改有代码和证据（`run-07` 第 7→8 步）。

### ③ `run-09` 不是模型能力测试

它把 `max_steps` 压到 2，模型只来得及读完素材就触顶。
这组记录只证明"触顶会返回明确的未完成状态"，不证明模型的规划能力。

### ④ Mock 结果不能当效果证据

`--provider mock` 是写死的策略，跑通只说明流程没坏。
所有"模型能做出正确决策"的结论只引用 `examples/run-0*`。

### ⑤ 其他限制

- 温度非 0，真实模型每次输出可能有差异，不能保证逐字复现；结论层面的断言（选了什么时段、存没存草稿）是稳定的。
- 单素材处理，没有批量。
- 规则集只有 5 条，不含节假日/时区。
- 依赖百炼接口的可用性；离线时只能跑 mock。

## 3.5 复现方式

```bash
PY="D:/Anaconda/python.exe"

# 测试（离线，确定）
"$PY" -m pytest -q

# 真实模型（需要 .env 里的 DASHSCOPE_API_KEY）
"$PY" -m src.cli run --scenario data/scenarios/conflict_m01.json --provider qwen --out runs/repro-conflict
"$PY" -m src.cli run --scenario data/scenarios/save_fail_m01.json --provider qwen --out runs/repro-save-fail

# 离线流程
"$PY" -m src.cli run --scenario data/scenarios/duplicate_m06.json --provider mock --out runs/repro-duplicate
```

每次运行都会在该目录下生成 `input.json`、`output.json`、`trace.json`、`drafts.json`、`console.log`，
可直接与 `examples/` 下的记录对照。
