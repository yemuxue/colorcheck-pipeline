# colorcheck-pipeline

色彩感知的退化探针：用一张物理色卡、七个退化版本和 504 次多模态调用，
测「模型看颜色」这件事在什么条件下会失真。

## 目录

```text
colorcheck-pipeline/
├── colorcheck_pipeline.py      光度校准流水线（代码入口，保持根目录）
├── experiments/colorcheck/     评测脚本、刺激图、504 条原始结果
├── 01_正式文档/                 方法文章（站点由此同步）
├── 02_核心图_PNG/               核心图（编号 + 图名）
├── 04_构建脚本/                 生成核心图的脚本
└── .github/workflows/          文档变更时触发站点重建
```

> 代码入口留在根目录，是为了不破坏 `import colorcheck_pipeline` 与既有用法；
> 作品集相关的文档、图、脚本按 5.6 的编号规范单独成目录。

## 核心图

| 编号 | 图名 | 回答的问题 |
|---|---|---|
| F01 | 实验设计 | 测了什么、怎么对照 |
| C01 | 三模型结果矩阵 | 退化掉多少、校正回补多少 |

生成：

```bash
python3 04_构建脚本/render_figures.py
```

## 实验

```bash
# 起点：七个退化版本 + 基准图
python3 experiments/colorcheck/probe.py --models kimi-k3,glm-5.3-flash,deepseek-v4.1-flash
# 归因：同图不同问法 / 同问法不同图
python3 experiments/colorcheck/experiment.py --mode ab
```

结果落在 `experiments/colorcheck/results/`，一条记录一次调用。

## 站点

`01_正式文档/colorcheck-method.md` 是 [方法文章](https://xsssxspace.com/notes/colorcheck-method/)
的唯一来源；推送后由 GitHub Actions 通知站点重建，约 3 分钟上线。
