# colorcheck-pipeline

把一张含 24 色卡（ColorChecker）的照片，校正成"照标准色值"的图像，并给出逐色块的 CIEDE2000 (ΔE00) 报告。

只依赖 `numpy` + `Pillow`（交互点选额外需要 `tkinter`），单文件实现，无 OpenCV / scipy。

## 快速开始

```bash
# 1) 交互点选 4 个角块中心（TL, TR, BL, BR）—— 每张照片只需做一次
python colorcheck_pipeline.py photo.jpg --markers
#    生成 photo_marked.png（带绿色十字）和 photo.hint.json（角点坐标）

# 2) 跑校正（自动复用 hint 文件，无需交互）
python colorcheck_pipeline.py photo.jpg --report

# 也可以直接给角点：像素坐标或 0~1 归一化坐标
python colorcheck_pipeline.py photo.jpg --hint 107,438 247,536 33,530 178,628
python colorcheck_pipeline.py photo.jpg --hint 0.17,0.51 0.39,0.63 0.05,0.62 0.28,0.74
```

| 开关 | 作用 |
|---|---|
| `--out DIR` | 输出目录（默认 `out`） |
| `--report` | 额外出前后对比 + 逐块 ΔE00 卡片图 |
| `--model NAME` | 强制校正模型：`none` / `linear gain` / `linear affine` / `3x3 matrix` / `quadratic` |
| `--auto` | 尽力自动找色卡（只对干净影棚图可靠，务必看 `_grid.png` 确认对齐） |
| `--rotate-180` | 先把图片转 180°（色卡在照片里是倒的时候用） |
| `--hint-file F` | 指定 hint json 路径 |

输出（`<name>` = 输入文件名）：

```
<name>_corrected.png / .webp   校正后的整幅图像
<name>_grid.png                网格叠加 + 每块校正 ΔE00（先看这张确认对齐）
<name>_report.png              左原图 / 中校正 / 右逐块数据表（--report）
<name>_results.json            完整数据：模型、参数、逐块 RGB/参考/ΔE00、模型比较、reliable 标记
<name>_patches.csv             24 行表格，便于 Excel 查看
```

## 流水线做什么

1. **定位网格**：hint 角点 → 绿色 marker → （可选）自动搜索；再用"色块内部方差最小"做坐标下降微调。
2. **取色**：把 6×4 网格投影到图像，采样每块中心区域；按标准 ColorChecker 布局一一对应。
3. **比色**：sRGB → 线性 → XYZ(D65) → Lab，算 CIEDE2000。
4. **拟合并选模型**：`none / 线性增益 / 线性仿射 / 3×3 矩阵+偏置 / 逐通道二次`，用**留一交叉验证**选误差最小的，同时用"图外色彩一致性"（对整个 sRGB 网格预测后的平均 ΔE00）排除会把画面其他饱和色搞坏的模型。
5. **应用到整幅图**并输出报告。

## 设计说明

**为什么必须给 4 个角点。** 自动找色卡也实现了，但实测证明在随手拍的照片上不可靠：光照不均、反光、手和背景物体都在干扰，"方差最小 / 颜色最像"的代价函数存在大量伪极小值——一个错位的网格能比正确网格得分更低（在本项目的测试图上，真值网格的排序落在 4 万余名）。因此 `--auto` 是可选路径，其结果会被检查：校正后平均 ΔE00 > 5 会打印 `WARNING`，并在 json 里写 `reliable: false`。四次点击即可彻底消除这类失败。

**为什么不做最近色匹配。** 早期版本按颜色把采样块匹配到参考色，结果在曝光/材质偏移下产生错配，反而把校正做坏（平均 ΔE00 从 8.57 变 10.27）。现在改为按几何位置对应标准布局，另外用灰阶亮度单独判断并自动翻转 180° 倒置的网格。

## 两个诚实的限制

单张照片里有两件事无法消除，脚本只报告、不掩盖：

* **光泽反光会抬高黑块**：黑块实测常常比参考值亮一截，靠曲线拉不回去；
* **材质同色异谱**：印刷色卡的色料与 ColorChecker 原版不同，在未知光源 + 相机色彩科学下，紫 / 红 / 深肤色等几块会残留几个 ΔE00。

`results.json` 里的 `chart_consistency` 就是"在最佳光度拟合之后"每个色块的真实残差，别把它当成色卡的出厂规格。

## License

MIT — 见 [LICENSE](LICENSE)。
