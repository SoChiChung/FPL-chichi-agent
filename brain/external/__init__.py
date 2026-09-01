"""外部预测数据源适配层（FPL Joe 等）。

与 FPL 官方 API 完全解耦；外部源只提供概率/期望类字段，
通过内部统一结构（见 external/fpl_joe.py 的 normalize）向上传递。
决策层接入（ExternalSignalProvider 接口）在下一阶段实现。
"""
