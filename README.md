# Exam-Master-Skill: 期末通关级 PDF 解析与自动总结框架

**Exam-Master-Skill** 是一套专为大学生设计的深度学习辅助工具。它不只是简单的 PDF 转文本，而是通过**视觉特征解析 (Computer Vision)** + **NLP 关键句抽取** + **DeepSeek 逻辑推理**，将杂乱、无重点的课件 PDF 转化为精准的考试通关笔记。

---

## ⚡ 核心功能 (Why it works)

* **视觉重构**：自动解析 PDF 字号（识别标题层级）、字体颜色（自动提取标红重点）和粗体特征，通过预处理直接告知 AI 哪里是考点。
* **语义降噪**：内置课件废话过滤算法，剔除“如图所示”、“请大家看这里”等无效信息，最大化利用 AI 上下文窗口。
* **考点矩阵输出**：将知识点强制转化为易于记忆的【考点矩阵表】，涵盖【考频指数】、【命题陷阱】、【核心原理解析】。
* **闭环自测机制**：自动调用 DeepSeek-R1 生成模拟考题，并根据题库反向检测笔记盲区，实现知识点的“回填式补充”。
* **多维度交付**：一键生成精美排版 PDF（支持打印/iPad 背诵）、Markdown 源文件、Anki 闪卡 (.apkg/.csv) 以及考前一页纸速查表。

---

## 🛠️ 快速上手

### 1. 环境准备

确保已安装 Python 3.9+，并安装必要的依赖：

```bash
pip install -r requirements.txt
# macOS 用户请安装额外依赖
brew install pango cairo gdk-pixbuf

```

### 2. 配置 API

在项目根目录新建 `config.json`：

```json
{
  "DEEPSEEK_API_KEY": "你的API_KEY",
  "API_URL": "https://api.deepseek.com/v1"
}

```

### 3. 一键运行

处理单个文件或整个目录的课件：

```bash
# 生成基础复习笔记
python app.py lecture.pdf

# 开启全套通关模式：生成速查表 + Anki 闪卡 + 原生 PDF 导出
python app.py lecture.pdf -o 期末必过 --cheat-sheet --apkg --keep-md

```

---

## 🏗️ 工作流逻辑

1. **特征解析层**：提取 PDF 坐标、字号、颜色、矢量图形架构。
2. **内容提炼层**：通过 TextRank 抽取 35% 关键句 + TF-IDF 提取高频关键词，构建“高价值输入块”。
3. **推理引擎层**：基于学科模板 (STEM/医疗/文科/商科) 驱动 DeepSeek 进行结构化重构。
4. **闭环反馈层**：自动生成考题 -> 笔记盲区回填 -> 最终考点定稿。

---

## 📋 功能矩阵

| 模块 | 说明 |
| --- | --- |
| **视觉提取** | 红色重点识别、表格转 Markdown、矢量图结构分析 |
| **智能摘要** | TextRank 抽取 + TF-IDF 权重加持，AI 加工更精准 |
| **学科适配** | 支持 STEM、文科、医学、商科、通用五种模板 |
| **辅助输出** | 考前速查表 (A4)、Anki 闪卡 (.apkg)、盲区回填笔记 |

---

## 🤝 贡献与开源

本项目欢迎任何形式的优化贡献。如果你在使用中发现了特定的课件排版类型无法解析，欢迎提交 Issue。

* **开发者提示**：如果想让生成的 PDF 样式更符合你的审美，可以直接修改 `app.py` 中的 `export_to_pdf` 函数内的 CSS 样式定义。

---

*Inspired by the need to survive finals with minimal pain.*
