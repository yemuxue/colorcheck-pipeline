# 色彩感知评测实验

用 24 色卡 + CIEDE2000 测量多模态模型在图像退化条件下的颜色判断能力。
配套作品页：<https://xsssxspace.com/works/colorcheck-probe/>

## 结论摘要

| 项 | 数字 |
|---|---|
| 调用次数 | 504（432 基线 + 72 对照） |
| 被测模型 | Kimi K3、GLM-5.3-Flash、DeepSeek V4.1 |
| 干净图最好成绩 | 95.8% |
| 强偏色后 | 62.5%（−29pp） |
| 只做输入校正 | +25pp，平均 ΔE 下降 62% |

## 目录

```
images/     7 张受控刺激图（6 种退化 + 1 张校正后）
results/    504 条 JSONL 记录 + 各模型汇总 JSON
names.py    24 色块标准中文名与参考 RGB
experiment.py   批量实验脚本（生成问题、调用模型、判分）
probe.py        单次连通性测试
```

## 复现步骤

```bash
pip install numpy pillow      # 生成刺激图需要
cp .env.example .env          # 填入端点、Key、模型名
python3 probe.py images/v_clean.png          # 先确认能读图
python3 experiment.py --model <model> --conditions all --patches all --effort low
```

`experiment.py` 会用 `images/v_<cond>.png` 作为输入，按 24 个色块逐个提问，
把回答解析成「色名 + hex」，再用 CIEDE2000 与参考值比对，结果写入 `results/`。

## 刺激图是怎么来的

由已知 sRGB 值合成 6×4 色卡，再施加退化：

| 条件 | 退化方式 |
|---|---|
| `clean` | 无 |
| `mild` | R×1.06、B×0.94 |
| `strong` | R×1.22、B×0.78 |
| `noise` | 偏色 + 高斯噪声 σ=4 |
| `dark` | 整体 ×0.55 + 偏色 |
| `jpeg` | 偏色 + JPEG quality 35 |
| `strong_fixed` | 用父仓库的 pipeline 校正后的 `strong` |

## 判分口径

- **准确率**：24 选 1，色名完全匹配
- **ΔE00**：模型给出的 hex 与参考色的 CIEDE2000 色差

## 已知限制

- 刺激图是合成的，不是真实拍摄
- 24 选 1 存在词汇歧义（天蓝/蓝色、砖红/红色）
- 每个条件仅 24 条样本，单次运行，未做重复性验证
- 未锁定模型版本

## 许可

与父仓库一致（MIT）。
