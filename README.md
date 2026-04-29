# 面向鸿蒙ArkTS的代码异味检测与代码重构评测集

## 包含测试用例的仓库路径

[agc-template-market-harmonyos-demos](https://github.com/buaa-icf/agc-template-market-harmonyos-demos)

[ostest_integration_test](https://github.com/buaa-icf/ostest_integration_test)

[openharmony_tpc_samples](https://github.com/buaa-icf/openharmony_tpc_samples)

[cases](https://github.com/buaa-icf/cases)

[model-evaluation-testsuite](https://github.com/buaa-icf/model-evaluation-testsuite)

[applications_photos](https://github.com/buaa-icf/applications_photos)

[applications_settings](https://github.com/buaa-icf/applications_settings)

## 数据集说明

目前正在检查同学们生成的测试用例，已检查完毕的条目在仓库 [arkts-code-smell](https://github.com/buaa-icf/arkts-code-smell) 的 `dataset/instrument-test` 与 `dataset/local-test` 文件下来。

临时测试覆盖率统计见 `dataset/division-data-clumps` 与 `dataset/division` 下的 csv 文件。

检查完毕的总数见 `dataset/count.txt`。

## JSON 字段说明

评测集 JSON 文件主要分为两大类：通用 linter 输出（Long Method / Feature Envy / Switch Statement / Code Clone 等）和 Data Clumps 专用格式。

### 1. 通用 linter JSON（顶层为对象数组）

每个数组元素代表一个被检测出问题的源文件，结构如下：

| 字段                  | 类型   | 含义                                                         |
| --------------------- | ------ | ------------------------------------------------------------ |
| `filePath`            | string | 被检测源文件的绝对路径，作为该条记录下所有 `messages[]` 的归属文件；若 `messages[].message` 中出现 `is similar to <file>:<start>-<end>`，则相似片段以该路径为准。 |
| `messages`            | array  | 该文件下命中的所有问题列表，单条记录可有多条 message，每条都需作为独立的评测目标处理。 |
| `messages[].line`     | number | 问题起始行号（从 1 开始）。在 Long Method / Feature Envy 中表示目标方法声明所在行；在 Code Clone 中表示原片段（左侧）起始行。 |
| `messages[].column`   | number | 问题起始列号（从 0/1 开始，依规则而定），用于在 IDE 中精确定位。 |
| `messages[].severity` | string | 问题级别，常见取值 `SUGGESTION`、`WARN`、`ERROR`，目前评测集多为 `SUGGESTION`。 |
| `messages[].message`  | string | 人类可读的问题描述。Code Clone 规则中会以 `A:start-end is similar to /abs/path/B.ets:start-end` 形式给出原片段与相似片段的范围，需双向解析为两条评测目标（`fragment_role` 分别为 `original` 与 `similar`）。 |
| `messages[].rule`     | string | 触发该问题的规则名，例如 `@extrulesproject/long-method-check`、`@extrulesproject/feature-envy-check`、`@extrulesproject/switch-statement-check`、`@extrulesproject/code-clone-fragment-check`。CSV 中的 `rule` 列与之对应。 |

> 嵌套展开规则：在解析 `messages[]` 时必须把父级的 `filePath` 合并进来，否则只剩 `line`/`column`/`message`/`rule` 时无法定位文件，会导致后续覆盖率统计失败。

### 2. Data Clumps JSON（`cleanarch-json/DataClumps/*.json`）

每个数组元素代表一处 Data Clumps（数据泥团）目标函数，结构如下：

| 字段         | 类型     | 含义                                                         |
| ------------ | -------- | ------------------------------------------------------------ |
| `id`         | number   | 该目标函数在原始 Data Clumps 数据集中的全局唯一编号，可与 `HSP` / `pathList` 中引用的 id 相互对应。 |
| `path`       | string   | 目标函数的限定名，格式形如 `<Pkg 模块名>.<File 相对源码路径>.类名.方法名`（含 Windows 风格反斜杠），用于唯一定位被检测的方法。 |
| `row`        | number   | 目标方法声明所在行号。CSV 导出时即作为 `range_start` 的初始锚点，再结合覆盖率函数区间解析出 `range_end`。 |
| `rol`        | number   | 目标方法声明所在列号（role/column），辅助定位重载或同名方法。 |
| `NOPAR`      | number   | Number Of PARameters，目标方法的参数个数；Data Clumps 衡量"参数泥团"严重度的关键指标。 |
| `HSP`        | number[] | High Similarity Peers，与本目标在参数列表上高度相似的同类函数 id 列表，用于追溯所有需要一并修复或一并测试的函数。 |
| `pathList`   | string[] | 与 `HSP` 一一对应的限定名列表，列出所有相似函数的 `path`，便于直接跳转到这些同类目标。 |
| `sourceFile` | string   | 该条记录在原始 cleanarch 数据集中的来源 JSON 路径，用于回溯原始检测文件。 |

> Data Clumps 评测时：`row` 用作目标函数声明行；测试运行结束后，再从匹配到的覆盖率函数区间补齐 `range_start` / `range_end`，写入 CSV。

## `*_coverage.csv` 列说明

所有 `*_coverage.csv` 严格遵守 27 列固定 schema（顺序不可调整），用于记录每个评测目标的测试归属、运行状态及方法级覆盖率：

| #    | 列名                                 | 含义                                                         |
| ---- | ------------------------------------ | ------------------------------------------------------------ |
| 1    | `record_index`                       | JSON 顶层数组中的记录序号（从 1 开始），唯一标识一条源文件级记录。 |
| 2    | `message_index`                      | 该记录内 `messages[]` 数组的序号（从 1 开始）；同一文件存在多条问题时用于区分。 |
| 3    | `fragment_role`                      | 片段角色：`original` 表示原片段，`similar` 表示 Code Clone 中的相似片段；其它规则统一为 `original`。 |
| 4    | `rule`                               | 触发的检测规则名，与 JSON 中 `messages[].rule` 一致。        |
| 5    | `source_file`                        | 评测目标所在源码文件的绝对路径。Code Clone 的 `similar` 角色行使用相似片段所在文件。 |
| 6    | `range_start`                        | 目标方法/片段起始行号（含）。                                |
| 7    | `range_end`                          | 目标方法/片段结束行号（含）。                                |
| 8    | `has_test_case`                      | 是否存在同模块测试用例：`yes`/`no`。仅当 `List.test.ets` 真正 import 或调用了对应 suite 才算 `yes`。 |
| 9    | `test_files`                         | 与该目标相关的测试文件绝对路径列表，多个路径以 `;` 分隔（通常包含 `List.test.ets` 与具体测试文件）。 |
| 10   | `test_kind`                          | 测试类型：`local-test`（`src/test`，纯逻辑单测）或 `instrument-test`（`src/ohosTest`，带设备的 UI 测试）。 |
| 11   | `test_suite`                         | 实际运行的 `describe(...)` 套件名，对应 `--scope` 参数。     |
| 12   | `test_command`                       | 真实执行的 hvigor 命令（含必要环境变量），原样写入，便于复现。 |
| 13   | `test_run_status`                    | 运行状态。常见取值：`passed`、`build_failed_missing_dependencies`、`failed_beforeAll_missing_host_anchor`、`failed_no_device`、`not_run_no_test`；`failed_missing_signed_hap` 类目标会被直接丢弃，不出现在 CSV 中。 |
| 14   | `test_result_file`                   | `test_result.txt` 的绝对路径（Local Test 在 `.test/default/intermediates/test/coverage_data/` 下，Instrument Test 在 `ohosTest/coverage_data/` 下）。 |
| 15   | `coverage_report`                    | 本次使用的 `coverageReport.json` 绝对路径。                  |
| 16   | `coverage_report_current`            | 是否为本轮真实生成的覆盖率报告：`yes_<日期>_<时间>` 表示已校验 mtime，`no` 表示沿用旧产物。 |
| 17   | `line_total_in_range`                | 目标行范围 `[range_start, range_end]` 内的可执行行总数（忽略 `executedLineCount` 缺失或为负的行）。 |
| 18   | `line_covered_in_range`              | 上述行范围内被覆盖（`executedLineCount > 0`）的行数。        |
| 19   | `line_coverage_pct`                  | 行覆盖率 = `line_covered_in_range / line_total_in_range × 100%`，保留两位小数。 |
| 20   | `branch_side_total_in_range`         | 范围内分支边总数：每个分支贡献 true 与 false 两条边。        |
| 21   | `branch_side_covered_in_range`       | 范围内 `count > 0` 的分支边数。                              |
| 22   | `branch_coverage_pct`                | 分支覆盖率；当范围内不存在任何分支边时输出 `N/A`，否则保留两位小数。 |
| 23   | `function_total_overlapping_range`   | 与目标范围相交的覆盖率函数区间数量。                         |
| 24   | `function_covered_overlapping_range` | 上述函数中被实际调用（`count > 0`）的数量。                  |
| 25   | `function_coverage_pct`              | 函数覆盖率，保留两位小数。                                   |
| 26   | `overlapping_coverage_functions`     | 与范围相交的所有函数名列表，以 `;` 分隔（常见匿名函数会标记为 `anonymous_<行号>`）。 |
| 27   | `note`                               | 备注列：记录补丁应用情况、复跑信息、限制说明等，例如 `rerun passed: 5/5 local tests`、`hypium patch applied`、`device offline` 等。 |

> 计数规则要点：
>
> - 行覆盖率仅计可执行行，跳过 `executedLineCount` 缺失或为负的条目；
> - 分支覆盖率以"分支边"为单位（true/false 两条边各自独立判定）；
> - 函数覆盖率以与目标行范围相交的函数区间数为分母，避免把整文件函数都算进来；
> - `failed_missing_signed_hap`（签名缺失）类记录不写入 CSV，会在最终报告中以"skipped (missing signed HAP)"清单单独列出，待用户配置本地签名后重跑即可补齐。

## `merged_coverage_summary.json` 字段说明

`dataset/merged_coverage_summary.json` 是对 `merged_coverage_all.csv` 的聚合摘要，记录合并后的总条目数及按测试类型 / 规则切分的方法级覆盖率均值，便于快速查阅整体进度。顶层结构如下：

| 字段           | 类型   | 含义                                                         |
| -------------- | ------ | ------------------------------------------------------------ |
| `merged_csv`   | string | 合并后 CSV 的相对路径（相对仓库根目录），与 `merged_count` 行数一致。 |
| `merged_count` | number | 去重合并后的最终评测目标数（CSV 实际行数，不含表头）。       |
| `raw_count`    | number | 合并前所有 per-source CSV 行数之和，用于核对去重前的原始规模。 |
| `deduped`      | number | 合并过程中被判定为重复并丢弃的条目数 = `raw_count - merged_count`。 |
| `overall`      | object | 全量评测目标的聚合统计，结构见下方"统计对象字段"。           |
| `by_kind`      | object | 以 `test_kind`（`local-test` / `instrument-test`）为键，分别给出每种测试类型的统计对象。 |
| `by_rule`      | object | 以 `rule`（如 `@extrulesproject/long-method-check`、`Data Clumps` 等）为键，分别给出每条规则的统计对象。 |

### 统计对象字段（`overall` / `by_kind.*` / `by_rule.*` 通用）

| 字段          | 类型   | 含义                                                         |
| ------------- | ------ | ------------------------------------------------------------ |
| `label`       | string | 该分组的标签名：`overall` 表示全量；`by_kind` 下取测试类型名；`by_rule` 下取规则名。 |
| `total`       | number | 该分组下的评测目标总数（即 CSV 行数）。                      |
| `withTest`    | number | `has_test_case == "yes"` 的目标数。                          |
| `withoutTest` | number | `has_test_case == "no"` 的目标数，等于 `total - withTest`。  |
| `failed`      | number | `test_run_status` 非 `passed` 的目标数（含构建失败、缺设备、缺锚点等）。 |
| `lineAvg`     | string | 行覆盖率均值（百分数，保留两位小数），仅在 `line_coverage_pct` 非空的样本上求平均。 |
| `branchAvg`   | string | 分支覆盖率均值（百分数，保留两位小数），仅在 `branch_coverage_pct` 非 `N/A` 的样本上求平均。 |
| `funcAvg`     | string | 函数覆盖率均值（百分数，保留两位小数），仅在 `function_coverage_pct` 非空的样本上求平均。 |
| `nLine`       | number | 参与 `lineAvg` 计算的样本数（含有效行覆盖率的目标数）。      |
| `nBranch`     | number | 参与 `branchAvg` 计算的样本数（范围内存在分支边的目标数）。  |
| `nFunc`       | number | 参与 `funcAvg` 计算的样本数（含有效函数覆盖率的目标数）。    |

> 计算约定：
>
> - 三个 `*Avg` 都是算术平均，分母即对应的 `n*`，遇到 `N/A` 或缺失值会跳过而不计入；
> - `by_kind` / `by_rule` 中各分组的 `total` 之和应等于 `overall.total`，可用于交叉校验去重和分类是否正确；
> - 当某规则在范围内不存在分支边（如部分 Data Clumps 目标）时，`nBranch` 会显著小于 `total`，此时 `branchAvg` 仅反映有分支样本的子集表现。
