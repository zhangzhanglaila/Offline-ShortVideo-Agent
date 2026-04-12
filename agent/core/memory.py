# -*- coding: utf-8 -*-
"""
Agent记忆系统 - 短期记忆、工作记忆、长期记忆
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from collections import deque
import json
import time
import sqlite3
from pathlib import Path


@dataclass
class MemoryItem:
    """记忆条目"""
    role: str  # "user", "assistant", "system", "tool"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata
        }


@dataclass
class ShortTermMemory:
    """短期记忆 - 对话窗口内的上下文"""
    max_size: int = 20

    def __post_init__(self):
        self._buffer: deque = deque(maxlen=self.max_size)

    def add(self, item: MemoryItem):
        self._buffer.append(item)

    def add_message(self, role: str, content: str, metadata: Dict = None):
        self.add(MemoryItem(
            role=role,
            content=content,
            metadata=metadata or {}
        ))

    def get_recent(self, count: int = 10) -> List[MemoryItem]:
        """获取最近N条记忆"""
        items = list(self._buffer)[-count:]
        return items

    def get_conversation_format(self) -> List[Dict]:
        """获取对话格式（用于LLM）"""
        return [{"role": m.role, "content": m.content} for m in self._buffer]

    def clear(self):
        self._buffer.clear()

    def __len__(self):
        return len(self._buffer)


@dataclass
class WorkingMemory:
    """工作记忆 - 当前任务执行状态"""
    task_id: str
    task_description: str
    current_state: str = "initialized"
    collected_data: Dict[str, Any] = field(default_factory=dict)
    execution_history: List[Dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def add_data(self, key: str, value: Any):
        self.collected_data[key] = value

    def add_step(self, step_type: str, tool: str, params: Dict, result: Any):
        self.execution_history.append({
            "step_type": step_type,
            "tool": tool,
            "params": params,
            "result": str(result)[:500] if result else None,
            "timestamp": time.time()
        })

    def to_context_prompt(self) -> str:
        """转换为上下文提示文本"""
        lines = [
            f"当前任务: {self.task_description}",
            f"任务状态: {self.current_state}",
            "",
            "已收集数据:"
        ]
        for k, v in self.collected_data.items():
            lines.append(f"  - {k}: {str(v)[:200]}")

        if self.execution_history:
            lines.append("")
            lines.append("执行历史:")
            for step in self.execution_history[-3:]:
                lines.append(f"  - [{step['step_type']}] {step['tool']}")

        return "\n".join(lines)


class LongTermMemory:
    """长期记忆 - 持久化知识库（基于SQLite）"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or "data/agent_memory.db"
        self._ensure_db()

    def _ensure_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT,
                category TEXT,
                created_at REAL,
                accessed_at REAL,
                access_count INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                task_summary TEXT,
                outcome TEXT,
                created_at REAL,
                ended_at REAL
            )
        """)
        conn.commit()
        conn.close()

    def store(self, key: str, value: Any, category: str = "general"):
        """存储记忆"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        value_str = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value

        cursor.execute("""
            INSERT OR REPLACE INTO memory (key, value, category, created_at, accessed_at, access_count)
            VALUES (?, ?, ?, ?, ?,
                COALESCE((SELECT access_count FROM memory WHERE key = ?), 0) + 1)
        """, (key, value_str, category, time.time(), time.time(), key))

        conn.commit()
        conn.close()

    def retrieve(self, key: str) -> Optional[Any]:
        """检索记忆"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE memory SET accessed_at = ?, access_count = access_count + 1
            WHERE key = ?
        """, (time.time(), key))

        cursor.execute("SELECT value FROM memory WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()

        if row:
            try:
                return json.loads(row[0])
            except:
                return row[0]
        return None

    def search(self, query: str, category: str = None, limit: int = 10) -> List[Dict]:
        """搜索记忆"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if category:
            cursor.execute("""
                SELECT key, value, category, access_count
                FROM memory
                WHERE category = ? AND (key LIKE ? OR value LIKE ?)
                ORDER BY access_count DESC
                LIMIT ?
            """, (category, f"%{query}%", f"%{query}%", limit))
        else:
            cursor.execute("""
                SELECT key, value, category, access_count
                FROM memory
                WHERE key LIKE ? OR value LIKE ?
                ORDER BY access_count DESC
                LIMIT ?
            """, (f"%{query}%", f"%{query}%", limit))

        rows = cursor.fetchall()
        conn.close()

        return [{"key": r[0], "value": r[1], "category": r[2], "access_count": r[3]} for r in rows]

    def save_session(self, session_id: str, task_summary: str, outcome: str):
        """保存会话记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO sessions (session_id, task_summary, outcome, created_at, ended_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, task_summary, outcome, time.time(), time.time()))

        conn.commit()
        conn.close()


class AgentMemory:
    """Agent统一记忆接口"""

    def __init__(self, db_path: str = None):
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory(db_path)
        self._current_working: Optional[WorkingMemory] = None

    def start_task(self, task_id: str, description: str) -> WorkingMemory:
        """开始新任务"""
        self._current_working = WorkingMemory(
            task_id=task_id,
            task_description=description
        )
        return self._current_working

    @property
    def current_task(self) -> Optional[WorkingMemory]:
        """当前任务"""
        return self._current_working

    def get_context_for_llm(self) -> str:
        """获取LLM上下文"""
        parts = []

        if self._current_working:
            parts.append(self._current_working.to_context_prompt())

        recent = self.short_term.get_recent(5)
        if recent:
            parts.append("\n最近对话:")
            for m in recent:
                role_label = "用户" if m.role == "user" else "助手"
                parts.append(f"- {role_label}: {m.content[:200]}")

        return "\n".join(parts)
