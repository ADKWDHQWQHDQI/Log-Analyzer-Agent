"""
DevOps Log Analysis Agent - SK Orchestration + Ollama Runtime
Properly uses Semantic Kernel's OllamaChatCompletion connector (no raw API bypass)
"""

import asyncio
import sys
import json
import re
import os
import base64
import time
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import List
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.ollama import OllamaChatCompletion
from semantic_kernel.contents import ChatHistory
from flask import Flask, request, jsonify
from pyngrok import ngrok
import requests
from dotenv import load_dotenv
from build_store import BuildStore, AnalysisResult

load_dotenv()

NGROK_AUTHTOKEN = "2pZMxvKMAfJyQIgvp8KfpryFoG3_iq3iqgMNNNUVCor6QazJ"
MODEL = "llama3.2:3b"
AZURE_DEVOPS_ORG = "sandeepkuruva"
AZURE_DEVOPS_PROJECT = "AI-Enhanced Productivity Metric Calculator"
AZURE_DEVOPS_PAT = os.environ.get('AZURE_DEVOPS_PAT')
TEAMS_WEBHOOK_URL = os.environ.get('TEAMS_WEBHOOK_URL')

if not AZURE_DEVOPS_PAT:
    print("[WARNING] AZURE_DEVOPS_PAT not set. Log fetching will fail.")
if not TEAMS_WEBHOOK_URL:
    print("[WARNING] TEAMS_WEBHOOK_URL not set. Teams notifications disabled.")

@dataclass
class BuildEvent:
    build_id: str
    build_name: str
    status: str
    logs: str
    timestamp: datetime
    resource: dict = field(default_factory=dict)

app = Flask(__name__)
store = BuildStore()
processed_recently = set()

print("Initializing SK with OllamaChatCompletion...")
kernel = Kernel()
chat_service = OllamaChatCompletion(ai_model_id=MODEL, host="http://localhost:11434")
kernel.add_service(chat_service)
print(f"✓ SK + Ollama ({MODEL}) connected\n")

