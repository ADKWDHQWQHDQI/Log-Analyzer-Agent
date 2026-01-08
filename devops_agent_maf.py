"""
DevOps Log Analysis Agent - SK Orchestration + Ollama Runtime
Properly uses Semantic Kernel's OllamaChatCompletion connector (no raw API bypass)
"""

import asyncio
import sys
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.ollama import OllamaChatCompletion
from semantic_kernel.functions import kernel_function
from semantic_kernel.contents import ChatHistory
from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, field
import json
from flask import Flask, request, jsonify
from pyngrok import ngrok
import requests
import os
import base64
from dotenv import load_dotenv
import sqlite3
from pathlib import Path

load_dotenv()

# ------------------- Configuration -------------------
NGROK_AUTHTOKEN = "2pZMxvKMAfJyQIgvp8KfpryFoG3_iq3iqgMNNNUVCor6QazJ"
MODEL = "llama3.2:3b"
AZURE_DEVOPS_ORG = "sandeepkuruva"
AZURE_DEVOPS_PROJECT = "AI-Enhanced Productivity Metric Calculator"

AZURE_DEVOPS_PAT = os.environ.get('AZURE_DEVOPS_PAT')
TEAMS_WEBHOOK_URL = os.environ.get('TEAMS_WEBHOOK_URL')

if not AZURE_DEVOPS_PAT:
    print(" [WARNING] AZURE_DEVOPS_PAT not set. Log fetching will fail.")
    print("   Set it with: set AZURE_DEVOPS_PAT=your_pat_here")

if not TEAMS_WEBHOOK_URL:
    print("  [WARNING] TEAMS_WEBHOOK_URL not set. Teams notifications disabled.")
    print("   Set it with: set TEAMS_WEBHOOK_URL=your_webhook_here")
# ----------------------------------------------------


# ============================================================================
# DATA MODELS (Structured)
# ============================================================================

@dataclass
class BuildEvent:
    build_id: str
    build_name: str
    status: str
    logs: str
    timestamp: datetime
    resource: dict = field(default_factory=dict)

@dataclass
class AnalysisResult:
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


# ============================================================================
# PERSISTENCE LAYER
# ============================================================================

class BuildStore:
    def __init__(self, db_path: str = "builds.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_builds (
                    build_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                )
            """)
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
    
    def is_processed(self, build_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM processed_builds WHERE build_id = ?", (build_id,))
            return cursor.fetchone() is not None
    
    def mark_processed(self, build_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_builds (build_id, processed_at) VALUES (?, ?)",
                (build_id, datetime.now().isoformat())
            )
            conn.commit()
    
    def save_analysis(self, result: AnalysisResult, log_preview: str):
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
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM build_history ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_history_count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM build_history")
            return cursor.fetchone()[0]


app = Flask(__name__)
store = BuildStore()


# ============================================================================
# SK KERNEL + OLLAMA CHAT COMPLETION (Proper Integration)
# ============================================================================

print("Initializing SK with OllamaChatCompletion...")
kernel = Kernel()

chat_service = OllamaChatCompletion(
    ai_model_id=MODEL,
    host="http://localhost:11434"
)
kernel.add_service(chat_service)

print(f"✓ SK + Ollama ({MODEL}) connected\n")


# ============================================================================
# DEVOPS LOG ANALYSIS AGENT (Proper Agent Boundary)
# ============================================================================

class DevOpsLogAgent:
    def __init__(self, kernel: Kernel, store: BuildStore):
        self.kernel = kernel
        self.store = store
        
    async def _fetch_logs(self, build_id: str, resource: dict) -> str:
        logs_list_url = resource.get('logs', {}).get('url')
        if not logs_list_url:
            logs_list_url = f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/{AZURE_DEVOPS_PROJECT}/_apis/build/builds/{build_id}/logs?api-version=7.1"
        
        auth = base64.b64encode(f":{AZURE_DEVOPS_PAT}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
        
        try:
            response = requests.get(logs_list_url, headers=headers, timeout=10)
            if response.status_code == 200:
                logs = response.json().get('value', [])
                print(f"[LOG FETCH] Found {len(logs)} log entries")
                
                main_logs = [log for log in logs if log.get('type') == 'Container'] or logs
                full_log = ""
                
                for log in main_logs[:3]:
                    log_id = log['id']
                    log_content_url = f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/{AZURE_DEVOPS_PROJECT}/_apis/build/builds/{build_id}/logs/{log_id}?api-version=7.1"
                    headers['Accept'] = "text/plain"
                    
                    content_response = requests.get(log_content_url, headers=headers, timeout=10)
                    if content_response.status_code == 200:
                        full_log += content_response.text + "\n\n"
                        print(f"[LOG FETCH] Retrieved log {log_id} ({len(content_response.text)} chars)")
                
                return full_log if full_log else json.dumps(resource, indent=2)
            else:
                print(f"[ERROR] Log fetch failed: {response.status_code}")
                return json.dumps(resource, indent=2)
        except Exception as e:
            print(f"[ERROR] Log fetch exception: {str(e)}")
            return json.dumps(resource, indent=2)
    
    async def _analyze_logs(self, event: BuildEvent) -> AnalysisResult:
        if event.status not in ['failed', 'partiallySucceeded']:
            return AnalysisResult(
                build_id=event.build_id,
                build_name=event.build_name,
                status=event.status,
                error_quote="",
                explanation="Build succeeded. No action required.",
                fix_steps=[],
                severity="success",
                timestamp=event.timestamp
            )
        
        print(f"[SK-OLLAMA] Analyzing {len(event.logs)} chars...")
        
        log_snippet = event.logs[-2000:] if len(event.logs) > 2000 else event.logs
        
        prompt = f"""You are a DevOps expert analyzing Azure DevOps build failures.

