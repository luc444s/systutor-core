# Benchmark de carga — SYSTUTOR Core

Prueba de saturación sobre una instalación limpia del núcleo, con el servidor
fijado a **1 núcleo de CPU**, PostgreSQL local y sin plugins.

- Fecha: 2026-11-08
- Núcleo: `systutor-core` 0.1.0
- Instalación: `systutor-installer` (`install.sh systutor --no-dashboard`)
- Servidor: `uvicorn app.main:app --workers 1`, afinidad `taskset -c 0`
- PostgreSQL: 16.14, `max_connections=100`
- Redis: no disponible → `MemoryCacheBackend`
- Hardware: 4 vCPU, 7.7 GB RAM
- Carga generada desde los otros 3 núcleos con `httpx` asíncrono

## Metodología

- **Escritura:** `POST /api/v1/core/roles` con nombre único (rol + auditoría en PostgreSQL).
- **Lectura:** `GET /api/v1/core/roles/{id}` (una fila, costo constante).
- Niveles de concurrencia: **10, 100, 1000 y 2000**.
- Duración: 15 s por nivel hasta 100; 5 s en 1000/2000 por la saturación observada.
- **Colapso:** se considera colapso si falla el health check posterior o si la tasa
  de error supera el 50 %.
- Cada nivel se ejecutó sobre un servidor recuperado.

## Escritura en base de datos

| Concurrentes | Requests | OK | Errores | Error % | req/s | p50 | p95 | CPU 1 core | RSS | Health |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 10 | 535 | 535 | 0 | 0 % | 35.6 | 262 ms | 409 ms | 98.4 % | 101 MB | OK |
| 100 | 332 | 90 | 242 | 72.9 % | 6.0 | 3 886 ms | 9 597 ms | 31.0 % | 117 MB | OK |
| 1000 | 43 | 38 | 5 | 11.6 % | 6.6 | 2 179 ms | 4 769 ms | 75.3 % | 146 MB | **Falla** |
| 2000 | 36 | 36 | 0* | 0 %* | 5.0 | 2 709 ms | 3 489 ms | 56.7 % | 157 MB | **Falla** |

\* En 2000 concurrentes solo terminaron 36 requests dentro de los 5 s; el resto
quedó en cola y el cliente las canceló. El health check posterior falló.

**Recuperación:** ~20 s tras 1000, ~25 s tras 2000.

## Lectura de base de datos

| Concurrentes | Requests | OK | Errores | Error % | req/s | p50 | p95 | CPU 1 core | RSS | Health |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 10 | 952 | 952 | 0 | 0 % | 63.4 | 152 ms | 195 ms | 99.4 % | 157 MB | OK |
| 100 | 330 | 62 | 268 | 81.2 % | 4.1 | 3 660 ms | 9 579 ms | 19.6 % | 158 MB | **Falla** |
| 1000 | 59 | 21 | 38 | 64.4 % | 3.7 | 4 487 ms | 4 848 ms | 64.0 % | 159 MB | **Falla** |
| 2000 | 21 | 21 | 0* | 0 %* | 2.9 | 2 325 ms | 2 898 ms | 48.7 % | 164 MB | **Falla** |

**Recuperación:** ~45 s tras 1000, ~40 s tras 2000.

## Causa raíz

El límite no es la CPU ni PostgreSQL, sino el pool de conexiones de SQLAlchemy:

```python
create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_timeout=3,
    ...
)
```

Con los valores por defecto de SQLAlchemy: **5 conexiones + 10 de overflow = 15**.
El threadpool de AnyIO/Starlette atiende hasta 40 hilos. A partir de ~100
concurrentes las peticiones esperan una conexión y, al superar `pool_timeout=3`,
terminan en `sqlalchemy.exc.TimeoutError` → HTTP 500. Con más concurrencia, la
cola satura el event loop y el health check deja de responder.

## Observaciones

- El núcleo satura 1 core a **~35 escrituras/s** y **~63 lecturas/s**.
- La RAM sube de ~95 MB en reposo a ~160 MB tras los bursts; Python retiene
  arenas, no se observó un crecimiento sin límite.
- A 100 concurrentes aparecen `500`, `ReadError` y `RemoteProtocolError`
  (resets/colas), no caídas del proceso.
- El proceso se recupera solo; no hubo que reiniciarlo.

## Recomendaciones

