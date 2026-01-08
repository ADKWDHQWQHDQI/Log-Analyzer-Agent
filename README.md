# DevOps Log Analysis Agent

AI-powered agent that analyzes Azure DevOps build failures and sends actionable insights to Microsoft Teams.

## Features

- 🤖 **AI-Powered Analysis**: Uses Semantic Kernel + Ollama (Llama 3.2) for intelligent log analysis
- 🔍 **Full Log Fetching**: Retrieves complete build logs from Azure DevOps REST API
- 📢 **Teams Notifications**: Sends rich Adaptive Cards to Teams channels
- 🎯 **Smart Filtering**: Only analyzes failures, skips successful builds
- 🔄 **Deduplication**: Prevents duplicate analysis of the same build
- 📝 **Build History**: Tracks all processed builds

## Architecture

```
Azure DevOps → Webhook → Flask Server → Semantic Kernel → Ollama LLM → Teams
```

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.ai/) installed and running locally
- Ollama model: `llama3.2:3b`
- Azure DevOps account with PAT (Build Read permissions)
- Microsoft Teams incoming webhook

## Setup

### 1. Install Dependencies

```bash
pip install semantic-kernel ollama flask pyngrok requests python-dotenv
```

### 2. Configure Environment Variables

Create a `.env` file (use `.env.example` as template):

```env
AZURE_DEVOPS_PAT=your_pat_here
TEAMS_WEBHOOK_URL=your_webhook_here
```

### 3. Start Ollama

```bash
ollama pull llama3.2:3b
ollama serve
```

### 4. Run the Agent

**Option 1: Using batch script (Windows)**
```cmd
run_agent.bat --webhook
```

**Option 2: Manual**
```cmd
python devops_agent_maf.py --webhook
```

### 5. Configure Azure DevOps Webhook

1. Go to Project Settings → Service Hooks
2. Add "Web Hooks" subscription
3. Event: **Build completed**
4. URL: Copy the ngrok URL from console (e.g., `https://xxxx.ngrok-free.app/analyze`)
5. Save

## Usage

### Webhook Mode (Production)
```bash
python devops_agent_maf.py --webhook
```
Starts Flask server with ngrok tunnel for Azure DevOps webhooks.

### CLI Mode (Testing)
```bash
python devops_agent_maf.py --cli
```
Interactive mode for manual log analysis.

## API Endpoints

- `GET /` - Health check
- `POST /analyze` - Webhook endpoint for build events
- `GET /history` - View build history

## Project Structure

```
Log-Analyzer-Agent/
├── devops_agent_maf.py    # Main agent code
├── devops_agent.py         # Legacy version
├── .env.example            # Environment template
├── .env                    # Your credentials (gitignored)
├── .gitignore              # Git ignore rules
├── run_agent.bat           # Windows startup script
├── SETUP.md                # Detailed setup guide
└── README.md               # This file
```

## How It Works

1. **Webhook Reception**: Azure DevOps sends build event to Flask endpoint
2. **Filtering**: Agent checks if build failed (skips successes)
3. **Log Fetching**: Retrieves full logs via Azure DevOps REST API
4. **AI Analysis**: Semantic Kernel orchestrates Ollama LLM to analyze logs
5. **Insights Generation**: AI extracts error, explains it, and provides 3 fix steps
6. **Teams Notification**: Posts Adaptive Card with analysis to Teams channel

## Sample Teams Notification

```
🔴 Build Failure Alert: ID 31
Status: failed
Timestamp: 2026-01-08 11:29:04

AI Analysis:
The exact error: `exit 1`
This means the script exited with failure status.

Fix steps:
1. Replace `exit 1` with `exit 0`
2. Remove unnecessary echo statements
3. Add meaningful logging before exit

[View Build]
```

## Configuration

Edit `devops_agent_maf.py` to customize:

- `MODEL`: Change LLM model (default: `llama3.2:3b`)
- `AZURE_DEVOPS_ORG`: Your Azure DevOps organization
- `AZURE_DEVOPS_PROJECT`: Your project name

## Security

- ✅ Credentials stored in `.env` (gitignored)
- ✅ PAT requires only Build (Read) permissions
- ✅ Environment variables validated on startup
- ✅ No hardcoded secrets in code

## Troubleshooting

**"Event loop is closed" error**
- Fixed in latest version - event loop is now reused across requests

**"WARNING: AZURE_DEVOPS_PAT not set"**
- Ensure `.env` file exists with `AZURE_DEVOPS_PAT=your_pat`
- Run `python-dotenv` is installed: `pip install python-dotenv`

**Teams notification not sent**
- Verify `TEAMS_WEBHOOK_URL` is set in `.env`
- Test webhook URL manually with curl/Postman

**Ollama connection failed**
- Ensure Ollama is running: `ollama serve`
- Verify model is pulled: `ollama pull llama3.2:3b`

## Tech Stack

- **Semantic Kernel**: AI orchestration framework
- **Ollama**: Local LLM runtime
- **Flask**: Web framework
- **Ngrok**: Tunnel for webhooks
- **Azure DevOps REST API**: Log fetching
- **Microsoft Teams**: Notifications

## License

MIT

## Author

Developed for Canarys Automations
