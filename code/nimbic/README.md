# SaaN Shield AI Gateway (saan-ai-gateway)

A high-performance, developer-friendly, and enterprise-grade AI Gateway designed to route, load-balance, cache, and trace outbound API requests to various AI providers (OpenAI, Anthropic, Gemini, etc.) with beautiful real-time analytics.

---

## ⚡ 5-Minute Quickstart

Get up and running locally with just a few commands:

1. **Clone the repository** and navigate to the project directory:
   ```bash
   git clone <repo-url> saan-shield
   cd saan-shield
   ```

2. **Set up your environment variables**:
   ```bash
   cp .env.example .env
   ```
   *Open `.env` and fill in your AI provider credentials (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).*

3. **Start the environment** (starts Postgres, Redis, FastAPI Backend, and Next.js Frontend with hot reload):
   ```bash
   make dev
   ```

4. **Run migrations** (executes Alembic to set up the DB schemas):
   *In a new terminal window:*
   ```bash
   make migrate
   ```

5. **Seed the database** (creates "Demo Org", configures standard OpenAI/Anthropic providers, and generates your first admin API key):
   ```bash
   make seed
   ```
   *Keep an eye on the output! It will print your secure SaaN Shield gateway key, e.g. `nim_adm_xxxx...` (save this!).*

6. **Explore the dashboard**:
   Open **[http://localhost:3000](http://localhost:3000)** in your browser to inspect real-time logs, manage API keys, view latency and cost statistics, and manage your foundational model configurations!

---

## 🐍 Python SDK Quickstart

Deploy SaaN Shield into your existing Python codebase in 3 lines of code:

```python
from saan_shield import SaaNShieldClient

client = SaaNShieldClient(api_key="nim_adm_xxxx...")
response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "Hello!"}])
print(response.choices[0].message.content)
```

### 🚀 Zero-Code-Change Migration (Monkey Patching)
SaaN Shield includes a powerful monkey-patching feature that intercepts all standard `openai.OpenAI` clients globally. Simply add this to the entrypoint of your application to route 100% of your OpenAI traffic through the SaaN Shield gateway with **zero** down-stream modifications:

```python
import openai
import saan_shield

# Monkey patch OpenAI globally
saan_shield.monkey_patch()

# Your existing OpenAI code runs completely unchanged, routed via SaaN Shield!
client = openai.OpenAI(api_key="nim_adm_xxxx...") # Passes the SaaN Shield key
response = client.chat.completions.create(
    model="gpt-4o", 
    messages=[{"role": "user", "content": "Hello via global proxy!"}]
)
```

---

## 🔌 Node.js SDK Quickstart

Deploy SaaN Shield into your Node/TypeScript codebase in 3 lines of code:

```typescript
import { SaaNShieldClient } from 'saan-ai-gateway';

const client = new SaaNShieldClient({ apiKey: 'nim_adm_xxxx...' });
const response = await client.chat.completions.create({ model: 'gpt-4o', messages: [{ role: 'user', content: 'Hello!' }] });
```

### 🔌 Easy OpenAI Instance Patching
If you are already importing and using the official OpenAI client throughout your codebase, you can easily redirect it to the SaaN Shield gateway with our `patchOpenAI` utility:

```typescript
import OpenAI from 'openai';
import { patchOpenAI } from 'saan-ai-gateway';

const openai = new OpenAI({ apiKey: 'nim_adm_xxxx...' });
patchOpenAI(openai); // Instantly updates baseURL and custom headers

// Everything now routes automatically through SaaN Shield!
const response = await openai.chat.completions.create({
  model: 'gpt-4o',
  messages: [{ role: 'user', content: 'Hello from patched client!' }]
});
```

---

## 🛠️ Orchestration Commands

All routine orchestration is simplified via a central `Makefile`:

| Command | Action |
|---|---|
| `make dev` | Spins up all services via Docker Compose with hot reload |
| `make migrate` | Applies all pending Alembic schema updates |
| `make seed` | Creates default organization, keys, and provider configs |
| `make test` | Runs the FastAPI Pytest suite inside the backend container |
| `make logs` | Streams logs from all active running services |
| `make down` | Gracefully tears down all active services |
| `make clean` | Tears down services and deletes PostgreSQL volume databases |

---

## 📁 Monorepo Folder Structure

```
SaaN Shield/
├── backend/          # FastAPI Python application (routing, DB schema, key validation)
├── frontend/         # Next.js 14 Dashboard Web App (Admin Metrics, Key Management)
├── sdk/
│   ├── python/       # SaaN Shield python library (subclasses openai client for zero friction)
│   └── node/         # saan-ai-gateway node library (TypeScript compliant)
├── infra/
│   ├── docker/       # Dockerfile.backend and Dockerfile.frontend
│   └── seed.py       # DB population and key provisioning script
├── Makefile          # One-click orchestration script
├── docker-compose.yml# Multi-container service configuration
└── README.md         # Developer quickstart reference manual
```

---

## 🛡️ License

This project is licensed under the MIT License.
