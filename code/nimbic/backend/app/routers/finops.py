import csv
import io
import uuid
import calendar
from uuid import UUID
from decimal import Decimal
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.routers.orgs import get_current_admin_api_key
from app.models.api_key import ApiKey
from app.models.finops import Team, Project, Budget, BudgetAlert, BudgetScopeEnum
from app.models.request_log import RequestLog
from app.finops import budget_service, forecast_service
from app.schemas.finops import (
    TeamCreate, TeamUpdate, TeamResponse,
    ProjectCreate, ProjectUpdate, ProjectResponse,
    BudgetCreate, BudgetStatusResponse,
    SpendSummaryResponse, ForecastResponse,
    ModelComparisonResponse, BudgetAlertResponse,
    ChargebackResponse
)

router = APIRouter(prefix="/finops", tags=["FinOps Management & Analytics"])


# ==========================================
# TEAMS ENDPOINTS
# ==========================================

@router.get("/teams", response_model=List[TeamResponse])
async def list_teams(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists all teams in the organization, attaching active spend and budget usage percentages.
    """
    stmt = select(Team).where(Team.org_id == api_key.org_id)
    res = await db.execute(stmt)
    teams = res.scalars().all()

    results = []
    for team in teams:
        # Calculate monthly spend for this team to attach usage indicators
        spend = await budget_service.get_spend(
            org_id=api_key.org_id,
            scope_type="team",
            scope_id=team.id,
            period="monthly",
            db=db
        )
        
        results.append(TeamResponse(
            id=team.id,
            org_id=team.org_id,
            name=team.name,
            department=team.department,
            budget_limit_usd=team.budget_limit_usd,
            budget_alert_pct=team.budget_alert_pct,
            created_at=team.created_at,
            updated_at=team.updated_at,
            current_spend_usd=spend["total_cost_usd"],
            usage_pct=spend["usage_pct"]
        ))
    return results


@router.post("/teams", response_model=TeamResponse)
async def create_team(
    payload: TeamCreate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Registers a new team inside the organization.
    """
    team = Team(
        org_id=api_key.org_id,
        name=payload.name,
        department=payload.department,
        budget_limit_usd=payload.budget_limit_usd,
        budget_alert_pct=payload.budget_alert_pct
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return team


@router.patch("/teams/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: UUID,
    payload: TeamUpdate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Updates general metadata and budget thresholds of an existing team.
    """
    stmt = select(Team).where(and_(Team.id == team_id, Team.org_id == api_key.org_id))
    res = await db.execute(stmt)
    team = res.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")

    if payload.name is not None:
        team.name = payload.name
    if payload.department is not None:
        team.department = payload.department
    if payload.budget_limit_usd is not None:
        team.budget_limit_usd = payload.budget_limit_usd
    if payload.budget_alert_pct is not None:
        team.budget_alert_pct = payload.budget_alert_pct

    db.add(team)
    await db.commit()
    await db.refresh(team)
    return team


@router.delete("/teams/{team_id}")
async def delete_team(
    team_id: UUID,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Removes the team record from the database.
    """
    stmt = select(Team).where(and_(Team.id == team_id, Team.org_id == api_key.org_id))
    res = await db.execute(stmt)
    team = res.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")

    await db.delete(team)
    await db.commit()
    return {"message": "Team successfully deleted."}


# ==========================================
# PROJECTS ENDPOINTS
# ==========================================

@router.get("/projects", response_model=List[ProjectResponse])
async def list_projects(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists all projects in the organization with active spend and budget usage stats.
    """
    stmt = select(Project).where(Project.org_id == api_key.org_id)
    res = await db.execute(stmt)
    projects = res.scalars().all()

    results = []
    for project in projects:
        spend = await budget_service.get_spend(
            org_id=api_key.org_id,
            scope_type="project",
            scope_id=project.id,
            period="monthly",
            db=db
        )
        results.append(ProjectResponse(
            id=project.id,
            org_id=project.org_id,
            team_id=project.team_id,
            name=project.name,
            budget_limit_usd=project.budget_limit_usd,
            budget_alert_pct=project.budget_alert_pct,
            is_active=project.is_active,
            created_at=project.created_at,
            updated_at=project.updated_at,
            current_spend_usd=spend["total_cost_usd"],
            usage_pct=spend["usage_pct"]
        ))
    return results


@router.post("/projects", response_model=ProjectResponse)
async def create_project(
    payload: ProjectCreate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a new project linked optionally to a team.
    """
    if payload.team_id:
        # Validate team exists
        team_stmt = select(Team).where(and_(Team.id == payload.team_id, Team.org_id == api_key.org_id))
        team_res = await db.execute(team_stmt)
        if not team_res.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Linked team does not exist.")

    project = Project(
        org_id=api_key.org_id,
        team_id=payload.team_id,
        name=payload.name,
        budget_limit_usd=payload.budget_limit_usd,
        budget_alert_pct=payload.budget_alert_pct,
        is_active=True
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Updates project thresholds, active states, or parent team associations.
    """
    stmt = select(Project).where(and_(Project.id == project_id, Project.org_id == api_key.org_id))
    res = await db.execute(stmt)
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    if payload.name is not None:
        project.name = payload.name
    if payload.team_id is not None:
        team_stmt = select(Team).where(and_(Team.id == payload.team_id, Team.org_id == api_key.org_id))
        team_res = await db.execute(team_stmt)
        if not team_res.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Linked team does not exist.")
        project.team_id = payload.team_id
    if payload.budget_limit_usd is not None:
        project.budget_limit_usd = payload.budget_limit_usd
    if payload.budget_alert_pct is not None:
        project.budget_alert_pct = payload.budget_alert_pct
    if payload.is_active is not None:
        project.is_active = payload.is_active

    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: UUID,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Deactivates a project, keeping historical records but stopping active budget attribution.
    """
    stmt = select(Project).where(and_(Project.id == project_id, Project.org_id == api_key.org_id))
    res = await db.execute(stmt)
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    project.is_active = False
    db.add(project)
    await db.commit()
    return {"message": "Project successfully deactivated."}


# ==========================================
# BUDGETS ENDPOINTS
# ==========================================

@router.get("/budgets", response_model=List[BudgetStatusResponse])
async def list_budgets(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns all configured budgets with current spend status (ok/warning/critical/exceeded).
    """
    return await budget_service.get_all_budgets(api_key.org_id, db)


@router.post("/budgets", response_model=BudgetStatusResponse)
async def create_budget(
    payload: BudgetCreate,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Sets or updates a budget limit for a specific scope (org, team, project, or user).
    """
    try:
        budget = await budget_service.set_budget(
            org_id=api_key.org_id,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            limit_usd=payload.limit_usd,
            period=payload.period,
            alert_pct=payload.alert_pct,
            hard_limit=payload.hard_limit,
            db=db
        )
        
        # Format the newly upserted budget into a status response
        all_budgets = await budget_service.get_all_budgets(api_key.org_id, db)
        for b in all_budgets:
            if b["id"] == str(budget.id):
                return b
        raise HTTPException(status_code=500, detail="Failed to retrieve status for created budget.")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upsert budget: {str(e)}")


@router.delete("/budgets/{budget_id}")
async def delete_budget(
    budget_id: UUID,
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Removes a budget threshold, disabling enforcement and alerting.
    """
    stmt = select(Budget).where(and_(Budget.id == budget_id, Budget.org_id == api_key.org_id))
    res = await db.execute(stmt)
    budget = res.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found.")

    await db.delete(budget)
    await db.commit()
    return {"message": "Budget successfully deleted."}


# ==========================================
# SPEND & ANALYTICS ENDPOINTS
# ==========================================

@router.get("/spend/org", response_model=SpendSummaryResponse)
async def get_org_spend(
    period: str = Query("monthly", pattern="^(daily|weekly|monthly)$"),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns org-level spend summary.
    """
    return await budget_service.get_spend(api_key.org_id, "org", api_key.org_id, period, db)


@router.get("/spend/teams", response_model=List[SpendSummaryResponse])
async def list_team_spends(
    period: str = Query("monthly", pattern="^(daily|weekly|monthly)$"),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns spend summaries side by side for all teams in the organization.
    """
    stmt = select(Team).where(Team.org_id == api_key.org_id)
    res = await db.execute(stmt)
    teams = res.scalars().all()

    results = []
    for team in teams:
        spend = await budget_service.get_spend(api_key.org_id, "team", team.id, period, db)
        results.append(spend)
    return results


@router.get("/spend/projects", response_model=List[SpendSummaryResponse])
async def list_project_spends(
    period: str = Query("monthly", pattern="^(daily|weekly|monthly)$"),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns spend summaries for all active projects in the organization.
    """
    stmt = select(Project).where(and_(Project.org_id == api_key.org_id, Project.is_active == True))
    res = await db.execute(stmt)
    projects = res.scalars().all()

    results = []
    for project in projects:
        spend = await budget_service.get_spend(api_key.org_id, "project", project.id, period, db)
        results.append(spend)
    return results


@router.get("/spend/users")
async def list_user_spends(
    period: str = Query("monthly", pattern="^(daily|weekly|monthly)$"),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns per-user spends for the top 20 users by cost.
    """
    period_enum = budget_service.BudgetPeriodEnum(period)
    period_start = budget_service._get_period_start(period_enum)

    # Aggregate by user identifier
    stmt = select(
        RequestLog.user_identifier,
        func.sum(RequestLog.cost_usd).label("cost_usd"),
        func.count(RequestLog.id).label("requests"),
        func.sum(RequestLog.total_tokens).label("total_tokens")
    ).where(
        and_(
            RequestLog.org_id == api_key.org_id,
            RequestLog.created_at >= period_start,
            RequestLog.status_code == 200,
            RequestLog.user_identifier.isnot(None)
        )
    ).group_by(RequestLog.user_identifier).order_by(func.sum(RequestLog.cost_usd).desc()).limit(20)

    res = await db.execute(stmt)
    results = []
    for r in res.fetchall():
        results.append({
            "user_identifier": r.user_identifier,
            "cost_usd": Decimal(r.cost_usd or "0.000000"),
            "requests": r.requests or 0,
            "total_tokens": r.total_tokens or 0
        })
    return results


@router.get("/forecast")
async def get_forecasts(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns cost forecast projection for both the organization level and each team.
    """
    org_forecast = await forecast_service.forecast_spend(api_key.org_id, "org", api_key.org_id, db)
    
    # Get all teams
    team_stmt = select(Team).where(Team.org_id == api_key.org_id)
    team_res = await db.execute(team_stmt)
    teams = team_res.scalars().all()

    team_forecasts = []
    for team in teams:
        tf = await forecast_service.forecast_spend(api_key.org_id, "team", team.id, db)
        team_forecasts.append({
            "team_id": str(team.id),
            "team_name": team.name,
            "forecast": tf
        })

    return {
        "org": org_forecast,
        "teams": team_forecasts
    }


@router.get("/model-comparison", response_model=ModelComparisonResponse)
async def get_model_comparison_comparison(
    period_days: int = Query(30, ge=1, le=60),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns model scenario comparisons (CFO slide) for optimized routing options.
    """
    return await forecast_service.get_model_comparison(api_key.org_id, period_days, db)


@router.get("/alerts", response_model=List[BudgetAlertResponse])
async def list_recent_alerts(
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves the 50 most recent budget alerts logged.
    """
    stmt = select(BudgetAlert).where(BudgetAlert.org_id == api_key.org_id).order_by(BudgetAlert.created_at.desc()).limit(50)
    res = await db.execute(stmt)
    return res.scalars().all()


# ==========================================
# CHARGEBACK ENDPOINTS
# ==========================================

@router.get("/chargeback", response_model=ChargebackResponse)
async def get_chargeback_report(
    period: str = Query(..., regex="^\\d{4}-\\d{2}$"),  # Format YYYY-MM
    group_by: str = Query("team", regex="^(team|department|project)$"),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Compiles a comprehensive monthly chargeback report grouped by team, department, or project.
    """
    # Parse period string to start/end dates
    year, month = map(int, period.split("-"))
    start_date = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    _, num_days = calendar.monthrange(year, month)
    end_date = datetime(year, month, num_days, 23, 59, 59, 999999, tzinfo=timezone.utc)

    # 1. Total cost in this period for percentage calculations
    total_stmt = select(func.sum(RequestLog.cost_usd)).where(
        and_(
            RequestLog.org_id == api_key.org_id,
            RequestLog.created_at >= start_date,
            RequestLog.created_at <= end_date,
            RequestLog.status_code == 200
        )
    )
    total_res = await db.execute(total_stmt)
    total_cost = Decimal(total_res.scalar() or "0.000000")

    # 2. Group query
    if group_by == "team":
        group_col = RequestLog.team_id
    elif group_by == "project":
        group_col = RequestLog.project_id
    else:  # department
        group_col = RequestLog.department

    stmt = select(
        group_col.label("key"),
        func.sum(RequestLog.cost_usd).label("cost_usd"),
        func.count(RequestLog.id).label("requests"),
        func.sum(RequestLog.total_tokens).label("total_tokens")
    ).where(
        and_(
            RequestLog.org_id == api_key.org_id,
            RequestLog.created_at >= start_date,
            RequestLog.created_at <= end_date,
            RequestLog.status_code == 200
        )
    ).group_by(group_col)

    res = await db.execute(stmt)
    items = []
    total_report_cost = Decimal("0.000000")
    total_report_requests = 0
    total_report_tokens = 0

    for r in res.fetchall():
        key_val = r.key
        cost = Decimal(r.cost_usd or "0.000000")
        reqs = r.requests or 0
        tokens = r.total_tokens or 0

        # Resolve display name for groups
        group_name = "Unassigned"
        if key_val:
            if group_by == "team":
                t_res = await db.execute(select(Team.name).where(Team.id == key_val))
                t_name = t_res.scalar_one_or_none()
                group_name = t_name if t_name else str(key_val)
            elif group_by == "project":
                p_res = await db.execute(select(Project.name).where(Project.id == key_val))
                p_name = p_res.scalar_one_or_none()
                group_name = p_name if p_name else str(key_val)
            else:  # department
                group_name = str(key_val)

        percentage = float((cost / total_cost) * 100) if total_cost > 0 else 0.0

        items.append({
            "group_key": group_name,
            "total_cost_usd": cost,
            "total_requests": reqs,
            "total_tokens": tokens,
            "percentage": percentage
        })

        total_report_cost += cost
        total_report_requests += reqs
        total_report_tokens += tokens

    return {
        "period": period,
        "group_by": group_by,
        "total_cost_usd": total_report_cost,
        "total_requests": total_report_requests,
        "total_tokens": total_report_tokens,
        "items": items
    }


@router.get("/chargeback/download")
async def download_chargeback_report(
    period: str = Query(..., regex="^\\d{4}-\\d{2}$"),
    group_by: str = Query("team", regex="^(team|department|project)$"),
    api_key: ApiKey = Depends(get_current_admin_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Compiles a comprehensive monthly chargeback report and downloads it as a CSV file.
    """
    report = await get_chargeback_report(period=period, group_by=group_by, api_key=api_key, db=db)
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write metadata headers
    writer.writerow(["SaaN Shield AI Gateway Chargeback Report"])
    writer.writerow(["Period", report["period"]])
    writer.writerow(["Grouped By", report["group_by"]])
    writer.writerow([])
    
    # Write summary
    writer.writerow(["Total Cost (USD)", f"${report['total_cost_usd']:.6f}"])
    writer.writerow(["Total Requests", report["total_requests"]])
    writer.writerow(["Total Tokens", report["total_tokens"]])
    writer.writerow([])
    
    # Write table data
    writer.writerow([report["group_by"].capitalize(), "Cost (USD)", "Requests", "Tokens", "Percentage (%)"])
    for item in report["items"]:
        writer.writerow([
            item["group_key"],
            f"{item['total_cost_usd']:.6f}",
            item["total_requests"],
            item["total_tokens"],
            f"{item['percentage']:.2f}%"
        ])
        
    output.seek(0)
    filename = f"chargeback_{report['group_by']}_{period}.csv"
    
    headers = {
        "Content-Disposition": f"attachment; filename={filename}"
    }
    
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers=headers
    )
