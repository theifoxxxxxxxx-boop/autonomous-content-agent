# autonomous-content-agent

一个可运行的 Monorepo Demo：`Next.js + FastAPI + LangGraph + browser-use`，实现“Autonomous Content Agent / 自媒体操盘手”。

流程：前端上传图片与需求 -> 后端触发 LangGraph（A/B/C/D/E）-> 审稿 Loop 最多 3 次 -> 打开创作中心并停在发布按钮前 -> SSE 实时通知前端。

## 目录结构

```text
autonomous-content-agent/
  backend/                      # FastAPI + LangGraph + browser-use
    src/
      main.py                   # API + SSE
      workflow/graph.py         # 5 节点 StateGraph + 条件边
      services/
      platforms/                # DouyinAdapter / XhsAdapter
      review/rules.py           # 字数、emoji、禁词、规则审查
    tests/
  frontend/                     # Next.js TypeScript
    app/page.tsx                # 上传、提交、日志流、就绪提示
```

## 核心能力

- LangGraph `StateGraph` 五节点：
  - `Node A` 视觉分析（`gpt-4o` 或 `Claude Sonnet`，配置切换）
  - `Node B` 爆款文案生成（DeepSeek）
  - `Node C` 主编审稿（确定性规则 + DeepSeek 审校，不通过回到 B）
  - `Node D` browser-use/Playwright 浏览器操盘（严格不点发布）
  - `Node E` SSE 通知前端（`BROWSER_READY` 等）
- Review Loop：最大重试次数可配置（默认 3）
- Browser 模式：
  - `real`（默认）：复用本机 Chrome 登录态
  - `cloud`（可选）：支持返回 `live_url`（若可获得）
  - `mock`：不调用真实模型，不打开浏览器，用于联调
- SSE：`/api/events/{job_id}` 实时输出节点日志和状态
- 测试：包含规则检测和 Loop 路由单测

## 启动步骤

## 1) 启动 Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium
copy .env.example .env
uvicorn src.main:app --reload --port 8000
```

## 2) 启动 Frontend

```powershell
cd frontend
npm install
copy .env.local.example .env.local
npm run dev
```

访问：`http://localhost:3000`

## 3) 一键同时启动（可选）

在已完成依赖安装后，可直接执行：

```powershell
cd .
.\scripts\run-dev.ps1
```

## Backend `.env.example`

文件位置：`backend/.env.example`

```env
APP_ENV=dev
LOG_LEVEL=INFO
MOCK_MODE=true
CORS_ORIGINS=http://localhost:3000
UPLOAD_DIR=./uploads
DEFAULT_MAX_RETRIES=3
VISION_PROVIDER=gpt4o
OPENAI_API_KEY=
OPENAI_BASE_URL=
GPT4O_MODEL=gpt-4o
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-3-5-sonnet-latest
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
BROWSER_MODE=real
BROWSER_USE_ENABLED=true
BROWSER_HEADLESS=false
BROWSER_KEEP_ALIVE=true
BROWSER_OPERATION_TIMEOUT_SEC=240
BROWSER_EXECUTABLE_PATH=C:/Program Files/Google/Chrome/Application/chrome.exe
BROWSER_USER_DATA_DIR=C:/Users/<YOUR_USER>/AppData/Local/Google/Chrome/User Data
BROWSER_PROFILE_DIRECTORY=Default
BROWSER_CLOUD_PROJECT_ID=
BROWSER_CLOUD_LIVE_URL=
```

## Frontend `.env.local`

文件位置：`frontend/.env.local.example`

```env
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
```

## API

- `GET /api/health`：健康检查
- `POST /api/jobs`：创建任务（multipart: `platform`, `user_requirement`, `images[]`, `max_retries`）
- `GET /api/events/{job_id}`：SSE 日志流
- `GET /api/jobs/{job_id}`：任务最终状态

## Mock 联调

将 `backend/.env` 中 `MOCK_MODE=true` 与 `BROWSER_MODE=mock`，即可在不调用真实模型/浏览器时完整跑通前后端联调。

## 常见问题（FAQ）

1. Playwright 启动失败  
   检查是否执行过 `playwright install chromium`，并确认系统可运行 Chromium。
2. Chrome 登录态未复用  
   确认 `BROWSER_USER_DATA_DIR` 指向本机 Chrome `User Data`，且 `BROWSER_PROFILE_DIRECTORY` 正确（如 `Default`）。
3. 打不开发布入口或上传失败  
   平台页面可能改版；优先通过适配器关键词识别，必要时更新 fallback selector。
4. 反爬/风控导致页面异常  
   建议降低操作频率，先人工登录创作中心后再触发自动化，必要时手动完成最后步骤。
5. 安全与合规  
   系统不会自动点击“发布”按钮；会对极限词给替换建议，最终发布必须人工确认。

## 扩展新平台

1. 新增适配器文件：`backend/src/platforms/<new_platform>.py`
2. 定义：
   - `creator_center_url`
   - 发布入口关键词 + fallback selectors
   - 标题/正文输入框 selectors
   - 上传 input selectors
   - 发布按钮关键词（用于“停在发布前”）
3. 在 `backend/src/platforms/__init__.py` 注册路由
4. 前端平台下拉框增加选项

## 端到端 Demo（两张截图 -> 小红书笔记 -> 停在发布前）

1. 后端设置：
   - `MOCK_MODE=false`
   - `BROWSER_MODE=real`
   - 配置 DeepSeek/OpenAI 或 Anthropic Key
2. 启动后端与前端。
3. 打开 `http://localhost:3000`：
   - 平台选 `xhs`
   - 上传两张截图
   - 输入需求（如“真实测评风格，突出前后对比，语气亲切”）
   - 点击“开始执行”
4. 观察右侧 SSE 日志：Node A -> B -> C（可能循环）-> D -> E。
5. 当出现 `BROWSER_READY`：
   - 若 cloud 模式有 `live_url`，点击按钮进入；
   - 若 real 模式，查看本机弹出的 Chrome。
6. 页面应停在“发布/发布笔记”按钮前，不会自动点击；人工核对后手动发布。
