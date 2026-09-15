"""
混合检索引擎 — FAISS 语义检索 + 规范 BM25 + GPS 真实邻近图谱。
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter, defaultdict
from typing import Optional

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from app.knowledge.destinations import DESTINATIONS

logger = logging.getLogger(__name__)

# ── 全局单例（延迟初始化）────────────────────────────────────

_model: Optional[SentenceTransformer] = None
_index: Optional[faiss.IndexFlatIP] = None
_chunks: list[dict] = []

# BM25 倒排：token → [(chunk_id, tf), ...]；另存 doc_len / avgdl / df
_bm25_postings: dict[str, list[tuple[int, float]]] = {}
_doc_len: dict[int, int] = {}
_avgdl: float = 1.0
_df: dict[str, int] = {}
_BM25_K1 = 1.5
_BM25_B = 0.75

_init_lock = threading.Lock()

# 知识图谱：景点名 → 关系（nearby 由真实 GPS 距离算出）
_knowledge_graph: dict[str, dict] = {}
# 邻近半径（公里）：同城 2.5km 内视为可步行/短途可达
_NEARBY_RADIUS_KM = 2.5
_MAX_NEARBY = 5


def _tokenize(text: str) -> list[str]:
    """中文按字 + 英文按词；对短中文查询足够且零依赖。"""
    tokens: list[str] = []
    for word in re.findall(r"[a-zA-Z]+", text.lower()):
        tokens.append(word)
    for char in text:
        if "一" <= char <= "鿿":
            tokens.append(char)
    return tokens


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _build_bm25_index():
    """构建规范 BM25：存原始 tf，查询时用 k1/b 饱和公式。"""
    global _bm25_postings, _doc_len, _avgdl, _df
    _bm25_postings = defaultdict(list)
    _doc_len = {}
    _df = defaultdict(int)

    if not _chunks:
        _avgdl = 1.0
        return

    total_len = 0
    for chunk in _chunks:
        tokens = _tokenize(chunk["text"])
        length = max(1, len(tokens))
        _doc_len[chunk["id"]] = length
        total_len += length
        tf = Counter(tokens)
        for token, count in tf.items():
            _bm25_postings[token].append((chunk["id"], float(count)))
            _df[token] += 1

    _avgdl = total_len / len(_chunks)


def _bm25_search(query: str, top_k: int = 20) -> list[tuple[int, float]]:
    """Okapi BM25 打分。"""
    tokens = _tokenize(query)
    if not tokens or not _chunks:
        return []

    n = len(_chunks)
    scores: dict[int, float] = defaultdict(float)

    for token in tokens:
        postings = _bm25_postings.get(token)
        if not postings:
            continue
        df = _df.get(token, 0)
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        for chunk_id, tf in postings:
            dl = _doc_len.get(chunk_id, 1)
            denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / _avgdl)
            scores[chunk_id] += idf * (tf * (_BM25_K1 + 1) / denom)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


def _get_attraction_coords(name: str) -> Optional[tuple[float, float]]:
    try:
        from app.tools.route_optimizer import ATTRACTION_COORDS
        return ATTRACTION_COORDS.get(name)
    except Exception:
        return None


def _build_knowledge_graph():
    """
    用真实 GPS 坐标建邻近关系（阈值半径内按距离排序），
    同类型关系保留。不再用“列表相邻”冒充图谱。
    """
    global _knowledge_graph
    _knowledge_graph = {}

    for dest_name, dest_data in DESTINATIONS.items():
        attractions = dest_data.get("attractions", [])
        type_groups: dict[str, list[str]] = defaultdict(list)
        for attr in attractions:
            type_groups[attr["type"]].append(attr["name"])

        coords: dict[str, tuple[float, float]] = {}
        for attr in attractions:
            c = _get_attraction_coords(attr["name"])
            if c:
                coords[attr["name"]] = c

        for attr in attractions:
            name = attr["name"]
            nearby: list[tuple[str, float]] = []
            if name in coords:
                lat1, lng1 = coords[name]
                for other, (lat2, lng2) in coords.items():
                    if other == name:
                        continue
                    dist = _haversine_km(lat1, lng1, lat2, lng2)
                    if dist <= _NEARBY_RADIUS_KM:
                        nearby.append((other, dist))
                nearby.sort(key=lambda x: x[1])

            same_type = [n for n in type_groups.get(attr["type"], []) if n != name][:3]
            _knowledge_graph[name] = {
                "destination": dest_name,
                "type": attr["type"],
                "nearby": [n for n, _ in nearby[:_MAX_NEARBY]],
                "nearby_km": [round(d, 2) for _, d in nearby[:_MAX_NEARBY]],
                "same_type": same_type,
                "ticket": attr.get("ticket", ""),
                "duration": attr.get("duration", ""),
                "has_gps": name in coords,
            }


def _init_index():
    """延迟初始化所有索引（线程安全，double-check locking）"""
    global _model, _index, _chunks

    if _index is not None:
        return

    with _init_lock:
        if _index is not None:
            return

        logger.info("正在构建混合检索索引...")
        _model = SentenceTransformer("BAAI/bge-small-zh-v1.5")

        _chunks = []
        chunk_id = 0
        for dest_name, dest_data in DESTINATIONS.items():
            info_parts = [
                f"目的地：{dest_name}（{dest_data['country']}）",
                f"货币：{dest_data['currency']}，语言：{dest_data['language']}，时区：{dest_data['timezone']}",
                f"最佳季节：{dest_data['best_season']}",
                f"签证：{dest_data['visa']}",
                f"电源：{dest_data['electricity']}",
            ]
            _chunks.append({
                "id": chunk_id,
                "text": " | ".join(info_parts),
                "destination": dest_name,
                "category": "basic",
            })
            chunk_id += 1

            for attr in dest_data.get("attractions", []):
                _chunks.append({
                    "id": chunk_id,
                    "text": f"【景点】{dest_name} - {attr['name']}（{attr['type']}）：门票{attr['ticket']}，游览时间{attr['duration']}，提示：{attr['tip']}",
                    "destination": dest_name,
                    "category": "attraction",
                    "entity": attr["name"],
                })
                chunk_id += 1

            for rest in dest_data.get("restaurants", []):
                price = rest.get("budget") or rest.get("price", "未知")
                name = rest.get("name", "")
                if not name:
                    continue
                _chunks.append({
                    "id": chunk_id,
                    "text": f"【餐厅】{dest_name} - {name}（{rest.get('type', '')}）：人均{price}，必点{rest.get('must_try', '')}，提示：{rest.get('tip', '')}",
                    "destination": dest_name,
                    "category": "restaurant",
                    "entity": name,
                })
                chunk_id += 1

            transport = dest_data.get("transportation", {}) or dest_data.get("transport", {})
            if transport:
                airport_to_city = transport.get("airport_to_city", "")
                daily_transport = transport.get("daily_transport") or transport.get("daily_pass", "")
                tip = transport.get("tip") or transport.get("tips", "")
                _chunks.append({
                    "id": chunk_id,
                    "text": f"【交通】{dest_name} - 机场到市区：{airport_to_city}，市内交通：{daily_transport}，提示：{tip}",
                    "destination": dest_name,
                    "category": "transport",
                })
                chunk_id += 1

            budget = dest_data.get("budget", {}) or dest_data.get("budget_estimate", {})
            if budget:
                budget_parts = []
                if "budget_per_day_usd" in budget:
                    budget_parts.append(f"经济：${budget['budget_per_day_usd']}/天")
                    if "comfort_per_day_usd" in budget:
                        budget_parts.append(f"舒适：${budget['comfort_per_day_usd']}/天")
                    if "luxury_per_day_usd" in budget:
                        budget_parts.append(f"豪华：${budget['luxury_per_day_usd']}/天")
                    if "note" in budget:
                        budget_parts.append(f"备注：{budget['note']}")
                else:
                    for tier, info in budget.items():
                        if isinstance(info, dict) and "daily" in info:
                            budget_parts.append(f"{tier}：{info['daily']}（{info.get('note', '')}）")
                if budget_parts:
                    _chunks.append({
                        "id": chunk_id,
                        "text": f"【预算】{dest_name} - " + " | ".join(budget_parts),
                        "destination": dest_name,
                        "category": "budget",
                    })
                    chunk_id += 1

            safety = dest_data.get("safety", {})
            if safety:
                emergency = safety.get("emergency", "")
                if isinstance(emergency, dict):
                    police = emergency.get("police", "")
                    ambulance = emergency.get("ambulance", "")
                else:
                    police = str(emergency)
                    ambulance = "同上"
                embassy = safety.get("embassy", "")
                tips = safety.get("tips", [])
                tips_str = "；".join(tips[:3]) if isinstance(tips, list) and tips else str(tips)
                _chunks.append({
                    "id": chunk_id,
                    "text": f"【安全】{dest_name} - 报警电话：{police}，急救电话：{ambulance}，大使馆：{embassy}，提示：{tips_str}",
                    "destination": dest_name,
                    "category": "safety",
                })
                chunk_id += 1

        texts = [c["text"] for c in _chunks]
        embeddings = _model.encode(texts, normalize_embeddings=True)
        embeddings = np.array(embeddings, dtype="float32")

        dim = embeddings.shape[1]
        _index = faiss.IndexFlatIP(dim)
        _index.add(embeddings)

        _build_bm25_index()
        _build_knowledge_graph()

        logger.info(
            f"混合检索索引构建完成：{len(_chunks)} chunk | "
            f"{len(_bm25_postings)} token | "
            f"{len(_knowledge_graph)} 实体关系"
        )


_CATEGORY_ALIASES = {
    "food": "restaurant",
    "restaurants": "restaurant",
    "dining": "restaurant",
    "eat": "restaurant",
    "景点": "attraction",
    "attractions": "attraction",
    "交通": "transport",
    "住宿": "budget",
    "hotel": "budget",
    "安全": "safety",
    "基本信息": "basic",
    "info": "basic",
}


def is_destination_covered(destination: Optional[str]) -> bool:
    """
    知识库是否覆盖该目的地。

    用于检索入口的前置校验。库外城市（如「梅州五华」）走向量检索会命中语义
    相近的其它城市内容（吉隆坡 / 孟买 / 布宜诺斯艾利斯…）——内容为真但与查询
    无关，且因为没有假事实，比幻觉更难被发现。
    详见 docs/缺陷记录_检索静默失败.md
    """
    if not destination or not destination.strip():
        return True  # 未指定目的地 → 不做覆盖校验，沿用原有不过滤的行为
    dest = destination.strip()
    return any(dest in name or name in dest for name in DESTINATIONS)


def search_knowledge(
    query: str,
    destination: Optional[str] = None,
    category: Optional[str] = None,
    top_k: int = 5,
    hybrid: bool = True,
) -> list[dict]:
    """混合检索：FAISS 语义 + Okapi BM25 加权融合。"""
    if category:
        category = _CATEGORY_ALIASES.get(category.lower(), category.lower())

    # 覆盖校验（总闸）：kNN 没有「拒答」能力，库外目的地必然返回无关城市的内容。
    # 这里直接跳过向量/BM25 检索并提前返回；用户上传的文档仍可命中（见下方）。
    skip_vector = not is_destination_covered(destination)
    if skip_vector:
        logger.info(f"知识库未覆盖「{destination}」，跳过向量/BM25 检索")
        ranked = []
    else:
        _init_index()

        query_vec = _model.encode([query], normalize_embeddings=True).astype("float32")
        search_k = min(top_k * 4, len(_chunks))
        semantic_scores, semantic_indices = _index.search(query_vec, search_k)

        semantic_results = {}
        for score, idx in zip(semantic_scores[0], semantic_indices[0]):
            if idx >= 0:
                semantic_results[int(idx)] = float(score)

        if hybrid:
            bm25_results = _bm25_search(query, top_k=top_k * 4)
            if bm25_results:
                max_bm25 = max(s for _, s in bm25_results)
                bm25_normalized = {idx: s / max_bm25 for idx, s in bm25_results}
            else:
                bm25_normalized = {}

            alpha = 0.7
            beta = 0.3
            all_indices = set(semantic_results.keys()) | set(bm25_normalized.keys())
            combined_scores = {}
            for idx in all_indices:
                combined_scores[idx] = (
                    alpha * semantic_results.get(idx, 0) + beta * bm25_normalized.get(idx, 0)
                )
        else:
            combined_scores = semantic_results

        ranked = sorted(combined_scores.items(), key=lambda x: x[1], reverse=True)

    results = []
    for idx, score in ranked:
        chunk = _chunks[idx]
        if destination and destination not in chunk["destination"] and chunk["destination"] not in destination:
            continue
        if category and chunk["category"] != category:
            continue
        results.append({
            "text": chunk["text"],
            "destination": chunk["destination"],
            "category": chunk["category"],
            "score": round(score, 4),
            "source": "knowledge_base",
        })
        if len(results) >= top_k:
            break

    if len(results) < top_k:
        try:
            from app.knowledge.ingest import search_documents
            doc_results = search_documents(
                query,
                destination=destination,
                category=category,
                top_k=top_k - len(results),
            )
            for dr in doc_results:
                if not any(r["text"][:50] in dr["text"] for r in results):
                    dr["source"] = "document"
                    results.append(dr)
        except Exception as e:
            logger.warning(f"文档索引检索失败: {e}")

    return results[:top_k]


def get_attraction_relations(attraction_name: str) -> Optional[dict]:
    _init_index()
    return _knowledge_graph.get(attraction_name)


def search_with_context(
    query: str,
    destination: str,
    category: Optional[str] = None,
    top_k: int = 3,
) -> str:
    results = search_knowledge(query, destination=destination, category=category, top_k=top_k)
    if not results:
        return "未找到相关信息。"

    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"{i}. {r['text']}")
        entity_match = re.search(r"【景点】\S+ - (.+?)（", r["text"])
        if entity_match:
            entity_name = entity_match.group(1)
            relations = get_attraction_relations(entity_name)
            if relations:
                if relations.get("nearby"):
                    pairs = []
                    for name, km in zip(relations["nearby"], relations.get("nearby_km", [])):
                        pairs.append(f"{name}({km}km)" if km is not None else name)
                    parts.append(f"   ↳ 附近景点（GPS≤{_NEARBY_RADIUS_KM}km）：{'、'.join(pairs)}")
                if relations.get("same_type"):
                    parts.append(f"   ↳ 同类景点：{'、'.join(relations['same_type'])}")

    return "\n".join(parts)


def format_search_results(results: list[dict]) -> str:
    if not results:
        return "未找到相关信息。"
    return "\n".join(f"{i}. {r['text']}" for i, r in enumerate(results, 1))
