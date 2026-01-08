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
        """Initialize database schema with build_history table"""
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
        """Check if build has already been analyzed (for deduplication)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM build_history WHERE build_id = ?",
                (build_id,)
            )
            return cursor.fetchone()[0] > 0
