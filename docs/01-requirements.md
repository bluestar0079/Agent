# 01 需求分析

## 1.1 业务背景

内容团队每天要发布多条素材，排期环节需要人工核对三件事：素材本身是否合规、目标时段是否被占用、
是否和同账号已有排期挨得太近。核对靠经验和表格，容易漏；时段冲突时要么硬排、要么反复沟通改期。

本项目的目标不是把这个流程全自动化，而是**把判断做扎实、把需要人拍板的部分标清楚**，
让运营拿到结果后只需要做"确认"或"改一下"，而不是从头核对。

## 1.2 用户与目标

| 项 | 内容 |
|---|---|
| 主要用户 | 内容运营 / 排期同学，不写代码，通过命令行提交一条素材和发布要求 |
| 用户目标 | 知道这条素材能不能发、该发在几点、有没有需要自己拍板的事 |
| 系统目标 | 完成审核判定与时段安排，保存草稿，输出可核对的结果 |
| 使用频率 | 按天、按账号使用；单次处理一条素材 |

## 1.3 输入

| 输入 | 来源 | 说明 |
|---|---|---|
| 素材 ID | 用户在命令行/场景文件指定 | 如 `M01` |
| 素材元数据 | `data/assets.json` | 标题、账号、类目、硬性发布时间 |
| 补充信息 | `data/asset_details.json` | 主数据缺字段时，从"内容库"补查 |
| 审核规则 | `data/rules.json` | R1~R5，参数化，改数据即可改行为 |
| 已有排期 | `data/bookings.json` 或场景 seed | 账号维度的占用情况 |
| 用户要求 | 场景文件的 `user_request` | 自然语言，含发布时间要求 |

## 1.4 输出

每条素材输出一个结构化结果（`finish` 的载荷，见 `src/schemas.py`）：

| 字段 | 含义 |
|---|---|
| `review_status` | 审核结论：`no_risk` / `risk_blocked` / `risk_needs_review` / `unknown` |
| `risk_reasons` | 命中的规则编号与中文原因，必须来自 `check_rules` 返回 |
| `required_slot` | 用户原始硬性要求，**任何情况下都不改写** |
| `suggested_slot` | 建议时段 |
| `slot_changed` | 建议时段是否偏离了硬性要求 |
| `pending_confirmation` | 需要人拍板的事项（如时段变更） |
| `draft` | 草稿保存的真实结果：`saved` / `draft_id` / `status` |
| `task_status` | `completed` / `pending_confirmation` / `blocked` / `incomplete` / `step_limit` |

**输出中不存在"已发布"这一状态**，因为系统没有发布能力。

## 1.5 范围

### 做

- **一批素材的串行处理**：按用户给出的顺序逐条完成审核与排期，最后一次提交全部结果
- 单条素材的审核判定与时段安排
- 时段冲突时给出合规的替代时段，并标记待确认
- 素材字段缺失时的补查与降级处理
- 草稿防重复（行为层查重 + 存储层幂等）
- 保存失败的重试决策与如实上报
- 每一步的执行记录（步骤、工具参数、返回摘要、后续动作、决策依据）
- 步数与工具调用双重上限，触顶返回明确状态

### 不做

| 不做 | 原因 |
|---|---|
| 真实发布 | 题目明确要求"仅保存草稿，不接真实发布" |
| 多 Agent 协作、并发处理 | 题目说明非必需；批次内串行处理足够，并发会引入草稿竞争 |
| 前端界面 / 线上部署 | 题目说明非必需 |
| 素材**内容**本身的风险识别 | 只做元数据规则判定，不做图像/文本审核 |
| 时区、节假日日历 | 规则集简化为 5 条，日历能力留作下一步 |
| 账号鉴权与并发控制 | 单机命令行工具，不涉及多租户 |

## 1.6 硬性约束

1. **只存草稿，不发布**。这是题目要求和业务红线，实现上通过"不提供发布工具"来保证。
2. **不得擅自更改用户的硬性发布时间**。冲突时只能建议并标记待确认。
3. **失败不得虚报成功**。`draft.saved` 必须严格等于 `save_draft` 的真实返回。
4. **风险原因必须可追溯**。每条原因都要有 `rule_id`，不能是模型编的。
5. **必须有执行上限**。任何情况下不能无限循环。

## 1.7 成功标准

| # | 标准 | 如何验证 |
|---|---|---|
| S1 | 九类场景都能给出符合预期的结论 | `tests/test_agent_decision.py`，9 个用例 |
| S2 | 两条执行路径的工具序列不同，且差异源于工具返回 | `test_two_paths_use_different_tool_sequences` |
| S3 | 任何输出都不含"已发布"语义 | `test_no_publish_capability_exists`、`test_result_schema_cannot_express_published` |
| S4 | 保存失败时 `draft.saved` 为 false | `test_failure_is_never_reported_as_success` |
| S5 | 重复提交只产生一份草稿 | `test_duplicate_submission_does_not_save_again`、`test_save_draft_is_idempotent` |
| S6 | 达上限返回 `step_limit` 且带原因 | `test_step_limit_is_enforced_for_looping_model` |
| S7 | 真实模型能跑出上述行为 | `examples/run-01` ~ `run-09` |

## 1.8 验收点拆解

| 题目验收点 | 拆成什么可执行的行为 |
|---|---|
| 正常排期 | 时段空闲 → 校验通过 → 查重为空 → 存草稿 → `completed` |
| 素材信息缺失 | 主数据缺 `category` → 主动调用 `get_asset_detail` → 补到"医疗健康" → 命中 R1 → `blocked` |
| 规则风险 | 类目敏感 → R1 block → 不查时段、不存草稿 → `blocked` |
| 时段冲突 | 目标时段被占 → 候选时段违规 → 换时段 → 通过 → 存草稿并标待确认 |
| 硬性要求边界 | 要求 19:00 违规 → 建议 20:00 → `required_slot` 仍为 19:00 → `pending_confirmation` 非空 |
| 重复提交 | 已有草稿 → 不调 `save_draft` → `draft.status=existing_draft` |
| 保存失败 | 可重试 → 模型重试；不可重试 → `incomplete` 且 `saved=false` |
| 步数上限 | 压低 `max_steps` → `step_limit` + 原因 |
