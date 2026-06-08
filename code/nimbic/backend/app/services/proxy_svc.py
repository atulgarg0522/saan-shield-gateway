import time
import json
import secrets
import uuid
from decimal import Decimal
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, AsyncGenerator
import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider_config import ProviderConfig
from app.models.request_log import ProviderEnum
from app.services.crypto_svc import decrypt_api_key
from app.services.cost_svc import calculate_cost
from app.routing.semantic_cache import SemanticCache, CacheResult
from app.routing.prompt_classifier import PromptClassifier, PromptClassification
from app.routing.smart_router import SmartRouter, RouteDecision

logger = structlog.get_logger()


class ProviderTimeoutError(Exception):
    pass


class ProviderRateLimitError(Exception):
    pass


class AllProvidersFailedError(Exception):
    pass


@dataclass
class ProxyRequest:
    org_id: Any
    api_key_id: Any
    provider: ProviderEnum
    model: str
    messages: List[Dict[str, str]]
    stream: bool = False
    extra_params: Dict[str, Any] = field(default_factory=dict)
    client_ip: str = "127.0.0.1"
    request_id: Optional[str] = None
    headers: Optional[Dict[str, str]] = None


@dataclass
class ProxyResult:
    response_body: Any  # Can be dict, str, or AsyncGenerator[str, None]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal = Decimal("0.000000")
    latency_ms: int = 0
    status_code: int = 200
    error_message: Optional[str] = None
    actual_provider: Optional[str] = None
    actual_model: Optional[str] = None
    team_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None
    department: Optional[str] = None
    user_identifier: Optional[str] = None



async def save_violations_bg(org_id: Any, request_id: str, violations: list, prompt_snippet: str) -> None:
    from app.db.session import async_session_local
    from app.models.security_violation import SecurityViolation, ViolationTypeEnum, SeverityEnum, ViolationActionEnum
    
    if not violations:
        return
        
    async with async_session_local() as db:
        try:
            for v in violations:
                violation_db = SecurityViolation(
                    org_id=org_id,
                    request_id=request_id,
                    violation_type=ViolationTypeEnum(v.violation_type),
                    severity=SeverityEnum(v.severity),
                    action_taken=ViolationActionEnum(v.action_applied),
                    details=v.details,
                    prompt_snippet=prompt_snippet
                )
                db.add(violation_db)
            await db.commit()
            await logger.adebug("Background violations saved successfully", count=len(violations))
        except Exception as e:
            await logger.aerror("Failed to save background violations", error=str(e))


