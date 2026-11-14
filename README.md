# 乡村运营条件组态的多解最小化与反例追溯

本项目提供模糊隶属度和双向集合关系相关资料的现有业务摘要，以及可反复调整口径的模糊集组态分析（探索性）。

app/catalog.py 按集合和记录键解析修订，拒绝同修订冲突；app/baseline.py 承载当前业务计算；app/service.py 输出摘要及输入摘要哈希。app/fsqca.py 与 app/fsqca_service.py 提供真值表、必要性、三解族多解最小化、反例追溯与版本对照，详见 [组态分析说明](docs/fsqca.md)。
数据字段及现有接口见 [资料契约](docs/data-contract.md)。fixtures/ 为合成小数据，不对应真实村庄或个人。
运行环境为 Python 3.14，现有模块仅使用标准库。

读取并计算当前摘要：

    python -m app.service fixtures/sample.json

模糊集组态分析（正向/否定/版本对照）：

    python -m app.fsqca_service analyze fixtures/fsqca_sample.json
    python -m app.fsqca_service analyze fixtures/fsqca_sample.json --both
    python -m app.fsqca_service diff 结果A.json 结果B.json

运行当前模块检查：

    python -m unittest discover -s tests -v

检查源码编译：

    python -m compileall -q app tests
