# Conductor Layered TDD

Workflows de Conductor que guían un cambio de código mediante TDD por capas con aprobación humana: el flujo lineal (ltdd) y el flujo en estrella.

## Flujo en estrella

**Hub**:
Nodo central del flujo en estrella que propone el siguiente radio cuando la tabla canónica no basta.
_Avoid_: orquestador, nodo central LLM

**Radio**:
Un paso de ltdd ejecutado como destino del flujo en estrella, que siempre devuelve el control al centro con un outcome.
_Avoid_: spoke, nodo, rama

**Outcome**:
Resultado cerrado con el que termina un radio: `done`, `blocked`, `needs-human` o `incoherence`.
_Avoid_: estado del radio, resultado

**Router**:
Componente determinista que elige el siguiente radio aplicando la tabla canónica al ledger.
_Avoid_: dispatcher, hub determinista

**Guard**:
Componente determinista que veta cualquier transición que rompa una garantía TDD, venga de quien venga.
_Avoid_: validador, gatekeeper

**Tabla canónica**:
Conjunto declarativo de transiciones `fase + condición sobre hechos → siguiente radio` que reproduce el camino feliz de ltdd.
_Avoid_: reglas de routing, grafo

**Caso frontera**:
Situación que la tabla canónica no resuelve y que provoca un escalado.
_Avoid_: edge case, excepción

**Escalado**:
Paso del router al hub ante un caso frontera.
_Avoid_: fallback, derivación

**Ledger**:
Estado durable de un run en estrella, compuesto por un cursor de fase y un conjunto de hechos, escrito solo por componentes deterministas.
_Avoid_: estado, contexto, checkpoint

**Cursor de fase**:
Posición del run dentro del orden canónico de fases de ltdd.
_Avoid_: paso actual, nodo actual

**Hecho**:
Afirmación verificable registrada en el ledger que condiciona las transiciones, por ejemplo el estado del red gate.
_Avoid_: flag, variable

**Exención**:
Hecho que permite tocar código de producción sin red gate. Solo existe para un triage fast acotado o para una capa `cleanup` o `infrastructure` aprobada por humano.
_Avoid_: waiver (en ltdd es un estado del red gate, no una exención), bypass