async def execute_proxy(request: ProxyRequest, db: AsyncSession) -> ProxyResult:
    # 0. Check security policy
    from app.security.policy_engine import PolicyEngine
    from app.redis import redis_client
    import secrets
    import asyncio
    
    req_id = request.request_id or f"req_{secrets.token_hex(12)}"
    
    # Extract user prompt from request messages
    user_prompt = ""
    user_msg_idx = -1
    for idx, msg in enumerate(request.messages):
        if msg.get("role") == "user":
            user_prompt = msg.get("content", "")
            user_msg_idx = idx
            break

    engine = PolicyEngine()
    decision = await engine.evaluate(
        prompt=user_prompt,
        request_ip=request.client_ip,
        provider=request.provider.value,
        org_id=request.org_id,
        db=db,
        redis=redis_client,
        request_id=req_id
    )

    if decision.should_log_violation and decision.violations:
        asyncio.create_task(save_violations_bg(
            org_id=request.org_id,
            request_id=req_id,
            violations=decision.violations,
            prompt_snippet=user_prompt[:197] + "..." if len(user_prompt) > 200 else user_prompt
        ))

    if decision.action == "block":
        return ProxyResult(
            response_body={"error": "Request blocked by security policy", "reason": decision.block_reason},
            status_code=403,
            error_message=decision.block_reason
        )
        
    if decision.action == "redact":
        if user_msg_idx != -1:
            request.messages[user_msg_idx]["content"] = decision.final_prompt

    # --- 1.5 FinOps Attribution & Budget Check ---
    from app.finops.attribution_service import resolve_attribution, check_budget, dispatch_alerts
    
    attr = await resolve_attribution(
        org_id=request.org_id,
        headers=request.headers,
        metadata=request.extra_params,
        db=db
    )
    
    budget_check = await check_budget(
        org_id=request.org_id,
        team_id=attr.team_id,
        project_id=attr.project_id,
        user_identifier=attr.user_identifier,
        db=db,
        redis=redis_client
    )
    
    if budget_check.warnings:
        asyncio.create_task(dispatch_alerts(request.org_id, budget_check.warnings, db))

    if budget_check.hard_blocked:
        return ProxyResult(
            response_body={
                "error": "Budget limit exceeded",
                "reason": "The request was blocked because the configured budget limit has been exceeded. Please upgrade your plan or contact your administrator."
            },
            status_code=429,
            error_message="Budget limit exceeded"
        )

    # --- 2. Semantic Cache Lookup ---
    semantic_cache = SemanticCache()
    cache_result = await semantic_cache.lookup(decision.final_prompt, str(request.org_id), db, redis_client)
    if cache_result.hit:
        # Cache hit! Log cost savings in background and return formatted response
        asyncio.create_task(log_cache_hit_bg(
            org_id=request.org_id,
            request_id=req_id,
            cache_result=cache_result,
            original_model=request.model
        ))
        
        # Format the cached response text into the provider-specific format expected
        cached_resp_body = format_cache_response(request.provider, cache_result.cached_model or request.model, cache_result.response)
        
        # Estimate prompt/completion tokens for cache hit
        try:
            import tiktoken
            encoding = tiktoken.get_encoding("cl100k_base")
            p_tokens = len(encoding.encode(decision.final_prompt))
            c_tokens = len(encoding.encode(cache_result.response))
        except Exception:
            p_tokens = len(decision.final_prompt.split())
            c_tokens = len(cache_result.response.split())
            
        return ProxyResult(
            response_body=cached_resp_body,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            cost_usd=Decimal("0.000000"),
            latency_ms=0,
            status_code=200,
            actual_provider="cache",
            actual_model=cache_result.cached_model or "cache_hit",
            team_id=attr.team_id,
            project_id=attr.project_id,
            department=attr.department,
            user_identifier=attr.user_identifier
        )

    # --- 3. Active A/B Test Check or Smart Router Routing ---
    from app.routing.ab_test import ABTestManager
    ab_manager = ABTestManager()
    active_test = await ab_manager.get_active_test(str(request.org_id), redis_client)

    assigned_variant = None
    route = None
    if active_test:
        if getattr(active_test, 'test_mode', 'traffic_split') == 'shadow':
            assigned_variant = "A"
            provider = active_test.provider_a
            model = active_test.model_a
        else:
            assigned_variant = await ab_manager.assign_variant(req_id, active_test)
            if assigned_variant == "B":
                provider = active_test.provider_b
                model = active_test.model_b
            else:
                provider = active_test.provider_a
                model = active_test.model_a
        fallback_chain = [(provider, model)]
    else:
        classifier = PromptClassifier()
        smart_router = SmartRouter()
        classification = await classifier.classify(decision.final_prompt)
        route = await smart_router.route(classification, str(request.org_id), db, redis_client)
        provider = route.provider
        model = route.model
        fallback_chain = [(provider, model)] + route.fallback_chain

    # --- 4. Execute fallback chain ---
    result = None
    last_error = None

    for (chain_prov, chain_model) in fallback_chain:
        try:
            result = await call_provider(chain_prov, chain_model, decision.final_prompt, request, db)
            break  # Success!
        except (ProviderTimeoutError, ProviderRateLimitError) as e:
            await logger.awarn("Fallback triggered on error", provider=chain_prov, model=chain_model, error=str(e))
            last_error = e
            continue
    else:
        err_msg = f"All providers in fallback chain failed. Last error: {str(last_error)}"
        await logger.aerror(err_msg, org_id=str(request.org_id))
        return ProxyResult(
            response_body={"error": {"message": err_msg, "type": "gateway_network_error"}},
            status_code=502,
            error_message=err_msg,
            team_id=attr.team_id,
            project_id=attr.project_id,
            department=attr.department,
            user_identifier=attr.user_identifier
        )

    # --- 5. Store Cache (Background) ---
    # Only cache successful (200 OK) responses and if not streaming
    if result.status_code == 200 and not request.stream:
        resp_text = extract_response_text(request.provider, result.response_body)
        if resp_text:
            has_pii = (decision.action == "redact" or any(v.violation_type == "pii" for v in decision.violations))
            asyncio.create_task(store_cache_bg(
                prompt=decision.final_prompt,
                response=resp_text,
                model=result.actual_model or model,
                org_id=str(request.org_id),
                has_pii=has_pii
            ))

    # --- 6. Log Cost Savings (Background) or record A/B test result ---
    if result.status_code == 200:
        if active_test and assigned_variant:
            try:
                await ab_manager.record_result(
                    request_id=req_id,
                    variant=assigned_variant,
                    cost=result.cost_usd,
                    latency=result.latency_ms,
                    org_id=str(request.org_id),
                    db=db
                )
            except Exception as e:
                await logger.aerror("Failed to record A/B test result", error=str(e), request_id=req_id)
                
            # Trigger shadow request in background
            if getattr(active_test, 'test_mode', 'traffic_split') == 'shadow' and assigned_variant == 'A':
                asyncio.create_task(run_shadow_request(
                    prompt=decision.final_prompt,
                    test=active_test,
                    primary_result=result,
                    org_id=str(request.org_id)
                ))
                
            try:
                from app.services.cost_svc import calculate_cost
                baseline_cost = await calculate_cost("gpt-4o", result.prompt_tokens, result.completion_tokens)
                asyncio.create_task(log_cost_savings_bg(
                    org_id=request.org_id,
                    request_id=req_id,
                    route_provider=result.actual_provider or provider,
                    route_model=result.actual_model or model,
                    baseline_cost_usd=baseline_cost,
                    actual_cost=result.cost_usd
                ))
            except Exception:
                pass
        else:
            asyncio.create_task(log_cost_savings_bg(
                org_id=request.org_id,
                request_id=req_id,
                route_provider=result.actual_provider or provider,
                route_model=result.actual_model or model,
                baseline_cost_usd=route.baseline_cost_usd if route else Decimal("0.000000"),
                actual_cost=result.cost_usd
            ))

    if result:
        result.team_id = attr.team_id
        result.project_id = attr.project_id
        result.department = attr.department
        result.user_identifier = attr.user_identifier

    return result


