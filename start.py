"""
Travel Planner 启动脚本
======================
启动 FastAPI 后端服务，监听 8000 端口。
前端请另开终端进入 frontend/ 目录运行 npm run dev。
"""

import subprocess
import sys
import os

def main():
    # 切换到项目根目录
    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)

    print("=" * 50)
    print("  Travel Planner - 多智能体旅行规划系统")
    print("=" * 50)
    print(f"  项目目录: {project_dir}")
    print(f"  后端地址: http://localhost:8000")
    print(f"  API 文档: http://localhost:8000/docs")
    print("=" * 50)
    print("\n[启动中...] 后端服务\n")

    try:
        subprocess.run(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"],
            cwd=project_dir,
            check=True,
        )
    except KeyboardInterrupt:
        print("\n[已停止] 服务已关闭")
    except subprocess.CalledProcessError as e:
        print(f"\n[错误] 启动失败，退出码: {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()
