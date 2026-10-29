# 02 方案设计

## 2.1 总体结构

```
CLI (src/cli.py)
  └── run_agent (src/agent.py)          ReAct 主循环 + 预算 + 终止判定
        ├── provider (src/provider.py)   Qwen / Scripted / Mock，统一 chat() 接口
        └── ToolBox (src/tools.py)       8 个工具，统一 dispatch() 返回 {ok, ...}
              ├── rules (src/rules.py)   规则引擎 R1~R5
              └── store (src/store.py)   素材 / 排期 / 草稿 / 故障注入
```

分层的目的很直接：**让"决策"和"执行"彻底分开**。`agent.py` 只负责"把模型的动作交给工具、
把工具的结果交回模型"，它不认识任何业务规则；规则全在 `rules.py`，数据全在 `store.py`。
这样面试时问"为什么 19:00 不行"，答案只在一个函数里。

## 2.2 一次运行的生命周期

```
1. CLI 读取场景 JSON → 初始化 DataStore（含场景 seed）与 FaultInjector
2. Provider 收到 [system_prompt, user_message]
3. 循环（最多 max_steps 次）：
     a. provider.chat() 返回一个动作（工具调用或 finish）
     b. 记录 decision_basis（模型自己写的一句话）
     c. 是 finish → pydantic 校验 → 返回
     d. 是工具 → toolbox.dispatch() → 记录 observation → 以 role=tool 回灌
4. 触顶 → 返回 step_limit，并附带已完成的步骤
5. 落盘 trace.json / output.json / drafts.json / console.log
```

`decision_basis` 取的是模型 `content` 里的原话（系统提示词要求它每步写一句 40 字以内的理由）。
若模型是推理模型、把话写在 `reasoning_content` 里，只截取第一句作为摘要，**完整思维过程不落盘**，
符合题目"无须提交模型内部思维过程"的要求。

## 2.3 动态决策体现在哪

题目要求"模型选择动作 → 执行工具 → 观察结果 → 模型调整下一步或结束"。本实现里有三处真实发生：

### ① 时段不是查表，是两步推理

`query_slots` 只回答"哪个整点被占用"。但 `19:00` 空闲不代表合规——它距该账号 `18:00` 的排期
只隔 60 分钟，违反 R2（间隔 ≥120 分钟）。所以模型必须：

```
query_slots → 看到 18:00 被占、19:00 空闲
check_rules(slot=18:00) → R2 block
（观察到 19:00 距 18:00 只有 60 分钟，推断 19:00 也不行）
check_rules(slot=20:00) → 只有 R5 confirm，无 block
→ 选 20:00
```

真实模型的 trace（`examples/run-02-conflict`）里，第 5 步的决策依据是：
> "20:00 仅 R5 需确认，非阻断；19:00 距 X02 仅 60 分钟必违反 R2，故 20:00 为最早合规时段。"

**它自己跳过了 19:00 的验证**，这个推断不是代码写的分支。

### ② 信息缺失时改道补查

`get_asset` 返回 `missing_fields: ["category"]`，模型据此调用 `get_asset_detail`，
补到"医疗健康"后再跑规则，命中 R1 阻断。工具序列变成
`get_asset → get_asset_detail → check_rules`，与正常路径完全不同。

### ③ 工具失败后的三种不同走向

| 失败类型 | 模型的行为 | 证据 |
|---|---|---|
| 可重试的存储错误 | 重试一次 | `run-07` 第 6→7 步 |
| 重试时漏传参数 | 读错误信息 → 补全 `slot` → 再试 → 成功 | `run-07` 第 7→8 步 |
| 不可重试的权限错误 | 停止重试，`incomplete` 并写清原因 | `run-08` |

第 7 步是意外收获：模型第一次重试漏了 `slot`，工具返回
`"缺少必需参数：slot。本工具必需参数：asset_id, slot, deviation_from_required，请补全后重试。"`，
模型据此在第 8 步补全参数并成功。**如果工具只抛 `TypeError` 的英文原文，这个自我修正不会发生**——
所以我把"错误信息要能指导模型"当成了工具层的一条设计要求。

## 2.4 技术取舍

### 原生实现，不用 LangGraph / AgentScope

题目明确写了"框架名称不加分，能解释取舍与修改结果才有价值"。原生实现的好处是：
步数预算、终止条件、trace 结构都是自己写的，可以逐行解释；坏处是要自己处理消息格式和循环控制，
但本项目的循环只有 60 行，不值得为此引入框架的学习和调试成本。

### 工具层再做一次规则校验（防御性重复）

`save_draft` 内部会重新跑一次 `check_rules`，发现 block 就拒绝写入并返回 `rule_violation`。
这跟系统提示词里的"必须先校验"是重复的，但重复是故意的：
提示词管不住的时候，工具层得能兜住。测试 `test_save_draft_guard_rejects_blocked_slot` 专门验证这一点。

### 防重复做两层

- **行为层**：`find_existing_draft` 让模型自己发现已有草稿，然后不调 `save_draft`。
  这是题目要看的"模型决策"。
- **存储层**：`draft_key = asset_id + slot`，即使模型反复调用也只产生一份。这是工程兜底。

两层分开的好处是可分别验证：`test_duplicate_submission_does_not_save_again` 验证行为层，
`test_save_draft_is_idempotent` 验证存储层。

### 没有发布工具，而不是"有发布工具但禁止调用"

如果提供 `publish()` 再靠提示词拦住，就永远存在"模型不听话"的风险。本实现的工具表里根本没有发布能力，
`FinishResult` 里也没有 `published` 字段——越权发布在数据结构上就无法表达。
测试 `test_no_publish_capability_exists` 和 `test_result_schema_cannot_express_published` 把这条固化成断言。

### Mock 与真实模型分开作为证据

- `MockProvider`：写死的规则策略，离线、确定。用来验证**流程与状态机**（测试全都用它）。
- `QwenProvider`：真实模型。用来验证**决策能力**（`examples/run-0*` 都是它跑的）。

两者在 README 和 `docs/03-evaluation.md` 里都标注了能力边界，
避免拿 Mock 的结果冒充模型效果。

## 2.5 规则与数据

规则参数全部在 `data/rules.json`，改数据就能改行为，不用动代码：

| 规则 | 级别 | 作用 |
|---|---|---|
| R1 敏感类目 | block | 医疗健康 / 金融理财 / 未成年人相关 → 必须法务审核 |
| R2 发布间隔 | block | 同账号两次发布间隔 ≥120 分钟 |
| R3 单日条数 | block | 同账号单日 ≤3 条 |
| R4 维护窗口 | block | 02:00–06:00 不可发布 |
| R5 硬性要求 | confirm | 建议时段偏离 `required_time` 时要求确认 |

R5 特意设成 `confirm` 而不是 `block`：它不阻止保存草稿，但会把事项写进 `pending_confirmation`，
这正是"确认边界"的落点。

## 2.6 终止条件与预算

只有四种退出方式，全部显式：

| 退出方式 | 触发 |
|---|---|
| `finish` | 模型主动提交，且通过 schema 校验 |
| `blocked` | 规则阻断（仍然是模型调用 finish，只是状态是 blocked） |
| `incomplete` | 工具持续失败、模型连续两次不调用工具 |
| `step_limit` | 步数或工具调用触顶 |

预算双上限：`max_steps`（默认 14）与 `max_tool_calls`（默认 12），任一触顶立即终止并返回**未完成状态**。
`test_step_limit_is_enforced_for_looping_model` 用一个永远重复同一动作的脚本模型验证它不会跑飞。