def format_cache_response(provider: ProviderEnum, model: str, response_text: str) -> Dict[str, Any]:
    import secrets
    import time
    
    if provider == ProviderEnum.ANTHROPIC:
        return {
            "id": f"msg_{secrets.token_hex(12)}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [
                {
                    "type": "text",
                    "text": response_text
                }
            ],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0
            }
        }
    else:
        # OpenAI / Gemini / Default
        return {
            "id": f"chatcmpl-{secrets.token_hex(12)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_text
                    },
                    "logprobs": None,
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }


def extract_response_text(provider: ProviderEnum, body: Any) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    try:
        if "choices" in body and body["choices"]:
            choice = body["choices"][0]
            if "message" in choice and choice["message"]:
                return choice["message"].get("content")
            elif "text" in choice:
                return choice.get("text")
        elif "content" in body and body["content"]:
            content = body["content"]
            if isinstance(content, list) and len(content) > 0:
                if isinstance(content[0], dict) and content[0].get("type") == "text":
                    return content[0].get("text")
                elif isinstance(content[0], str):
                    return content[0]
            elif isinstance(content, str):
                return content
    except Exception:
        pass
    return None


async def store_cache_bg(prompt: str, response: str, model: str, org_id: str, has_pii: bool = False) -> None:
    from app.db.session import async_session_local
    from app.redis import redis_client
    from app.routing.semantic_cache import SemanticCache
    
    async with async_session_local() as db:
        try:
            cache = SemanticCache()
            await cache.store(prompt, response, model, org_id, db, redis_client, has_pii=has_pii)
        except Exception as e:
            await logger.aerror("Failed to store prompt in semantic cache in background", error=str(e))


async def log_cache_hit_bg(org_id: Any, request_id: str, cache_result: Any, original_model: str) -> None:
    from app.db.session import async_session_local
    from app.models.routing_cache import CostSavingsLog, CostSavingsSource
    
    async with async_session_local() as db:
        try:
            source = CostSavingsSource.faq_hit if cache_result.source == "faq" else CostSavingsSource.cache_hit
            saved_cost = cache_result.saved_cost_usd or Decimal("0.000000")
            log = CostSavingsLog(
                org_id=org_id,
                request_id=request_id,
                actual_model=cache_result.cached_model or "cache",
                actual_cost_usd=Decimal("0.000000"),
                baseline_model=original_model,
                baseline_cost_usd=saved_cost,
                savings_usd=saved_cost,
                source=source
            )
            db.add(log)
            await db.commit()
            await logger.ainfo("Cache hit logged successfully", request_id=request_id)
        except Exception as e:
            await logger.aerror("Failed to log cache hit in background", error=str(e), request_id=request_id)


