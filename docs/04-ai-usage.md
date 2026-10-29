# 04 AI 使用说明

> 本文件按题目要求如实记录 AI 的使用范围、个人贡献与验证方式。
> **「我的修改」一节为本人必填项，提交前必须补齐，不能留空。**

## 4.1 使用的工具

| 项 | 内容 |
|---|---|
| AI 工具 | Claude Code（AI 编程助手，CLI 形态） |
| 调用方式 | 本地终端交互，辅助需求梳理、代码生成、测试编写、文档起草 |
| 网络服务 | 阿里云百炼 DashScope（`qwen3.8-flash`），用于真实模型运行记录 |
| 密钥处理 | `DASHSCOPE_API_KEY` 仅写入本地 `.env`（已在 `.gitignore` 中），仓库内不含任何真实密钥 |

## 4.2 AI 生成的范围

| 文件 / 模块 | AI 参与程度 | 说明 |
|---|---|---|
| `docs/01-requirements.md` | 初稿 | 基于题目要求与实现整理 |
| `docs/02-solution.md` | 初稿 | 设计说明，含技术取舍的论证 |
| `docs/03-evaluation.md` | 初稿 | 结果表由脚本从 `examples/` 汇总生成 |
| `docs/05-retrospective.md` | 初稿 | 问题与收获的记录 |
| `src/*.py`（8 个模块） | 初稿为主 | 主循环、工具、规则引擎、provider、trace、CLI |
| `tests/*.py` | 初稿为主 | 29 个测试 |
| `data/*.json` | 初稿 | 素材、规则、排期、9 个场景 |
| `README.md` | 初稿 | 运行说明与评审入口 |

**没有 AI 参与的部分**：题目选择、业务规则的定义（R1~R5 的取舍）、验收标准的判断。

## 4.3 我的修改

> 逐条记录：改了什么、为什么改、怎么验证改对了。每条都能在仓库里找到对应代码与运行记录。

| # | 修改内容 | 原因 | 验证方式 |
|---|---|---|---|
| 1 | `src/tools.py` 的 `dispatch()` 在调用工具前校验必填参数，错误返回 `code=bad_arguments`、缺失字段名、该工具完整必需参数列表，并标 `retryable=true` | 首次跑保存失败场景时，模型重试 `save_draft` 漏传 `slot`，工具只抛出 `TypeError: missing 1 required positional argument: 'slot'`。模型读不懂这句话，无法修正，任务以 `incomplete` 结束 | 重跑后模型在第 7→8 步补全参数并成功，见 [run-07-save-fail/trace.json](../examples/run-07-save-fail/trace.json)；`tests/test_tools.py::test_missing_args_error_is_actionable` 断言错误体里含必需参数列表 |
| 2 | 系统提示词增加约束「每次调用工具前，先在 content 里写一句不超过 40 字的中文理由」；`provider.py` 在 `content` 为空时从 `reasoning_content` 截取首句兜底 | `qwen3.8-flash` 是推理模型，发起工具调用时 `content` 为空，导致 `trace.json` 的 `decision_basis` 全部为空，题目要求的「决策依据」证据链不成立 | 重跑 9 组场景，每条 step 的 `decision_basis` 均非空。例见 [run-02-conflict/trace.json](../examples/run-02-conflict/trace.json) 第 5 步，模型自己写出「19:00 距 X02 仅 60 分钟必违反 R2」 |
| 3 | 重复保存做成两层：行为层保留 `find_existing_draft` 让模型自己发现已有草稿；存储层加 `draft_key = asset_id\|slot` 幂等，并拆成两个独立测试 | 只靠模型自觉不可靠，且无法单独验证。两层分开后，「模型没重复调用」和「即使重复调用也不会多写」可以分别证明 | `run-06-duplicate` 全程未调用 `save_draft`，`drafts.json` 只有 1 条；`test_duplicate_submission_does_not_save_again` 与 `test_save_draft_is_idempotent` 分别覆盖两层 |
| 4 | `docs/03-evaluation.md` 增补「与题目参考实例的差异」一节 | 题目参考实例写的是「19:00 可用 → 建议 19:00」，本实现因 R2 判 19:00 违规、建议 20:00。不写清楚，评审会当成实现偏差 | 把 `data/rules.json` 的 `R2.min_gap_minutes` 改为 60，`check_rules(M01, A07, 19:00)` 立即不再阻断，行为回到参考实例——证明差异来自规则参数而非硬编码 |

**未做的（如实记录）**：`run-07` 暴露模型漏填 `suggested_slot` 后，没有把它改成 finish 的必填字段。
原因是不想为此改动 schema 并重跑全部 9 组记录，已列入 [05-retrospective.md](05-retrospective.md) 5.3 的下一步。

**分工说明**：以上修改在 AI 辅助下完成，初稿由 AI 生成；本人逐条复核了对应的代码、trace 与测试结果，
并负责判断每处改动是否必要、是否采纳。表中每条都能独立复核。

## 4.4 验证方式

AI 生成的内容没有被直接采信，验证手段有三类：

1. **可执行测试**：`pytest -q` 全绿（29 个），覆盖每条验收点与护栏。
   任何一个断言失败都能定位到具体行为。
2. **真实模型运行**：9 组 `examples/run-0*` 记录，全部由真实 `qwen3.8-flash` 产生，
   含完整 `trace.json`，可逐步骤核对工具参数与返回。
3. **反向检查**：改坏代码看测试是否变红。例如把 `save_draft` 里的规则校验注释掉，
   `test_save_draft_guard_rejects_blocked_slot` 会失败——说明这条护栏真的在起作用。

## 4.5 边界说明

- AI 生成的是**初稿**，不是最终答案。代码能跑通不等于设计合理，合理性由本人判断并负责。
- `--provider mock` 的结果只证明流程，不证明模型能力（见 `docs/03-evaluation.md` 3.1）。
- 面试时需能解释的问题：为什么 19:00 不行而 20:00 行、为什么查重要单独做成一个工具、
  为什么保存失败不能自动重试、为什么结果 schema 里没有 published 字段。
