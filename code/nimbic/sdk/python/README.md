# saan-ai-gateway Python SDK

The official Python client library for integrating with the **saan-ai-gateway** AI router.

## Installation

```bash
pip install saan-ai-gateway
```

## Quick Start

```python
import asyncio
from saan_ai_gateway_sdk import SaanAIGateway

async def main():
    # Initialize the SDK client
    client = SaanAIGateway(
        base_url="http://localhost:8000",
        api_key="your-gateway-api-key"
    )

    # Route a prompt automatically
    response = await client.completions.create(
        model="gpt-4o",  # The gateway will handle routing/fallback logic
        messages=[{"role": "user", "content": "Hello Gateway!"}],
        provider="openai"
    )
    print(response)

asyncio.run(main())
```
