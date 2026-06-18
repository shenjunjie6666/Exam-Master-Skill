import fitz  # PyMuPDF
import json
import re
import sys
import os
import csv
import io
import hashlib
import pickle
import glob
import requests
import markdown
import argparse
import math
from weasyprint import HTML
from collections import Counter

# ---- 可选依赖：jieba 中文分词 ----
try:
    import jieba
    HAS_JIEBA = True
except ImportError:
    HAS_JIEBA = False

# ---- 可选依赖：pytesseract OCR ----
try:
    import pytesseract
    from PIL import Image as PILImage
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

# ---- 可选依赖：genanki 原生 Anki 包 ----
try:
    import genanki
    HAS_GENANKI = True
except ImportError:
    HAS_GENANKI = False


# ============================================================
# 学科模板
# ============================================================
DISCIPLINE_PROMPTS = {
    "stem": """## Role: 理工科期末复习导师

## 你的任务
将课件转化为【公式推导链 + 定理边界条件 + 典型题型】三层笔记。

## Rules:
- 每个公式必须标注：适用条件、各符号含义、单位/量纲
- 定理必须拆为【条件→结论→证明要点→常见误用】
- <red> 标签内容 = 必考公式/必考定理
- 至少列出 3 个"看似正确实则错误的典型理解"
- 重复概念打 🔥高频必考

## 输出格式:
| 公式/定理 | 适用条件 | 考试题型 | 易错点 |
""",

    "liberal-arts": """## Role: 文科期末复习导师

## 你的任务
将课件转化为【时间轴 + 因果链 + 学派对比表】三层笔记。

## Rules:
- 每个事件/理论必须标注：时间、代表人物、核心主张、历史影响
- 对立学派/理论必须做对比表（维度：前提假设、方法论、结论、代表人物）
- <red> 标签内容 = 必考名词解释/必考论述题素材
- 至少列出 3 个"跨章节串联考点"（如 A 章理论如何影响 B 章事件）
- 重复概念打 🔥高频必考
""",

    "medical": """## Role: 医学期末复习导师

## 你的任务
将课件转化为【机制→症状→诊断→治疗】四维笔记。

## Rules:
- 每个疾病按：病因机制、典型/非典型表现、实验室检查、一线/二线治疗方案
- 鉴别诊断做对比表（相似病症的关键区分点）
- <red> 标签内容 = 必考诊断标准/必考用药
- 药名标注：通用名（商品名）、作用机制、核心副作用、禁忌症
- 重复概念打 🔥高频必考
""",

    "business": """## Role: 商科期末复习导师

## 你的任务
将课件转化为【模型框架 + 适用场景 + 经典案例 + 局限性】四层笔记。

## Rules:
- 每个模型/框架标注：提出者、核心要素、应用步骤、前提假设、学界批评
- 重要模型做对比表（如波特五力 vs SWOT 的适用场景差异）
- <red> 标签内容 = 必考模型/必考计算
- 至少列出 3 个"模型的边界条件"（什么情况下不适用）
- 重复概念打 🔥高频必考
""",

    "general": """## Role: 期末阅卷组长 & 课件精简大师

## Skills:
1. 视觉线索捕捉：识别 <red></red> 标签标记的红色重点、**加粗**内容
2. 图表意图解码：将复杂插图、流程图抽象为结构化逻辑步骤
3. 知识点原子化：拆解为【概念 -> 核心原理 -> 考试可能怎么考】

## Rules:
- 严禁废话，剔除口语过渡词
- 重复出现 2 次以上的概念打上 🔥高频必考 标签
- 保留原始课件中的核心专业术语
- 对 <red> 标签内容进行【加粗+核心考点】突出处理
""",
}


# ============================================================
# 红色判定 — 四档全覆盖
# ============================================================
def is_red_color(color_int):
    r = (color_int >> 16) & 0xFF
    g = (color_int >> 8) & 0xFF
    b = color_int & 0xFF
    if r == g == b:
        return False
    if r < 80:
        return False
    if r > 180 and g < 120 and b < 120:
        return True
    if r > 100 and g < 60 and b < 60 and r > g * 1.5 and r > b * 1.5:
        return True
    if r > 200 and g < 150 and b < 80 and r > g:
        return True
    if r > 200 and g < 180 and b > g and b > 120:
        return True
    return False


def is_bold(span):
    flags = span.get("flags", 0)
    font_name = span.get("font", "")
    return (flags & 2) != 0 or "Bold" in font_name or "Heavy" in font_name


# ============================================================
# 保守降噪
# ============================================================
NOISE_WORDS = [
    "如图所示", "请大家看", "欢迎同学",
    "请看屏幕", "注意听讲", "同学们好",
    "点击此处", "返回目录",
]

NOISE_PATTERNS = [
    re.compile(r"如图\s*\d*[\s\S]{0,4}所示"),
    re.compile(r"请\s*大家?\s*看\s*[屏幕投影]?"),
    re.compile(r"点击.*?按钮"),
    re.compile(r"P\.?\d+\s*$"),
]


def filter_noise(text):
    for noise in NOISE_WORDS:
        text = text.replace(noise, "")
    for pattern in NOISE_PATTERNS:
        text = pattern.sub("", text)
    return text.strip()


# ============================================================
# 字号层级
# ============================================================
def _collect_font_sizes(doc):
    sizes = []
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        sizes.append(span["size"])
    return sizes


def _build_size_thresholds(sizes):
    if not sizes:
        return 999, 999, 999
    max_size = max(sizes)
    sorted_sizes = sorted(set(sizes), reverse=True)
    n = len(sorted_sizes)

    def percentile(pct):
        idx = min(int(n * pct), n - 1)
        return sorted_sizes[idx]

    h1_th = percentile(0.15)
    h2_th = percentile(0.35)
    h3_th = percentile(0.60)
    if h1_th - h2_th < 1.0:
        h1_th = max_size * 0.85
    if h2_th - h3_th < 0.5:
        h2_th = max_size * 0.65
    return h1_th, h2_th, h3_th


