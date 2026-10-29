# 素材审核与发布排期 Agent

内容团队每天都要回答同一组问题：这条素材能不能发？排在哪天、哪个时段？会不会和已有排期撞上？
哪些事必须由人来拍板？

这个工具把「找待处理素材 → 判风险 → 排时段 → 存草稿 → 列出待确认项」串成一条可复核的流程。
**它只保存草稿，不具备任何发布能力。**

---

## 快速开始

### 0. 环境自检（Windows 上必做）

```bash
python --version
```

如果这条命令**没有任何输出**且退出码非 0，说明 `python` 指向的是 Windows 应用商店的占位程序，
请改用真实解释器路径（本仓库开发时使用的是）：

```bash
PY="D:/Anaconda/python.exe"
"$PY" --version
```

下文所有命令沿用这个 `$PY` 变量。macOS / Linux 上直接用 `python` 即可。

### 1. 安装依赖

```bash
"$PY" -m pip install -r requirements.txt
```

依赖只有三个：`openai`（调用百炼的 OpenAI 兼容接口）、`pydantic`（结果 schema 校验）、`pytest`。

### 2. 离线跑通（不需要 API Key）

```bash
"$PY" -m src.cli run --scenario data/scenarios/conflict_m01.json --provider mock --out runs/mock-conflict
```

这条命令会完整走一遍「读素材 → 查规则 → 查时段 → 校验时段 → 查重 → 存草稿 → 结束」的循环，
并打印逐步执行记录。看全部可用场景：

```bash
"$PY" -m src.cli list-scenarios
```

> ⚠️ `--provider mock` 是一个写死的规则策略，**只用于验证流程和状态机能否跑通**，
> 不能作为「模型能做出正确决策」的证据。真实模型效果请看 `examples/run-0*`。

### 3. 接真实模型

```bash
cp .env.example .env      # 填入 DASHSCOPE_API_KEY
"$PY" -m src.cli run --scenario data/scenarios/conflict_m01.json --provider qwen --out runs/my-conflict

# 一批三条素材（冲突 / 阻断 / 正常），一次 finish 交全部结论
"$PY" -m src.cli run --scenario data/scenarios/batch_mixed_m01_m03_m06.json --provider qwen --out runs/my-batch
```

默认模型是 `qwen3.8-flash`，走百炼的 OpenAI 兼容端点
`https://dashscope.aliyuncs.com/compatible-mode/v1`。

### 4. 跑测试

```bash
"$PY" -m pytest -q
```

34 个测试全部离线执行（用 `MockProvider` 和 `ScriptedProvider`），不依赖网络。

---

## 从哪里开始看（5 分钟）

| 想看的 | 去哪看 |
|---|---|
| 真实的模型决策循环长什么样 | [examples/run-02-conflict/console.log](examples/run-02-conflict/console.log)（人读）<br>[examples/run-02-conflict/trace.json](examples/run-02-conflict/trace.json)（机读） |
| 两条完全不同的执行路径 | 对比 `run-01-normal`（一路顺）与 `run-02-conflict`（中途改道） |
| 工具失败后模型怎么调整 | `run-07-save-fail`（判断可重试后重试成功）vs `run-08-save-fail-persistent`（放弃并如实上报） |
| 步数上限 | `run-09-step-limit` |
| 一批素材怎么逐条推进 | `run-10-batch`（第 8、10 步是模型自己切换到下一条素材） |
| 设计取舍 | [docs/02-solution.md](docs/02-solution.md) |
| 每个场景的实际结果 | [docs/03-evaluation.md](docs/03-evaluation.md) |
| 测试的完整实际输出 | [examples/pytest-output.txt](examples/pytest-output.txt) |
| AI 用在哪、人改了什么 | [docs/04-ai-usage.md](docs/04-ai-usage.md) |

---

## 目录结构

```
src/
  config.py      .env 解析与运行预算（不引入 python-dotenv）
  schemas.py     finish 载荷的 pydantic 定义
  store.py       素材/排期/草稿读写、草稿幂等、故障注入器
  rules.py       规则引擎（R1~R5，参数全部来自 data/rules.json）
  tools.py       8 个工具的 OpenAI function schema 与实现
  provider.py    QwenProvider / ScriptedProvider / MockProvider
  trace.py       逐步执行记录（不含模型内部思维过程）
  agent.py       ReAct 主循环、步数/工具调用预算、终止判定
  cli.py         命令行入口
tests/           34 个测试，覆盖场景与护栏
examples/        10 组运行记录（input / output / trace / drafts / console.log）+ pytest 全量输出
data/            素材、规则、已有排期、10 个场景定义
docs/            01-requirements ~ 05-retrospective
```

---

## 场景覆盖

