import time
import uuid
import json
import secrets
from decimal import Decimal
from typing import AsyncGenerator, Dict, Any, Optional
import structlog
from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db, async_session_local
from app.models.api_key import ApiKey
from app.models.request_log import RequestLog, ProviderEnum
from app.services.key_svc import validate_api_key
from app.services.cost_svc import calculate_cost
from app.services.proxy_svc import execute_proxy, ProxyRequest, ProxyResult

logger = structlog.get_logger()
router = APIRouter()


# --- DATABASE BACKGROUND LOGGING FUNCTION ---

async def log_proxy_transaction(
    org_id: uuid.UUID,
    api_key_id: Optional[uuid.UUID],
    request_id: str,
    provider: ProviderEnum,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: Decimal,
    latency_ms: int,
    status_code: int,
    error_message: Optional[str] = None,
    user_identifier: Optional[str] = None,
    metadata_payload: Optional[dict] = None,
    team_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    department: Optional[str] = None
) -> None:
    """
    Writes a trace record of the proxy request execution into requests_log.
    Executed in the background via FastAPI BackgroundTasks to keep gateway latency low.
    """
    async with async_session_local() as db:
        try:
            log_record = RequestLog(
                org_id=org_id,
                api_key_id=api_key_id,
                request_id=request_id,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                status_code=status_code,
                error_message=error_message,
                user_identifier=user_identifier,
                request_metadata=metadata_payload,
                team_id=team_id,
                project_id=project_id,
                department=department
            )
            db.add(log_record)
            await db.commit()
            await logger.adebug("Transaction logged successfully", request_id=request_id)

            # --- Post-request FinOps Alerting & Cache Invalidation ---
            from app.redis import redis_client
            from app.finops.attribution_service import invalidate_spend_cache, check_budget, dispatch_alerts
            
            await invalidate_spend_cache(org_id, team_id, project_id, user_identifier, redis_client)
            
            budget_check = await check_budget(
                org_id=org_id,
                team_id=team_id,
                project_id=project_id,
                user_identifier=user_identifier,
                db=db,
                redis=redis_client
            )
            
            if budget_check.warnings:
                await dispatch_alerts(org_id, budget_check.warnings, db)

        except Exception as e:
            await logger.aerror("Failed to write request transaction log to database", error=str(e), request_id=request_id)


# --- SECURITY API KEY INJECTION DEPENDENCY ---