def _span_to_heading_prefix(size, h1_th, h2_th, h3_th):
    if size >= h1_th:
        return "# "
    elif size >= h2_th:
        return "## "
    elif size >= h3_th:
        return "### "
    return ""


# ============================================================
# 图片上下文 — 去重 + 标题检测 + OCR（可选）
# ============================================================
# 图片去重：记录已见过的图片哈希
_SEEN_IMAGE_HASHES = set()


def _simple_image_hash(image_bytes):
    """简易感知哈希：对前 1024 字节采样"""
    if len(image_bytes) < 64:
        return hashlib.md5(image_bytes).hexdigest()
    # 分段采样
    step = max(1, len(image_bytes) // 64)
    sample = bytes(image_bytes[i] for i in range(0, len(image_bytes), step))
    return hashlib.md5(sample).hexdigest()


CAPTION_PATTERNS = [
    re.compile(r"图\s*[0-9零一二三四五六七八九十]+[\.\-\s:：]?\s*([^\n]{4,80})"),
    re.compile(r"Fig(?:ure)?\.?\s*[0-9]+[\.\-\s:：]?\s*([^\n]{4,80})", re.IGNORECASE),
    re.compile(r"表\s*[0-9零一二三四五六七八九十]+[\.\-\s:：]?\s*([^\n]{4,80})"),
]


def _extract_caption(text):
    """从文本中检测图表标题"""
    for pattern in CAPTION_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(0).strip()
    return ""


def _ocr_image(image_bytes):
    """OCR 识别图片中的文字（需 pytesseract）"""
    if not HAS_OCR:
        return ""
    try:
        img = PILImage.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        return text.strip()[:200]
    except Exception:
        return ""


def _extract_image_contexts_rich(page, page_num):
    """增强版图片上下文：去重 + 标题检测 + OCR + 坐标文本"""
    contexts = []
    try:
        images = page.get_images(full=True)
        text_blocks = page.get_text("blocks")
        page_text = page.get_text("text")

        for i, img in enumerate(images):
            xref = img[0]
            img_rects = page.get_image_rects(xref)
            if not img_rects:
                continue

            # ---- 去重 ----
            base_image = page.parent.extract_image(xref)
            img_bytes = base_image.get("image", b"") if base_image else b""
            img_hash = _simple_image_hash(img_bytes) if img_bytes else str(i)
            is_dup = img_hash in _SEEN_IMAGE_HASHES
            _SEEN_IMAGE_HASHES.add(img_hash)

            img_rect = img_rects[0]
            w, h = img_rect.width, img_rect.height

            # 跳过太小的图片（icon/logo）
            if w < 80 and h < 80:
                continue

            context_parts = []

            # ---- 1. 标题检测 ----
            caption = _extract_caption(page_text)
            if caption:
                context_parts.append(f"标题: {caption}")
            else:
                # 从图片周围的文本块检测
                above_texts = []
                below_texts = []
                for block in text_blocks:
                    if block[6] == 0:
                        block_y = (block[1] + block[3]) / 2
                        if abs(block[0] - img_rect.x0) < 250:
                            if block_y < img_rect.y0 and img_rect.y0 - block_y < 180:
                                above_texts.append(block[4].strip())
                            elif block_y > img_rect.y1 and block_y - img_rect.y1 < 180:
                                below_texts.append(block[4].strip())

                if above_texts:
                    context_parts.append("上文: " + " ".join(above_texts[-2:]))
                if below_texts:
                    caption_candidate = " ".join(below_texts[:2])
                    # 再检查下方文字是否为标题格式
                    detected = _extract_caption(caption_candidate)
                    if detected:
                        context_parts.append(f"标题: {detected}")
                    else:
                        context_parts.append("下文: " + caption_candidate[:120])

            # ---- 2. OCR（可选） ----
            if img_bytes:
                ocr_text = _ocr_image(img_bytes)
                if ocr_text:
                    context_parts.append(f"OCR: {ocr_text}")

            # ---- 3. 组装 ----
            dup_tag = " [重复]" if is_dup else ""
            if not context_parts:
                all_text_lines = page_text.strip().split("\n")
                context_parts.append("页面上下文: " + " ".join(all_text_lines[-3:]))

            contexts.append(
                f"插图{dup_tag} {page_num+1}-{i+1} ({w:.0f}x{h:.0f}px): {' | '.join(context_parts)}"
            )
    except Exception:
        pass
    return contexts[:6]


# ============================================================
# 表格提取
# ============================================================
def _table_to_markdown(table):
    try:
        data = table.extract()
        if not data:
            return ""
        lines = []
        for i, row in enumerate(data):
            cells = [str(cell).replace("\n", " ") if cell else "" for cell in row]
            lines.append("| " + " | ".join(cells) + " |")
            if i == 0:
                lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
        return "\n".join(lines)
    except Exception:
        return ""


# ============================================================
# 矢量图结构分析 — 不依赖视觉模型，读懂流程图骨架
# ============================================================
def _analyze_vector_diagrams(page):
    """提取页面矢量绘图指令，分析流程图/结构图骨架"""
    drawings = page.get_drawings()
    if not drawings:
        return []

    results = []
    for d_idx, drawing in enumerate(drawings):
        items = drawing.get("items", [])
        if len(items) < 3:
            continue  # 太少的绘图指令 → 可能是装饰线，跳过

        rect = drawing.get("rect")
        if not rect:
            continue

        # 统计绘图指令类型
        lines = sum(1 for cmd, _ in items if cmd == "l")
        rects = sum(1 for cmd, _ in items if cmd == "re")
        curves = sum(1 for cmd, _ in items if cmd in ("c", "qu"))
        total = len(items)

        # 分类
        if rects > total * 0.4:
            dtype = "框图/组织结构"
        elif curves > total * 0.3:
            dtype = "曲线图/数据趋势"
        elif lines > total * 0.5:
            dtype = "流程图/连线图"
        else:
            dtype = "混合图形"

        # 填充色统计 → 判断信息密度
        fills = set()
        for cmd, _ in items:
            fill = items[0] if isinstance(items, list) else None
        for item in items:
            if len(item) > 2 and item[2] is not None:
                fills.add(str(item[2]) if isinstance(item[2], (int, float)) else "")

        results.append({
            "type": dtype,
            "rects": rects,
            "lines": lines,
            "curves": curves,
            "fill_colors": len(fills),
            "bounds": f"{rect.width:.0f}x{rect.height:.0f}",
        })

    return results


def _vector_diagram_context(page, text_blocks):
    """将矢量图分析结果与文本块关联"""
    drawings = page.get_drawings()
    if not drawings:
        return []

    contexts = []
    for d_idx, drawing in enumerate(drawings[:8]):
        items = drawing.get("items", [])
        if len(items) < 3:
            continue
        rect = drawing.get("rect")
        if not rect or (rect.width < 60 and rect.height < 60):
            continue

        # 统计类型
        n_rects = sum(1 for cmd, _ in items if cmd == "re")
        n_lines = sum(1 for cmd, _ in items if cmd == "l")
        n_curves = sum(1 for cmd, _ in items if cmd in ("c", "qu"))

        if n_rects > len(items) * 0.4:
            dtype = "框图(组织结构/分类)"
        elif n_lines > len(items) * 0.5:
            dtype = "流程图(步骤/连线)"
        elif n_curves > len(items) * 0.3:
            dtype = "图表(曲线/数据)"
        else:
            dtype = "图形"

        # 查找图形区域内或附近的文字
        nearby_text = []
        for block in text_blocks:
            if block[6] == 0:  # text block
                bx0, by0, bx1, by1 = block[0], block[1], block[2], block[3]
                # 文字与图形重叠或紧邻
                h_overlap = max(0, min(rect.x1, bx1) - max(rect.x0, bx0))
                v_dist = min(abs(rect.y1 - by0), abs(by1 - rect.y0))
                if h_overlap > 20 or v_dist < 50:
                    text_content = block[4].strip()
                    if len(text_content) > 2:
                        nearby_text.append(text_content)

        text_summary = " | ".join(nearby_text[:3]) if nearby_text else "无关联文字"
        contexts.append(
            f"矢量{dtype} ({rect.width:.0f}x{rect.height:.0f}px, "
            f"{len(items)}个绘图指令): {text_summary}"
        )

    return contexts[:6]


# ============================================================
# 学科自动检测
# ============================================================
DISCIPLINE_KEYWORDS = {
    "stem": [
        "公式", "定理", "证明", "推导", "微分", "积分", "矩阵", "向量",
        "算法", "复杂度", "方程", "函数", "极限", "概率", "统计",
        "编程", "代码", "数据结构", "电路", "信号", "力学", "量子",
    ],
    "medical": [
        "诊断", "治疗", "症状", "病理", "药理", "手术", "临床",
        "综合征", "病因", "预后", "剂量", "禁忌", "副作用",
        "血管", "神经", "细胞", "免疫", "感染", "肿瘤",
    ],
    "liberal-arts": [
        "历史", "哲学", "文学", "政治", "经济", "社会", "文化",
        "理论", "主义", "学派", "运动", "革命", "制度",
        "思想", "伦理", "美学", "辩证", "意识形态",
    ],
    "business": [
        "管理", "营销", "战略", "财务", "会计", "投资",
        "市场", "竞争", "组织", "领导力", "供应链", "品牌",
        "模型", "框架", "SWOT", "KPI", "ROI", "案例分析",
    ],
}


def auto_detect_discipline(text):
    """基于关键词匹配自动推断学科"""
    text_lower = text.lower()
    scores = {}
    for discipline, keywords in DISCIPLINE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        scores[discipline] = score

    best = max(scores, key=scores.get)
    if scores[best] >= 3:
        return best
    return "general"


# ============================================================
# PDF 解析
# ============================================================
def extract_pdf_with_features(pdf_path):
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"❌ 无法打开 PDF 文件: {e}")
        sys.exit(1)

    print("   → 分析字号层级...")
    sizes = _collect_font_sizes(doc)
    h1_th, h2_th, h3_th = _build_size_thresholds(sizes)
    print(f"   → 字号阈值: h1≥{h1_th:.0f}pt  h2≥{h2_th:.0f}pt  h3≥{h3_th:.0f}pt")

    full_text = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_w = page.rect.width
        text_instances = page.get_text("dict")
        page_text = f"--- Page {page_num + 1} ---\n"

        for block in text_instances["blocks"]:
            if "lines" in block:
                for line in block["lines"]:
                    line_spans = []
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if not text:
                            continue
                        text = filter_noise(text)
                        if not text:
                            continue

                        # 字号层级
                        prefix = _span_to_heading_prefix(span["size"], h1_th, h2_th, h3_th)

                        # 居中文本 → 升一级标题
                        bbox = span.get("bbox")
                        if bbox and not prefix:
                            span_center_x = (bbox[0] + bbox[2]) / 2
                            if abs(span_center_x - page_w / 2) < page_w * 0.12:
                                if span["size"] >= h3_th:
                                    prefix = "## "

                        if is_red_color(span["color"]):
                            text = f"<red>{text}</red>"
                        elif is_bold(span):
                            text = f"**{text}**"
                        if prefix:
                            text = prefix + text
                        line_spans.append(text)

                    if line_spans:
                        page_text += "".join(line_spans) + "\n"

        # 表格
        tables = page.find_tables()
        if tables:
            page_text += "\n[提取表格]\n"
            for table in tables:
                md_table = _table_to_markdown(table)
                if md_table:
                    page_text += md_table + "\n"

        # 图片上下文（增强版）
        image_contexts = _extract_image_contexts_rich(page, page_num)
        if image_contexts:
            page_text += "\n[图片上下文]\n" + "\n".join(image_contexts) + "\n"

        # 矢量图分析
        all_blocks = page.get_text("blocks")
        vector_contexts = _vector_diagram_context(page, all_blocks)
        if vector_contexts:
            page_text += "\n[矢量图结构]\n" + "\n".join(vector_contexts) + "\n"

        full_text.append(page_text)

    doc.close()
    return "\n".join(full_text)


