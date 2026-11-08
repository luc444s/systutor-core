from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from systutor.core.cache import cache
from systutor.core.config import get_settings
from systutor.kernel.auth.models import User
from systutor.kernel.permissions.service import list_user_permissions
from systutor.kernel.tenants.service import list_user_warehouse_ids

CONTEXT_VERSION_TTL_SECONDS = 86400


@dataclass(slots=True, frozen=True)
class TenantContext:
    current_tenant_id: str
    current_branch_id: str | None
    current_user_id: str
    current_permissions: tuple[str, ...]
    current_warehouse_ids: tuple[str, ...] | None
    is_superadmin: bool

    def has_permission(self, permission_name: str) -> bool:
        return self.is_superadmin or permission_name in self.current_permissions

    def has_warehouse_access(self, warehouse_id: str) -> bool:
        return (
            self.is_superadmin
            or self.current_warehouse_ids is None
            or warehouse_id in self.current_warehouse_ids
        )


def _version_key(tenant_id: str) -> str:
    return f"tenant-context-version:{tenant_id}"


def _context_version(tenant_id: str) -> int:
    try:
        raw = cache().get(_version_key(tenant_id))
        return int(raw) if raw else 0
    except Exception:  # pragma: no cover - la caché nunca debe romper auth
        return 0


def invalidate_tenant_context_cache(tenant_id: str) -> None:
    """Sube la versión del tenant: invalida todos sus contextos cacheados."""
    try:
        cache().set(
            _version_key(tenant_id),
            str(_context_version(tenant_id) + 1),
            ttl=CONTEXT_VERSION_TTL_SECONDS,
        )
    except Exception:  # pragma: no cover
        pass


def _context_key(tenant_id: str, user_id: str, version: int) -> str:
    return f"tenant-context:{tenant_id}:{user_id}:{version}"


def _context_to_json(context: TenantContext) -> str:
    return json.dumps(asdict(context), separators=(",", ":"))


def _context_from_json(raw: str) -> TenantContext:
    data = json.loads(raw)
    data["current_permissions"] = tuple(data.get("current_permissions") or ())
    warehouse_ids = data.get("current_warehouse_ids")
    data["current_warehouse_ids"] = tuple(warehouse_ids) if warehouse_ids is not None else None
    return TenantContext(**data)


def build_tenant_context(db: Session, user: User) -> TenantContext:
    settings = get_settings()
    cache_enabled = settings.auth_cache_enabled and settings.auth_cache_ttl_seconds > 0
    version = _context_version(user.tenant_id) if cache_enabled else 0
    key = _context_key(user.tenant_id, user.id, version)

    if cache_enabled:
        try:
            cached = cache().get(key)
            if cached:
                return _context_from_json(cached)
        except Exception:  # pragma: no cover - fallback a base de datos
            pass

    context = TenantContext(
        current_tenant_id=user.tenant_id,
        current_branch_id=user.branch_id,
        current_user_id=user.id,
        current_permissions=tuple(
            list_user_permissions(db, user_id=user.id, tenant_id=user.tenant_id)
        ),
        current_warehouse_ids=(
            tuple(list_user_warehouse_ids(db, tenant_id=user.tenant_id, user_id=user.id)) or None
        ),
        is_superadmin=user.is_superadmin,
    )

    if cache_enabled:
        try:
            cache().set(key, _context_to_json(context), ttl=settings.auth_cache_ttl_seconds)
        except Exception:  # pragma: no cover
            pass

    return context
