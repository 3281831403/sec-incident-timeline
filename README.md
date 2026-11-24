几台机器的日志合并成事件时间线之后，因果顺序全乱了，根因被排在了结果后面：偏移估计取全部样本平均导致慢样本污染结果；校正时间对同一事件重复应用偏移导致时间被放大；拓扑排序忽略因果边只按时间排序；因果环不检测导致排序死循环；并列排序按插入顺序而不是来源与序号导致结果不稳定；因果标记中的自环未剔除导致事件依赖自身；缺口检测只看序号连续性导致跨来源边界不重置；时间缺失的事件被当成零时间导致排到最前；校验不检查序号重复导致重复事件被当成两条；批量合并复用偏移估计导致第二批沿用第一批的偏移。timeline/merge.py 是核心，tests/test_merge.py 15个红。要修的是：按上面的顺序把 10 个缺陷逐个修掉。

# sec-incident-timeline

多源安全事件时间线合并。每台机器使用自己的本地时钟，日志序号只在本来源内单调递增；事件还可能带有同来源的因果标记。实现需要先估计并去除钟差，再在因果约束下稳定地排序，同时报出序号缺口和数据问题。各批次必须完全独立。

## 运行方式

在仓库根目录执行：

    python -m pytest tests -q

初始状态预期：15 failed，无 collection error，全部失败类型都是 NotImplementedError。

自检输出尾部示例：

    15 failed in 0.xxs

不联网、不访问真实日志、不引入外部依赖。测试只使用内存里的 dict 和 int。

## 字段表

| 字段 | 取值 |
| --- | --- |
| 所属领域 | 安全 |
| 子方向 | 漏洞检测与攻防 |
| 任务类型 | Bugfix |
| 语言/框架 | Python 3, pytest |
| 环境可复现等级 | 无外部依赖 |

## 目录结构

    sec-incident-timeline/
      README.md
      pyproject.toml
      tests/conftest.py
      src/timeline/__init__.py
      src/timeline/merge.py
      tests/test_merge.py

采用 src 布局。src/timeline/merge.py 是唯一实现文件，恰好 8 个公开桩；tests/test_merge.py 是唯一测试入口。tests/conftest.py 只把仓库的 src 目录加入 sys.path，不包含业务逻辑。

## 数据契约

### 事件与解析

事件是 dict。原始事件字段为 id、source、local_time、seq、type、caused_by。id 是全局唯一字符串；source 是来源名；local_time 是本地时钟的整数毫秒；seq 是来源内序号；type 是事件类型；caused_by 是同一来源内先行事件 id 的列表。

parse_event 接收原始事件 dict，返回归一化事件 dict，键包括 id、source、local_time、seq、type、caused_by、corrected_time。corrected_time 初始为 None。缺少 local_time 或 seq 时对应值为 None。函数不修改输入；caused_by 必须是新列表。id 或 source 缺失时抛 ValueError。

### 钟差估计与校正

钟差样本是 dict，字段为 source、offset、rtt，offset 与 rtt 都是整数毫秒。offset 是本地时间到参考时间的修正量。

estimate_skew 按来源分组，每个来源只使用 rtt 最小的那一个样本，返回 source 到 offset 的 dict。相同最小 rtt 时取输入中先出现的样本；不得使用平均值或中位数。

correct_time 返回校正后的整数毫秒：local_time 加该来源的 offset，来源没有样本时 offset 为 0。若事件的 corrected_time 已非 None，直接返回它，绝不再加一次 offset。函数不修改 event，重复调用结果相同。local_time 与 corrected_time 都为 None 时返回 None。

### 因果边与拓扑排序

causal_edge 返回 (cause_id, effect_id) 元组列表。只有两端事件都存在、来源相同、且 cause_id 不等于 effect_id 的边才合法。自环、跨来源边、指向不存在事件的边全部丢弃。重复边去重，保持 effect 在输入中的顺序和 caused_by 中的顺序。

topo_order 返回事件 id 的列表。使用 Kahn 拓扑排序；每一步从当前入度为 0 的事件中选排序键最小者。排序键为 corrected_time、source、seq、id 的升序，corrected_time 为 None 的事件排在所有有时间的事件之后。因果边存在时，cause 必须排在 effect 之前，即使 effect 的校正时间更早。输入顺序不得影响结果。检测到因果环时抛 ValueError，消息以 causal cycle 开头，不得死循环。

### 缺口检测

detect_gap 接收事件序列和来源名序列，返回缺口列表。按 sources 的顺序逐个来源处理，绝不在来源之间拼接序号。每个来源取所有非 None 的 seq，形成去重集合；只在最小 seq 与最大 seq 之间寻找缺失整数，不推断第一个之前或最后一个之后。每个有缺口的来源返回一个 dict，形如 {"source": "web-01", "missing": [2, 3]}；没有缺口的来源不出现。结果只包含 source 与 missing 两个键。

### 校验

validate_timeline 返回字符串列表，按以下顺序报告：

1. corrected_time 为 None：missing_time:ID，按事件输入顺序，每条一次。
2. 同一 source 下 seq 相同且非 None：duplicate_seq:SOURCE:SEQ，每个重复键一次，按首次出现顺序。
3. 合法因果边中的环：causal_cycle:ID1,ID2，环内 id 排序后用逗号连接，每个环一次，排在最后。

重复事件不会被删除，也不会因为 seq 相同而被当成一条。校验只读取事件，不修改事件。

### 批量合并

batches 是 dict 序列，每项含 batch_id、sources、events、samples。policy 是 dict，当前必须支持 {"skew": "min_rtt"}；缺少 skew 键时按 min_rtt 处理，其他取值抛 ValueError。

timeline_batch 返回以 batch_id 为键的 dict。每个值含 batch_id、skew、events、order、gaps、issues。events 是解析并写回 corrected_time 的新事件列表，顺序与输入一致；order 是事件 id 列表；gaps 与 issues 分别是 detect_gap 与 validate_timeline 的结果。每个批次只使用自己的 samples 和 events，独立估计偏移、独立校正、独立排序与校验，不得复用上一批的偏移或中间结果。重复 batch_id 抛 ValueError。

## tests 约束

tests/test_merge.py 恰好包含 15 个测试，交付后不可修改。实现者只能补全 src/timeline/merge.py 中的 8 个桩，不得改测试、删测试、跳过测试、动态生成测试或放宽断言。不得联网、不得使用随机数、不得引入外部依赖。

## 15 个测试

10 个缺陷对应测试：

1. test_skew_uses_min_rtt_sample_not_mean
2. test_correct_time_applies_skew_once
3. test_topo_order_puts_cause_before_earlier_effect
4. test_causal_cycle_is_reported_and_topo_raises
5. test_tie_break_is_source_then_seq_not_input_order
6. test_causal_edge_drops_self_and_cross_source
7. test_gap_detection_resets_per_source
8. test_missing_time_sorts_last_not_zero
9. test_duplicate_sequence_is_reported_once
10. test_batch_does_not_reuse_first_skew

5 个正向与回归测试：

11. test_single_source_orders_by_corrected_time
12. test_causal_pair_order_cannot_reverse
13. test_tie_break_stable_by_source_and_seq
14. test_gap_is_reported
15. test_two_batches_match_individual_merges