# ============================================================
# TextRank 抽取式摘要 — 在喂给 AI 之前先筛关键句
# ============================================================
class TextRank:
    """TextRank 抽取式摘要，适配中文"""

    def __init__(self, damping=0.85, max_iter=100, min_diff=1e-5):
        self.damping = damping
        self.max_iter = max_iter
        self.min_diff = min_diff

    def _split_sentences(self, text):
        """中文分句"""
        sentences = re.split(r"(?<=[。！？\n])\s*", text)
        return [s.strip() for s in sentences if len(s.strip()) > 5]

    def _tokenize_sentence(self, text):
        """句子 → token 集合。jieba 可用时用词级分词，否则退化为 char bigram"""
        if HAS_JIEBA:
            words = jieba.lcut(text)
            return set(w for w in words if len(w.strip()) > 1 and not w.strip().isdigit())
        else:
            chars = re.sub(r"\s+", "", text)
            return set(chars[i:i+2] for i in range(len(chars) - 1))

    def _build_similarity_matrix(self, sentences):
        """基于 Jaccard 相似度构建句子图"""
        n = len(sentences)
        token_sets = [self._tokenize_sentence(s) for s in sentences]

        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                set_i = token_sets[i]
                set_j = token_sets[j]
                if not set_i or not set_j:
                    continue
                intersection = len(set_i & set_j)
                union = len(set_i | set_j)
                if union > 0:
                    sim = intersection / union
                    matrix[i][j] = sim
                    matrix[j][i] = sim
        return matrix

    def _pagerank(self, similarity_matrix):
        """PageRank 迭代至收敛"""
        n = len(similarity_matrix)
        if n == 0:
            return []
        if n == 1:
            return [1.0]

        scores = [1.0 / n] * n

        for _ in range(self.max_iter):
            prev_scores = scores[:]
            for i in range(n):
                incoming = 0.0
                for j in range(n):
                    if similarity_matrix[j][i] > 0:
                        # 归一化：除以 j 的出度
                        out_degree = sum(similarity_matrix[j][k] for k in range(n))
                        if out_degree > 0:
                            incoming += (similarity_matrix[j][i] / out_degree) * prev_scores[j]
                scores[i] = (1 - self.damping) / n + self.damping * incoming

            # 收敛检查
            diff = sum(abs(scores[i] - prev_scores[i]) for i in range(n))
            if diff < self.min_diff:
                break

        return scores

    def summarize(self, text, ratio=0.35):
        """返回 top-ratio 的关键句 + 原始索引"""
        sentences = self._split_sentences(text)
        if len(sentences) <= 3:
            return sentences, list(range(len(sentences)))

        sim_matrix = self._build_similarity_matrix(sentences)
        scores = self._pagerank(sim_matrix)

        # 按分数排序，取 top ratio
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        top_n = max(3, int(len(sentences) * ratio))
        top_indices = sorted(idx for idx, _ in ranked[:top_n])
        top_sentences = [sentences[i] for i in top_indices]

        return top_sentences, top_indices


