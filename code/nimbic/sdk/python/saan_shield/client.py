import sys
from typing import Optional, Any, Dict, Union
import openai
import httpx

# --- PYTHON SDK CLIENT FOR SaaN Shield GATEWAY ---

class SaaNShieldClient(openai.OpenAI):
    """
    SaaNShieldClient mirrors openai.OpenAI exactly.
    Routes queries through the high-performance SaaN Shield gateway instead.
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.saan-shield.ai",
        provider_key: Optional[str] = None,
        **kwargs: Any
    ) -> None:
        # Resolve api_key from args/kwargs/env
        resolved_api_key = api_key or kwargs.get("api_key") or openai.api_key
        
        default_headers = kwargs.pop("default_headers", {}) or {}
        if resolved_api_key:
            default_headers["Authorization"] = f"Bearer {resolved_api_key}"
        if provider_key:
            default_headers["X-Provider-Key"] = provider_key
            
        kwargs["default_headers"] = default_headers

        super().__init__(
            api_key=resolved_api_key or "SaaNShield-dummy-key",
            base_url=base_url,
            **kwargs
        )

        # Intercept chat.completions.create
        orig_create = self.chat.completions.create
        def custom_create(*args: Any, **kwargs: Any) -> Any:
            p_key = kwargs.pop("provider_key", None)
            if p_key:
                extra_headers = kwargs.get("extra_headers") or {}
                extra_headers["X-Provider-Key"] = p_key
                kwargs["extra_headers"] = extra_headers
            return orig_create(*args, **kwargs)
        self.chat.completions.create = custom_create


class AsyncSaaNShieldClient(openai.AsyncOpenAI):
    """
    AsyncSaaNShieldClient mirrors openai.AsyncOpenAI exactly.
    Routes async queries through the high-performance SaaN Shield gateway.
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.saan-shield.ai",
        provider_key: Optional[str] = None,
        **kwargs: Any
    ) -> None:
        # Resolve api_key from args/kwargs/env
        resolved_api_key = api_key or kwargs.get("api_key") or openai.api_key
        
        default_headers = kwargs.pop("default_headers", {}) or {}
        if resolved_api_key:
            default_headers["Authorization"] = f"Bearer {resolved_api_key}"
        if provider_key:
            default_headers["X-Provider-Key"] = provider_key
            
        kwargs["default_headers"] = default_headers

        super().__init__(
            api_key=resolved_api_key or "SaaNShield-dummy-key",
            base_url=base_url,
            **kwargs
        )

        # Intercept chat.completions.create
        orig_create = self.chat.completions.create
        async def custom_create(*args: Any, **kwargs: Any) -> Any:
            p_key = kwargs.pop("provider_key", None)
            if p_key:
                extra_headers = kwargs.get("extra_headers") or {}
                extra_headers["X-Provider-Key"] = p_key
                kwargs["extra_headers"] = extra_headers
            return await orig_create(*args, **kwargs)
        self.chat.completions.create = custom_create


def monkey_patch() -> None:
    """
    Monkey patches the global openai package to replace OpenAI clients
    globally with SaaN Shield clients. Requires zero code changes in client applications.
    """
    openai.OpenAI = SaaNShieldClient
    openai.AsyncOpenAI = AsyncSaaNShieldClient
    
    # Also patch submodules to be absolutely thorough
    sys.modules["openai"].OpenAI = SaaNShieldClient
    sys.modules["openai"].AsyncOpenAI = AsyncSaaNShieldClient

