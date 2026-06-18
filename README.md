# Exam-Master-Skill

期末划重点神器：从课件 PDF 生成结构化复习笔记 + 模拟考题 + 速查表 + Anki 闪卡。

## 安装

```bash
pip install -r requirements.txt
brew install pango cairo gdk-pixbuf  # macOS weasyprint 依赖
```

## 使用

```bash
# 单文件
python app.py 课件.pdf

# 多文件 / 整个目录
python app.py ch1.pdf ch2.pdf ch3.pdf
python app.py ~/课件目录/

# 指定学科 + 输出名 + 全部可选输出
python app.py 课件.pdf \
  -o 操作系统复习 \
  --discipline stem \
  --cheat-sheet \
  --anki \
  --keep-md

# 跳过缓存（强制重新处理）
python app.py 课件.pdf --no-cache
```

## 参数

| 参数 | 说明 |
|---|---|
| `pdf` | PDF 文件路径（支持多个/glob/目录） |
| `-o, --output` | 输出文件名前缀（默认：复习笔记） |
| `--discipline` | 学科：`stem` `liberal-arts` `medical` `business` `general` |
| `--cheat-sheet` | 生成一页考前速查表 |
| `--anki` | 导出 Anki 闪卡 CSV |
| `--keep-md` | 保留 Markdown 源文件 |
| `--no-mock` | 跳过模拟考题 |
| `--no-blind-spot` | 跳过盲区回填 |
| `--no-cache` | 跳过缓存 |

## 学科模板

| 参数 | 输出侧重 |
|---|---|
| `stem` | 公式推导链、定理边界条件、典型题型 |
| `liberal-arts` | 时间轴、因果链、学派对比表 |
| `medical` | 机制→症状→诊断→治疗四维 |
| `business` | 模型框架、适用场景、局限性 |
| `general` | 考点矩阵，自动适应 |

## 功能矩阵

| 功能 | 说明 |
|---|---|
| TextRank 预摘要 | 本地抽取 35% 关键句，AI 在精选原料上加工 |
| 字号层级识别 | 两遍扫描，自动检测 h1/h2/h3 标题 |
| 红色文字捕捉 | 亮红/暗红/橙红/粉红四档 RGB 阈值 |
| 表格提取 | PyMuPDF 自动识别并转 Markdown 表格 |
| 图片上下文 | 基于坐标提取插图周围文本块 |
| TF-IDF 关键词 | 本地提取高频词，引导 AI 覆盖核心概念 |
| 数学公式保留 | 检测 LaTeX/数学符号区域 |
| 盲区回填 | 出题→反查→补充笔记缺漏（闭环） |
| 缓存断点续传 | PDF 指纹 + 配置哈希，阶段级缓存 |
| 多文件批处理 | 支持多 PDF / glob / 目录输入 |
| 多格式输出 | PDF + Markdown 源文件 + Anki CSV |
| 考前速查表 | 浓缩一页 A4 |
| 视觉模型（可选） | 配置后自动分析插图 |

## 输出

| 文件 | 内容 |
|---|---|
| `复习笔记.pdf` | 考点矩阵、红字边框、表格、盲区补充 |
| `复习笔记.md` | Markdown 源文件（需 `--keep-md`） |
| `复习笔记-模拟考题.pdf` | 3 道高难度考题 + 解析 |
| `复习笔记-速查表.pdf` | 一页 A4 考前速查（需 `--cheat-sheet`） |
| `复习笔记-anki.csv` | Anki 可导入闪卡（需 `--anki`） |

## 配置

```json
{
  "DEEPSEEK_API_KEY": "sk-xxxx",
  "API_URL": "https://api.deepseek.com/v1",
  "VISION_API_KEY": "sk-xxxx (可选)",
  "VISION_API_URL": "https://api.openai.com/v1/chat/completions (可选)",
  "VISION_MODEL": "gpt-4o (可选)"
}
```

## 工作流

```
多PDF合并 → 字号层级 → 红字/加粗标记 → 表格提取 → 图片坐标上下文
  → TextRank 关键句抽取 → TF-IDF 关键词 → DeepSeek 学科模板生成笔记
  → DeepSeek-R1 出题 → 盲区对比回填 → weasyprint PDF 导出
  → (可选) 视觉模型插图分析 → (可选) 速查表 → (可选) Anki 闪卡
```
