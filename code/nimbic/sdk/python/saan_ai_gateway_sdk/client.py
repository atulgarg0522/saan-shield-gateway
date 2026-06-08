from typing import List, Dict, Any, Optional
import httpx


class Completions:
    def __init__(self, client: "SaanAIGateway"):
        self._client = client

    async def create(
        self,
        model: str,
        messages: List[Dict[str, str]],
        provider: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Sends a completion request to the saan-ai-gateway.
        """
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if provider:
            payload["provider"] = provider
        if max_tokens:
            payload["max_tokens"] = max_tokens
        
        # Merge extra parameter kwargs
        payload.update(kwargs)

        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {self._client.api_key}",
                "Content-Type": "application/json"
            }
            response = await client.post(
                f"{self._client.base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=self._client.timeout
            )
            response.raise_for_status()
            return response.json()


class SaanAIGateway:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 60.0
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

        # Attach endpoints
        self.completions = Completions(self)