RULES:
1. Quote the EXACT error from the log
2. Explain what it means (1-2 sentences)
3. Provide 3 specific fix steps (copy-paste ready commands where possible)
4. Classify severity: critical, high, medium, low
5. Keep under 150 words

Build Status: {event.status}
Log:
{log_snippet}

Return in format:
ERROR: <exact error quote>
EXPLANATION: <what it means>
SEVERITY: <critical|high|medium|low>
FIXES:
1. <step 1>
2. <step 2>
3. <step 3>"""
        
        chat_history = ChatHistory()
        chat_history.add_user_message(prompt)
        
        response = await self.kernel.get_service(type=OllamaChatCompletion).get_chat_message_content(
            chat_history=chat_history,
            settings=self.kernel.get_prompt_execution_settings_from_service_id(service_id=MODEL)
        )
        
        if response is None:
            return AnalysisResult(
                build_id=event.build_id,
                build_name=event.build_name,
                status=event.status,
                error_quote="",
                explanation="Error: No response from AI model",
                fix_steps=[],
                severity="unknown",
                timestamp=event.timestamp
            )
        
        analysis_text = str(response.content).strip()
        print(f"[SK-OLLAMA] Generated {len(analysis_text)} chars\n")
        
        error_quote = ""
        explanation = ""
        severity = "medium"
        fix_steps = []
        
        for line in analysis_text.split('\n'):
            line = line.strip()
            if line.startswith('ERROR:'):
                error_quote = line.replace('ERROR:', '').strip()
            elif line.startswith('EXPLANATION:'):
                explanation = line.replace('EXPLANATION:', '').strip()
            elif line.startswith('SEVERITY:'):
                severity = line.replace('SEVERITY:', '').strip().lower()
            elif line and line[0].isdigit() and '.' in line[:3]:
                fix_steps.append(line[line.index('.')+1:].strip())
        
        if not explanation:
            explanation = analysis_text
        
        return AnalysisResult(
            build_id=event.build_id,
            build_name=event.build_name,
            status=event.status,
            error_quote=error_quote,
            explanation=explanation,
            fix_steps=fix_steps,
            severity=severity,
            timestamp=event.timestamp
        )
    
    async def _send_teams_notification(self, result: AnalysisResult):
        if result.severity == "success" or not TEAMS_WEBHOOK_URL:
            return
        
        print(f"[TEAMS] Sending notification for build {result.build_id}...")
        
        fix_steps_text = "\n".join([f"{i+1}. {step}" for i, step in enumerate(result.fix_steps)])
        analysis_text = f"**Error:** {result.error_quote}\n\n**Explanation:** {result.explanation}\n\n**Fixes:**\n{fix_steps_text}"
        
        teams_payload = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.2",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"🔴 Build Failure: {result.build_name}",
                            "weight": "bolder",
                            "size": "medium",
                            "color": "attention"
                        },
                        {
                            "type": "TextBlock",
                            "text": f"Build ID: {result.build_id} | Status: {result.status} | Severity: {result.severity.upper()}",
                            "isSubtle": True,
                            "spacing": "small"
                        },
                        {
                            "type": "TextBlock",
                            "text": f"Timestamp: {result.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
                            "isSubtle": True,
                            "spacing": "small"
                        },
                        {
                            "type": "TextBlock",
                            "text": "AI Analysis:",
                            "weight": "bolder",
                            "spacing": "medium"
                        },
                        {
                            "type": "TextBlock",
                            "text": analysis_text,
                            "wrap": True,
                            "spacing": "small"
                        }
                    ],
                    "actions": [{
                        "type": "Action.OpenUrl",
                        "title": "View Build",
                        "url": f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/{AZURE_DEVOPS_PROJECT}/_build/results?buildId={result.build_id}"
                    }]
                }
            }]
        }
        
        try:
            teams_response = requests.post(TEAMS_WEBHOOK_URL, json=teams_payload, timeout=10)
            if teams_response.status_code == 200:
                print("[TEAMS] ✓ Notification sent successfully")
            else:
                print(f"[TEAMS] ✗ Notification failed: {teams_response.status_code}")
        except Exception as e:
            print(f"[TEAMS] ✗ Notification exception: {str(e)}")
    
    async def handle(self, event: BuildEvent) -> AnalysisResult:
        if event.status not in ['failed', 'partiallySucceeded']:
            print(f"[SKIP] Build {event.build_id} - status: {event.status} (not a failure)\n")
            return AnalysisResult(
                build_id=event.build_id,
                build_name=event.build_name,
                status=event.status,
                error_quote="",
                explanation=f"Build {event.status}, no analysis needed",
                fix_steps=[],
                severity="ignored",
                timestamp=event.timestamp
            )
        
        if self.store.is_processed(event.build_id):
            print(f"[SKIP] Build {event.build_id} already processed\n")
            return AnalysisResult(
                build_id=event.build_id,
                build_name=event.build_name,
                status=event.status,
                error_quote="",
                explanation="Duplicate - already processed",
                fix_steps=[],
                severity="duplicate",
                timestamp=event.timestamp
            )
        
        self.store.mark_processed(event.build_id)
        
        result = await self._analyze_logs(event)
        self.store.save_analysis(result, event.logs[:200])
        
        await self._send_teams_notification(result)
        
        return result


# ============================================================================
# TRANSPORT ADAPTER (Framework-Agnostic)
# ============================================================================

class FlaskAdapter:
    def __init__(self, agent: DevOpsLogAgent):
        self.agent = agent
    
    @staticmethod
    def parse_webhook(data: dict) -> BuildEvent:
        resource = data.get('resource', {})
        build_id = str(resource.get('id', 'unknown'))
        build_name = resource.get('definition', {}).get('name', 'Unknown Build')
        status = resource.get('result', resource.get('status', 'unknown'))
        
        return BuildEvent(
            build_id=build_id,
            build_name=build_name,
            status=status,
            logs="",
            timestamp=datetime.now(),
            resource=resource
        )
    
    async def receive(self, data: dict) -> AnalysisResult:
        event = self.parse_webhook(data)
        
        print(f"[LOG FETCH] Retrieving full logs for build {event.build_id}...")
        event.logs = await self.agent._fetch_logs(event.build_id, event.resource)
        print(f"[LOG FETCH] Total log content: {len(event.logs)} chars")
        
        return await self.agent.handle(event)


agent = DevOpsLogAgent(kernel, store)
adapter = FlaskAdapter(agent)
print("DevOps Log Analysis Agent ready!\n")


# ============================================================================
# WEB INTERFACE (Flask)
# ============================================================================

@app.route('/')
def home():
    return jsonify({
        'status': 'alive',
        'agent': 'DevOps-Log-Agent',
        'model': MODEL,
        'builds_processed': store.get_history_count()
    })


@app.route('/analyze', methods=['POST'])
def webhook():
    data = request.get_json(force=True)
    
    print("\n" + "="*60)
    print("[WEBHOOK] Received build event")
    print("="*60)
    
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    result = loop.run_until_complete(adapter.receive(data))
    
    print(f"\n[ANALYSIS] Build {result.build_id}:")
    print(f"Error: {result.error_quote}")
    print(f"Severity: {result.severity}")
    print(f"Explanation: {result.explanation}")
    print("="*60 + "\n")
    
    return jsonify(result.to_dict()), 200


@app.route('/history', methods=['GET'])
def get_history():
    return jsonify({
        'total': store.get_history_count(),
        'recent': store.get_recent_history()
    })


# ============================================================================
# CLI MODE
# ============================================================================

async def cli_mode():
    print("="*60)
    print("DevOps Log Analysis Agent - CLI Mode")
    print("="*60)
    print("Commands: 'quit' to exit")
    print("="*60)
    
    while True:
        log_text = input("\nPaste build log (or 'quit'): ").strip()
        
        if log_text.lower() == 'quit':
            break
        
        status = input("Status (failed/succeeded): ").strip() or "failed"
        build_name = input("Build name (optional): ").strip() or "CLI Test Build"
        
        event = BuildEvent(
            build_id=f'cli-{datetime.now().timestamp()}',
            build_name=build_name,
            status=status,
            logs=log_text,
            timestamp=datetime.now(),
            resource={}
        )
        
        result = await agent.handle(event)
        print("\n" + "="*60)
        print("ANALYSIS:")
        print(f"Error: {result.error_quote}")
        print(f"Severity: {result.severity}")
        print(f"Explanation: {result.explanation}")
        if result.fix_steps:
            print("Fix Steps:")
            for i, step in enumerate(result.fix_steps, 1):
                print(f"  {i}. {step}")
        print("="*60)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    ngrok.set_auth_token(NGROK_AUTHTOKEN)
    
    mode = sys.argv[1] if len(sys.argv) > 1 else "--cli"
    
    if mode == "--webhook":
        tunnel = ngrok.connect("5000", "http")
        print(f"\nNgrok tunnel started!")
        print(f"Public URL: {tunnel.public_url}/analyze")
        print(f"Update this URL in Azure DevOps webhook settings\n")
        app.run(port=5000)
    else:
        asyncio.run(cli_mode())
