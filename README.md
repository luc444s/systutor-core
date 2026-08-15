# systutor-core

Kernel open source de SYSTUTOR. Framework de infraestructura para aplicaciones
multi-tenant con plugins de negocio. Licencia MIT.

## Que incluye

- **auth**: JWT, usuarios, password hashing, sesion de usuario.
- **RBAC**: permisos declarativos (catalogo global) y roles por tenant.
- **tenancy**: aislamiento activo por `tenant_id` + `branch_id`.
- **auditoria**: `audit_log` persistente con actor, entidad, resultado y correlacion.
- **eventos**: event bus con `event_log` y `event_outbox`, dispatcher reutilizable
  sin Redis y worker Dramatiq.
- **runtime de plugins**: descubrimiento por manifiesto, validacion estricta,
  dependencias, estados (discovered → validated → installed → enabled),
  migraciones propias por plugin y hooks de ciclo de vida.
- **documentos**: versionado de documentos por entidad con render PDF y
  descarga firmada (signed URLs).
- **firmas**: sesiones de firma con evidencia por entidad.
- **SDK**: `systutor.sdk` para construir plugins (contexto, permisos, eventos, rutas).
- **contratos**: `systutor.contracts` con contratos compartidos de eventos, auditoria y plugins.

## Que NO incluye (por diseno)

- Ningun dominio de negocio. Los modulos de negocio viven en plugins externos
  que declaran identidad, version, permisos, eventos y migraciones propias.
- Migraciones del negocio. Este repo trae el baseline de modelos; las
  aplicaciones huesped mantienen su propio arbol Alembic.

## Estructura

```text
src/systutor/
├── kernel/       auth, audit, documents, events, permissions, plugins,
│                 signatures, tasks, tenants
├── core/         config, database, errors, lifecycle, logging, pagination,
│                 request_context, cache
├── api/          deps, seed, v1 (management APIs + system)
├── contracts/    contratos compartidos
└── sdk/          SDK de plugins
app/              aplicacion API ejecutable de referencia
tests/            suite del kernel (SQLite, sin plugins de negocio)
```

## Instalacion

```bash
python3 -m pip install -e ".[dev]"
```

## Configuracion

Variables de entorno (prefijo `SYSTUTOR_`):

| Variable | Default | Descripcion |
|---|---|---|
| `SYSTUTOR_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/systutor` | URL SQLAlchemy |
| `SYSTUTOR_REDIS_URL` | `redis://localhost:6379/0` | Redis para cache y worker |
| `SYSTUTOR_JWT_SECRET_KEY` | `change-me` | Clave JWT (obligatoria en produccion) |
| `SYSTUTOR_PLUGINS_DIR` | `<repo>/plugins` | Directorio de plugins |
| `SYSTUTOR_CORS_ORIGINS` | `http://localhost:5173,...` | Origenes CORS separados por coma |
| `SYSTUTOR_API_PREFIX` | `/api/v1` | Prefijo de la API |

El `.env` en la raiz del proyecto se carga automaticamente. Ver `.env.example`.

## Aplicacion huesped

Una aplicacion huesped (app privada con plugins de negocio) usa el kernel asi:

```python
from fastapi import FastAPI
from systutor.core.config import Settings, get_settings, register_settings_factory
from systutor.core.lifecycle import bootstrap_app_state, lifespan

# 1. (Opcional) subclase de Settings con opciones propias del negocio
class AppSettings(Settings):
    extra_business_flag: bool = True

# 2. Registrar la factory ANTES de la primera llamada a get_settings()
register_settings_factory(AppSettings)

app = FastAPI(lifespan=lifespan)
settings = get_settings()
bootstrap_app_state(app, settings)
```

Los plugins se descubren desde `settings.plugins_dir` y sus routers se montan
en `<api_prefix>/plugins/<plugin_id>` al habilitarse.

## Plugin minimo

```json
{
  "id": "mi-modulo",
  "name": "Mi Modulo",
  "version": "0.1.0",
  "api_version": "1",
  "requires": [],
  "backend_entrypoint": "backend.plugin:register",
  "frontend_entrypoint": "frontend/register.ts",
  "permissions": ["mi-modulo.documento.read"],
  "events": ["mi-modulo.documento.creado"]
}
```

```python
from systutor.sdk import PluginContext

def register(context: PluginContext) -> None:
    context.register_permissions(["mi-modulo.documento.read"])
    context.register_events(["mi-modulo.documento.creado"])
```

## Ejecutar la API de referencia

```bash
uvicorn app.main:app --reload
```

- `GET /api/v1/system/health`
- `GET /api/v1/system/ready`
- `POST /api/v1/auth/login`
- `GET /api/v1/core/users` (requiere rol con `core.users.read`)

## Seed demo

```bash
python3 -c "
from app.main import app
from systutor.api.seed import seed_demo_data
from systutor.core.database import build_session_factory

settings = app.state.settings
with build_session_factory(settings)() as db:
    result = seed_demo_data(db, settings, app.state.plugin_runtime.list_results())
    print(result)
"
```

Credenciales por defecto: `admin@example.com` / `ChangeMe123!` (cambiar en produccion).

## Pruebas

```bash
python3 -m pytest tests -q
ruff check .
python3 -m pyright
```

## Licencia

MIT. Ver `LICENSE`.
