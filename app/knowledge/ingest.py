"""
文档摄入管道 — 从 Markdown 文件构建 FAISS 向量索引。

流程：.md 文档 → 按 ## 标题分块 → sentence-transformers 编码 → FAISS 索引

使用：
    python -m app.knowledge.ingest

支持：
    - 从 documents/ 目录读取 .md 文件
    - 按 Markdown 标题分块（## 为大段，### 为子段）
    - 自动提取目的地名称和类别标签
    - 构建并持久化 FAISS 索引
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOCUMENTS_DIR = PROJECT_ROOT / "documents"
INDEX_DIR = PROJECT_ROOT / "data" / "faiss_index"


def _parse_markdown(filepath: Path) -> list[dict]:
    """
    将 Markdown 文件解析为 chunk 列表。

    每个 chunk 包含：
        text: 文本内容
        source: 源文件名
        destination: 目的地（从 # 一级标题提取）
        section: 所属章节
        category: 自动推断的类别标签
    """
    content = filepath.read_text(encoding="utf-8")
    filename = filepath.stem

    # 从一级标题提取目的地
    h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    destination = h1_match.group(1).replace("旅行指南", "").strip() if h1_match else filename

    chunks = []
    # 按 ## 二级标题分块
    sections = re.split(r"(?=^##\s+)", content, flags=re.MULTILINE)

    for section in sections:
        if not section.strip():
            continue

        # 提取二级标题
        h2_match = re.match(r"^##\s+(.+)$", section, re.MULTILINE)
        section_title = h2_match.group(1).strip() if h2_match else "概述"

        # 推断类别
        category = _infer_category(section_title, section)

        # 如果有 ### 三级标题，进一步细分
        subsections = re.split(r"(?=^###\s+)", section, flags=re.MULTILINE)

        for sub in subsections:
            if not sub.strip():
                continue

            h3_match = re.match(r"^###\s+(.+)$", sub, re.MULTILINE)
            sub_title = h3_match.group(1).strip() if h3_match else ""

            # 清理 markdown 格式符号
            text = re.sub(r"^#{1,6}\s+", "", sub, flags=re.MULTILINE)
            text = text.strip()

            if len(text) < 20:
                continue

            # 合并标题信息
            full_title = section_title
            if sub_title:
                full_title = f"{section_title} - {sub_title}"

            chunks.append({
                "text": text,
                "source": filepath.name,
                "destination": destination,
                "section": full_title,
                "category": category,
            })

    return chunks


_CATEGORY_KEYWORDS = {
    "attraction": ["景点", "attraction", "temple", "museum", "tower", "park", "palace", "cathedral"],
    "restaurant": ["美食", "restaurant", "寿司", "拉面", "dining", "food", "cuisine"],
    "transport": ["交通", "transport", "airport", "地铁", "train", "metro", "bus"],
    "budget": ["预算", "budget", "费用", "cost", "price", "日元", "欧元", "dollar"],
    "safety": ["安全", "safety", "emergency", "紧急", "警察", "police", "骗局", "scam"],
    "basic": ["基本", "basic", "info", "签证", "visa", "货币", "currency", "电压"],
}


def _infer_category(section_title: str, content: str) -> str:
    """根据标题和内容推断类别"""
    text_lower = (section_title + " " + content[:200]).lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return cat
    return "basic"


def ingest_documents(documents_dir: Optional[Path] = None) -> dict:
    """
    摄入文档目录中的所有 .md 文件，构建 FAISS 索引。

    Returns:
        {"total_files": N, "total_chunks": N, "index_path": "..."}
    """
    import json

    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    docs_dir = documents_dir or DOCUMENTS_DIR
    index_dir = INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)

    # 收集所有 chunk
    all_chunks = []
    md_files = sorted(docs_dir.glob("*.md"))

    if not md_files:
        logger.warning(f"未找到 .md 文件: {docs_dir}")
        return {"total_files": 0, "total_chunks": 0, "index_path": ""}

    for md_file in md_files:
        logger.info(f"摄入文档: {md_file.name}")
        chunks = _parse_markdown(md_file)
        all_chunks.extend(chunks)
        logger.info(f"  → {len(chunks)} 个 chunk")

    if not all_chunks:
        return {"total_files": len(md_files), "total_chunks": 0, "index_path": ""}

    # 编码
    logger.info(f"编码 {len(all_chunks)} 个 chunk...")
    model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    texts = [c["text"] for c in all_chunks]
    embeddings = model.encode(texts, show_progress_bar=True, normalize_embeddings=True)
    embeddings = np.array(embeddings, dtype=np.float32)

    # 构建 FAISS 索引
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)

    # 持久化（用 Python I/O 避免中文路径问题）
    faiss_path = str(index_dir / "docs.faiss")
    meta_path = str(index_dir / "docs_meta.json")

    # faiss.write_index 不支持非 ASCII 路径，改用 serialize + Python I/O
    byte_array = faiss.serialize_index(index)
    with open(faiss_path, "wb") as f:
        f.write(byte_array)

    # 保存元数据
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ 摄入完成: {len(md_files)} 文件, {len(all_chunks)} chunk")
    logger.info(f"   FAISS 索引: {faiss_path}")
    logger.info(f"   元数据: {meta_path}")

    return {
        "total_files": len(md_files),
        "total_chunks": len(all_chunks),
        "index_path": faiss_path,
    }


# ── 从已摄入索引检索 ─────────────────────────────────────

_doc_index = None
_doc_chunks = None
_doc_model = None


def _load_doc_index():
    """懒加载文档索引和模型"""
    global _doc_index, _doc_chunks, _doc_model

    if _doc_index is not None:
        return

    faiss_path = INDEX_DIR / "docs.faiss"
    meta_path = INDEX_DIR / "docs_meta.json"

    if not faiss_path.exists() or not meta_path.exists():
        logger.info("文档索引不存在，使用知识库数据")
        return

    import json

    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    with open(str(faiss_path), "rb") as f:
        _doc_index = faiss.deserialize_index(np.frombuffer(f.read(), dtype=np.uint8))
    with open(meta_path, "r", encoding="utf-8") as f:
        _doc_chunks = json.load(f)
    _doc_model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    logger.info(f"文档索引已加载: {len(_doc_chunks)} chunk")


def search_documents(
    query: str,
    destination: Optional[str] = None,
    category: Optional[str] = None,
    top_k: int = 3,
) -> list[dict]:
    """
    从已摄入的文档中检索。

    Returns:
        [{"text": ..., "destination": ..., "category": ..., "section": ..., "score": ...}, ...]
    """
    import numpy as np

    _load_doc_index()

    if _doc_index is None or _doc_chunks is None:
        return []

    # 编码查询
    query_vec = _doc_model.encode([query], normalize_embeddings=True)
    query_vec = np.array(query_vec, dtype=np.float32)

    # FAISS 检索
    scores, indices = _doc_index.search(query_vec, top_k * 3)

    results = []
    for idx, score in zip(indices[0], scores[0]):
        if idx < 0 or idx >= len(_doc_chunks):
            continue

        chunk = _doc_chunks[idx]

        # 过滤
        if destination and chunk.get("destination") not in destination:
            continue
        if category and chunk.get("category") != category:
            continue

        results.append({
            "text": chunk["text"],
            "destination": chunk.get("destination", ""),
            "category": chunk.get("category", "basic"),
            "section": chunk.get("section", ""),
            "source": chunk.get("source", ""),
            "score": float(score),
        })

        if len(results) >= top_k:
            break

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = ingest_documents()
    print(f"\n摄入完成: {result}")
