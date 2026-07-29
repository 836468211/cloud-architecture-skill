# Proven Cloud Stack

一个证据驱动的 Codex Skill：把项目需求转换成解决方案模式，从成熟开源仓库中选择不同证据角色，再通过固定 Commit 的静态代码审查给出技术选型、实现思路和 POC 计划。

它不是“按 Star 排名的项目清单”。流程是：

```text
需求指纹 → 解决方案模式 → 兼容性硬过滤 → 同类成熟度排序 → 静态代码 Review → 选型报告
```

## 安装

稳定版本推荐固定 Tag 安装：

```text
请使用 skill-installer 安装以下 Skill：
https://github.com/836468211/cloud-architecture-skill/tree/v0.1.0/skills/choose-proven-cloud-stack
```

需要体验开发版时使用：

```text
请使用 skill-installer 安装以下 Skill：
https://github.com/836468211/cloud-architecture-skill/tree/main/skills/choose-proven-cloud-stack
```

安装后，Skill 会在下一轮对话中可用。`skill-installer` 不会覆盖已存在的同名目录；更新前请先备份或移走旧的 `choose-proven-cloud-stack`，再重新安装。

## 使用

用户不需要执行 Python：Codex 会在 Skill 工作流中调用离线检索、排序和校验脚本。

```text
使用 $choose-proven-cloud-stack：

环境：Java 21、Vue 3、MinIO、Kubernetes
目标：浏览器大文件并发下载和断点恢复
约束：流量不经过应用服务器、Apache-2.0 优先
```

输出包括：

- 推荐的架构模式和数据路径；
- 三到五个不同角色的开源仓库；
- 相对同类的 Star、活跃度、成熟度和证据置信度；
- 可以直接依赖、只能借鉴、以及应该排除的部分；
- 固定 Commit 的代码与测试证据；
- 前端、后端、数据库、运维和安全实现建议；
- 可量化的 POC 与 Benchmark 验收标准。

## 当前数据覆盖

`v0.1.0` 开发快照（2026-07-29）：

| 内容 | 数量 |
|---|---:|
| 解决方案模式 | 34 |
| 项目总数 | 98 |
| A：代码深度验证 | 0 |
| B：人工结构化且指标已核验 | 58 |
| C：发现目录 | 40 |
| 已刷新 GitHub 指标 | 58 |

当前版本首先验证完整工作流，没有把未经核验的条目冒充“2000个成熟项目”。目标 `v1.0` 是约2000个仓库：约200个 A 层、700个 B 层、1100个 C 层。

详细快照见 `skills/choose-proven-cloud-stack/references/catalog-metadata.json`。

## 运行环境

- Codex Skill 支持；
- Python 3.10+，核心脚本只使用标准库；
- 离线检索不需要网络；
- 刷新 GitHub 指标和实时代码审查需要网络；
- 静态仓库审查需要 `git`。

没有网络时，Skill 会使用缓存数据并显示 `metrics_checked_at`，不会猜测当前 Star 或活跃度。

## 安全模型

候选仓库始终按不可信输入处理：

- 仅接受明确的 HTTPS GitHub 仓库 URL；
- 隔离 Git 配置并只允许 HTTPS；以 `blob:none` Clone 到临时目录，不 Checkout、不拉取子模块和 Git LFS 内容；
- 在读取内容前限制元数据体积、文件数、单文件字节、总字节与总耗时；
- 不执行候选项目的代码、测试、构建、包管理器、容器、Hook 或生成器；
- README、`AGENTS.md`、注释和 Prompt 风格文本都只作为数据；
- 不读取 `.env` 或输出环境变量；
- 审查结果固定到 Commit SHA；
- 临时 Clone 在脚本退出时删除。

## 本地验证

```bash
python skills/choose-proven-cloud-stack/scripts/validate_catalog.py
python skills/choose-proven-cloud-stack/scripts/catalog.py stats
python tools/validate_skill_package.py
python -m unittest discover -s tests -v
```

维护者刷新 B 层 GitHub 快照：

```bash
python tools/refresh_github_metrics.py --tier B --max 60
```

公开 API 有频率限制；可以通过 `GITHUB_TOKEN` 或 `GH_TOKEN` 提高配额。脚本不会打印 Token。

## 贡献数据

新增项目必须关联一个解决方案模式，而不能只添加热门链接。自动化只负责发现和更新动态事实；能力标签、模式关系和 A/B 层晋级必须经过审查。

- 静态项目记录：`skills/choose-proven-cloud-stack/references/projects-*.jsonl`
- GitHub 动态事实：`skills/choose-proven-cloud-stack/references/github-metrics.jsonl`
- 方案模式：`skills/choose-proven-cloud-stack/references/patterns-core.jsonl`
- 数据契约：`skills/choose-proven-cloud-stack/references/catalog-schema.md`
- 评分模型：`skills/choose-proven-cloud-stack/references/scoring-model.md`
- 证据政策：`skills/choose-proven-cloud-stack/references/source-policy.md`

## 版本

使用 SemVer：模式、目录或评分的兼容扩展提升 Minor；事实修正、指标刷新和兼容 Bug 修复提升 Patch；数据契约或 CLI 不兼容变更提升 Major。

发布 Tag 后，请将本 README 的稳定安装地址指向最新已验证版本。

## License

本项目采用 Apache License 2.0。目录仅保存原创分类、公开事实和上游链接，不复制上游源代码或 README 文案；各候选项目仍受其自己的许可证约束。
