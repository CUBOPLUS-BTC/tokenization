## Infraestructura Docker

### 1. Stacks disponibles

El repositorio queda dividido en tres escenarios operativos principales:

- `infra/docker-compose.regtest.yml`: stack local completo sobre Bitcoin `regtest`.
- `infra/docker-compose.testnet4.yml`: stack completo sobre Bitcoin `testnet4`.
- `infra/docker-compose.observability.yml`: stack opcional de monitoreo con Prometheus, Grafana, Alertmanager, Blackbox y cAdvisor.

### 2. Regtest

`infra/docker-compose.regtest.yml` quedó corregido para usar rutas relativas válidas desde la carpeta `infra/`.
Antes estaba intentando leer rutas como `./infra/.env.local` y montar `./:/app`, lo cual terminaba apuntando a ubicaciones incorrectas y podía dejar servicios levantando con variables no deseadas.

Ahora el stack de `regtest` usa:

- `infra/.env.local`
- `infra/bitcoin/bitcoin.conf`
- `infra/lnd/lnd.conf`
- `infra/elements/elements.conf`
- el repositorio raíz montado como `../:/app`

Para levantarlo:

```bash
docker compose -f infra/docker-compose.regtest.yml up -d
```

Para apagarlo:

```bash
docker compose -f infra/docker-compose.regtest.yml down
```

Para limpiar volúmenes:

```bash
docker compose -f infra/docker-compose.regtest.yml down -v
```

Detalles de red del stack:

- Bitcoin Core corre en `regtest`.
- LND corre en `regtest`.
- Elements corre en `elementsregtest`.
- `wallet`, `tokenization`, `marketplace`, `nostr`, `admin`, `auth` y `gateway` quedan cableados a esa misma topología.

### 3. Testnet4

Se agregó `infra/docker-compose.testnet4.yml` para ejecutar todo el sistema sobre Bitcoin `testnet4`.

Archivos asociados:

- `infra/.env.testnet4.example`
- `infra/bitcoin/bitcoin.testnet4.conf`
- `infra/lnd/lnd.testnet4.conf`
- `infra/elements/elements.testnet4.conf`

Características de este perfil:

- Bitcoin Core usa `testnet4` y RPC en `48332`.
- LND se conecta al `bitcoind` de `testnet4`, pero internamente sigue usando el modo `testnet` de LND. Eso es intencional: hoy LND expone esta red bajo el namespace `testnet`, por eso el macaroon queda en `data/chain/bitcoin/testnet/...`.
- Elements usa `liquidtestnet`.
- La aplicación ahora acepta `BITCOIN_NETWORK=testnet4` y lo trata correctamente en validaciones, reconciliación y derivación de direcciones Liquid.

Para levantarlo:

```bash
docker compose -f infra/docker-compose.testnet4.yml up -d
```

Para apagarlo:

```bash
docker compose -f infra/docker-compose.testnet4.yml down
```

Notas operativas:

- `testnet4` no mina bloques on demand como `regtest`; depende de la red pública.
- El arranque de `elementsd` fue ajustado para minar bloques bootstrap solo en `elementsregtest`. En `liquidtestnet` no intenta minar localmente.
- Si vas a personalizar credenciales o endpoints, parte de `infra/.env.testnet4.example` y crea tu propio archivo operativo fuera de Git.

### 4. Observabilidad

`infra/docker-compose.observability.yml` no es obligatorio para que el proyecto funcione. Su función es monitoreo, no runtime principal.

Debes correrlo en simultáneo con el proyecto solo si quieres:

- métricas en Prometheus,
- dashboards en Grafana,
- alertas en Alertmanager,
- probes HTTP/TCP con Blackbox,
- métricas de contenedores con cAdvisor.

Para ejecutarlo localmente o en un host separado:

```bash
docker compose -f infra/docker-compose.observability.yml up -d
```

Puntos importantes del estado actual:

- El archivo `infra/observability/prometheus/prometheus.yml` hoy está orientado a ambientes compartidos y scrapea hosts como `prod-gateway.internal` y `beta-gateway.internal`.
- Eso significa que sí puede correr separado del stack principal, pero Prometheus solo funcionará si esos nombres resuelven desde el contenedor.
- Si quieres observar `regtest` o `testnet4`, debes adaptar esos targets a los hostnames o dominios reales de esos entornos.

En resumen:

- Para desarrollo puro, no necesitas levantar observabilidad.
- Para validación operativa, release gate o troubleshooting, sí conviene correrla en paralelo.
- Puede ir en el mismo host o en otro host, siempre que Prometheus pueda alcanzar al `gateway` y a los servicios que quieras sondear.

### 5. Cómo correr proyecto + observabilidad en simultáneo

Opción simple en la misma máquina:

```bash
docker compose -f infra/docker-compose.regtest.yml up -d
docker compose -f infra/docker-compose.observability.yml up -d
```

O con `testnet4`:

```bash
docker compose -f infra/docker-compose.testnet4.yml up -d
docker compose -f infra/docker-compose.observability.yml up -d
```

Para que el monitoreo realmente vea el stack, debes hacer una de estas dos cosas:

1. Cambiar los targets de Prometheus a URLs alcanzables desde el stack de observabilidad.
2. Publicar el `gateway` y usar DNS o dominios internos estables.

La forma más limpia es monitorear a través del `gateway`, porque ya expone:

- `/health`
- `/ready/<service>`
- `/metrics/<service>`

### 6. Cómo montarlo en Coolify

La forma recomendada en Coolify es separar la aplicación del monitoreo en dos recursos o dos stacks:

#### Stack A: aplicación

Usa uno de estos compose como base:

- `infra/docker-compose.regtest.yml`
- `infra/docker-compose.testnet4.yml`

Recomendaciones:

- Define variables y secretos en Coolify en vez de depender de archivos `.env` locales.
- Monta volúmenes persistentes para PostgreSQL, Redis, Bitcoin, LND y Elements.
- Expón solo el `gateway` al exterior, no cada microservicio.
- Si vas con `testnet4`, usa secretos reales para JWT, cifrado de wallet y credenciales RPC.

#### Stack B: observabilidad

Usa `infra/docker-compose.observability.yml` como stack separado.

Recomendaciones:

- Ajusta `prometheus.yml` para apuntar al dominio o DNS interno del `gateway` publicado por Coolify.
- Mantén Grafana y Alertmanager en una red privada o con autenticación fuerte.
- Si Coolify o el host restringen contenedores privilegiados, `cadvisor` puede requerir trato especial o ejecución fuera de Coolify.

Patrón recomendado en Coolify:

1. Desplegar primero el stack de aplicación.
2. Verificar que el `gateway` responda `/health` y `/metrics/...`.
3. Desplegar luego el stack de observabilidad.
4. Reemplazar en Prometheus los targets placeholder por el dominio interno o externo real del gateway.
5. Configurar los webhooks reales de Alertmanager.

### 7. Resumen operativo

- `docker-compose.regtest.yml` es el perfil correcto para desarrollo determinista y minería manual.
- `docker-compose.testnet4.yml` es el perfil correcto para integración sobre red pública `testnet4`.
- `docker-compose.observability.yml` es opcional, pero debe correr junto al proyecto cuando quieras monitoreo y alertas.
- En Coolify, la mejor práctica es desplegar aplicación y observabilidad por separado y conectar Prometheus al `gateway` mediante DNS estable.