def extract_key_sentences(text, ratio=0.35):
    """TextRank 抽取关键句"""
    tr = TextRank()
    key_sentences, indices = tr.summarize(text, ratio)
    return key_sentences, indices


# ============================================================
# 数学公式检测
# ============================================================
MATH_INDICATORS = re.compile(
    r"[\$\^_{}\\]|\\times|\\div|\\sum|\\int|\\frac|\\sqrt|\\alpha|\\beta|\\gamma|\\theta|\\pi|\\lambda"
)


def detect_math_regions(text):
    regions = []
    for i, line in enumerate(text.split("\n")):
        if MATH_INDICATORS.search(line):
            regions.append(i)
    return regions


# ============================================================
# TF-IDF 关键词
# ============================================================
def _tokenize(text):
    """分词：jieba 优先，fallback 正则"""
    if HAS_JIEBA:
        words = jieba.lcut(text.lower())
        return [w for w in words if len(w) >= 2 and not w.isdigit() and not re.match(r"^\s+$", w)]
    tokens = re.findall(r"[一-鿿]+|[a-zA-Z]+", text.lower())
    return [t for t in tokens if len(t) >= 2 and not t.isdigit()]


def extract_keywords_tfidf(text, top_n=20):
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 20]
    if len(paragraphs) < 2:
        paragraphs = [text[i:i+500] for i in range(0, len(text), 500)]
    N = len(paragraphs)
    tokenized_docs = [_tokenize(p) for p in paragraphs]
    df = Counter()
    for doc in tokenized_docs:
        for token in set(doc):
            df[token] += 1
    tfidf_scores = Counter()
    for doc in tokenized_docs:
        tf = Counter(doc)
        for token, count in tf.items():
            idf = math.log((N + 1) / (df[token] + 1)) + 1
            tfidf_scores[token] += count * idf
    keywords = [w for w, _ in tfidf_scores.most_common(top_n * 3)]
    return [w for w in keywords if not w.isdigit() and not w.startswith("0x")][:top_n]


