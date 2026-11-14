# 模糊集组态分析（探索性）

`app.fsqca` / `app.fsqca_service` 在既有二值配置摘要之外，提供可由研究人员反复调整口径的
模糊集组态分析。定位为**探索性比较分析**：输出描述样本内的集合关系与可能并存的多条路径，
不构成已识别的因果效应。每次分析绑定 `input_digest`、`data_version` 与 `calibration`，
结果可留存并与后续版本对照。

    python -m app.fsqca_service analyze 资料.json [选项.json] [--negated|--both]
    python -m app.fsqca_service diff 结果A.json 结果B.json

## 数据口径

- 隶属度在 `[0,1]` 或为 `null`；`null` 即缺测，**不补 0**。缺测案例按列表删除，
  排除名单及原因随结果公开（`cases.excluded`）。
- 隶属度恰为 `0.5` 的案例不参与真值表角点归属（`truth_table.assignment_excluded`），
  但仍参与解与必要性的模糊集合指标；评估案例集规模见 `cases.evaluated`。
- 案例权重 `weight` 须为非负有限数，进入全部频数、一致性与覆盖计算；
  权重为 0 的角点按未观察处理。
- 集合运算：交取 min，并取 max，否定取 1-x（与资料契约一致）。

## 真值表

每个配置（角点）报告：加权频数 `n_weighted`、案例数、充分性一致性
`consistency = Σw·min(配置隶属度, 结果) / Σw·配置隶属度`、案例清单与四分类之一：

| 分类 | 含义 | 可否被最小化借用 |
|---|---|---|
| `outcome` | 达到频数与一致性门槛 | 进入结果行（ON） |
| `contradictory` | 已观察但未达一致性门槛（冲突） | **永不借用** |
| `remainder_below_frequency` | 有案例但低于频数门槛 | 按声明政策借用 |
| `remainder_unobserved` | 无加权案例 | 按声明政策借用 |

频数门槛 `frequency_cutoff`（默认 1.0）与一致性门槛 `consistency_cutoff`（默认 0.8）
由研究人员按口径设定；原始统计量全部随行输出，可自行重新划行。
冲突行与未观察行分开标记，且冲突行结构上不会进入任何解的借用集。

## 必要性

`necessity` 对每一条件的在场（1）与缺席（0）报告：一致性
`Σw·min(y,x)/Σw·y`、覆盖 `Σw·min(y,x)/Σw·x`、支持 `Σw·x`、案例数，
以及反例（结果隶属度超过条件隶属度的案例，按加权缺口降序，至多列 10 条并给出总数）。
`necessity_cutoff` 仅做标记（`passes_cutoff`），不做筛选。

## 充分性三解族（共享搜索）

复杂解与简约解各做一次 Quine-McCluskey 全质蕴涵项生成；中间解复用两者结果，不再搜索：

- **complex**：不借用任何逻辑余项。
- **parsimonious**：按声明政策 `remainder_policy` 借用余项——`'all'`（默认）或
  `{'include': [配置键...]}`；include 中含非余项配置会被拒绝。政策原文随结果公开。
- **intermediate**：候选项 = 方向预期允许的简约解质蕴涵项 ∪ 不借用反事实的复杂解质蕴涵项。
  方向预期 `directional_expectations: {"条件": 0或1}` 表示该条件的缺席/在场预期有助于结果；
  含有与预期相反文字的质蕴涵项不得进入中间解（即只允许"容易的"反事实）。
  未声明方向预期时中间解退化为简约解口径。

每个解项报告：文字集、一致性、原始覆盖、唯一覆盖、支持（`support_weighted`、
`n_cases_gt_half`）、反例（`deviating_cases`）、覆盖的原始配置
（`covers_configurations`）、支持案例（`cases`）与借用的余项
（`borrowed_remainders`，即该项依赖的反事实清单）。解级另报一致性与覆盖。
所有解都能展开回原始配置与案例。

## 多解最小化

最小标准为字典序 **（项数, 文字数）**：先比解的项数，再比全部项的文字总数。
枚举**全部**达到该标准的解，不报告单次贪心合并结果。全部质蕴涵项随结果公开
（`prime_implicants`）。跨全部最小解：

- `core_literals`：每个最小解都使用的文字（条件+方向）；
- `peripheral_literals`：仅在部分最小解出现的文字。

注意核心/边缘按"文字是否出现在解中"判定；项级结构不同而文字集相同的情形会如实呈现。

## 否定结果

`negated_outcome: true`（或 CLI `--negated` / `--both`）在 `1-y` 上完整重算真值表、
必要性与三解族，输出 `outcome_direction: 'absence'`。正向结论不可取反充当否定分析；
两个方向的结果不可互相 diff。

## 版本对照

`diff 结果A.json 结果B.json`（要求同方向、同条件口径）：

- `truth_table_changes`：配置分类迁移、一致性变化、案例进出；
- 每个解族：`disappeared_terms`（消失的路径）、`new_terms`（新增路径）、
  `persistent_terms`（持续路径及其支持案例增减、一致性/覆盖变化）；
- `options_changed`：两次口径差异；`same_input`：输入摘要是否一致。

路径身份＝项的文字集。校准方案或个案改动后，可据此区分"路径消失"与"仅支持案例变化"。

## 资源预算与已认证范围

`budget` 选项：`max_conditions`（默认 10）、`max_implicants`、`max_cover_nodes`、
`max_solutions`、`time_seconds`。条件数超限直接拒绝；最小化超预算时按阶段降级：

- 真值表与必要性总是完备（`certification`）；
- 质蕴涵项超预算 → 该解族 `status: 'unavailable'`，其余已认证范围保留；
- 覆盖枚举超预算 → `status: 'partial'`，已找到的最优解如实给出但不宣称完备，
  核心/边缘条件不给出。

## 选项一览

```json
{
  "conditions": ["组织", "空间"],            // 默认取 metadata.conditions 全部；子集与顺序即口径
  "frequency_cutoff": 1.0,
  "consistency_cutoff": 0.8,
  "necessity_cutoff": null,
  "directional_expectations": {"组织": 1},
  "remainder_policy": "all",
  "negated_outcome": false,
  "budget": {"max_conditions": 10, "max_implicants": 100000,
             "max_cover_nodes": 500000, "max_solutions": 2000, "time_seconds": 60}
}
```

未知选项会被拒绝（防止口径笔误）。解析后的完整选项随结果回显（`options`）。

## 局限

- 纯标准库实现，面向数十案例、十余条件以内的探索性比较；更大规模请提高预算并接受部分认证。
- 中间解的方向预期过滤在质蕴涵项层面执行（与主流 QCA 实现一致）。
- 结果为样本内集合关系描述，不构成已识别的因果效应；结论外推由研究人员负责。
