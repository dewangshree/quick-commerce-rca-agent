"""Read-only analytics endpoints (role: viewer+)."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from qc_rca.analytics import service
from qc_rca.analytics.rca import render_markdown
from qc_rca.db.models import Role
from qc_rca.security.auth import Principal, require_role

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])
Viewer = Annotated[Principal, Depends(require_role(Role.viewer))]
City = Annotated[str | None, Query(max_length=60, pattern=r"^[A-Za-z .\-]+$")]
StoreCode = Annotated[str, Query(max_length=20, pattern=r"^[A-Za-z0-9_]+$")]


def _default_date(d: date | None) -> date:
    if d:
        return d
    dates = service.real_dates() or service.available_dates()  # prefer real data over synthetic
    if not dates:
        raise service.NotFoundError("No data loaded")
    return date.fromisoformat(dates[-1])


@router.get("/meta")
def meta(_: Viewer):
    return {"cities": service.list_cities(), "dates": service.available_dates(),
            "date_info": service.dataset_dates(), "stores": service.list_stores()}


@router.get("/daily-trend")
def daily_trend(_: Viewer, city: City = None,
                store: Annotated[str | None, Query(max_length=20, pattern=r"^[A-Za-z0-9_]+$")] = None):
    return service.daily_trend(city, store)


@router.get("/overview")
def overview(_: Viewer, day: Annotated[date | None, Query(alias="date")] = None, city: City = None):
    d = _default_date(day)
    return {"overview": service.sla_overview(d, city), "by_hour": service.breach_by_hour(d, city)}


@router.get("/cities")
def cities(_: Viewer, day: Annotated[date | None, Query(alias="date")] = None):
    return service.city_comparison(_default_date(day))


@router.get("/cities/{city}/stores")
def city_stores(city: Annotated[str, Path(max_length=60, pattern=r"^[A-Za-z .\-]+$")], _: Viewer,
                day: Annotated[date | None, Query(alias="date")] = None):
    return service.city_summary(city, _default_date(day))


@router.get("/breach-by-hour")
def breach_by_hour(_: Viewer, day: Annotated[date | None, Query(alias="date")] = None, city: City = None,
                   store: Annotated[str | None, Query(max_length=20, pattern=r"^[A-Za-z0-9_]+$")] = None):
    return service.breach_by_hour(_default_date(day), city, store)


@router.get("/top-stores")
def top_stores(_: Viewer, day: Annotated[date | None, Query(alias="date")] = None, city: City = None,
               limit: Annotated[int, Query(ge=1, le=100)] = 10,
               min_orders: Annotated[int, Query(ge=0, le=100000)] = 50):
    return service.top_breaching_stores(_default_date(day), city, limit, min_orders)


@router.get("/stores/compare")
def compare_stores(_: Viewer, stores: Annotated[str, Query(max_length=120, pattern=r"^[A-Za-z0-9_,]+$")],
                   day: Annotated[date | None, Query(alias="date")] = None):
    codes = [s for s in stores.split(",") if s][:5]
    return service.store_comparison(codes, _default_date(day))


@router.get("/stores/{store}/hours")
def store_hours(store: Annotated[str, Path(max_length=20, pattern=r"^[A-Za-z0-9_]+$")], _: Viewer, day: Annotated[date | None, Query(alias="date")] = None,
                hour_from: Annotated[int, Query(ge=0, le=23)] = 0, hour_to: Annotated[int, Query(ge=0, le=23)] = 23):
    return service.store_hours(store, _default_date(day), hour_from, hour_to)


@router.get("/stores/{store}/rca")
def store_rca(store: Annotated[str, Path(max_length=20, pattern=r"^[A-Za-z0-9_]+$")], _: Viewer, day: Annotated[date | None, Query(alias="date")] = None):
    rca = service.store_day_rca(store, _default_date(day))
    return {"rca": rca.model_dump(), "markdown": render_markdown(rca)}