async def log_cost_savings_bg(org_id: Any, request_id: str, route_provider: str, route_model: str, baseline_cost_usd: Decimal, actual_cost: Decimal) -> None:
    from app.db.session import async_session_local
    from app.models.routing_cache import CostSavingsLog, CostSavingsSource
    
    async with async_session_local() as db:
        try:
            savings = baseline_cost_usd - actual_cost
            log = CostSavingsLog(
                org_id=org_id,
                request_id=request_id,
                actual_model=route_model,
                actual_cost_usd=actual_cost,
                baseline_model="gpt-4o",
                baseline_cost_usd=baseline_cost_usd,
                savings_usd=savings,
                source=CostSavingsSource.model_routing
            )
            db.add(log)
            await db.commit()
            await logger.ainfo("Cost savings logged successfully", request_id=request_id)
        except Exception as e:
            await logger.aerror("Failed to log cost savings in background", error=str(e), request_id=request_id)


async def call_provider(
    provider_str: str,
    model: str,
    prompt: str,
    request: ProxyRequest,
    db: AsyncSession
) -> ProxyResult:
    try:
        provider_enum = ProviderEnum(provider_str.lower())
    except ValueError:
        provider_enum = ProviderEnum(provider_str)

    stmt = select(ProviderConfig).where(
        ProviderConfig.org_id == request.org_id,
        ProviderConfig.provider == provider_enum,
        ProviderConfig.is_active == True
    )
    db_result = await db.execute(stmt)
    config = db_result.scalars().first()

    if not config:
        err_msg = f"No active provider credentials configured for provider: {provider_str}"
        await logger.aerror(err_msg, org_id=str(request.org_id))
        raise ProviderTimeoutError(err_msg)

    # Decrypt key
    decrypted_key = decrypt_api_key(config.api_key_encrypted)

    # Configure endpoint URLs and headers
    url = ""
    headers = {"Content-Type": "application/json"}
    payload = {}

    timeout = httpx.Timeout(60.0, connect=10.0)

    # Make copy of messages and update user content with the final/redacted prompt
    messages_copy = [dict(m) for m in request.messages]
    for msg in messages_copy:
        if msg.get("role") == "user":
            msg["content"] = prompt
            break

    # Map request payload shape according to upstream provider requirements
    if provider_enum in (ProviderEnum.OPENAI, ProviderEnum.AZURE_OPENAI):
        url = f"{config.base_url.rstrip('/')}/chat/completions" if config.base_url else "https://api.openai.com/v1/chat/completions"
        headers["Authorization"] = f"Bearer {decrypted_key}"
        payload = {
            "model": model,
            "messages": messages_copy,
            "stream": request.stream,
            **request.extra_params
        }
        if request.stream and provider_enum == ProviderEnum.OPENAI:
            payload["stream_options"] = {"include_usage": True}

    elif provider_enum == ProviderEnum.ANTHROPIC:
        url = f"{config.base_url.rstrip('/')}/v1/messages" if config.base_url else "https://api.anthropic.com/v1/messages"
        headers["x-api-key"] = decrypted_key
        headers["anthropic-version"] = "2023-06-01"
        
        max_tokens = request.extra_params.get("max_tokens", 1024)
        payload = {
            "model": model,
            "messages": messages_copy,
            "stream": request.stream,
            "max_tokens": max_tokens,
            **{k: v for k, v in request.extra_params.items() if k != "max_tokens"}
        }

    elif provider_enum == ProviderEnum.GEMINI:
        url = f"{config.base_url.rstrip('/')}/openai/chat/completions" if config.base_url else "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        headers["Authorization"] = f"Bearer {decrypted_key}"
        payload = {
            "model": model,
            "messages": messages_copy,
            "stream": request.stream,
            **request.extra_params
        }

    else:
        # Fallback/Generic handler: proxies directly using base url
        url = config.base_url
        headers["Authorization"] = f"Bearer {decrypted_key}"
        payload = {
            "model": model,
            "messages": messages_copy,
            "stream": request.stream,
            **request.extra_params
        }

    if not url:
        err_msg = f"Target base URL not configured for generic provider: {provider_enum}"
        raise ProviderTimeoutError(err_msg)

    start_time = time.perf_counter()
    client = httpx.AsyncClient(timeout=timeout)

    try:
        if request.stream:
            # SSE streaming execution
            response = await client.send(
                client.build_request("POST", url, json=payload, headers=headers),
                stream=True
            )

            if response.status_code != 200:
                await response.aread()
                await client.aclose()
                if response.status_code == 429:
                    raise ProviderRateLimitError(f"Rate limit hit on provider {provider_str}: status 429")
                elif response.status_code in (500, 502, 503, 504):
                    raise ProviderTimeoutError(f"Upstream provider error status: {response.status_code}")
                else:
                    try:
                        body = response.json()
                    except Exception:
                        body = {"error": {"message": response.text, "type": "upstream_error"}}
                    latency = int((time.perf_counter() - start_time) * 1000)
                    return ProxyResult(
                        response_body=body,
                        status_code=response.status_code,
                        latency_ms=latency,
                        error_message=f"Upstream provider returned status {response.status_code}",
                        actual_provider=provider_str,
                        actual_model=model
                    )

            async def sse_wrapper() -> AsyncGenerator[str, None]:
                try:
                    async for line in response.aiter_lines():
                        if line:
                            yield f"{line}\n"
                finally:
                    await response.aclose()
                    await client.aclose()

            latency = int((time.perf_counter() - start_time) * 1000)
            return ProxyResult(
                response_body=sse_wrapper(),
                status_code=200,
                latency_ms=latency,
                actual_provider=provider_str,
                actual_model=model
            )

        else:
            response = await client.post(url, json=payload, headers=headers)
            latency = int((time.perf_counter() - start_time) * 1000)
            await client.aclose()

            await logger.ainfo("Upstream response received", status_code=response.status_code, provider=provider_str, model=model)

            if response.status_code != 200:
                if response.status_code == 429:
                    raise ProviderRateLimitError(f"Rate limit hit on provider {provider_str}: status 429")
                elif response.status_code in (500, 502, 503, 504):
                    raise ProviderTimeoutError(f"Upstream provider error status: {response.status_code}")
                else:
                    try:
                        body = response.json()
                    except Exception:
                        body = {"error": {"message": response.text, "type": "upstream_error"}}
                    return ProxyResult(
                        response_body=body,
                        status_code=response.status_code,
                        latency_ms=latency,
                        error_message=f"Upstream provider returned status {response.status_code}",
                        actual_provider=provider_str,
                        actual_model=model
                    )

            try:
                body = response.json()
            except Exception:
                body = {"error": {"message": response.text, "type": "upstream_error"}}

            prompt_tokens = 0
            completion_tokens = 0

            if provider_enum in (ProviderEnum.OPENAI, ProviderEnum.GEMINI, ProviderEnum.AZURE_OPENAI):
                usage = body.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
            elif provider_enum == ProviderEnum.ANTHROPIC:
                usage = body.get("usage", {})
                prompt_tokens = usage.get("input_tokens", 0)
                completion_tokens = usage.get("output_tokens", 0)

            cost = await calculate_cost(model, prompt_tokens, completion_tokens)

            return ProxyResult(
                response_body=body,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
                latency_ms=latency,
                status_code=200,
                actual_provider=provider_str,
                actual_model=model
            )

    except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as e:
        latency = int((time.perf_counter() - start_time) * 1000)
        await client.aclose()
        err_str = f"Network or execution failure proxying request to {provider_str}: {str(e)}"
        await logger.aerror(err_str, error=str(e))
        raise ProviderTimeoutError(err_str)


