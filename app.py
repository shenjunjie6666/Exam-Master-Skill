import fitz  # PyMuPDF
import json
import requests

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
                        text = span["text"]
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

def call_deepseek_api(prompt_skill, input_text):
    # 这里配置你的 DeepSeek API 终端端点 (从你框架里获取)
    api_url = "https://api.deepseek.com/v1/chat/completions" 
    headers = {
        "Authorization": "Bearer YOUR_DEEPSEEK_API_KEY",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "deepseek-chat", # 或者 deepseek-reasoner (R1推理模型效果更好)
        "messages": [
            {"role": "system", "content": prompt_skill},
            {"role": "user", "content": f"请帮我精简这份课件。注意含有 <red> 标签和加粗的内容：\n\n{input_text}"}
        ],
        "stream": False
    }
    
    response = requests.post(api_url, json=payload, headers=headers)
    return response.json()['choices'][0]['message']['content']

if __name__ == "__main__":
    print("正在解析 PDF 视觉特征（红字与加粗）...")
    extracted_text = extract_pdf_with_features("your_ppt.pdf")
    
    # 读取 README 中的 Skill 提示词
    with open("README.md", "r", encoding="utf-8") as f:
        skill_prompt = f.read()
        
    print("正在调用 DeepSeek 进行学霸级期末总结...")
    result = call_deepseek_api(skill_prompt, extracted_text)
    
    with open("复习笔记.md", "w", encoding="utf-8") as f:
        f.write(result)
    print("✨ 复习笔记已生成至：复习笔记.md")