class DevOpsLogAgent:
    def __init__(self, kernel: Kernel, store: BuildStore):
        self.kernel = kernel
        self.store = store
        
    async def _fetch_logs(self, build_id: str, resource: dict) -> str:
        logs_list_url = resource.get('logs', {}).get('url') or f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/{AZURE_DEVOPS_PROJECT}/_apis/build/builds/{build_id}/logs?api-version=7.1"
        auth = base64.b64encode(f":{AZURE_DEVOPS_PAT}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
        
        try:
            response = requests.get(logs_list_url, headers=headers, timeout=10)
            if response.status_code != 200:
                print(f"[ERROR] Log fetch failed: {response.status_code}" + (" (Check PAT token)" if response.status_code == 401 else ""))
                return json.dumps(resource, indent=2)
            
            logs = response.json().get('value', [])
            main_logs = [log for log in logs if log.get('type') == 'Container'] or logs
            full_log = ""
            
            for log in main_logs[:3]:
                log_content_url = f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/{AZURE_DEVOPS_PROJECT}/_apis/build/builds/{build_id}/logs/{log['id']}?api-version=7.1"
                headers['Accept'] = "text/plain"
                content_response = requests.get(log_content_url, headers=headers, timeout=10)
                if content_response.status_code == 200:
                    full_log += content_response.text + "\n\n"
            
            return full_log if full_log else json.dumps(resource, indent=2)
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
        
        # Use the beginning of logs where errors typically appear
        log_snippet = event.logs[:3000] if len(event.logs) > 3000 else event.logs
        
        prompt = f"""You are a DevOps expert analyzing Azure DevOps build failures.

Analyze this build failure and provide a structured response.

Build Status: {event.status}
Log:
{log_snippet}

Provide your analysis in this EXACT format:

SEVERITY: [critical/high/medium/low]
- critical: Production-blocking, security issues, data loss
- high: Build completely broken, major functionality impaired
- medium: Partial failures, workarounds available
- low: Minor issues, warnings, cosmetic problems

ERROR:
[Quote the exact error message(s) from the log - be concise, 1-2 lines max]

EXPLANATION:
[Explain what went wrong in 2-3 sentences]

FIX_STEPS:
1. [First specific, actionable fix step with commands if applicable]
2. [Second specific fix step]
3. [Third specific fix step]

Analysis:"""
        
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
        
        # Parse structured response
        severity = self._extract_severity(analysis_text)
        error_quote = self._extract_error_quote(analysis_text, log_snippet)
        explanation = self._extract_explanation(analysis_text)
        fix_steps = self._extract_fix_steps(analysis_text)
        
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
    
    def _extract_severity(self, text: str) -> str:
        match = re.search(r'SEVERITY:\s*\[?(\w+)\]?', text, re.IGNORECASE)
        if match and match.group(1).lower() in ['critical', 'high', 'medium', 'low']:
            return match.group(1).lower()
        text_lower = text.lower()
        for severity, keywords in [('critical', ['critical', 'severe', 'security']), ('high', ['high', 'broken', 'major']), ('low', ['low', 'minor', 'warning'])]:
            if any(k in text_lower for k in keywords):
                return severity
        return 'medium'
    
    def _extract_error_quote(self, text: str, log_snippet: str) -> str:
        match = re.search(r'ERROR:\s*\n(.+?)(?:\n\n|\nEXPLANATION:|\nFIX_STEPS:|$)', text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip().split('\n')[0][:200]
        for pattern in [r'##\[error\](.+)', r'ERROR:(.+)', r'exit\s+(\d+)', r'FAILED:(.+)', r'Exception:(.+)']:
            match = re.search(pattern, log_snippet, re.IGNORECASE)
            if match:
                return match.group(0)[:200].strip()
        return "Error details not found in log"
    
    def _extract_explanation(self, text: str) -> str:
        match = re.search(r'EXPLANATION:\s*\n(.+?)(?:\n\nFIX_STEPS:|\n\n\d+\.|\Z)', text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        cleaned = re.sub(r'(SEVERITY:.*?(?=ERROR:|EXPLANATION:|FIX_STEPS:|$)|ERROR:.*?(?=EXPLANATION:|FIX_STEPS:|$)|FIX_STEPS:.*$)', '', text, flags=re.DOTALL | re.IGNORECASE)
        return cleaned.strip() or text.strip()
    
    def _extract_fix_steps(self, text: str) -> List[str]:
        match = re.search(r'FIX_STEPS:\s*\n(.+)', text, re.DOTALL | re.IGNORECASE)
        steps_text = match.group(1) if match else text
        steps = []
        for m in re.finditer(r'^\s*(\d+)\.\s*(.+?)(?=^\s*\d+\.|$)', steps_text, re.MULTILINE | re.DOTALL):
            step = re.sub(r'\n+', ' ', m.group(2).strip())
            if step and (match or len(step) > 10):
                steps.append(step[:500])
        return steps[:5]
    
    async def _send_teams_notification(self, result: AnalysisResult):
        if result.severity == "success" or not TEAMS_WEBHOOK_URL:
            return
        
        icon = {'critical': '🔴', 'high': '🟠', 'medium': '🟡', 'low': '🟢'}.get(result.severity, '⚠️')
        card_body = [
            {"type": "TextBlock", "text": f"{icon} Build Failure: {result.build_name}", "weight": "bolder", "size": "medium", "color": "attention"},
            {"type": "TextBlock", "text": f"Build ID: {result.build_id} | Status: {result.status} | Severity: {result.severity.upper()}", "isSubtle": True, "spacing": "small"},
            {"type": "TextBlock", "text": f"Timestamp: {result.timestamp.strftime('%Y-%m-%d %H:%M:%S')}", "isSubtle": True, "spacing": "small"}
        ]
        
        if result.error_quote:
            card_body.extend([{"type": "TextBlock", "text": "Error:", "weight": "bolder", "spacing": "medium"}, {"type": "TextBlock", "text": result.error_quote, "wrap": True, "spacing": "small", "color": "attention"}])
        
        card_body.extend([{"type": "TextBlock", "text": "Explanation:", "weight": "bolder", "spacing": "medium"}, {"type": "TextBlock", "text": result.explanation, "wrap": True, "spacing": "small"}])
        
        if result.fix_steps:
            card_body.append({"type": "TextBlock", "text": "Fix Steps:", "weight": "bolder", "spacing": "medium"})
            card_body.extend([{"type": "TextBlock", "text": f"{i}. {step}", "wrap": True, "spacing": "small"} for i, step in enumerate(result.fix_steps, 1)])
        
        payload = {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.2", "body": card_body, "actions": [{"type": "Action.OpenUrl", "title": "View Build", "url": f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/{AZURE_DEVOPS_PROJECT}/_build/results?buildId={result.build_id}"}]}}]}
        
        try:
            response = requests.post(TEAMS_WEBHOOK_URL, json=payload, timeout=10)
            print(f"[TEAMS] {'✓' if response.status_code == 200 else '✗'} Notification {('sent' if response.status_code == 200 else 'failed: ' + str(response.status_code))}")
        except Exception as e:
            print(f"[TEAMS] ✗ Exception: {str(e)}")
    
    async def handle(self, event: BuildEvent) -> AnalysisResult:
        if event.status not in ['failed', 'partiallySucceeded']:
            return AnalysisResult(build_id=event.build_id, build_name=event.build_name, status=event.status, error_quote="", explanation=f"Build {event.status}, no analysis needed", fix_steps=[], severity="ignored", timestamp=event.timestamp)
        result = await self._analyze_logs(event)
        self.store.save_analysis(result, event.logs[:200])
        await self._send_teams_notification(result)
        return result