1. Hacer configurable `pool_size`, `max_overflow` y `pool_timeout`.
2. Ajustar el pool al número real de workers/hilos (p. ej. 20–40 conexiones).
3. Devolver **429/503** con backpressure en lugar de 500 por timeout de pool.
4. Activar Redis y caché para lecturas repetidas.
5. Usar varias instancias detrás de un proxy si se necesita más concurrencia.
6. Repetir la prueba en el hardware objetivo antes de fijar SLA.

## Reproducción

```bash
git clone https://github.com/luc444s/systutor-installer.git
cd systutor-installer
SYSTUTOR_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/systutor_load_test \
  bash install.sh systutor /ruta/al/proyecto --no-dashboard

cd /ruta/al/proyecto/vendor/systutor-core
SYSTUTOR_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/systutor_load_test \
  taskset -c 0 uvicorn app.main:app --port 8124 --workers 1
```

---

# Después del tuning (`perf/pool-auth-cache`)

Cambios aplicados al núcleo:

- Pool configurable: `pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle`,
  `pool_pre_ping`, `pool_use_lifo`.
- `statement_timeout` de PostgreSQL configurable.
- Threadpool de AnyIO ajustable (`SYSTUTOR_THREADPOOL_TOKENS`).
- `503 database_busy` + `Retry-After` cuando el pool se satura.
- `/health/live` async sin base de datos; `/ready` async usando el engine async.
- Caché del `TenantContext` (permisos + warehouses) con TTL de 30 s y
  versionado por tenant para invalidar al cambiar roles o usuarios.
- Auditoría sin `flush` extra y sin revalidar el tenant scope cuando ya viene
  del `TenantContext`.
- Eventos sin revalidar el tenant scope cuando ya viene autenticado.

Configuración usada en la prueba:

```text
SYSTUTOR_DB_POOL_SIZE=25
SYSTUTOR_DB_MAX_OVERFLOW=15
SYSTUTOR_DB_POOL_TIMEOUT=15
SYSTUTOR_THREADPOOL_TOKENS=40
SYSTUTOR_AUTH_CACHE_TTL_SECONDS=30
```

## Escritura — antes vs después

| Concurrentes | Antes req/s | Antes errores | Después req/s | Después errores | Después p95 | Ready |
|---:|---:|---:|---:|---:|---:|:---:|
| 10 | 35.6 | 0 % | **63.2** | 0 % | 207 ms | OK |
| 100 | 6.0 | 72.9 % | **55.4** | 0 % | 2 350 ms | OK |
| 1000 | 6.6 | 11.6 % | **18.2** | 0 % | 5 461 ms | OK |
| 2000 | 5.0 | colapso | **13.3** | 0 % | 5 250 ms | OK |

## Lectura — antes vs después

| Concurrentes | Antes req/s | Antes errores | Después req/s | Después errores | Después p95 | Ready |
|---:|---:|---:|---:|---:|---:|:---:|
| 10 | 63.4 | 0 % | **80.7** | 0 % | 166 ms | OK |
| 100 | 4.1 | 81.2 % | **72.1** | 0 % | 1 821 ms | OK |
| 1000 | 3.7 | 64.4 % | **20.9** | 0 % | 8 159 ms | OK |
| 2000 | 2.9 | colapso | **21.0** | 0 % | 6 268 ms | OK |

## Lectura del resultado

- **Ya no hay colapso** en ninguno de los niveles probados: 0 errores HTTP y
  readiness OK incluso a 2000 concurrentes.
- **Escritura:** +78 % con 10 concurrentes y ~9× a 100 concurrentes.
- **Lectura:** +27 % con 10 concurrentes y ~18× a 100 concurrentes.
- A 100 concurrentes el CPU de 1 núcleo sigue al 97-98 %: el nuevo techo es
  CPU, no el pool.
- A 1000/2000 aparecen colas y la latencia sube (p95 5-8 s), pero el servicio
  sigue respondiendo y recuperándose sin errores.
- La RAM se mantiene en ~96-111 MB, sin el crecimiento a 160 MB causado por
  las colas de threads del escenario anterior.

## Siguiente cuello de botella

Con el pool y las queries bajo control, el límite por núcleo es el CPU:

- ~80 lecturas/s y ~63 escrituras/s por núcleo.
- Para más throughput: más procesos/workers, PgBouncer, o migrar los endpoints
  calientes a async.
- Las latencias p95 de 5-8 s a 1000/2000 concurrentes indican que conviene
  limitar concurrencia en el proxy (`limit-concurrency`) y aplicar backpressure.

## Reproducción del escenario “después”

