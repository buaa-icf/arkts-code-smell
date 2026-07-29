# Scripts

## add_sample_metadata.py

批量刷新数据集样本的提交哈希。脚本只执行以下两项工作：

1. 为所有样本 JSON 补充或刷新 `commitHash`。
2. 为所有覆盖率 CSV 补充或刷新 `commit_hash`。

正样本的 `commitHash`/`commit_hash` 根据样本源码及其覆盖率表中记录的测试文件计算，
因此会包含测试用例改动对应的最新提交。负样本没有覆盖率表，提交哈希仅根据样本源码计算。

脚本不会修改路径、测试命令、测试状态、覆盖率数据，也不会重建合并表或统计摘要；
不会添加、刷新或删除 `sourceProject`/`source_project`；
`merged_coverage_all.csv` 仅刷新已有行的 `commit_hash`。

从 `arkts-code-smell` 仓库根目录运行：

```bash
python scripts/add_sample_metadata.py
```

## repro_signed_tests.py

批量复现 `dataset/merged_coverage_all.csv` 中所有评测目标的测试，并在执行前把 `bundleName` + `signingConfigs` 注入到每个项目，消除"找不到
签名"这一类阻塞。

## 用法

修改 `repro_signed_tests.py` 的 `BUNDLE_NAME` 与 `SIGNING_CONFIGS`。

```bash
# 1. 干跑（看每条命令会怎么跑，不真执行）
python3 scripts/repro_signed_tests.py --dry-run

# 2. 跑全部（默认 140 行）
python3 scripts/repro_signed_tests.py

# 3. 只跑 local-test，限前 10 条
python3 scripts/repro_signed_tests.py --kind local-test --limit 10

# 4. 中断后继续
python3 scripts/repro_signed_tests.py --resume

# 5. 还原所有被改过的 app.json5 / build-profile.json5
python3 scripts/repro_signed_tests.py --restore
```

常用参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--kind` | 任意 | `local-test` 或 `instrument-test` 过滤 |
| `--limit N` | 0 | 取前 N 条（过滤后） |
| `--timeout SEC` | 1200 | 单条超时；超时记录为 `timeout` |
| `--resume` | off | 跳过 `--output` 中已有的 `(record_index,message_index,fragment_role)` |
| `--no-clone` | off | 跳过缺仓库自动 `git clone` |
| `--output` | `dataset/repro_results.csv` | 结果 CSV 路径 |
| `--restore` | off | 还原备份并退出，不跑测试 |

## 工作流程

1. **克隆缺失仓库**：读取 CSV 中 `source_file` 首段（如 `applications_settings`），
   不存在就 `git clone --depth 1 https://github.com/buaa-icf/<name>.git`。
2. **定位项目根**：从 `repos/<source_file>` 向上查找同时含
   `AppScope/app.json5` 与 `build-profile.json5` 的目录。
3. **签名补丁（每个项目仅一次）**：
   - 备份到 `/tmp/repro_signed_tests_backups/<sha1>/`
   - `app.json5`: `bundleName` → `com.atomicservice.6917603885746666480`
   - `build-profile.json5`: 整个 `signingConfigs` 数组替换为 MuseumTicket 的调试签名块
4. **规范化命令**：
   - 剥离 env-var 前缀（`DEVECO_SDK_HOME=... NODE_HOME=... /path/hvigorw.js`）
   - 强制 `-p scope=<suite>`（如缺失则取 `test_suite` 第一项），**避免运行整个模块**
   - 同时识别 `-p scope=` 和 `--scope` 两种风格
5. **执行**：`hvigorw <args>` 从项目根运行，env 包含 DevEco Studio 自带的
   `DEVECO_SDK_HOME` / `NODE_HOME` / `PATH`。
6. **落 CSV**：原 25 列 + 新增 8 列：`project_root, scope_used, patch_status,
   test_command_final, exit_code, duration_sec, status, log_path`。
   完整 stdout/stderr 落到 `dataset/repro_logs/<rec>_<msg>_<role>_<scope>.log`。

## 状态取值

- `passed` — 退出码 0
- `failed_compile` — 日志命中 `arkts compiler error`
- `failed_sign` — 日志命中 `profile is invalid` / `verify profile signature failed`
- `failed_no_device` — 日志命中 `no device` / `no connected device`
- `failed` — 其它非 0 退出
- `timeout` — 超出 `--timeout`
- `error_no_project_root` — 上溯不到 `AppScope/app.json5`
- `error_no_scope` — 命令缺 scope 且 `test_suite` 为空
- `error_unparseable_command` / `error_no_hvigorw` / `error_exec` — 其它前置失败

## 还原

如果改动需要回滚（例如要 git commit 原项目），运行：

```bash
python3 scripts/repro_signed_tests.py --restore
```

会按备份目录里的 `project_path.txt` 把所有被改过的 `app.json5` /
`build-profile.json5` 写回。
