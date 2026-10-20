# -*- coding: utf-8 -*-
"""
AI评测引擎 — LLM-as-Judge评分 + Badcase管理 + 回归测试 + 质量报告
覆盖面试模块：效果评估 / 幻觉检测 / Badcase闭环 / 部署可观测

设计:
  - 离线评测：对已生成脚本/视频进行4维度打分
  - Badcase收集：自动标记低分案例，存储LLM原始输出供分析
  - 回归跑分：固定评测集，每次Prompt变更后批量重跑
  - 质量报告：生成Markdown格式的质量趋势报告
"""
import json
import sqlite3
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import config


# ═══════════════════════════════════════════════════════════════
# 数据库
# ═══════════════════════════════════════════════════════════════

def _init_eval_db():
    db_path = config.DATA_DIR / "eval_results.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER,
            eval_type TEXT NOT NULL DEFAULT 'script',
            scores TEXT NOT NULL,
            badcase_flag INTEGER DEFAULT 0,
            reviewer_notes TEXT,
            raw_llm_response TEXT,
            prompt_version TEXT,
            model_version TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_badcases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER,
            eval_result_id INTEGER,
            issue_category TEXT,
            issue_description TEXT,
            severity TEXT DEFAULT 'medium',
            fixed INTEGER DEFAULT 0,
            fixed_at TIMESTAMP,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (eval_result_id) REFERENCES eval_results(id)
        )
    """)
    conn.commit()
    conn.close()
    return str(db_path)


EVAL_DB_PATH = _init_eval_db()


# ═══════════════════════════════════════════════════════════════
# Evaluator
# ═══════════════════════════════════════════════════════════════

class ScriptEvaluator:
    """LLM-as-Judge 脚本质量评估。

    评估4个维度(每个1-10分):
      - 相关性(relevance): 脚本与选题的契合度
      - 创意性(creativity): 钩子是否有吸引力，内容是否有新意
      - 结构(structure): 分镜分段是否合理，节奏是否恰当
      - 准确性(accuracy): 数据/事实是否准确，有无明显幻觉

    总分40分，单项<4分自动标记badcase。
    """

    DIMENSIONS = {
        "relevance": "脚本内容与选题的关联程度，是否紧扣主题而非泛泛而谈",
        "creativity": "开头钩子的吸引力、内容的新颖程度、表达方式的感染力",
        "structure": "分镜分段的合理性、节奏控制、CTA号召力",
        "accuracy": "数据事实的准确性，是否存在凭空编造的内容（幻觉）",
    }

    def evaluate(self, topic_title: str, category: str, full_script: str,
                 storyboard: list, raw_llm_response: str = "",
                 video_id: int = None) -> Dict:
        """对脚本进行4维度打分。返回完整评估结果。"""
        # 构建评估prompt
        storyboard_summary = json.dumps(storyboard[:3], ensure_ascii=False) if storyboard else "无"
        eval_prompt = f"""你是短视频脚本质量评审专家。请对以下脚本进行4维度评分(1-10分整数)：

【选题】{topic_title}（赛道：{category}）
【脚本】{full_script[:800]}
【分镜概要】{storyboard_summary[:300]}

评分维度：
1. 相关性 — {self.DIMENSIONS['relevance']}
2. 创意性 — {self.DIMENSIONS['creativity']}
3. 结构 — {self.DIMENSIONS['structure']}
4. 准确性 — {self.DIMENSIONS['accuracy']}

