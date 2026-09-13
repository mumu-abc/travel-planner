"""快速测试 Agent 调用"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from app.crew import build_travel_crew

print(">>> 开始测试 Agent 调用...")
try:
    result = build_travel_crew("东京, 日本", 3, 1500, "美食")
    print(">>> 成功！结果前200字:")
    print(result[:200])
except Exception as e:
    print(f">>> 失败: {e}")
    import traceback
    traceback.print_exc()