| 场景 | 用例 | 证据 |
|---|---|---|
| 正常排期 | `normal_m01` | [examples/run-01-normal](examples/run-01-normal) |
| 时段冲突 → 替代时段 + 待确认 | `conflict_m01` | [examples/run-02-conflict](examples/run-02-conflict) |
| 素材信息缺失 → 先补查 | `missing_meta_m02` | [examples/run-03-missing-meta](examples/run-03-missing-meta) |
| 规则风险 → 阻断 | `rule_risk_m03` | [examples/run-04-rule-risk](examples/run-04-rule-risk) |
| 硬性要求不可擅改 | `hard_requirement_m04` | [examples/run-05-hard-requirement](examples/run-05-hard-requirement) |
| 重复提交不产生第二份草稿 | `duplicate_m06` | [examples/run-06-duplicate](examples/run-06-duplicate) |
| 保存失败 → 重试 / 如实上报 | `save_fail_m01`、`save_fail_persistent_m01` | [run-07](examples/run-07-save-fail)、[run-08](examples/run-08-save-fail-persistent) |
| 步数上限 → 明确未完成 | `step_limit_m01` | [examples/run-09-step-limit](examples/run-09-step-limit) |
| 一批素材一次处理完 | `batch_mixed_m01_m03_m06` | [examples/run-10-batch](examples/run-10-batch) |

---

## 三个设计要点

1. **时段冲突不是查表**。`19:00` 可能空闲，但距该账号 `18:00` 的已有排期只隔 60 分钟，
   违反「间隔 ≥120 分钟」。模型必须把 `query_slots` 和 `check_rules` 两次结果合起来判断，
   才能得出 `20:00`。这正是 `run-02` 里发生的事。

2. **越权发布在架构上不可能**。工具表里没有 publish，结果 schema 里没有 published 字段。
   不是靠提示词拦，是没有那条路。

3. **工具失败要能被模型读懂**。所有工具返回 `{ok, error:{code, message, retryable}}`，
   参数错误还会附带缺失字段和完整必需参数列表，模型据此就能补全重试。

---

## 关键约束与证据

这个项目有几条硬约束，不是写在文档里的承诺，而是代码结构或测试保证的。每条都能点开验证：

| 约束 | 怎么保证的 | 在哪验证 |
|---|---|---|
| 只存草稿，没有发布能力 | 工具表里没有 `publish`，`FinishResult` 里没有 `published` 字段——越权发布在数据结构上无法表达 | `test_no_publish_capability_exists`、`test_result_schema_cannot_express_published` |
| 失败不许上报成成功 | `draft.saved` 只由 `save_draft` 的真实返回决定，模型不能自行构造 | [run-08](examples/run-08-save-fail-persistent)（持续失败 → `incomplete`）、`test_persistent_save_failure_returns_incomplete` |
| 动作由模型选，不是固定分支 | 主循环只负责「把模型的工具调用交给工具、把结果交回去」，不认识任何业务规则 | 10 组运行跑出 7 种不同的工具序列；[run-02](examples/run-02-conflict) 里模型自己跳过了空闲的 19:00 |
| 执行有上限 | `max_steps` 与 `max_tool_calls` 双预算，任一触顶立即返回 `step_limit` 并说明原因 | [run-09](examples/run-09-step-limit)、`test_step_limit_is_enforced_for_looping_model` |
| 结果可逐步骤复核 | 每步记录工具名、参数、返回摘要、下一步动作和模型自己写的 40 字决策依据 | [run-02/trace.json](examples/run-02-conflict/trace.json) |
| 不落模型内部思维 | `reasoning_content` 只截取首句作摘要，完整过程不写盘 | `src/agent.py` 的 `_decision_hint()` |
| 离线也能跑通 | `--provider mock` 是写死策略，34 个测试全部离线执行、无网络依赖 | README 快速开始第 2 步；[examples/pytest-output.txt](examples/pytest-output.txt) |
| 不含真实密钥 | `.env` 在 `.gitignore` 中，仓库内只有 `.env.example` | `git ls-files` 里没有任何 `.env` |

前四条是「审计型约束」——即使模型不配合也绕不过去，因为拦在工具层和 schema 层，不在提示词里。

---

## 已知限制

- **批量是串行的**：一批素材按顺序处理，不并发；批次越大，单条的处理步数越容易被预算压缩。
- **草稿之间不互相占位**：`save_draft` 只写草稿，不会把时段写进 `bookings`，所以同账号的两条素材
  在同一批里理论上可能拿到同一个建议时段（见 [docs/03-evaluation.md](docs/03-evaluation.md) 3.4）。
- **规则引擎是简化版**：只有类目、间隔、日上限、维护窗口、硬性要求五条，没有节假日/时区。
- **草稿存储是 JSON 文件**：每次运行独立目录，没有并发与事务。
- **Mock 只证明流程**：`--provider mock` 是写死的策略，不代表真实模型的决策能力。
- **没有真实发布链路**：这是设计约束（仅保存草稿），不是缺失。

---

## 文档

- [01-requirements.md](docs/01-requirements.md) — 用户、目标、范围、成功标准
- [02-solution.md](docs/02-solution.md) — 调用流程、动态决策、技术取舍
- [03-evaluation.md](docs/03-evaluation.md) — 测试预期、实际结果、失败与限制
- [04-ai-usage.md](docs/04-ai-usage.md) — AI 辅助范围、本人修改与验证
- [05-retrospective.md](docs/05-retrospective.md) — 最难的问题、学习收获、下一步
