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
import json
from flask import Flask, request, jsonify
from pyngrok import ngrok
import requests
import os
import base64
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ------------------- Configuration -------------------
NGROK_AUTHTOKEN = "2pZMxvKMAfJyQIgvp8KfpryFoG3_iq3iqgMNNNUVCor6QazJ"
MODEL = "llama3.2:3b"
AZURE_DEVOPS_ORG = "sandeepkuruva"
AZURE_DEVOPS_PROJECT = "AI-Enhanced Productivity Metric Calculator"

# Environment variables (REQUIRED - set before running)
AZURE_DEVOPS_PAT = os.environ.get('AZURE_DEVOPS_PAT')
TEAMS_WEBHOOK_URL = os.environ.get('TEAMS_WEBHOOK_URL')

# Validate required environment variables
if not AZURE_DEVOPS_PAT:
    print(" [WARNING] AZURE_DEVOPS_PAT not set. Log fetching will fail.")
    print("   Set it with: set AZURE_DEVOPS_PAT=your_pat_here")

if not TEAMS_WEBHOOK_URL:
    print("  [WARNING] TEAMS_WEBHOOK_URL not set. Teams notifications disabled.")
    print("   Set it with: set TEAMS_WEBHOOK_URL=your_webhook_here")
# ----------------------------------------------------

app = Flask(__name__)

# Simple memory (no over-engineering)
build_history: List[Dict] = []
processed_builds: set = set()


# ============================================================================
# SK KERNEL + OLLAMA CHAT COMPLETION (Proper Integration)
# ============================================================================

print("Initializing SK with OllamaChatCompletion...")
kernel = Kernel()

# Add Ollama service via SK connector (NO raw API bypass)
chat_service = OllamaChatCompletion(
    ai_model_id=MODEL,
    host="http://localhost:11434"
)
kernel.add_service(chat_service)

print(f"✓ SK + Ollama ({MODEL}) connected\n")


# ============================================================================
# DEVOPS PLUGIN (SK-native, no ollama.generate bypass)
# ============================================================================

class DevOpsPlugin:
    """DevOps tools plugin using SK properly"""
    
    @kernel_function(name="analyze_log", description="Analyze failed build logs")
    async def analyze_log(self, kernel: Kernel, status: str, log_text: str) -> str:
        """Analyze using SK's chat completion (no raw ollama bypass)"""
        
        # Only analyze failures (filter success builds)
        if status != "failed":
            return "Build succeeded. No action required."
        
        print(f"[SK-OLLAMA] Analyzing {len(log_text)} chars...")
        
        # Proper SK prompt execution
        prompt = f"""You are a DevOps expert analyzing Azure DevOps build failures.

RULES:
1. Quote the EXACT error from the log
2. Explain what it means (1-2 sentences)
3. Provide 3 specific fix steps (copy-paste ready commands where possible)
4. Keep under 150 words

Build Status: {status}
Log:
{log_text[:1500]}

Analysis:"""
        
        # Use SK's invoke (proper orchestration)
        chat_history = ChatHistory()
        chat_history.add_user_message(prompt)
        
        response = await kernel.get_service(type=OllamaChatCompletion).get_chat_message_content(
            chat_history=chat_history,
            settings=kernel.get_prompt_execution_settings_from_service_id(service_id=MODEL)
        )
        
        if response is None:
            return "Error: No response from AI model"
        
        analysis = str(response.content).strip()
        print(f"[SK-OLLAMA] Generated {len(analysis)} chars\n")
        return analysis


# Add plugin to kernel
tools = DevOpsPlugin()
kernel.add_plugin(tools, plugin_name="devops")

print("Semantic Kernel agent ready!\n")


# ============================================================================
# AGENT LOGIC (SK Orchestration)
# ============================================================================