async def run_shadow_request(prompt: str, test, primary_result, org_id: str) -> None:
    from app.db.session import async_session_local
    from app.models.ab_test import ShadowResult
    import hashlib
    import uuid
    
    # 1. Create a dummy ProxyRequest
    dummy_req = ProxyRequest(
        org_id=uuid.UUID(org_id),
        api_key_id=None,
        provider=ProviderEnum(test.provider_b),
        model=test.model_b,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        extra_params={}
    )
    
    # 2. Call provider in background
    async with async_session_local() as db:
        try:
            shadow_result = await call_provider(
                provider_str=test.provider_b,
                model=test.model_b,
                prompt=prompt,
                request=dummy_req,
                db=db
            )
            
            if shadow_result.status_code == 200:
                prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                
                # Record the shadow result in db
                record = ShadowResult(
                    test_id=test.id,
                    prompt_hash=prompt_hash,
                    model_a_cost=primary_result.cost_usd,
                    model_b_cost=shadow_result.cost_usd,
                    model_a_latency=primary_result.latency_ms,
                    model_b_latency=shadow_result.latency_ms,
                    model_a_tokens=primary_result.prompt_tokens + primary_result.completion_tokens,
                    model_b_tokens=shadow_result.prompt_tokens + shadow_result.completion_tokens
                )
                db.add(record)
                await db.commit()
                await logger.ainfo("Shadow mode request recorded successfully", test_id=str(test.id))
        except Exception as e:
            await logger.aerror("Failed to run or record shadow request", error=str(e), test_id=str(test.id))


