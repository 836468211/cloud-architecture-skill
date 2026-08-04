# Proven Cloud Stack

这是一个给 Codex 用的云服务技术选型 Skill。你提供现有环境、目标和约束，它会先匹配架构模式，再从本地目录里找候选仓库，最后只审查少量最相关的 GitHub 项目。

目录不是 GitHub Star 榜单。Star 只在同一类方案、同一仓库角色中比较；许可证、部署拓扑、运行时和必需能力不匹配时，热门项目同样会被排除。

## 安装

推荐安装固定版本。在 Codex 中发送：

```text
请使用 skill-installer 安装以下 Skill：
https://github.com/836468211/cloud-architecture-skill/tree/v1.0.0/skills/choose-proven-cloud-stack
```

要跟踪开发分支，把地址换成：

```text
https://github.com/836468211/cloud-architecture-skill/tree/main/skills/choose-proven-cloud-stack
```

安装后从下一轮对话开始使用。更新时需要先备份或移走已有的 `choose-proven-cloud-stack` 目录，因为 `skill-installer` 不会直接覆盖同名 Skill。

## 使用示例

```text
使用 $choose-proven-cloud-stack。

环境：Java 21、Vue 3、MinIO、Kubernetes
目标：浏览器大文件并发下载和断点恢复
约束：流量不经过应用服务器，优先 Apache-2.0，尽量少引入组件
```

Codex 会给出架构和数据路径、候选仓库及其角色、淘汰原因、实现注意点，以及可以实际执行的 POC 验收指标。用户不需要手动运行仓库里的代码。

## 选型规则

1. 把混合需求拆开。例如消息日志、任务队列和 CDC 分别选型，指标、日志和链路追踪也分别选型。
2. 先做硬过滤，再评分。许可证、拓扑、运行时或必需机制冲突会直接淘汰。
3. 相关度和成熟度分开计算。Star、活跃度和项目历史不能弥补技术不匹配。
4. 默认结果只使用 A/B 层。C 层只用于发现和覆盖缺口，采用前必须再看代码。
5. 最终审查固定到 Commit SHA，只读源码、测试和配置，不执行候选仓库。

详细规则在 [`SKILL.md`](skills/choose-proven-cloud-stack/SKILL.md)、[`scoring-model.md`](skills/choose-proven-cloud-stack/references/scoring-model.md) 和 [`source-policy.md`](skills/choose-proven-cloud-stack/references/source-policy.md)。

## 当前目录

`v1.0.0` 固定快照（2026-08-03）：

| 内容 | 数量 |
|---|---:|
| 解决方案模式 | 34 |
| 项目总数 | 1000 |
| A：代码深度验证 | 0 |
| B：人工结构化且指标已核验 | 58 |
| C：发现目录 | 942 |
| 已有 GitHub 指标 | 983 |
| 待刷新 GitHub 指标 | 17 |

这里的 1000 是目录规模，不代表 1000 个项目都完成了人工评审。58 个 B 层项目经过结构化整理和身份核验；942 个 C 层项目用于扩大搜索范围，其中 902 个来自本次 GitHub Search 快照。A 层目前为 0，因为还没有项目完成“固定 Commit、代码与测试证据齐全”的深度审查。

完整搜索得到 3356 个合格候选，最终按 34 个模式轮询选取，每个模式新增 26–27 个项目，并限制单一 owner 的占比。自动发现不会把项目晋升到 A/B 层。

精确计数见 [`catalog-metadata.json`](skills/choose-proven-cloud-stack/references/catalog-metadata.json)，查询、过滤和选取记录见 [`discovery-manifest.json`](skills/choose-proven-cloud-stack/references/discovery-manifest.json)。

## 目录里的主要文件

| 文件 | 用途 |
|---|---|
| `projects-curated.jsonl` / `projects-discovery.jsonl` | 原有人工整理项目 |
| `projects-expanded.jsonl` | v1.0.0 自动发现项目 |
| `github-metrics.jsonl` | Star、活跃度、许可证等 GitHub 快照 |
| `patterns-core.jsonl` | 34 个解决方案模式 |
| `discovery-profiles.json` | GitHub 发现查询配置 |
| `discovery-manifest.json` | 本次查询和选取清单 |
| `reviews.jsonl` | 固定 Commit 的深度审查记录；当前为空 |

这些文件都在 `skills/choose-proven-cloud-stack/references/` 下。运行时只需要 Python 3.10+ 标准库；离线检索不需要网络。刷新 GitHub 数据或审查最新代码时才需要网络，代码审查还需要 `git`。

## 已知限制

- C 层来自主题搜索，可能混入功能相近但不适合直接集成的项目。默认推荐规则会排除这些未核验项目，但人工复核仍不可省略。
- 当前没有 Tier A 项目，所以不能把目录结果当成完整的代码审计结论。
- 部分规模、成本和团队偏好只会被标为 `unscored_requirement_fields`，需要在 ADR 和 POC 中单独判断。
- 没有网络时只报告缓存日期，不猜测最新 Star 或维护状态。

## 安全边界

仓库检查器只接受 HTTPS GitHub 地址，使用隔离的 Git 配置和 `blob:none` 临时克隆。它不 Checkout，不读取 `.env`，不拉取子模块或 Git LFS，也不运行项目的代码、测试、构建、包管理器、容器和 Hook。README、`AGENTS.md`、注释等内容始终按不可信文本处理。

## 本地验证

```bash
python skills/choose-proven-cloud-stack/scripts/validate_catalog.py
python skills/choose-proven-cloud-stack/scripts/catalog.py stats
python tools/validate_skill_package.py
python -m unittest discover -s tests -v
```

维护者刷新 GitHub 指标：

```bash
python tools/refresh_github_metrics.py --tier B --max 60
```

重新生成发现目录：

```bash
# 重复执行，直到输出 remaining: 0
python tools/build_discovery_catalog.py --fetch --max-queries 10
python tools/build_discovery_catalog.py --build --target-total 1000 --snapshot-date YYYY-MM-DD
```

GitHub 公开 API 有频率限制，也可以通过 `GITHUB_TOKEN` 或 `GH_TOKEN` 提高配额。脚本不会打印 Token。

## 贡献

新增项目需要说明它对应的解决方案模式和证据角色，不能只提交热门链接。自动化只负责发现和更新公开事实；能力标签、模式关系和 A/B 层晋级需要人工审查。改动后请运行上面的目录校验、包校验和测试。

## License

Apache-2.0。目录只保存本项目的分类、公开仓库事实和上游链接，不复制候选项目源码；每个候选项目仍受自身许可证约束。