# ============================================================
# 缓存系统 — 避免重复处理
# ============================================================
CACHE_DIR = ".cache"


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def _pdf_fingerprint(pdf_path):
    """基于文件大小+mtime生成指纹"""
    stat = os.stat(pdf_path)
    raw = f"{pdf_path}:{stat.st_size}:{stat.st_mtime}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _config_fingerprint(config):
    """基于关键配置生成指纹"""
    raw = f"{config.get('DEEPSEEK_API_KEY', '')[-8:]}:{config.get('API_URL', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def cache_get(pdf_path, config, stage):
    """读取缓存，命中返回 (data, cached_path) 否则返回 (None, cache_path)"""
    _ensure_cache_dir()
    fp = _pdf_fingerprint(pdf_path)
    cfp = _config_fingerprint(config)
    cache_path = os.path.join(CACHE_DIR, f"{fp}_{cfp}_{stage}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f), cache_path
        except Exception:
            pass
    return None, cache_path


def cache_set(data, cache_path):
    """写入缓存"""
    _ensure_cache_dir()
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


# ============================================================
# API 调用
# ============================================================
def _build_headers(config):
    return {
        "Authorization": f"Bearer {config['DEEPSEEK_API_KEY']}",
        "Content-Type": "application/json",
    }


def _api_post(config, payload, timeout=120, stream=False):
    """统一 API 调用，支持流式输出"""
    payload = dict(payload)  # 不修改原始 payload
    payload["stream"] = stream

    try:
        if stream:
            return _api_post_stream(config, payload, timeout)
        response = requests.post(
            config["API_URL"] + "/chat/completions",
            json=payload,
            headers=_build_headers(config),
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except requests.exceptions.Timeout:
        print(f"\n❌ API 请求超时（{timeout}s），请检查网络或稍后重试")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"\n❌ API 请求失败: {e}")
        sys.exit(1)
    except (KeyError, json.JSONDecodeError) as e:
        print(f"\n❌ API 返回格式异常: {e}")
        sys.exit(1)


def _api_post_stream(config, payload, timeout):
    """流式输出：实时打印 AI 回复"""
    response = requests.post(
        config["API_URL"] + "/chat/completions",
        json=payload,
        headers=_build_headers(config),
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()

    full_text = []
    for line in response.iter_lines(decode_unicode=True):
        if not line or line.startswith(":"):
            continue
        if line.startswith("data: "):
            data = line[6:]
            if data.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    print(content, end="", flush=True)
                    full_text.append(content)
            except json.JSONDecodeError:
                continue
    print()  # 换行
    return "".join(full_text)


def generate_notes(extracted_text, config, discipline, stream=False):
    """TextRank 预摘要 + TF-IDF 关键词 → 双重引导 AI"""
    # TextRank 抽取关键句
    print("   → TextRank 抽取关键句...")
    key_sentences, _ = extract_key_sentences(extracted_text, ratio=0.35)
    key_text = "\n".join(key_sentences)

    # TF-IDF 关键词
    keywords = extract_keywords_tfidf(extracted_text, top_n=15)

    prompt = DISCIPLINE_PROMPTS.get(discipline, DISCIPLINE_PROMPTS["general"])

    user_msg = (
        f"## [系统] 自动抽取的核心关键句（共 {len(key_sentences)} 句，优先覆盖）:\n"
        f"{key_text}\n\n"
        f"## [系统] 高频关键词: {', '.join(keywords)}\n\n"
        f"## [系统] 完整课件文本（含 <red> 重点和 **加粗**）:\n"
        f"{extracted_text}\n\n"
        f"请基于以上信息生成复习笔记。优先覆盖核心关键句和高频关键词，"
        f"同时从完整文本中补充细节。<red> 标签为红色重点。"
    )

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_msg},
        ],
    }
    return _api_post(config, payload, timeout=120, stream=stream)


