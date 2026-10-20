"""测试模板渲染 - 第二版"""
import os
import sys
from pathlib import Path
from datetime import datetime

# Fix encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from jinja2 import Template

# 读取模板
template_path = Path("templates/1080x1920/image_default.html")
with open(template_path, "r", encoding="utf-8") as f:
    template_content = f.read()

template = Template(template_content)

# 准备测试数据
context = {
    "title": "Python异步编程完全指南",
    "image": "https://images.unsplash.com/photo-1517694712202-14dd9538aa97?w=800",
    "text": "异步编程通过事件循环和协程实现高效并发，是Python处理I/O密集型任务的利器。",
    "author": "CodeMaster",
    "describe": "让编程更简单高效",
    "brand": "编程入门"
}

# 渲染并保存（使用时间戳避免缓存）
timestamp = datetime.now().strftime("%H%M%S")
output_path = Path(f"output/template_demo_{timestamp}.html")
output_path.parent.mkdir(exist_ok=True)
with open(output_path, "w", encoding="utf-8") as f:
    f.write(Template(template_content).render(context))

print(f"OK: {output_path.name}")
