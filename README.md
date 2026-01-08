# DevOps Log Analysis Agent

AI-powered agent that analyzes Azure DevOps build failures and sends actionable insights to Microsoft Teams.

## Features

- 🤖 **AI-Powered Analysis**: Uses Semantic Kernel + Ollama (Llama 3.2) for intelligent log analysis with structured output parsing
- 🔍 **Full Log Fetching**: Retrieves complete build logs from Azure DevOps REST API with parallel fetching
- 📢 **Teams Notifications**: Sends rich Adaptive Cards with severity indicators (🔴🟠🟡🟢) to Teams channels
- 🎯 **Smart Filtering**: Only analyzes failures, skips successful builds
- 🔄 **Time-Based Deduplication**: Persistent SQLite-backed TTL deduplication (5-minute window)
- 📝 **Build History**: Tracks all processed builds with SQLite persistence
- 🎚️ **LLM-Driven Severity**: Dynamic severity classification (critical/high/medium/low) based on impact
- 🔧 **Actionable Fix Steps**: Extracts and presents 3-5 specific, copy-paste-ready fix steps
- 🎨 **Structured Output**: Regex-based parsing extracts error quotes, explanations, and fix steps
- ⚡ **Optimized Codebase**: Reduced from 660 to ~380 lines while maintaining full functionality
- 🔁 **Retry Logic**: Exponential backoff for Azure DevOps API calls (handles 429, 5xx errors)
- 📊 **Error Tracking**: Persistent failure log with `/metrics` endpoint for observability
- 🔐 **Optional Authentication**: Bearer token or query param auth for all endpoints

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
WEBHOOK_TOKEN=optional_auth_token  # For endpoint authentication (optional)
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

- `GET /` - Health check with build count
- `POST /analyze` - Webhook endpoint for build events (requires `WEBHOOK_TOKEN` if set)
- `GET /history?limit=10` - View recent build history (requires auth)
- `GET /metrics` - Observability metrics: total builds, failures, last error (requires auth)

## Project Structure

```
Log-Analyzer-Agent/
├── devops_agent_maf.py    # Main agent (380 lines, optimized)
├── build_store.py          # SQLite persistence layer
├── clear_tables.py         # Database maintenance utility
├── devops_agent.py         # Legacy version (deprecated)
├── builds.db               # SQLite database (auto-created)
├── .env.example            # Environment template
├── .env                    # Your credentials (gitignored)
├── .gitignore              # Git ignore rules
├── run_agent.bat           # Windows startup script
├── SETUP.md                # Detailed setup guide
└── README.md               # This file
```

## How It Works

1. **Webhook Reception**: Azure DevOps sends build event to Flask endpoint
2. **Duplicate Check**: Two-tier validation (in-memory + persistent store)
3. **Log Fetching**: Retrieves full logs via Azure DevOps REST API (up to 3 container logs)
4. **AI Analysis**: Semantic Kernel orchestrates Ollama LLM with structured prompt
5. **Structured Parsing**: Regex extracts severity, error quote, explanation, and fix steps
6. **Persistence**: Saves analysis to SQLite database
7. **Teams Notification**: Posts Adaptive Card with severity icon and actionable insights

## Analysis Structure

The agent provides structured analysis in this format:

**Severity Classification** (LLM-driven):

- 🔴 **Critical**: Production-blocking, security issues, data loss
- 🟠 **High**: Build completely broken, major functionality impaired
- 🟡 **Medium**: Partial failures, workarounds available
- 🟢 **Low**: Minor issues, warnings, cosmetic problems

**Output Components**:

- **Error Quote**: Exact error message from logs
- **Explanation**: Root cause analysis (2-3 sentences)
- **Fix Steps**: 3-5 specific, actionable remediation steps with commands

## Sample Teams Notification

```
� Build Failure: MyProject-CI
Build ID: 52 | Status: failed | Severity: HIGH
Timestamp: 2026-01-08 15:27:56

Error:
exit 100

Explanation:
The second CmdLine@2 task fails due to exit 100 command which causes
the build process to exit with a non-zero status code, indicating failure.

Fix Steps:
1. Update the script for the second CmdLine@2 task to remove the exit 100 command
2. If this task requires specific output or actions, consider using a different task that supports these features
3. Add proper error handling and logging instead of hard exits

[View Build →]
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

- ✅ Fixed in v2.0 - Proper async cleanup with shutdown_asyncgens() and graceful thread handling

**"WARNING: AZURE_DEVOPS_PAT not set"**

- Ensure `.env` file exists with `AZURE_DEVOPS_PAT=your_pat`
- Verify `python-dotenv` is installed: `pip install python-dotenv`

**401 Unauthorized on log fetch**

- Check PAT token validity (may be expired)
- Regenerate PAT with Build (Read) permissions at https://dev.azure.com/[org]/_usersSettings/tokens

**Teams notification not sent**

- Verify `TEAMS_WEBHOOK_URL` is set in `.env`
- Test webhook URL manually with curl/Postman
- Check Teams channel webhook is not disabled

**Ollama connection failed**

- Ensure Ollama is running: `ollama serve`
- Verify model is pulled: `ollama pull llama3.2:3b`
- Check Ollama is listening on localhost:11434

**Duplicate webhook processing**

- ✅ Now handled by two-tier deduplication (in-memory + persistent store)
- Old builds are skipped automatically even after server restart

## Tech Stack

- **Semantic Kernel**: AI orchestration framework with structured prompt engineering
- **Ollama**: Local LLM runtime (llama3.2:3b)
- **Flask**: Lightweight web framework for webhook handling
- **SQLite**: Persistent storage for build history and deduplication
- **Ngrok**: Secure tunnel for webhook delivery
- **Azure DevOps REST API**: Build log retrieval
- **Microsoft Teams Adaptive Cards**: Rich notifications

## Recent Improvements

### v3.0 Production-Ready Enhancements

**Critical Fixes**:

1. **Time-Based Deduplication** - Replaced thread-unsafe global set with persistent SQLite `processing_queue` table

   - TTL-based (300s default), survives restarts
   - `is_recently_processed()`, `mark_processing()`, `unmark_processing()`

2. **Parallel Log Fetching** - Removed sequential 3-log limit

   - Fetches ALL Container logs via `asyncio.gather()`
   - Concurrent API requests for 3-5x speed improvement

3. **Retry Logic** - Exponential backoff for transient failures

   - Retries on 429, 5xx status codes
   - `_retry_request(url, headers, max_attempts=3)`

4. **Error Tracking** - Persistent failure log for observability

   - `failure_log` table stores all exceptions
   - `/metrics` endpoint returns failure statistics

5. **Endpoint Authentication** - Optional security via `WEBHOOK_TOKEN`
   - Bearer token or query param validation
   - Applies to `/analyze`, `/history`, `/metrics`

### v2.0 AI & Optimization

- ✅ **LLM-driven severity classification** - Removed hardcoded severity levels
- ✅ **Populated fix_steps** - Extracts actionable remediation steps from LLM response
- ✅ **Enhanced error extraction** - Regex patterns for common error formats
- ✅ **Store-backed deduplication** - Persistent duplicate prevention across restarts
- ✅ **Code optimization** - Reduced from 660 to ~380 lines (42% reduction)
- ✅ **Fixed event loop closure** - Proper async cleanup prevents RuntimeError
- ✅ **Improved Teams cards** - Severity icons, structured sections, better formatting

## License

MIT

## Author

Developed for Canarys Automations
