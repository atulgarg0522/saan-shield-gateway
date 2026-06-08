# saan-ai-gateway Node.js SDK

The official Node.js client library for integrating with the **saan-ai-gateway** AI router.

## Installation

```bash
npm install saan-ai-gateway
```

## Quick Start

```typescript
import { SaanAIGateway } from 'saan-ai-gateway';

const gateway = new SaanAIGateway({
  baseUrl: 'http://localhost:8000',
  apiKey: 'your-gateway-api-key'
});

async function run() {
  const response = await gateway.completions.create({
    model: 'gpt-4o',
    messages: [{ role: 'user', content: 'Hello Gateway!' }],
    provider: 'openai'
  });

  console.log(response);
}

run().catch(console.error);
```