def generate_mock_questions(notes_content, config):
    prompt = (
        f"基于以下复习笔记，出 3 道高难度的模拟考题（选择题/简答题混合），"
        f"并附带详细解析。确保笔记中没有涵盖的考点被指出：\n\n{notes_content}"
    )
    payload = {
        "model": "deepseek-reasoner",
        "messages": [
            {"role": "system", "content": "你是一位严格的考试命题专家，擅长挖掘知识盲区出题。"},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "thinking": {"type": "enabled"},
    }
    return _api_post(config, payload, timeout=180)


def detect_blind_spots(notes_md, questions_md, config):
    """盲区回填闭环"""
    prompt = (
        f"## 复习笔记:\n{notes_md}\n\n## 模拟考题:\n{questions_md}\n\n"
        f"请对比考题与笔记：\n"
        f"1. 列出考题涉及但笔记未覆盖的知识点\n"
        f"2. 为每个盲区补充考点笔记（概念、关键公式/理论、易错提醒）\n"
        f"3. 输出【补充笔记】，可直接附加在原笔记末尾"
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是严谨的考试复习专家，擅长发现知识漏洞并精准补充。"},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    return _api_post(config, payload, timeout=120)


def generate_cheat_sheet(notes_md, config, discipline):
    """一页速查表"""
    prompt = DISCIPLINE_PROMPTS.get(discipline, DISCIPLINE_PROMPTS["general"])
    user_msg = (
        f"请将以下复习笔记浓缩为一张 A4 篇幅的【考前速查表】：\n"
        f"- 只保留最核心的公式/定理/概念/关键词\n"
        f"- 使用紧凑列表或表格格式\n"
        f"- 省略所有例题解析和详细说明\n"
        f"- 适合考试前 10 分钟快速扫读\n\n{notes_md}"
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
    }
    return _api_post(config, payload, timeout=90)


def generate_anki_cards(notes_md, config):
    """生成 Anki 卡片 — 要求 AI 输出结构化 JSON，鲁棒解析"""
    prompt = (
        f"基于以下复习笔记，生成 10 张 Anki 闪卡。\n\n"
        f"请严格输出 JSON 数组，每项含 front 和 back 字段。不要输出其他内容：\n"
        f'[{{"front": "问题或概念", "back": "答案或详细解释"}}, ...]\n\n'
        f"{notes_md}"
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一个将知识点转化为问答卡片的工具。只输出 JSON 数组，不输出解释。"},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": 0.3,
    }
    return _api_post(config, payload, timeout=90)


def _parse_anki_json(raw_text):
    """多级容错解析 Anki JSON"""
    # 1. 直接解析
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    # 2. 提取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 提取第一个 [...] 数组
    m = re.search(r"\[.*\]", raw_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _parse_anki_text_fallback(raw_text):
    """文本格式兜底解析：Q:/A: 或 问：/答："""
    qa_pairs = []
    lines = raw_text.split("\n")
    current_q = None
    for line in lines:
        line = line.strip()
        if re.match(r"^(Q[:：]|问[:：]|Front[:：])", line):
            if current_q:
                qa_pairs.append((current_q[0], current_q[1] if len(current_q) > 1 else ""))
            current_q = [re.sub(r"^(Q[:：]|问[:：]|Front[:：])\s*", "", line), ""]
        elif re.match(r"^(A[:：]|答[:：]|Back[:：])", line):
            if current_q:
                current_q[1] = re.sub(r"^(A[:：]|答[:：]|Back[:：])\s*", "", line)
        elif line == "" and current_q and current_q[1]:
            qa_pairs.append((current_q[0], current_q[1]))
            current_q = None
    if current_q and current_q[1]:
        qa_pairs.append((current_q[0], current_q[1]))
    return qa_pairs


def _fallback_anki_from_notes(notes_md):
    """从笔记中提取 red/bold 关键词生成兜底卡片"""
    pairs = []
    red_items = re.findall(r"<red>(.*?)</red>", notes_md)
    bold_items = re.findall(r"\*\*(.*?)\*\*", notes_md)
    for item in (red_items + bold_items)[:15]:
        item = item.strip()
        if len(item) > 2:
            pairs.append((item, f"请根据复习笔记详细解释：{item}"))
    return pairs


def export_anki_csv(notes_md, config, output_path):
    """生成 Anki 可导入的 CSV — 三级容错"""
    print("   → 生成 Anki 闪卡...")
    raw = generate_anki_cards(notes_md, config)

    qa_pairs = []

    # 1. JSON 解析
    cards = _parse_anki_json(raw)
    if cards:
        for card in cards:
            if isinstance(card, dict):
                f = card.get("front") or card.get("question") or card.get("q") or ""
                b = card.get("back") or card.get("answer") or card.get("a") or ""
                if f and b:
                    qa_pairs.append((str(f), str(b)))

    # 2. 文本格式兜底
    if not qa_pairs:
        qa_pairs = _parse_anki_text_fallback(raw)

    # 3. 笔记关键词兜底
    if not qa_pairs:
        print("   ⚠️  无法解析 AI 输出，使用笔记关键词生成兜底卡片")
        qa_pairs = _fallback_anki_from_notes(notes_md)

    if not qa_pairs:
        print("   ⚠️  未能生成任何卡片")
        return

    # 去重
    seen = set()
    unique_pairs = []
    for q, a in qa_pairs:
        key = q[:30]
        if key not in seen:
            seen.add(key)
            unique_pairs.append((q, a))

    try:
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Front", "Back"])
            for q, a in unique_pairs:
                writer.writerow([q, a])
        print(f"   → 共 {len(unique_pairs)} 张卡片")
    except Exception as e:
        print(f"⚠️  Anki CSV 导出失败: {e}")


def export_anki_apkg(notes_md, config, output_path):
    """使用 genanki 生成原生 .apkg 文件（可直接导入 Anki）"""
    if not HAS_GENANKI:
        print("   ⚠️  genanki 未安装，回退至 CSV 导出。安装: pip install genanki")
        export_anki_csv(notes_md, config, output_path.replace(".apkg", ".csv"))
        return

    print("   → 生成 Anki 闪卡...")
    raw = generate_anki_cards(notes_md, config)
    cards = _parse_anki_json(raw)
    if not cards:
        cards_dicts = _parse_anki_text_fallback(raw)
        cards = [{"front": q, "back": a} for q, a in cards_dicts]
    if not cards:
        pairs = _fallback_anki_from_notes(notes_md)
        cards = [{"front": q, "back": a} for q, a in pairs]

    if not cards:
        print("   ⚠️  未能生成任何卡片")
        return

    # 去重
    seen = set()
    unique_cards = []
    for c in cards:
        if isinstance(c, dict):
            f = c.get("front") or c.get("question") or c.get("q") or ""
            b = c.get("back") or c.get("answer") or c.get("a") or ""
            key = f[:30]
            if f and b and key not in seen:
                seen.add(key)
                unique_cards.append((str(f), str(b)))

    # genanki model
    model_id = int(hashlib.md5("exam-master-skill".encode()).hexdigest()[:8], 16) % (1 << 31)
    model = genanki.Model(
        model_id,
        "Exam Master Card",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[{
            "name": "Card 1",
            "qfmt": '<div style="font-size:20px;text-align:center;padding:20px">{{Front}}</div>',
            "afmt": '<hr id="answer"><div style="font-size:16px;text-align:left;padding:10px">{{Back}}</div>',
        }],
    )

    deck_id = int(hashlib.md5(output_path.encode()).hexdigest()[:8], 16) % (1 << 31)
    deck = genanki.Deck(deck_id, "Exam Master 复习卡片")

    for front, back in unique_cards:
        deck.add_note(genanki.Note(model=model, fields=[front, back]))

    try:
        genanki.Package(deck).write_to_file(output_path)
        print(f"   → 共 {len(unique_cards)} 张卡片")
    except Exception as e:
        print(f"⚠️  Anki .apkg 导出失败: {e}，回退至 CSV")
        export_anki_csv(notes_md, config, output_path.replace(".apkg", ".csv"))


# ============================================================
# 视觉模型（可选）
# ============================================================
def analyze_images_with_vision(doc, config):
    descriptions = []
    vision_url = config.get("VISION_API_URL", "")
    vision_key = config.get("VISION_API_KEY", "")
    if not vision_url or not vision_key:
        return descriptions

    try:
        import base64
        for page_num in range(len(doc)):
            page = doc[page_num]
            for img_idx, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                base_image = doc.extract_image(xref)
                if base_image and len(base_image.get("image", b"")) < 5 * 1024 * 1024:
                    img_b64 = base64.b64encode(base_image["image"]).decode()
                    ext = base_image["ext"]
                    payload = {
                        "model": config.get("VISION_MODEL", "gpt-4o"),
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "用一句话描述这张课件插图的核心内容，直接说结论。"},
                                {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{img_b64}"}},
                            ],
                        }],
                        "max_tokens": 200,
                    }
                    try:
                        resp = requests.post(vision_url, json=payload,
                                             headers={"Authorization": f"Bearer {vision_key}",
                                                      "Content-Type": "application/json"},
                                             timeout=60)
                        desc = resp.json()["choices"][0]["message"]["content"]
                        descriptions.append(f"[图 {page_num+1}-{img_idx+1}]: {desc}")
                    except Exception:
                        pass
    except Exception:
        pass
    return descriptions


