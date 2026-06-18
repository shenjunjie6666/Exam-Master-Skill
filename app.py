import fitz  # PyMuPDF
import json
import requests

# --- 增加：语义降噪过滤列表 ---
NOISE_WORDS = ["如图所示", "请大家看", "课件页码", "欢迎同学"]

def filter_noise(text):
    for noise in NOISE_WORDS:
        text = text.replace(noise, "")
    return text

def extract_pdf_with_features(pdf_path):
    doc = fitz.open(pdf_path)
    full_text = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        # get_text("dict") 可以获取字体的颜色和大小
        text_instances = page.get_text("dict")
        page_text = f"--- Page {page_num + 1} ---\n"

        for block in text_instances["blocks"]:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        text = filter_noise(span["text"])
                        # 检查颜色 (PyMuPDF 中红色通常接近 16711680 或者通过RGB判断)
                        # 也可以根据 span["flags"] 判断是否加粗
                        color = span["color"]

                        # 简单的红色判断逻辑（根据实际课件可能需要微调）
                        if color == 16711680 or color == 14169141:
                            text = f"<red>{text}</red>"
                        elif span["flags"] & 2:  # 加粗
                            text = f"**{text}**"

                        page_text += text
                    page_text += "\n"
        full_text.append(page_text)

    return "\n".join(full_text)

def call_deepseek_api(prompt_skill, input_text, config):
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": prompt_skill},
            {"role": "user", "content": f"请帮我精简这份课件。注意含有 <red> 标签和加粗的内容：\n\n{input_text}"}
        ],
        "stream": False
    }

    headers = {
        "Authorization": f"Bearer {config['DEEPSEEK_API_KEY']}",
        "Content-Type": "application/json"
    }

    response = requests.post(config["API_URL"] + "/chat/completions", json=payload, headers=headers)
    return response.json()['choices'][0]['message']['content']

def generate_mock_questions(notes_content, config):
    """基于复习笔记，让 AI 出 3 道高难度模拟考题并附解析"""
    prompt = (
        f"基于以下复习笔记，出 3 道高难度的模拟考题（选择题/简答题混合），"
        f"并附带详细解析。确保笔记中没有涵盖的考点被指出：\n\n{notes_content}"
    )

    payload = {
        "model": "deepseek-reasoner",
        "messages": [
            {"role": "system", "content": "你是一位严格的考试命题专家，擅长挖掘知识盲区出题。"},
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "thinking": {"type": "enabled"}  # 开启思维链推理，提升出题质量
    }

    headers = {
        "Authorization": f"Bearer {config['DEEPSEEK_API_KEY']}",
        "Content-Type": "application/json"
    }

    response = requests.post(config["API_URL"] + "/chat/completions", json=payload, headers=headers)
    return response.json()['choices'][0]['message']['content']


if __name__ == "__main__":
    # 加载配置
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    print("正在解析 PDF 视觉特征（红字与加粗）...")
    extracted_text = extract_pdf_with_features("your_ppt.pdf")

    # 读取 README 中的 Skill 提示词
    with open("README.md", "r", encoding="utf-8") as f:
        skill_prompt = f.read()

    print("正在调用 DeepSeek 进行学霸级期末总结...")
    result = call_deepseek_api(skill_prompt, extracted_text, config)

    with open("复习笔记.md", "w", encoding="utf-8") as f:
        f.write(result)
    print("✨ 复习笔记已生成至：复习笔记.md")

    # --- 出题自测 ---
    print("正在生成高难度模拟考题...")
    questions = generate_mock_questions(result, config)

    with open("模拟考题.md", "w", encoding="utf-8") as f:
        f.write(questions)
    print("✨ 模拟考题已生成至：模拟考题.md")