"""
Build Store - SQLite persistence layer for DevOps build analysis
Handles processed builds tracking and analysis history storage
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List
import json
from dataclasses import dataclass


@dataclass
class AnalysisResult:
    """Structured output from build analysis"""
    build_id: str
    build_name: str
    status: str
    error_quote: str
    explanation: str
    fix_steps: List[str]
    severity: str
    timestamp: datetime
    
    def to_dict(self) -> dict:
        return {
            "build_id": self.build_id,
            "build_name": self.build_name,
            "status": self.status,
            "error_quote": self.error_quote,
            "explanation": self.explanation,
            "fix_steps": self.fix_steps,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat()
        }


class BuildStore:
    """SQLite-backed persistence for build processing state and analysis history"""
    
    def __init__(self, db_path: str = "builds.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS build_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    build_id TEXT NOT NULL,
                    build_name TEXT,
                    status TEXT NOT NULL,
                    error_quote TEXT,
                    explanation TEXT,
                    fix_steps TEXT,
                    severity TEXT,
                    timestamp TEXT NOT NULL,
                    log_preview TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processing_queue (
                    build_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS failure_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    build_id TEXT,
                    error_message TEXT,
                    error_type TEXT,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_build_id ON build_history(build_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON build_history(timestamp)")
            conn.commit()
    
    def save_analysis(self, result: AnalysisResult, log_preview: str):
        """Save analysis result to build history"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO build_history 
                (build_id, build_name, status, error_quote, explanation, fix_steps, severity, timestamp, log_preview)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.build_id,
                result.build_name,
                result.status,
                result.error_quote,
                result.explanation,
                json.dumps(result.fix_steps),
                result.severity,
                result.timestamp.isoformat(),
                log_preview
            ))
            conn.commit()
    
    def get_recent_history(self, limit: int = 10) -> List[dict]:
        """Retrieve recent build analysis history"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM build_history ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_history_count(self) -> int:
        """Get total count of analyzed builds in history"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM build_history")
            return cursor.fetchone()[0]
    
    def has_build(self, build_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM build_history WHERE build_id = ?", (build_id,))
            return cursor.fetchone()[0] > 0
    
    def is_recently_processed(self, build_id: str, ttl_seconds: int = 300) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT started_at FROM processing_queue WHERE build_id = ?",
                (build_id,)
            )
            row = cursor.fetchone()
            if not row:
                return False
            started_at = datetime.fromisoformat(row[0])
            age_seconds = (datetime.now() - started_at).total_seconds()
            if age_seconds > ttl_seconds:
                conn.execute("DELETE FROM processing_queue WHERE build_id = ?", (build_id,))
                conn.commit()
                return False
            return True
    
    def mark_processing(self, build_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO processing_queue (build_id, started_at) VALUES (?, ?)",
                (build_id, datetime.now().isoformat())
            )
            conn.commit()
    
    def unmark_processing(self, build_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM processing_queue WHERE build_id = ?", (build_id,))
            conn.commit()
    
    def log_failure(self, build_id: str, error_message: str, error_type: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO failure_log (build_id, error_message, error_type, timestamp) VALUES (?, ?, ?, ?)",
                (build_id, error_message, error_type, datetime.now().isoformat())
            )
            conn.commit()
    
    def get_metrics(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM build_history").fetchone()[0]
            failures = conn.execute("SELECT COUNT(*) FROM failure_log").fetchone()[0]
            last_error = conn.execute("SELECT error_message, timestamp FROM failure_log ORDER BY id DESC LIMIT 1").fetchone()
            return {
                "total_builds": total,
                "failed_analyses": failures,
                "last_error": {"message": last_error[0], "timestamp": last_error[1]} if last_error else None
            }