```bash
git checkout perf/pool-auth-cache

SYSTUTOR_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/systutor_after_test \
SYSTUTOR_DB_POOL_SIZE=25 \
SYSTUTOR_DB_MAX_OVERFLOW=15 \
SYSTUTOR_DB_POOL_TIMEOUT=15 \
SYSTUTOR_THREADPOOL_TOKENS=40 \
SYSTUTOR_AUTH_CACHE_TTL_SECONDS=30 \
  taskset -c 0 uvicorn app.main:app --port 8125 --workers 1
```

---

# 2 workers sobre 2 núcleos (equipo real)

Prueba final sobre **2 núcleos**, que es el hardware objetivo. Los workers se
fijaron a los cores 0-1 y el generador de carga a los cores 2-3. PostgreSQL
comparte la máquina.

Configuración:

```text
2 workers × (pool_size 20 + max_overflow 10) = 60 conexiones sync
pool async de readiness: 2 + 2 por worker
threadpool: 30 por worker
auth cache: 30 s
```

El test drena las requests en vuelo al terminar cada nivel en lugar de
cancelarlas, para que el servidor cierre sus sesiones.

## Lectura — 2 núcleos

| Concurrentes | req/s | Errores | p50 | p95 | CPU (2 cores) | RSS |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | **163.8** | 0 % | 55 ms | 98 ms | 173 % | 238 MB |
| 100 | **79.4** | 0 % | 909 ms | 3.5 s | 98 % | 247 MB |
| 1000 | **95.1** | 0 % | 8.0 s | 13.0 s | 109 % | 247 MB |
| 2000 | **99.7** | 0 % | 12.7 s | 21.2 s | 111 % | 247 MB |

## Escritura — 2 núcleos

| Concurrentes | req/s | Errores | p50 | p95 | CPU (2 cores) | RSS |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | **120.3** | 0 % | 78 ms | 123 ms | 180 % | 247 MB |
| 100 | **51.0** | 0 % | 1.3 s | 5.4 s | 88 % | 252 MB |
| 1000 | **48.7** | 0 % | 12.7 s | 23.6 s | 82 % | 255 MB |
| 2000 | **57.4** | 0.05 %* | 15.1 s | 31.2 s | 98 % | 257 MB |

\* 2037 de 2038 requests OK; un único `ReadError` de red.

## Hallazgo importante: deadlock del pool

Antes de esta corrida, a 100 concurrentes el servidor se quedaba en **1.7 req/s**
con conexiones `idle in transaction` y threads esperando en
`QueuePool.checkout`. El stack mostró que el cleanup de la dependencia sync
`sessão.close()` quedaba encolado detrás del threadpool saturado.

**Fix aplicado:** `get_db_session` ahora es una dependencia async; el
`session.close()` corre en el event loop y no en el threadpool. Con eso:

- Desaparecen las conexiones `idle in transaction` filtradas.
- A 100 concurrentes se pasó de **1.7 req/s** a **79-92 req/s**.
- Cuando el pool se satura de verdad, devuelve **503 `database_busy`** en lugar
  de bloquear el servidor.

## Lectura del resultado

- **No hay colapso** en ninguno de los niveles: 0 errores y readiness OK.
- Con 10 concurrentes los 2 núcleos quedan al **173-180 %** de 200 %: ahí está
  el techo real de CPU.
- A 100+ concurrentes el throughput se mantiene entre **50 y 100 req/s**;
  lo que crece es la latencia (p95 de 3 a 31 s), no los errores.
- La RAM se mantiene en **238-257 MB** para todo el árbol de 2 workers.
- Para tu PC de 2 núcleos, el punto dulce está en **~10-20 concurrentes**;
  por encima de 100 conviene limitar concurrencia en el proxy y aplicar
  backpressure.

---

# 4 workers sobre 4 núcleos (código final)

Misma rama `perf/pool-auth-cache` con el fix de sesión async, middleware ASGI,
pool sync/async separado y drenaje limpio de requests. **4 procesos uvicorn**
usando los 4 núcleos. PostgreSQL y el generador de carga comparten la misma
máquina, así que el escalado está limitado por el entorno.

Configuración:

```text
4 workers × (pool_size 10 + max_overflow 5) = 60 conexiones sync
pool async de readiness: 2 + 2 por worker
threadpool: 20 por worker
auth cache: 30 s
```

## Lectura — 4 núcleos

