# 资料与现有计算约定

## 修订快照

schema_version为整数1，metadata为业务方案字典，collections为业务集合字典。每条记录有非空字符串id和正整数revision。
同集合相同id取最高revision；同id同revision内容不同即冲突，即便存在更高修订也拒绝。deleted=true为撤回，之后更高修订可恢复。
撤回记录不要求携带业务字段。输入顺序与相同记录重试不改变语义；非有限数字拒绝。
Snapshot.rows返回独立副本；input_digest绑定有效修订和全部metadata。输出data_version沿用metadata，不由程序自造资料版本。
既有摘要入口为app.service.inspect(payload)，输出schema_version、input_digest、data_version和summary。

## 业务资料

cases 含 memberships、outcome、weight，隶属度在[0,1]或为null。conditions 定义条件顺序，calibration 是外部校准版本。二值配置按严格大于0.5记1，小于0.5记0，恰好0.5或缺失的案例不入二值表，仍保留在资料中。baseline.analyze 仅整理有效配置与案例键，不计算模糊集合关系或逻辑解。集合交用min，集合并用max，否定用1-x；缺测案例的处理范围应随结果公开。

各集合必要字段：{"cases": ["memberships", "outcome", "weight"]}。

## 当前能力

app.baseline.analyze(snapshot)产生已有摘要，app.service.inspect负责绑定输入身份；新增分析可以增设独立入口，已有接口及上述数据语义保持兼容。