请直接输出JSON（不要markdown），格式：
{{"relevance": 8, "creativity": 7, "structure": 8, "accuracy": 6, "comments": "整体评价(50字内)", "issues": ["问题1", "问题2"]}}"""

        scores = None
        comments = ""
        issues = []

        # 调用LLM评分
        try:
            from core.script_module import ScriptModule
            sm = ScriptModule()
            response = sm._call_ollama(eval_prompt, timeout=60)
            if '"error"' in response:
                response = sm._call_cloud_api(eval_prompt)
            if response:
                # 复用JSON提取逻辑
                json_str = sm._extract_json_from_text(response)
                if json_str:
                    parsed = json.loads(json_str)
                    if all(k in parsed for k in ("relevance", "creativity", "structure", "accuracy")):
                        scores = {k: min(10, max(1, int(parsed[k]))) for k in self.DIMENSIONS}
                        comments = parsed.get("comments", "")
                        issues = parsed.get("issues", [])
        except Exception:
            pass

        # 如果LLM评分失败，用启发式评分
        if scores is None:
            scores = self._heuristic_score(topic_title, full_script, storyboard)

        total = sum(scores.values())
        badcase = any(scores[k] < 4 for k in self.DIMENSIONS) or total < 16

        # 存储评估结果
        eval_id = self._save_result(video_id, scores, comments, badcase, raw_llm_response)

        # badcase自动记录
        if badcase:
            for issue in issues:
                self._save_badcase(video_id, eval_id, issue, "high" if total < 12 else "medium")

        return {
            "eval_id": eval_id,
            "scores": scores,
            "total": total,
            "max_total": 40,
            "badcase": badcase,
            "comments": comments,
            "issues": issues,
            "grade": self._grade(total),
        }

    def _heuristic_score(self, topic_title: str, full_script: str, storyboard: list) -> Dict:
        """启发式评分（LLM不可用时的降级方案）。"""
        scores = {"relevance": 5, "creativity": 5, "structure": 5, "accuracy": 5}
        script_lower = full_script.lower()

        # 相关性：选题关键词是否出现在脚本中
        if topic_title and any(ch in full_script for ch in topic_title[:3]):
            scores["relevance"] = 7
        # 创意性：是否包含问句/数字/悬念词
        creative_markers = ["你知道吗", "竟然", "原来", "揭秘", "惊人", "秘密", "?"
                           ]
        if any(m in full_script for m in creative_markers):
            scores["creativity"] = 7
        # 结构：是否有明确分段
        if len(storyboard) >= 3:
            scores["structure"] = 7
        elif len(storyboard) >= 1:
            scores["structure"] = 5
        # 准确性：脚本长度合理
        if len(full_script) > 100:
            scores["accuracy"] = 6
        if len(full_script) > 200:
            scores["accuracy"] = 7

        return scores

    def _grade(self, total: int) -> str:
        if total >= 35:
            return "S (卓越)"
        elif total >= 28:
            return "A (优秀)"
        elif total >= 20:
            return "B (合格)"
        elif total >= 12:
            return "C (待改进)"
        return "D (不合格)"

    def _save_result(self, video_id, scores, comments, badcase, raw_response) -> int:
        try:
            conn = sqlite3.connect(EVAL_DB_PATH)
            cursor = conn.execute(
                "INSERT INTO eval_results (video_id, eval_type, scores, badcase_flag, reviewer_notes, raw_llm_response) "
                "VALUES (?, 'script', ?, ?, ?, ?)",
                (video_id, json.dumps(scores), 1 if badcase else 0, comments, raw_response[:2000] if raw_response else "")
            )
            conn.commit()
            rid = cursor.lastrowid
            conn.close()
            return rid
        except Exception:
            return -1

    def _save_badcase(self, video_id, eval_id, description, severity):
        try:
            conn = sqlite3.connect(EVAL_DB_PATH)
            conn.execute(
                "INSERT INTO eval_badcases (video_id, eval_result_id, issue_category, issue_description, severity) "
                "VALUES (?, ?, 'quality', ?, ?)",
                (video_id, eval_id, description, severity)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# Badcase Collector
# ═══════════════════════════════════════════════════════════════

class BadcaseCollector:
    """Badcase收集与管理 — 驱动Prompt/流程迭代。"""

    def list_badcases(self, limit: int = 50, severity: str = None,
                      fixed: bool = None) -> List[Dict]:
        conn = sqlite3.connect(EVAL_DB_PATH)
        conn.row_factory = sqlite3.Row
        sql = """
            SELECT b.*, e.scores, e.reviewer_notes
            FROM eval_badcases b
            LEFT JOIN eval_results e ON b.eval_result_id = e.id
        """
        conditions = []
        params = []
        if severity:
            conditions.append("b.severity = ?")
            params.append(severity)
        if fixed is not None:
            conditions.append("b.fixed = ?")
            params.append(1 if fixed else 0)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY b.created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_fixed(self, badcase_id: int):
        conn = sqlite3.connect(EVAL_DB_PATH)
        conn.execute("UPDATE eval_badcases SET fixed=1, fixed_at=CURRENT_TIMESTAMP WHERE id=?", (badcase_id,))
        conn.commit()
        conn.close()

    def stats(self) -> Dict:
        conn = sqlite3.connect(EVAL_DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM eval_badcases").fetchone()[0]
        fixed = conn.execute("SELECT COUNT(*) FROM eval_badcases WHERE fixed=1").fetchone()[0]
        by_severity = {}
        for row in conn.execute("SELECT severity, COUNT(*) FROM eval_badcases GROUP BY severity").fetchall():
            by_severity[row[0]] = row[1]
        conn.close()
        return {"total": total, "fixed": fixed, "open": total - fixed, "by_severity": by_severity}


# ═══════════════════════════════════════════════════════════════
# Regression Runner
# ═══════════════════════════════════════════════════════════════

class RegressionRunner:
    """回归测试跑分器 — 固定评测集，每次Prompt/模型变更后批量重跑。

    用法:
      runner = RegressionRunner()
      results = runner.run(test_set, prompt_version="v2.1")
      print(runner.compare("v2.0", "v2.1"))
    """

    def __init__(self):
        self.evaluator = ScriptEvaluator()

    def run(self, test_cases: List[Dict], prompt_version: str = "latest",
            model_version: str = None) -> List[Dict]:
        """批量评估测试集，返回每个case的评估结果。"""
        results = []
        for i, case in enumerate(test_cases):
            print(f"  [{i+1}/{len(test_cases)}] 评估: {case.get('topic_title', '')[:30]}...")
            result = self.evaluator.evaluate(
                topic_title=case.get("topic_title", ""),
                category=case.get("category", ""),
                full_script=case.get("full_script", ""),
                storyboard=case.get("storyboard", []),
                raw_llm_response=case.get("raw_llm_response", ""),
                video_id=case.get("video_id"),
            )
            results.append(result)
        return results

    def compare(self, version_a: str, version_b: str) -> Dict:
        """比较两个Prompt版本的评估结果差异。"""
        conn = sqlite3.connect(EVAL_DB_PATH)
        conn.row_factory = sqlite3.Row
        a_rows = conn.execute(
            "SELECT scores FROM eval_results WHERE prompt_version=? ORDER BY id", (version_a,)
        ).fetchall()
        b_rows = conn.execute(
            "SELECT scores FROM eval_results WHERE prompt_version=? ORDER BY id", (version_b,)
        ).fetchall()
        conn.close()

        if not a_rows or not b_rows:
            return {"error": "缺少对比数据", "a_count": len(a_rows), "b_count": len(b_rows)}

        avg_a = self._avg_scores(a_rows)
        avg_b = self._avg_scores(b_rows)

        return {
            "version_a": {"version": version_a, "count": len(a_rows), "avg": avg_a},
            "version_b": {"version": version_b, "count": len(b_rows), "avg": avg_b},
            "delta": {k: round(avg_b.get(k, 0) - avg_a.get(k, 0), 2) for k in avg_a},
        }

    def _avg_scores(self, rows) -> Dict:
        dims = ["relevance", "creativity", "structure", "accuracy"]
        totals = {d: 0.0 for d in dims}
        count = 0
        for r in rows:
            try:
                s = json.loads(r["scores"]) if isinstance(r["scores"], str) else r["scores"]
                for d in dims:
                    totals[d] += s.get(d, 5)
                count += 1
            except Exception:
                continue
        if count == 0:
            return totals
        return {d: round(totals[d] / count, 2) for d in dims}


# ═══════════════════════════════════════════════════════════════
# Quality Report
# ═══════════════════════════════════════════════════════════════

def generate_quality_report(days: int = 30) -> str:
    """生成Markdown格式的质量趋势报告。"""
    conn = sqlite3.connect(EVAL_DB_PATH)
    conn.row_factory = sqlite3.Row

    # 总体统计
    total_evals = conn.execute("SELECT COUNT(*) FROM eval_results").fetchone()[0]
    total_badcases = conn.execute("SELECT COUNT(*) FROM eval_badcases").fetchone()[0]
    open_badcases = conn.execute("SELECT COUNT(*) FROM eval_badcases WHERE fixed=0").fetchone()[0]

    # 平均分趋势（按日期）
    avg_scores = conn.execute("""
        SELECT DATE(created_at) as day, COUNT(*) as count,
               AVG(CAST(json_extract(scores, '$.relevance') AS REAL)) as avg_rel,
               AVG(CAST(json_extract(scores, '$.creativity') AS REAL)) as avg_cre,
               AVG(CAST(json_extract(scores, '$.structure') AS REAL)) as avg_str,
               AVG(CAST(json_extract(scores, '$.accuracy') AS REAL)) as avg_acc
        FROM eval_results
        WHERE created_at >= DATE('now', ?)
        GROUP BY DATE(created_at) ORDER BY day DESC
    """, (f"-{days} days",)).fetchall()

    # 评分等级分布
    grade_dist = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0}
    for row in conn.execute("SELECT scores FROM eval_results").fetchall():
        try:
            s = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            total = sum(s.values())
            if total >= 35: grade_dist["S"] += 1
            elif total >= 28: grade_dist["A"] += 1
            elif total >= 20: grade_dist["B"] += 1
            elif total >= 12: grade_dist["C"] += 1
            else: grade_dist["D"] += 1
        except Exception:
            pass

    conn.close()

    report = f"""# 脚本质量报告 ({datetime.now().strftime('%Y-%m-%d')})