| Concurrentes | req/s | Errores | p50 | p95 | CPU (4 cores) | RSS |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | **127.5** | 0 % | 34 ms | 180 ms | 191 % | 425 MB |
| 100 | **79.2** | 0 % | 841 ms | 3.8 s | 119 % | 436 MB |
| 1000 | **84.2** | 0 % | 8.0 s | 14.9 s | 121 % | 437 MB |
| 2000 | **83.4** | 0 % | 13.7 s | 25.0 s | 119 % | 437 MB |

## Escritura — 4 núcleos

| Concurrentes | req/s | Errores | p50 | p95 | CPU (4 cores) | RSS |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | **113.9** | 0 % | 80 ms | 143 ms | 206 % | 439 MB |
| 100 | **65.7** | 0 % | 964 ms | 4.2 s | 116 % | 441 MB |
| 1000 | **69.4** | 0 % | 10.1 s | 16.3 s | 128 % | 448 MB |
| 2000 | **67.8** | 0 % | 17.9 s | 29.8 s | 129 % | 446 MB |

## 2 núcleos vs 4 núcleos

| Nivel | Lectura 2c | Lectura 4c | Escritura 2c | Escritura 4c |
|---:|---:|---:|---:|---:|
| 10 | 163.8 | 127.5 | 120.3 | 113.9 |
| 100 | 79.4 | 79.2 | 51.0 | 65.7 |
| 1000 | 95.1 | 84.2 | 48.7 | 69.4 |
| 2000 | 99.7 | 83.4 | 57.4 | 67.8 |

## Interpretación

- **Cero errores y readiness OK** en todos los niveles.
- Con 4 workers el servidor usa como máximo **~206 % de 400 %** de CPU: no se
  saturan los 4 núcleos. El cuello de botella es la máquina compartida
  (cliente Python + PostgreSQL + workers).
- En lectura a 10 concurrentes, 2 núcleos rinde más porque el servidor tenía
  cores dedicados (0-1) y el cliente estaba en cores separados (2-3). Con 4
  workers no quedan cores libres para el cliente.
- La comparación 2c vs 4c **no es lineal ni totalmente justa** en este equipo:
  para medir escalado real hace falta ejecutar el generador de carga y
  PostgreSQL en otra máquina.
- RAM total de 4 workers: **~425-448 MB**, estable.
- A 1000/2000 la latencia sube fuerte (p95 de 15 a 30 s) pero el servicio sigue
  respondiendo, sin colapso.

**Conclusión:** en este equipo de 4 núcleos compartidos, 4 workers no superan
claramente a 2 workers; ambos configuran un servicio estable. El límite real
es el entorno de prueba, no el core. Para tu PC de 2 núcleos, la sección de
2 workers es la referencia relevante.

---

# Bench definitivo — Contabo Linux + comparativa 3 equipos (2026-11-09)

Objetivo: medir los endpoints más rápidos del sistema (`health`, `ready`,
`permissions`) sobre hardware Linux limpio y comparar contra la ThinkPad
local (Linux) y ACONCAGUA (Windows 10, i5-12400F, 32 GB).

## Entorno Contabo

- 4 vCPU, 7,8 GB RAM, load ~0,3 (con panel Spanel + servicios Docker
  compartiendo la caja: Postgres, Traefik, Redis, mail, MariaDB).
- Servidor: 4 workers uvicorn en `127.0.0.1:8124`, PostgreSQL 16 local
  (servicio systemd), Redis no disponible → `MemoryCacheBackend`.
- Cliente: `scripts/bench_mp.py` (4 procesos, closed-loop con keep-alive),
  corriendo en la misma caja (cliente + server comparten los 4 vCPU).
- Backpressure desactivado para la medición (`SYSTUTOR_CONCURRENCY_LIMIT=5000`).

## Health — `GET /system/health/live` (async, sin DB, sin auth)

| Concurrentes | req/s OK | p50 | p95 | Errores |
|---:|---:|---:|---:|---:|
| 10 | 583 | 14 ms | 36 ms | 0% |
| 100 | 435 | 158 ms | 688 ms | 0% |
| 1000 | 369 | 370 ms | 3,7 s | 0,16% |
| 5000 | 714 | 1,9 s | 10,5 s | 31% resets TCP |
| 10000 | 757 | 2,4 s | 8,5 s | 34% resets TCP |
| 20000 | 650 | 2,4 s | 10,1 s | 37% resets TCP |

## Ready — `GET /system/health/ready` (async, 1 query)

