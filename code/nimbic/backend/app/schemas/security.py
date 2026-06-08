import uuid
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from app.models.security_policy import PiiActionEnum, PolicyActionEnum
from app.models.security_violation import ViolationTypeEnum, SeverityEnum, ViolationActionEnum


class CustomPatternSchema(BaseModel):
    name: str
    pattern: str
    regex: Optional[str] = None
    action: Optional[PolicyActionEnum] = PolicyActionEnum.block
    description: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def populate_pattern_regex(cls, data: Any) -> Any:
        if isinstance(data, dict):
            p = data.get("pattern")
            r = data.get("regex")
            if p and not r:
                data["regex"] = p
            elif r and not p:
                data["pattern"] = r
            
            # Regex validation
            val = data.get("pattern")
            if val:
                try:
                    re.compile(val)
                except Exception as e:
                    raise ValueError(f"Invalid regex: {str(e)}")
        return data


class SecurityPolicyResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    pii_action: PiiActionEnum
    code_action: PolicyActionEnum
    sensitive_action: PolicyActionEnum
    blocked_regions: List[str]
    allowed_providers_by_region: Dict[str, List[str]]
    custom_patterns: List[CustomPatternSchema]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SecurityPolicyUpdateRequest(BaseModel):
    pii_action: Optional[PiiActionEnum] = None
    code_action: Optional[PolicyActionEnum] = None
    sensitive_action: Optional[PolicyActionEnum] = None
    blocked_regions: Optional[List[str]] = None
    allowed_providers_by_region: Optional[Dict[str, List[str]]] = None
    custom_patterns: Optional[List[CustomPatternSchema]] = None
    is_active: Optional[bool] = None

    @field_validator("blocked_regions")
    @classmethod
    def validate_blocked_regions(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            for code in v:
                if not isinstance(code, str) or len(code) != 2 or not code.isalpha():
                    raise ValueError(f"Invalid country code: '{code}'. Must be 2-character ISO country code.")
            return [code.upper() for code in v]
        return v


class SecurityViolationResponse(BaseModel):
    id: uuid.UUID
    request_id: str
    violation_type: ViolationTypeEnum
    severity: SeverityEnum
    action_taken: ViolationActionEnum
    details: Dict[str, Any]
    prompt_snippet: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedViolationsResponse(BaseModel):
    items: List[SecurityViolationResponse]
    total: int
    page: int
    pages: int


class SecurityViolationStatsResponse(BaseModel):
    total_violations: int
    by_type: Dict[str, int]
    by_severity: Dict[str, int]
    by_action: Dict[str, int]
    blocked_requests_pct: float
    top_violation_hours: List[Dict[str, int]]
    trend: List[Dict[str, Any]]


class PolicyTestRequest(BaseModel):
    prompt: str
    provider: str
    request_ip: str


class ViolationRecordSchema(BaseModel):
    violation_type: str
    severity: str
    details: Dict[str, Any]
    action_applied: str


class PolicyDecisionResponse(BaseModel):
    action: str
    final_prompt: str
    violations: List[ViolationRecordSchema]
    should_log_violation: bool
    block_reason: Optional[str] = None


class CustomPatternCreate(BaseModel):
    name: str
    pattern: str
    action: Optional[PolicyActionEnum] = PolicyActionEnum.block
    description: Optional[str] = None

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        try:
            re.compile(v)
        except Exception as e:
            raise ValueError(f"Invalid regex pattern: {str(e)}")
        return v


SecurityStatsResponse = SecurityViolationStatsResponse


class CustomPatternValidateRequest(BaseModel):
    pattern: str
    test_text: str


class CustomPatternValidateResponse(BaseModel):
    valid: bool
    matches: List[str]
    error: Optional[str] = None