## 总览
| 指标 | 数值 |
|------|------|
| 总评估次数 | {total_evals} |
| Badcase总数 | {total_badcases} |
| 未解决Badcase | {open_badcases} |
| 解决率 | {((total_badcases - open_badcases) / max(total_badcases, 1) * 100):.1f}% |

## 评分等级分布
| 等级 | 数量 | 占比 |
|------|------|------|
| S (≥35) | {grade_dist['S']} | {grade_dist['S'] / max(total_evals, 1) * 100:.1f}% |
| A (28-34) | {grade_dist['A']} | {grade_dist['A'] / max(total_evals, 1) * 100:.1f}% |
| B (20-27) | {grade_dist['B']} | {grade_dist['B'] / max(total_evals, 1) * 100:.1f}% |
| C (12-19) | {grade_dist['C']} | {grade_dist['C'] / max(total_evals, 1) * 100:.1f}% |
| D (<12) | {grade_dist['D']} | {grade_dist['D'] / max(total_evals, 1) * 100:.1f}% |

## 日均评分趋势（近{days}天）
| 日期 | 样本数 | 相关性 | 创意性 | 结构 | 准确性 | 均分 |
|------|--------|--------|--------|------|--------|------|
"""
    for row in avg_scores:
        avg_total = (row["avg_rel"] + row["avg_cre"] + row["avg_str"] + row["avg_acc"]) / 4
        report += f"| {row['day']} | {row['count']} | {row['avg_rel']:.1f} | {row['avg_cre']:.1f} | {row['avg_str']:.1f} | {row['avg_acc']:.1f} | {avg_total:.1f} |\n"

    report += f"""
## 改进建议
"""
    if grade_dist['D'] + grade_dist['C'] > total_evals * 0.3:
        report += "- ⚠️ 低分占比偏高，建议检查LLM Prompt质量和模型版本\n"
    if open_badcases > total_badcases * 0.5:
        report += "- ⚠️ Badcase解决率偏低，建议优先处理高频issue\n"
    if total_evals < 10:
        report += "- 📝 评估样本较少，建议扩大评测集规模\n"
    report += "- 定期运行回归测试，监控Prompt变更后的评分变化\n"

    return report


# ═══════════════════════════════════════════════════════════════
# 单例
# ═══════════════════════════════════════════════════════════════

_evaluator_instance = None


def get_evaluator() -> ScriptEvaluator:
    global _evaluator_instance
    if _evaluator_instance is None:
        _evaluator_instance = ScriptEvaluator()
    return _evaluator_instance