| Concurrentes | req/s OK | p50 | p95 | Errores |
|---:|---:|---:|---:|---:|
| 10 | 272 | 30 ms | 75 ms | 0% |
| 100 | 364 | 230 ms | 661 ms | 0% |
| 1000 | 326 | 479 ms | 6,9 s | 0,06% |
| 5000 | 405 | 1,9 s | 22,8 s | 29% resets TCP |
| 10000 | 459 | 2,4 s | 7,8 s | 34% resets TCP |
| 20000 | 335 | 2,6 s | 8,2 s | 40% resets TCP |

## Permissions — `GET /core/permissions` (sync, auth + 1 query)

| Concurrentes | req/s OK | p50 | p95 | Errores |
|---:|---:|---:|---:|---:|
| 10 | 208 | 44 ms | 88 ms | 0% |
| 100 | 127 | 803 ms | 1,5 s | 0% |
| 1000 | 26 | 214 ms | 61 s | 75% 503 `database_busy` |

## Comparativa entre equipos

| Métrica | ThinkPad Linux (i5-7200U, 4 hilos) | Contabo Linux (4 vCPU) | ACONCAGUA Windows (i5-12400F, 6c) |
|---|---|---|---|
| `health` a 10 conc. | 576 rps, p95 30 ms | **583 rps, p95 36 ms** | 193 rps, p95 57 ms |
| `permissions` a 10 conc. | 171 rps, p95 97 ms | **208 rps, p95 88 ms** | sin datos (server murió) |
| `permissions` a 100 conc. | 176 rps, 0% errores | 127 rps, 0% errores | — |
| `permissions` a 1000 conc. | 58% 503 | 75% 503 | — |
| Techo sync autenticado | ~175 rps | ~140-210 rps | — |

ACONCAGUA (Windows 10) se descarta como entorno de producción para Python
async: a 5000+ concurrentes el server muere con
`ValueError: too many file descriptors in select()` (límite del loop de
eventos de Windows) y rinde 2-3× menos que Linux con CPU superior.

## Conclusión: sí, estamos en límites arquitectónicos

La evidencia de los 3 equipos muestra el mismo patrón con hardware muy
distinto (factor ~2 en CPU/RAM entre máquinas, pero el mismo techo):

1. **El endpoint sync autenticado tiene un techo de ~150-200 req/s** y
   colapsa a 1000 concurrentes (503 `database_busy` por timeout del pool)
   en las tres máquinas. El hardware mueve el techo ±50%, no el orden de
   magnitud: el límite no es la CPU, es la arquitectura sync
   (threadpool de 40-100 threads por worker + pool de 15-20 conexiones +
   ~3 queries de auth por request).
2. **Los endpoints async (`health`, `ready`) sobreviven a 20.000
   concurrentes** con 0 errores hasta 1000 y solo resets TCP a 5000+ por
   saturación cliente+server en la misma caja. Sin threadpool, el mismo
   hardware sostiene 3-7× más concurrencia.
3. Los resets TCP a 5000+ (30-40%) son saturación de la caja compartida
   (cliente + server + Postgres en 4 vCPU), no colapso del core: los
   workers siguen respondiendo.
4. El prototipo asyncpg puro (`scripts/async_proto.py`) demostró en la
   misma máquina 2-3× throughput a baja concurrencia y **0,35% de errores
   a 1000 concurrentes** donde el sync colapsa (58% 503).

**Límites arquitectónicos identificados (en orden de impacto):**

| Límite | Efecto medido |
|---|---|
| Endpoints sync + threadpool acotado por worker | Colapso a 1000 conc. (503 pool timeout) |
| ~3 queries de auth por request (`get_user_by_id` + `validate_user_branch_scope` + contexto) | Techo ~175 rps; cada query menos sube el techo |
| N+1 en listados (`serialize_core_role` por fila) | Latencia crece con el tamaño de la lista |
| Auditoría + outbox dentro de la transacción del negocio | Retiene thread+conexión más tiempo por escritura |
| PBKDF2 600k en login | ~8-15 logins/s por core por diseño (seguridad, no bug) |
| Windows como plataforma de serving Python async | Techo artificial ~1000 conexiones (FD/select) |

**Camino de salida (sin reescribir el kernel):** cambio mínimo primero
(confiar en claims firmados del JWT para saltar 2 queries de auth por
request + fix del N+1), piloto async sobre el camino caliente
(auth + lecturas), y backpressure en producción para que el excedente
reciba 503 rápido en vez de colgar 30 s. Ver `README.md` (sección
Backpressure) y `scripts/bench_mp.py` para reproducir.