async def process_event(data: dict) -> dict:
    """Process build event using SK orchestration with full log fetching"""
    resource = data.get('resource', {})
    status = resource.get('result', resource.get('status', 'unknown'))
    build_id = str(resource.get('id', 'unknown'))
    
    # FILTER: Only analyze failures (skip success builds)
    if status not in ['failed', 'partiallySucceeded']:
        print(f"[SKIP] Build {build_id} - status: {status} (not a failure)\n")
        return {"status": "ignored", "reason": f"Build {status}, no analysis needed"}
    
    # Deduplication
    if build_id in processed_builds:
        print(f"[SKIP] Build {build_id} already processed\n")
        return {"status": "skipped", "reason": "duplicate"}
    
    processed_builds.add(build_id)
    
    # Step 3-5: Fetch full logs from Azure DevOps
    log_text = ""
    try:
        print(f"[LOG FETCH] Retrieving full logs for build {build_id}...")
        
        # Get logs list URL (from payload or construct)
        logs_list_url = resource.get('logs', {}).get('url')
        if not logs_list_url:
            logs_list_url = f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/{AZURE_DEVOPS_PROJECT}/_apis/build/builds/{build_id}/logs?api-version=7.1"
        
        # Auth header (Base64 encode :PAT)
        pat = AZURE_DEVOPS_PAT
        auth = base64.b64encode(f":{pat}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json"
        }
        
        # Fetch list of logs
        response = requests.get(logs_list_url, headers=headers, timeout=10)
        if response.status_code == 200:
            logs = response.json().get('value', [])
            print(f"[LOG FETCH] Found {len(logs)} log entries")
            
            # Find main logs (type 'Container' or loop all)
            main_logs = [log for log in logs if log.get('type') == 'Container']
            if not main_logs:
                main_logs = logs  # Fallback to all logs
            
            full_log = ""
            for log in main_logs[:3]:  # Limit to first 3 for performance
                log_id = log['id']
                log_content_url = f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/{AZURE_DEVOPS_PROJECT}/_apis/build/builds/{build_id}/logs/{log_id}?api-version=7.1"
                headers['Accept'] = "text/plain"
                
                content_response = requests.get(log_content_url, headers=headers, timeout=10)
                if content_response.status_code == 200:
                    full_log += content_response.text + "\n\n"
                    print(f"[LOG FETCH] Retrieved log {log_id} ({len(content_response.text)} chars)")
            
            log_text = full_log if full_log else json.dumps(resource, indent=2)[:1500]
            print(f"[LOG FETCH] Total log content: {len(log_text)} chars")
        else:
            print(f"[ERROR] Log fetch failed: {response.status_code} - {response.text}")
            # Fallback to webhook payload
            log_text = (
                data.get('detailedMessage', {}).get('text') or
                data.get('message', {}).get('text') or
                json.dumps(resource, indent=2)[:1500]
            )
    except Exception as e:
        print(f"[ERROR] Log fetch exception: {str(e)}")
        # Fallback to webhook payload
        log_text = (
            data.get('detailedMessage', {}).get('text') or
            data.get('message', {}).get('text') or
            json.dumps(resource, indent=2)[:1500]
        )
    
    # Record in history
    build_history.append({
        "id": build_id,
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "log_preview": log_text[:200]
    })
    
    # Invoke SK plugin (proper orchestration)
    analysis = await kernel.invoke(
        function_name="analyze_log",
        plugin_name="devops",
        status=status,
        log_text=log_text
    )
    
    # Send to Teams if analysis done and not a success message
    if 'Build succeeded' not in str(analysis) and TEAMS_WEBHOOK_URL:
        print(f"[TEAMS] Sending notification for build {build_id}...")
        teams_payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.2",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": f"🔴 Build Failure Alert: ID {build_id}",
                                "weight": "bolder",
                                "size": "medium",
                                "color": "attention"
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Status: {status}",
                                "isSubtle": True,
                                "spacing": "small"
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
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
                                "text": str(analysis),
                                "wrap": True,
                                "spacing": "small"
                            }
                        ],
                        "actions": [
                            {
                                "type": "Action.OpenUrl",
                                "title": "View Build",
                                "url": f"https://dev.azure.com/{AZURE_DEVOPS_ORG}/{AZURE_DEVOPS_PROJECT}/_build/results?buildId={build_id}"
                            }
                        ]
                    }
                }
            ]
        }
        
        try:
            teams_response = requests.post(TEAMS_WEBHOOK_URL, json=teams_payload, timeout=10)
            if teams_response.status_code == 200:
                print("[TEAMS] ✓ Notification sent successfully")
            else:
                print(f"[TEAMS] ✗ Notification failed: {teams_response.status_code} - {teams_response.text}")
        except Exception as e:
            print(f"[TEAMS] ✗ Notification exception: {str(e)}")
    
    return {
        "build_id": build_id,
        "status": status,
        "analysis": str(analysis),
        "history_count": len(build_history),
        "teams_notified": bool(TEAMS_WEBHOOK_URL)
    }


# ============================================================================
# WEB INTERFACE (Flask)
# ============================================================================

@app.route('/')
def home():
    """Health check"""
    return jsonify({
        'status': 'alive',
        'agent': 'SK-DevOps-Agent',
        'model': MODEL,
        'builds_processed': len(build_history)
    })


@app.route('/analyze', methods=['POST'])
def webhook():
    """Webhook endpoint - async safe"""
    data = request.get_json(force=True)
    
    print("\n" + "="*60)
    print("[WEBHOOK] Received build event")
    print("="*60)
    
    # Async execution (reuse or create event loop - don't close it)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    # Run async function without closing the loop
    response = loop.run_until_complete(process_event(data))
    
    # Log response
    if 'analysis' in response:
        print(f"\n[ANALYSIS] Build {response['build_id']}:")
        print(response['analysis'])
        print("="*60 + "\n")
    
    return jsonify(response), 200


@app.route('/history', methods=['GET'])
def get_history():
    """View build history"""
    return jsonify({
        'total': len(build_history),
        'recent': build_history[-10:]
    })


# ============================================================================
# CLI MODE (Optional)
# ============================================================================

async def cli_mode():
    """CLI mode for testing"""
    print("="*60)
    print("SK DevOps Agent - CLI Mode")
    print("="*60)
    print("Commands: 'quit' to exit")
    print("="*60)
    
    while True:
        log_text = input("\nPaste build log (or 'quit'): ").strip()
        
        if log_text.lower() == 'quit':
            break
        
        status = input("Status (failed/succeeded): ").strip() or "failed"
        
        # Process via SK
        mock_data = {
            'resource': {
                'id': f'cli-{datetime.now().timestamp()}',
                'result': status
            },
            'detailedMessage': {'text': log_text}
        }
        
        response = await process_event(mock_data)
        print("\n" + "="*60)
        print("ANALYSIS:")
        print(response.get('analysis', response.get('reason', 'No analysis')))
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