async def get_current_api_key(request: Request, db: AsyncSession = Depends(get_db)) -> ApiKey:
    """
    FastAPI dependency validating the Authorization Bearer token header against active DB keys.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Expected: Bearer <key>"
        )

    raw_key = auth_header.replace("Bearer ", "").strip()
    api_key = await validate_api_key(raw_key, db)
    
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid, revoked, or expired saan-ai-gateway API Key."
        )
    return api_key


# --- CLIENT STREAM WRAPPER FOR SSE TRACKING ---

async def stream_with_logging(
    sse_generator: AsyncGenerator[str, None],
    org_id: uuid.UUID,
    api_key_id: uuid.UUID,
    request_id: str,
    provider: ProviderEnum,
    model: str,
    prompt_tokens: int,
    user_identifier: Optional[str] = None,
    metadata_payload: Optional[dict] = None,
    team_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    department: Optional[str] = None
) -> AsyncGenerator[str, None]:
    """
    Asynchronous generator wrapper that forwards SSE stream chunks to the client on-the-fly,
    aggregates streamed text to compute tokens/costs, and writes the DB log on connection closure.
    """
    start_time = time.perf_counter()
    accumulated_content = []
    status_code = 200
    err_msg = None

    try:
        async for chunk in sse_generator:
            yield chunk

            # Attempt to parse completion tokens from stream chunks
            # Standard SSE stream format data: {...}
            if chunk.startswith("data:"):
                raw_data = chunk.replace("data:", "").strip()
                if raw_data == "[DONE]":
                    continue
                try:
                    data_json = json.loads(raw_data)
                    # Check for OpenAI format stream usage updates
                    if "usage" in data_json and data_json["usage"]:
                        # If the provider sends usage details in the stream, load them
                        pass
                    # Extract delta text content to measure output tokens
                    choices = data_json.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            accumulated_content.append(content)
                except Exception:
                    pass

    except Exception as e:
        status_code = 502
        err_msg = str(e)
        raise e

    finally:
        # Stream has fully ended, calculate latency and token balances
        latency = int((time.perf_counter() - start_time) * 1000)
        
        # Word count mapping proxy for token estimation when standard Tiktoken is not local
        completion_text = "".join(accumulated_content)
        completion_words = len(completion_text.split())
        completion_tokens = max(1, int(completion_words * 1.33))

        # Calculate exact cost
        cost = await calculate_cost(model, prompt_tokens, completion_tokens)

        # Trigger database logging task
        await log_proxy_transaction(
            org_id=org_id,
            api_key_id=api_key_id,
            request_id=request_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            latency_ms=latency,
            status_code=status_code,
            error_message=err_msg,
            user_identifier=user_identifier,
            metadata_payload=metadata_payload,
            team_id=team_id,
            project_id=project_id,
            department=department
        )


# --- ROUTERS IMPLEMENTATION ---

@router.post("/v1/chat/completions", tags=["OpenAI Proxy"])
async def proxy_openai_chat_completions(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key: ApiKey = Depends(get_current_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    OpenAI-compatible chat completion proxy endpoint. Mirrors OpenAI specs exactly.
    Allows header overrides for targeting alternate providers.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload structure.")

    model = body.get("model")
    messages = body.get("messages")
    stream = body.get("stream", False)
    user_identifier = body.get("user")
    
    # Custom headers allow target overrides (defaults to OpenAI)
    provider_header = request.headers.get("X-Gateway-Provider", "openai")
    try:
        provider = ProviderEnum(provider_header.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported target provider: {provider_header}")

    if not model or not messages:
        raise HTTPException(status_code=400, detail="Parameters 'model' and 'messages' are required.")

    # Filter out standard proxy parameters to construct extra payloads
    extra_params = {k: v for k, v in body.items() if k not in ("model", "messages", "stream", "user")}
    
    # Word count estimation of input tokens
    prompt_words = sum(len(m.get("content", "").split()) for m in messages)
    prompt_tokens = max(1, int(prompt_words * 1.33))

    request_id = f"req_{secrets.token_hex(12)}"
    client_ip = request.headers.get("X-Forwarded-For", request.client.host or "127.0.0.1").split(",")[0].strip()

    proxy_req = ProxyRequest(
        org_id=api_key.org_id,
        api_key_id=api_key.id,
        provider=provider,
        model=model,
        messages=messages,
        stream=stream,
        extra_params=extra_params,
        client_ip=client_ip,
        request_id=request_id,
        headers=dict(request.headers)
    )

    result = await execute_proxy(proxy_req, db)

    actual_prov_enum = provider
    if result.actual_provider and result.actual_provider != "cache":
        try:
            actual_prov_enum = ProviderEnum(result.actual_provider.lower())
        except ValueError:
            pass
    actual_model = result.actual_model or model

    resolved_user = result.user_identifier or user_identifier

    if stream and result.status_code == 200:
        # Wrap the SSE stream with active log captures
        return StreamingResponse(
            stream_with_logging(
                sse_generator=result.response_body,
                org_id=api_key.org_id,
                api_key_id=api_key.id,
                request_id=request_id,
                provider=actual_prov_enum,
                model=actual_model,
                prompt_tokens=prompt_tokens,
                user_identifier=resolved_user,
                metadata_payload=extra_params,
                team_id=result.team_id,
                project_id=result.project_id,
                department=result.department
            ),
            media_type="text/event-stream"
        )

    # Standard non-streaming return: log in background
    background_tasks.add_task(
        log_proxy_transaction,
        api_key.org_id,
        api_key.id,
        request_id,
        actual_prov_enum,
        actual_model,
        result.prompt_tokens,
        result.completion_tokens,
        result.cost_usd,
        result.latency_ms,
        result.status_code,
        result.error_message,
        resolved_user,
        extra_params,
        result.team_id,
        result.project_id,
        result.department
    )

    return JSONResponse(content=result.response_body, status_code=result.status_code)


@router.post("/v1/messages", tags=["Anthropic Proxy"])
async def proxy_anthropic_messages(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key: ApiKey = Depends(get_current_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Anthropic-compatible messages proxy endpoint. Mirrors Anthropic specs exactly.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload structure.")

    model = body.get("model")
    messages = body.get("messages")
    stream = body.get("stream", False)
    
    if not model or not messages:
        raise HTTPException(status_code=400, detail="Parameters 'model' and 'messages' are required.")

    # Filter out standard proxy parameters to construct extra payloads
    extra_params = {k: v for k, v in body.items() if k not in ("model", "messages", "stream")}
    
    # Word count estimation of input tokens
    prompt_words = sum(len(m.get("content", "").split()) if isinstance(m.get("content"), str) else 0 for m in messages)
    prompt_tokens = max(1, int(prompt_words * 1.33))

    request_id = f"req_{secrets.token_hex(12)}"
    client_ip = request.headers.get("X-Forwarded-For", request.client.host or "127.0.0.1").split(",")[0].strip()

    proxy_req = ProxyRequest(
        org_id=api_key.org_id,
        api_key_id=api_key.id,
        provider=ProviderEnum.ANTHROPIC,
        model=model,
        messages=messages,
        stream=stream,
        extra_params=extra_params,
        client_ip=client_ip,
        request_id=request_id,
        headers=dict(request.headers)
    )

    result = await execute_proxy(proxy_req, db)

    actual_prov_enum = ProviderEnum.ANTHROPIC
    if result.actual_provider and result.actual_provider != "cache":
        try:
            actual_prov_enum = ProviderEnum(result.actual_provider.lower())
        except ValueError:
            pass
    actual_model = result.actual_model or model

    resolved_user = result.user_identifier

    if stream and result.status_code == 200:
        return StreamingResponse(
            stream_with_logging(
                sse_generator=result.response_body,
                org_id=api_key.org_id,
                api_key_id=api_key.id,
                request_id=request_id,
                provider=actual_prov_enum,
                model=actual_model,
                prompt_tokens=prompt_tokens,
                user_identifier=resolved_user,
                metadata_payload=extra_params,
                team_id=result.team_id,
                project_id=result.project_id,
                department=result.department
            ),
            media_type="text/event-stream"
        )

    # Standard non-streaming return: log in background
    background_tasks.add_task(
        log_proxy_transaction,
        api_key.org_id,
        api_key.id,
        request_id,
        actual_prov_enum,
        actual_model,
        result.prompt_tokens,
        result.completion_tokens,
        result.cost_usd,
        result.latency_ms,
        result.status_code,
        result.error_message,
        resolved_user,
        extra_params,
        result.team_id,
        result.project_id,
        result.department
    )

    return JSONResponse(content=result.response_body, status_code=result.status_code)


@router.post("/proxy/generic", tags=["Generic Proxy"])
async def proxy_generic_completion(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key: ApiKey = Depends(get_current_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Generic gateway routing router. Expects parameters: 'provider', 'model', 'messages'.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload structure.")

    provider_str = body.get("provider")
    model = body.get("model")
    messages = body.get("messages")
    stream = body.get("stream", False)
    user_identifier = body.get("user")

    if not provider_str or not model or not messages:
        raise HTTPException(
            status_code=400,
            detail="Parameters 'provider', 'model', and 'messages' are required."
        )

    try:
        provider = ProviderEnum(provider_str.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported provider specified: {provider_str}")

    extra_params = {k: v for k, v in body.items() if k not in ("provider", "model", "messages", "stream", "user")}
    
    prompt_words = sum(len(m.get("content", "").split()) for m in messages)
    prompt_tokens = max(1, int(prompt_words * 1.33))

    request_id = f"req_{secrets.token_hex(12)}"
    client_ip = request.headers.get("X-Forwarded-For", request.client.host or "127.0.0.1").split(",")[0].strip()

    proxy_req = ProxyRequest(
        org_id=api_key.org_id,
        api_key_id=api_key.id,
        provider=provider,
        model=model,
        messages=messages,
        stream=stream,
        extra_params=extra_params,
        client_ip=client_ip,
        request_id=request_id,
        headers=dict(request.headers)
    )

    result = await execute_proxy(proxy_req, db)

    actual_prov_enum = provider
    if result.actual_provider and result.actual_provider != "cache":
        try:
            actual_prov_enum = ProviderEnum(result.actual_provider.lower())
        except ValueError:
            pass
    actual_model = result.actual_model or model

    resolved_user = result.user_identifier or user_identifier

    if stream and result.status_code == 200:
        return StreamingResponse(
            stream_with_logging(
                sse_generator=result.response_body,
                org_id=api_key.org_id,
                api_key_id=api_key.id,
                request_id=request_id,
                provider=actual_prov_enum,
                model=actual_model,
                prompt_tokens=prompt_tokens,
                user_identifier=resolved_user,
                metadata_payload=extra_params,
                team_id=result.team_id,
                project_id=result.project_id,
                department=result.department
            ),
            media_type="text/event-stream"
        )

    # Standard non-streaming return: log in background
    background_tasks.add_task(
        log_proxy_transaction,
        api_key.org_id,
        api_key.id,
        request_id,
        actual_prov_enum,
        actual_model,
        result.prompt_tokens,
        result.completion_tokens,
        result.cost_usd,
        result.latency_ms,
        result.status_code,
        result.error_message,
        resolved_user,
        extra_params,
        result.team_id,
        result.project_id,
        result.department
    )

    return JSONResponse(content=result.response_body, status_code=result.status_code)
