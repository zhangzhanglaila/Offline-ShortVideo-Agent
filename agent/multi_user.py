# -*- coding: utf-8 -*-
"""
多用户支持模块
"""
import hashlib
import secrets
import time
import sqlite3
from typing import Dict, Optional, Any
from dataclasses import dataclass
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class User:
    """用户"""
    user_id: str
    username: str
    password_hash: str
    created_at: float
    last_login: float
    is_active: bool = True


class MultiUserManager:
    """
    多用户管理器

    支持：
    - 用户注册/登录
    - Token认证
    - 用户隔离（每个用户独立的记忆和配置）
    """

    def __init__(self, db_path: str = "data/users.db"):
        self.db_path = db_path
        self._ensure_db()
        self._tokens: Dict[str, str] = {}  # token -> user_id
        self._users: Dict[str, User] = {}    # user_id -> User (缓存)

    def _ensure_db(self):
        """确保数据库存在"""
        import sqlite3
        from pathlib import Path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at REAL,
                last_login REAL,
                is_active INTEGER DEFAULT 1
            )
        """)
        conn.commit()
        conn.close()

    def _hash_password(self, password: str, salt: str) -> str:
        """哈希密码"""
        return hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            100000
        ).hex()

    def _generate_salt(self) -> str:
        """生成盐值"""
        return secrets.token_hex(32)

    def register(self, username: str, password: str) -> Dict:
        """
        注册新用户

        Returns:
            {'success': True, 'user_id': ...} 或 {'success': False, 'error': ...}
        """
        import sqlite3

        # 检查用户名是否存在
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
        if cursor.fetchone():
            conn.close()
            return {"success": False, "error": "用户名已存在"}

        # 创建用户
        user_id = secrets.token_hex(16)
        salt = self._generate_salt()
        password_hash = self._hash_password(password, salt)
        created_at = time.time()

        cursor.execute("""
            INSERT INTO users (user_id, username, password_hash, salt, created_at, last_login)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, username, password_hash, salt, created_at, created_at))

        conn.commit()
        conn.close()

        return {"success": True, "user_id": user_id}

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """
        认证用户，返回token

        Returns:
            token字符串或None
        """
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, password_hash, salt, is_active
            FROM users WHERE username = ?
        """, (username,))

        row = cursor.fetchone()
        if not row:
            conn.close()
            return None

        user_id, stored_hash, salt, is_active = row
        if not is_active:
            conn.close()
            return None

        # 验证密码
        if self._hash_password(password, salt) != stored_hash:
            conn.close()
            return None

        # 生成token
        token = secrets.token_hex(32)
        self._tokens[token] = user_id

        # 更新最后登录时间
        cursor.execute("UPDATE users SET last_login = ? WHERE user_id = ?",
                     (time.time(), user_id))
        conn.commit()
        conn.close()

        return token

    def verify_token(self, token: str) -> Optional[str]:
        """
        验证token，返回user_id

        Returns:
            user_id或None
        """
        # 先检查内存缓存
        if token in self._tokens:
            return self._tokens[token]

        # 可以扩展：从数据库验证
        return None

    def revoke_token(self, token: str) -> bool:
        """撤销token"""
        if token in self._tokens:
            del self._tokens[token]
            return True
        return False

    def get_user_info(self, user_id: str) -> Optional[Dict]:
        """获取用户信息"""
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, username, created_at, last_login, is_active
            FROM users WHERE user_id = ?
        """, (user_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return {
            "user_id": row[0],
            "username": row[1],
            "created_at": row[2],
            "last_login": row[3],
            "is_active": bool(row[4])
        }

    def list_users(self) -> list:
        """列出所有用户（管理员用）"""
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, username, created_at, last_login, is_active
            FROM users ORDER BY created_at DESC
        """)

        rows = cursor.fetchall()
        conn.close()

        return [{
            "user_id": r[0],
            "username": r[1],
            "created_at": r[2],
            "last_login": r[3],
            "is_active": bool(r[4])
        } for r in rows]


# 全局单例
_user_manager = None


def get_user_manager() -> MultiUserManager:
    """获取用户管理器单例"""
    global _user_manager
    if _user_manager is None:
        _user_manager = MultiUserManager()
    return _user_manager
