# 03 效果验证

## 3.1 两类证据，边界不同

| 证据 | 用什么跑 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| `tests/` 34 个测试 | `MockProvider` / `ScriptedProvider` | 流程、状态机、护栏、幂等、预算是否按设计工作 | 模型能否做出正确决策 |
| `examples/run-0*` 10 组记录 | `qwen3.8-flash` 真实模型 | 模型在真实工具返回下能否调整动作 | 结果的可重复性（温度非 0，每次略有差异） |

下面分别列出。

## 3.2 测试结果

```bash
$ "D:/Anaconda/python.exe" -m pytest -q
..................................                                       [100%]
34 passed in 0.08s
```

逐条用例的完整输出见 [examples/pytest-output.txt](../examples/pytest-output.txt)。

| 测试文件 | 覆盖 | 用例数 |
|---|---|---|
| `test_tools.py` | 时段占用、R2 判定、草稿幂等、越权守门、参数错误可自愈、补查、故障注入 | 8 |
| `test_agent_decision.py` | 九类验收点的端到端行为 + 两条路径必须不同 + 一批素材一次跑完 | 11 |
| `test_guardrails.py` | 无发布能力、schema 无法表达 published、失败不虚报、步数与工具调用上限 | 5 |
| `test_schema.py` | finish 载荷校验（缺 reason、非法状态、required/suggested 分离、可排期时 suggested_slot 必填） | 10 |

关键断言举例：

```python
# 19:00 空闲 ≠ 合规：R2 必须阻断
assert any(r["rule_id"] == "R2" and r["level"] == "block" for r in obs["risks"])

# 模型跳过 check_rules 直接存非法时段，工具层必须拦住
assert obs["error"]["code"] == "rule_violation"

# 两条路径的工具序列必须不同
assert tool_sequence(normal) != tool_sequence(conflict)
```

## 3.3 十组真实模型运行结果

全部由 `qwen3.8-flash` 产生，`--provider qwen`。前九组预算 14 步 / 12 次工具调用，
批处理那组为 26 步 / 22 次（整批共享）。

| 记录 | 场景 | 状态 | 步数 | 关键结果 |
|---|---|---|---|---|
| [run-01-normal](../examples/run-01-normal) | 正常排期 | `completed` | 6 | 18:00 空闲 → 草稿 `D-F54BD617`，未改时段 |
| [run-02-conflict](../examples/run-02-conflict) | 时段冲突 | `pending_confirmation` | 7 | 18:00 被占、19:00 违规 → 建议 20:00，待确认 |
| [run-03-missing-meta](../examples/run-03-missing-meta) | 信息缺失 | `blocked` | 3 | 补查到"医疗健康" → R1 阻断，无草稿 |
| [run-04-rule-risk](../examples/run-04-rule-risk) | 规则风险 | `blocked` | 2 | R1 阻断，未查时段、未存草稿 |
| [run-05-hard-requirement](../examples/run-05-hard-requirement) | 硬性要求 | `pending_confirmation` | 7 | 要求 19:00 仍保留，建议 20:00，待确认非空 |
| [run-06-duplicate](../examples/run-06-duplicate) | 重复提交 | `completed` | 5 | 未调 `save_draft`，草稿数仍为 1 |
| [run-07-save-fail](../examples/run-07-save-fail) | 保存失败（可重试） | `completed` | 7 | 首次 `storage_unavailable` → 判断 retryable → 重试成功，草稿 `D-F54BD617` |
| [run-08-save-fail-persistent](../examples/run-08-save-fail-persistent) | 保存失败（不可重试） | `incomplete` | 6 | 明确失败，`draft.saved=false` |
| [run-09-step-limit](../examples/run-09-step-limit) | 步数上限 | `step_limit` | 2 | 触顶即停，返回未完成原因 |
| [run-10-batch](../examples/run-10-batch) | 一批三素材（冲突 + 阻断 + 正常） | `pending_confirmation` | 15 | 一次 finish 交 3 条结论，2 份草稿，被阻断的那条不存草稿 |

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

### ① `suggested_slot` 漏填 —— 已修复

早先一次 `run-07` 里，草稿实际保存在 18:00，但 finish 载荷的 `suggested_slot` 是 `null`。
任务结论（草稿已存、未发布）没受影响，但报告字段不完整。

修复方式是加一条条件必填：`review_status` 为 `no_risk` / `risk_needs_review` 时必须给出建议时段；
被阻断或信息不足的素材本来就没时段，不强制。校验失败时 `agent.py` 会把 pydantic 的错误体回灌给模型，
让它补填而不是直接失败。重跑后 `run-07` 正确填写了 `18:00`，
`test_schedulable_item_requires_suggested_slot` 把这个约束固化下来。

### ② 第一次跑 `run-07` 时任务失败

最早那次运行里，模型重试 `save_draft` 漏传了 `slot`，工具只返回
`TypeError: missing 1 required positional argument: 'slot'`，模型无法据此修正，最终 `incomplete`。
据此改了 `dispatch()`：调用前显式校验必填参数，错误信息里带上缺失字段、必需参数列表和 `retryable=true`。

需要说明的是，**修完之后的那次重跑里模型第一次重试就带全了参数**（`run-07` 第 6→7 步），
没有再触发参数错误，因此这条修复的当前证据是测试 `test_missing_args_error_is_actionable`，
而不是某次运行记录。原始失败记录在修 schema 后已不合规（`suggested_slot` 为空），未保留。

### ③ `run-09` 不是模型能力测试

它把 `max_steps` 压到 2，模型只来得及读完素材就触顶。
这组记录只证明"触顶会返回明确的未完成状态"，不证明模型的规划能力。

### ④ Mock 结果不能当效果证据

`--provider mock` 是写死的策略，跑通只说明流程没坏。
所有"模型能做出正确决策"的结论只引用 `examples/run-0*`。

### ⑤ 其他限制

- 温度非 0，真实模型每次输出可能有差异，不能保证逐字复现；结论层面的断言（选了什么时段、存没存草稿）是稳定的。
- **批量是串行的**：一批素材按顺序处理，不并发；批次越大，单条的处理步数越容易被预算压缩。
- **草稿之间不互相占位**：`save_draft` 只写草稿，不会把时段加进 `bookings`，所以同账号的两条素材在同一批里理论上可能都拿到同一个建议时段。真实系统里应当在草稿阶段就占位，本次未做。
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
"$PY" -m src.cli run --scenario data/scenarios/batch_mixed_m01_m03_m06.json --provider qwen --out runs/repro-batch

# 离线流程
"$PY" -m src.cli run --scenario data/scenarios/duplicate_m06.json --provider mock --out runs/repro-duplicate
```

每次运行都会在该目录下生成 `input.json`、`output.json`、`trace.json`、`drafts.json`、`console.log`，
可直接与 `examples/` 下的记录对照。