class FlaskAdapter:
    def __init__(self, agent: DevOpsLogAgent):
        self.agent = agent
    
    @staticmethod
    def parse_webhook(data: dict) -> BuildEvent:
        resource = data.get('resource', {})
        return BuildEvent(build_id=str(resource.get('id', 'unknown')), build_name=resource.get('definition', {}).get('name', 'Unknown Build'), status=resource.get('result', resource.get('status', 'unknown')), logs="", timestamp=datetime.now(), resource=resource)
    
    async def receive(self, data: dict) -> AnalysisResult:
        event = self.parse_webhook(data)
        event.logs = await self.agent._fetch_logs(event.build_id, event.resource)
        return await self.agent.handle(event)

agent = DevOpsLogAgent(kernel, store)
adapter = FlaskAdapter(agent)
print("DevOps Log Analysis Agent ready!\n")

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
    build_id = str(data.get('resource', {}).get('id', 'unknown'))
    print(f"\n{'='*60}\n[WEBHOOK] Build {build_id}\n{'='*60}")
    
    if build_id in processed_recently:
        return jsonify({"status": "skipped", "reason": "duplicate_in_memory"}), 200
    if store.has_build(build_id):
        return jsonify({"status": "skipped", "reason": "duplicate_in_store"}), 200
    
    processed_recently.add(build_id)
    if len(processed_recently) > 20:
        processed_recently.pop()
    
    def process_in_background():
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(adapter.receive(data))
            
            print(f"\n[ANALYSIS] Build {result.build_id}:\nSeverity: {result.severity.upper()}\nError: {result.error_quote}\nExplanation: {result.explanation}")
            if result.fix_steps:
                print(f"\nFix Steps:")
                for i, step in enumerate(result.fix_steps, 1):
                    print(f"  {i}. {step}")
            print("="*60 + "\n")
        except Exception as e:
            print(f"[ERROR] Processing failed: {str(e)}")
        finally:
            if loop:
                try:
                    time.sleep(0.5)
                    pending = asyncio.all_tasks(loop)
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    if not loop.is_closed():
                        loop.close()
                except:
                    pass
            time.sleep(1)
            processed_recently.discard(build_id)
    
    threading.Thread(target=process_in_background, daemon=True).start()
    return jsonify({"status": "accepted", "build_id": build_id}), 202


@app.route('/history', methods=['GET'])
def get_history():
    return jsonify({'total': store.get_history_count(), 'recent': store.get_recent_history()})

async def cli_mode():
    print(f"{'='*60}\nDevOps Log Analysis Agent - CLI Mode\n{'='*60}")
    
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

if __name__ == '__main__':
    ngrok.set_auth_token(NGROK_AUTHTOKEN)
    mode = sys.argv[1] if len(sys.argv) > 1 else "--cli"
    if mode == "--webhook":
        tunnel = ngrok.connect("5000", "http")
        print(f"\nNgrok tunnel: {tunnel.public_url}/analyze\nUpdate in Azure DevOps webhook settings\n")
        app.run(port=5000)
    else:
        asyncio.run(cli_mode())
