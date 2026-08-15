# AGENTS.md — systutor-core

## Proposito

Reglas operativas para agentes de IA en el kernel open source de SYSTUTOR (MIT).

## Principios

- kernel pequeno; cero logica de negocio (no logistics, no crm, no productos,
  no stock, no ventas, no commerce — ni nombres, ni permisos, ni config)
- logica de negocio vive en plugins de las aplicaciones huesped
- eventos para comunicacion entre modulos, no acoplamiento directo
- toda accion importante debe ser auditable
- ningun archivo >600 lineas; archivo nuevo antes de 400
- toda feature requiere spec y tests antes del PR
- tipos y contratos publicos estables: un cambio de firma en `systutor.*` es
  breaking para aplicaciones huesped

## Superficie publica

- `systutor.kernel` — auth, RBAC, tenancy, auditoria, eventos, plugins, documentos, firmas, tasks
- `systutor.core` — config (con `register_settings_factory`), database, lifecycle, errors, logging
- `systutor.api` — deps y management APIs del kernel
- `systutor.contracts` — contratos compartidos
- `systutor.sdk` — SDK para construir plugins

## Reglas

1. leer `docs/` de la aplicacion huesped nunca aplica aqui; este repo solo tiene kernel
2. no importar de `app.*` dentro de `src/systutor` (app es la referencia ejecutable)
3. no agregar campos de negocio a `Settings`: la app huesped los agrega por subclase
4. nuevos permisos del kernel usan namespace `core.*` y se agregan a
   `BASE_PERMISSIONS` en `systutor/api/seed.py`
5. nuevas migraciones de modelos kernel se escriben aqui con Alembic
6. todo cambio publico actualiza `README.md`

## Calidad

- `ruff check .`, `python3 -m pyright`, `python3 -m pytest tests -q` verdes antes de commit
- tests con SQLite + `TestClient`; plugins de prueba generados en tmp dirs (ver `tests/conftest.py`)