# ============================================================
# PDF 导出
# ============================================================
def export_to_pdf(markdown_content, output_path):
    md = markdown.Markdown(extensions=["extra"])
    html_body = md.convert(markdown_content)

    css = """
    @page { margin: 2cm; size: A4; }
    body {
        font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
        font-size: 13px; line-height: 1.8; color: #333;
    }
    h1 { border-bottom: 3px solid #c0392b; padding-bottom: 6px; color: #2c3e50; }
    h2 { border-left: 4px solid #e74c3c; padding-left: 10px; color: #2c3e50; }
    h3 { color: #c0392b; }
    red {
        background: #ffe0e0;
        color: #c0392b;
        font-weight: bold;
        padding: 2px 6px;
        border: 1.5px solid #e74c3c;
        border-radius: 3px;
    }
    strong { color: #2c3e50; font-weight: 700; }
    table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; }
    th { background: #c0392b; color: white; padding: 8px 12px; text-align: left; }
    td { border: 1px solid #ddd; padding: 8px 12px; }
    blockquote {
        border-left: 4px solid #e74c3c; background: #fdf2f2;
        padding: 10px 16px; margin: 12px 0;
    }
    code { background: #f5f5f5; padding: 1px 5px; border-radius: 2px; }
    @media print {
        body { font-size: 11px; }
        table { font-size: 10px; }
    }
    """

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body>{html_body}</body></html>"""

    try:
        HTML(string=html).write_pdf(output_path)
    except Exception as e:
        print(f"❌ PDF 生成失败: {e}")
        print("   提示：macOS 需先安装 brew install pango cairo gdk-pixbuf")
        sys.exit(1)


def export_markdown_raw(content, output_path):
    """保留 Markdown 源文件"""
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"⚠️  保存 Markdown 源文件失败: {e}")


def export_anki_csv(notes_md, config, output_path):
    """生成 Anki 可导入的 CSV"""
    print("   → 生成 Anki 闪卡...")
    cards_text = generate_anki_cards(notes_md, config)

    # 解析 Q: ... A: ... 格式
    qa_pairs = []
    current_q = None
    for line in cards_text.split("\n"):
        line = line.strip()
        if line.startswith("Q:") or line.startswith("问："):
            if current_q:
                qa_pairs.append((current_q, current_q[1] if len(current_q) > 1 else ""))
            current_q = [line[2:].strip(), ""]
        elif line.startswith("A:") or line.startswith("答："):
            if current_q:
                current_q[1] = line[2:].strip()
        elif line == "" and current_q and current_q[1]:
            qa_pairs.append((current_q[0], current_q[1]))
            current_q = None

    if current_q and current_q[1]:
        qa_pairs.append((current_q[0], current_q[1]))

    if not qa_pairs:
        # fallback: 从笔记中提取 <red> 内容做卡片
        red_items = re.findall(r"<red>(.*?)</red>", notes_md)
        qa_pairs = [(item, f"请根据复习笔记回答：{item}") for item in red_items[:10]]

    try:
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Front", "Back"])
            for q, a in qa_pairs:
                writer.writerow([q, a])
    except Exception as e:
        print(f"⚠️  Anki CSV 导出失败: {e}")


# ============================================================
# 配置
# ============================================================
def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("❌ 未找到 config.json，请先创建配置文件")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ config.json 格式错误: {e}")
        sys.exit(1)

    api_key = config.get("DEEPSEEK_API_KEY", "")
    if api_key in (None, "", "你的实际API_KEY_粘贴在这里"):
        print("❌ 请在 config.json 中填入你的 DeepSeek API Key")
        sys.exit(1)
    if not config.get("API_URL"):
        print("❌ config.json 缺少 API_URL 字段")
        sys.exit(1)
    return config


# ============================================================
# 多文件收集
# ============================================================
def collect_pdf_paths(args):
    """收集所有输入的 PDF 文件路径，支持 glob 和目录"""
    paths = []
    for arg in args:
        if "*" in arg or "?" in arg:
            paths.extend(sorted(glob.glob(arg)))
        elif os.path.isdir(arg):
            paths.extend(sorted(glob.glob(os.path.join(arg, "*.pdf"))))
        elif os.path.isfile(arg):
            paths.append(arg)
        else:
            print(f"⚠️  跳过无效路径: {arg}")

    if not paths:
        print("❌ 未找到有效的 PDF 文件")
        sys.exit(1)
    return paths


def merge_extracted_texts(pdf_paths):
    """合并多个 PDF 的提取文本"""
    all_text = []
    for path in pdf_paths:
        print(f"\n📄 处理: {os.path.basename(path)}")
        text = extract_pdf_with_features(path)
        all_text.append(f"\n\n===== 课件: {os.path.basename(path)} =====\n{text}")
    return "\n".join(all_text)


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Exam-Master-Skill: 期末划重点神器")
    parser.add_argument("pdf", nargs="+", help="课件 PDF 文件路径（支持多个文件/glob/目录）")
    parser.add_argument("-o", "--output", default="复习笔记", help="输出文件名前缀")
    parser.add_argument("--discipline", default="auto",
                        choices=["auto", "stem", "liberal-arts", "medical", "business", "general"],
                        help="学科类型（默认自动检测）")
    parser.add_argument("--no-mock", action="store_true", help="跳过模拟考题")
    parser.add_argument("--cheat-sheet", action="store_true", help="生成一页考前速查表")
    parser.add_argument("--no-blind-spot", action="store_true", help="跳过盲区回填")
    parser.add_argument("--anki", action="store_true", help="导出 Anki 闪卡 CSV")
    parser.add_argument("--apkg", action="store_true", help="导出 Anki 原生 .apkg 包（需 genanki）")
    parser.add_argument("--keep-md", action="store_true", help="保留 Markdown 源文件")
    parser.add_argument("--no-cache", action="store_true", help="跳过缓存，强制重新处理")
    parser.add_argument("--stream", action="store_true", help="流式输出 AI 回复（实时打字效果）")
    args = parser.parse_args()

    config = load_config()
    pdf_paths = collect_pdf_paths(args.pdf)
    print(f"📚 共 {len(pdf_paths)} 个 PDF 文件待处理")

    # ---- Stage 1: PDF 提取（带缓存） ----
    extracted_text = None
    if not args.no_cache and len(pdf_paths) == 1:
        cached, cache_path = cache_get(pdf_paths[0], config, "extract")
        if cached:
            print("✅ 命中缓存，跳过 PDF 解析")
            extracted_text = cached["text"]
            math_lines = cached.get("math_lines", [])

    if extracted_text is None:
        extracted_text = merge_extracted_texts(pdf_paths)
        math_lines = detect_math_regions(extracted_text)
        if math_lines:
            print(f"   → 检测到 {len(math_lines)} 行可能含数学公式")
        if not args.no_cache and len(pdf_paths) == 1:
            _, cache_path = cache_get(pdf_paths[0], config, "extract")
            cache_set({"text": extracted_text, "math_lines": math_lines}, cache_path)

    # ---- 自动检测学科 ----
    if args.discipline == "auto":
        detected = auto_detect_discipline(extracted_text)
        print(f"   → 自动检测学科: {detected}")
        discipline = detected
    else:
        discipline = args.discipline

    # ---- Stage 2: 生成笔记（带缓存） ----
    notes_md = None
    if not args.no_cache and len(pdf_paths) == 1:
        cached, _ = cache_get(pdf_paths[0], config, "notes")
        if cached:
            print("✅ 命中缓存，跳过笔记生成")
            notes_md = cached["notes"]

    if notes_md is None:
        if args.stream:
            print("正在调用 DeepSeek 生成考点矩阵笔记（流式输出）...")
        else:
            print("正在调用 DeepSeek 生成考点矩阵笔记...")
        notes_md = generate_notes(extracted_text, config, discipline, stream=args.stream)
        if not args.no_cache and len(pdf_paths) == 1:
            _, cache_path = cache_get(pdf_paths[0], config, "notes")
            cache_set({"notes": notes_md}, cache_path)

    # ---- Stage 3: 可选视觉模型 ----
    if config.get("VISION_API_KEY"):
        print("正在调用视觉模型分析插图...")
        try:
            doc = fitz.open(pdf_paths[0])
            image_descs = analyze_images_with_vision(doc, config)
            doc.close()
            if image_descs:
                notes_md += "\n\n## 插图解析\n" + "\n".join(image_descs)
        except Exception:
            pass

    # ---- Stage 4: 出题 + 盲区回填（带缓存） ----
    if not args.no_mock:
        questions_md = None
        if not args.no_cache and len(pdf_paths) == 1:
            cached, _ = cache_get(pdf_paths[0], config, "questions")
            if cached:
                print("✅ 命中缓存，跳过出题自测")
                questions_md = cached["questions"]

        if questions_md is None:
            print("正在调用 DeepSeek-R1 出题自测...")
            questions_md = generate_mock_questions(notes_md, config)
            if not args.no_cache and len(pdf_paths) == 1:
                _, cache_path = cache_get(pdf_paths[0], config, "questions")
                cache_set({"questions": questions_md}, cache_path)

        if not args.no_blind_spot:
            print("正在反向检测笔记盲区并补充...")
            supplement = detect_blind_spots(notes_md, questions_md, config)
            notes_md += "\n\n---\n\n## 📍 盲区补充（基于考题反查）\n" + supplement

        exam_pdf = f"{args.output}-模拟考题.pdf"
        print("正在导出模拟考题 PDF...")
        export_to_pdf(questions_md, exam_pdf)
        print(f"✨ 模拟考题 → {exam_pdf}")

    # ---- Stage 5: 导出笔记 ----
    notes_pdf = f"{args.output}.pdf"
    print("正在导出复习笔记 PDF（红字高亮+边框+表格）...")
    export_to_pdf(notes_md, notes_pdf)
    print(f"✨ 复习笔记 → {notes_pdf}")

    # ---- Stage 6: 可选输出 ----
    if args.keep_md:
        md_path = f"{args.output}.md"
        export_markdown_raw(notes_md, md_path)
        print(f"✨ Markdown 源文件 → {md_path}")

    if args.apkg:
        apkg_path = f"{args.output}-anki.apkg"
        export_anki_apkg(notes_md, config, apkg_path)
        print(f"✨ Anki .apkg → {apkg_path}")

    if args.anki and not args.apkg:
        anki_path = f"{args.output}-anki.csv"
        export_anki_csv(notes_md, config, anki_path)
        print(f"✨ Anki 闪卡 → {anki_path}")

    if args.cheat_sheet:
        print("正在生成考前速查表...")
        cheat_md = generate_cheat_sheet(notes_md, config, discipline)
        cheat_pdf = f"{args.output}-速查表.pdf"
        export_to_pdf(cheat_md, cheat_pdf)
        print(f"✨ 速查表 → {cheat_pdf}")

    print("\n✅ 全部完成")


if __name__ == "__main__":
    main()
