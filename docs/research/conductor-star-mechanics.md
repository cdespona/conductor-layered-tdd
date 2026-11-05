# Mecánica de Conductor para un workflow en estrella

Investigación para el issue #2 (hijo del mapa #1). Pregunta: ¿qué permite Conductor
v0.1.44 para un segundo workflow con forma de estrella, donde un script router
determinista decide las transiciones canónicas, un agente hub ligero resuelve los
casos límite, un script guard valida cada ruta propuesta, un `human_gate` confirma
los saltos no obvios y cada paso de ltdd es un radio que vuelve al hub?

## Fuentes

Fuente primaria: el paquete instalado
`~/.local/share/uv/tools/conductor-cli/lib/python3.12/site-packages/conductor/`
(`conductor --version` → `v0.1.44`). Las rutas de abajo son relativas a ese
directorio. Los patrones de uso real salen de
`bundle/workflows/conductor/layered-tdd.yaml`. Las afirmaciones marcadas
"(validado)" se comprobaron con `conductor validate` sobre los YAML de prueba del
apéndice. No se ejecutó ningún workflow real.

## Resumen

| Pregunta | Respuesta corta |
| --- | --- |
| 1. Ciclos hub → radio → hub | Sí. No hay detección de ciclos que bloquee; `max_iterations` cuenta **todos** los pasos. No existe un límite de visitas por agente; se emula con `context.history` en `when` o en el guard. |
| 2. Salida del radio al hub (`explicit`) | Solo por `input:` declarado. Cada ejecución **sobrescribe** `agent_outputs[nombre]` (gana la última). `context.history` (lista de nombres) e `context.iteration` siempre están disponibles. |
| 3. `type: script` como router/guard | Sí. El JSON de stdout se mezcla con `stdout`/`stderr`/`exit_code`. Un `exit_code` distinto de cero **no** hace fallar el paso; se enruta con `when`. |
| 4. Opciones dinámicas en `human_gate` | No. `options` (label/value/route) son estáticas. Solo el `prompt` se renderiza. El texto libre va por `prompt_for`. Para opciones dinámicas usa `type: questions` con `source:`. |
| 5. `parallel` con humano + AFK | No. El validador rechaza `human_gate`, `questions` y `script` en grupos paralelos, y `dialog` no se ejecuta en un grupo paralelo. Las rutas leen `output.outputs.<miembro>.<campo>`. |
| 6. Otros | `to:` es estático (no admite plantillas). La transformación `output` de una ruta solo se aplica con `$end`. `terminate` da un final explícito con estado. Al reanudar se conservan las iteraciones y se reinician el reloj de timeout y el presupuesto. |

## 1. Ciclos y cómo se acotan

**Los ciclos están permitidos.** El bucle principal sigue `current_agent_name`
de paso en paso. La validación solo comprueba que el destino de cada ruta exista
(`config/schema.py:3242` `validate_references`). El análisis de rutas solo
etiqueta el patrón `loop-back` (`cli/validate.py:715-729`). El workflow actual ya
tiene ciclos (por ejemplo, `fast_approval_gate` → `fast_implementor`).
(validado: `star-ok.yaml` pasa con `Patterns: ... loop-back`).

**`limits.max_iterations`** (`config/schema.py:538-541`, default 10, `ge=1, le=500`):

- Antes de cada paso se llama a `check_iteration`
  (`engine/workflow.py:6097-6098` → `engine/limits.py:170-195`). Falla si
  `current_iteration >= max_iterations`.
- `record_execution` se llama para **cada tipo de paso**: agent, `human_gate`
  (`workflow.py:6362`), `questions` (6384), `script` (6525), `set`, `wait`,
  `workflow` y `terminate` (6216). Un viaje router → hub → guard → gate → radio
  cuesta, por tanto, 5 iteraciones. Con un techo de 500, eso da para unas 100
  vueltas completas como mucho.
- Un grupo `parallel` consume tantas iteraciones como miembros tenga
  (`workflow.py:6058-6059`, `limits.py:197-227`). Un `for_each` consume una por
  elemento (`workflow.py:5978`).
- Al alcanzar el límite, el motor **no aborta directamente**. Primero pregunta al
  humano cuántas iteraciones añadir (`_check_iteration_with_prompt`,
  `workflow.py:7328-7400`; `increase_limit`, `limits.py:242`). Con
  `--skip-gates` se detiene sin preguntar (`gates/human.py:612-613`). El evento
  incluye `possible_loop` cuando las últimas 3 ejecuciones son del mismo paso.

**Límite de visitas por agente: no existe.** `get_agent_execution_count`
(`limits.py:333`) solo alimenta el contador `iteration` del dashboard
(`workflow.py:6101-6103`). El campo `max_agent_iterations` (`schema.py:1357`,
`2839`) limita las **vueltas de uso de herramientas dentro de una sola
ejecución**, no las visitas. Hay dos formas de emularlo:

- En una ruta: `when: "{{ (context.history | select('equalto','spoke_a') | list | length) < 5 }}"`.
  Funciona porque el contexto de las rutas incluye `context.history`
  (`engine/context.py:220-236`, `get_for_template` en 592). (validado)
- En el script guard, pasando `context.history` por `stdin` con `tojson`.

**`limits.timeout_seconds`** (`schema.py:544`): es el tiempo real de reloj de todo
el workflow, medido desde `start()` con `time.monotonic()` (`limits.py:259-290`).
Se comprueba en los límites entre pasos. Los scripts y los grupos
`parallel`/`for_each` se envuelven en `wait_for_with_timeout` con el tiempo
restante (`limits.py:415`; `workflow.py:2047`, `6032`, `5952`). Para cada agente
se usa `min(agent.timeout_seconds, restante)` (`workflow.py:2615-2640`). **El
tiempo de espera del humano en un gate también cuenta**, porque el reloj no se
pausa.

**`limits.budget_usd`** + `budget_mode: audit|enforce` (`schema.py:552-571`): se
evalúa con `_check_budget()` **después** de cada paso (`workflow.py:1291-1345`;
llamadas en 6528, 6891, 7089…). En modo `enforce` lanza `BudgetExceededError`,
que guarda un checkpoint y para. No corta a mitad de una llamada al LLM. Si el
modelo no tiene precio, el presupuesto no se aplica y solo se emite un aviso.

## 2. Cómo llega la salida de un radio al hub con `context.mode: explicit`

- `explicit` incluye solo lo que se declara en `input:` (`schema.py:519-529`;
  `context.py:212-228`). Las excepciones son `script` y `workflow`, que siempre
  ven todo `workflow.input` (`_LOCAL_RENDER_AGENT_TYPES`, `context.py:212-216`).
- Formatos de referencia (`context.py:274-347`; regex en
  `config/validator.py:141-149`): `agente.output`, `agente.output.campo[.sub]`,
  la forma corta `agente.campo`, `grupo.outputs[.miembro[.campo]]`,
  `grupo.errors[...]` y `workflow.input.x`. El sufijo `?` la vuelve opcional.
  **El hub tiene que declarar cada radio como `spoke_x.output?`**. Sin el `?`, si
  ese radio aún no se ha ejecutado, falla con
  `KeyError: Missing required agent output` (`context.py:346-347`).
  `layered-tdd.yaml` ya usa este patrón (líneas 85-88 y 127-128).
- **Semántica cuando un agente se ejecuta varias veces:**
  `WorkflowContext.store()` hace `agent_outputs[nombre] = output` (gana la
  última) y añade el nombre a `execution_history` (`context.py:155-170`). No se
  conservan las salidas anteriores por nombre. Si el hub necesita historia
  semántica, hay que acumularla en otro sitio (un fichero de artefacto que
  escriba cada radio, o un script/`set` que acumule).
- **Desfase:** una salida de un radio de una vuelta anterior sigue visible en
  vueltas posteriores. Para saber si es "fresca", el hub o el router deben mirar
  `context.history` (por ejemplo, si el último elemento es `spoke_a`).
- `context.history` y `context.iteration` se inyectan en **todos** los modos,
  incluido `explicit` (`context.py:220-224`). (validado: aparece en el prompt del
  hub y en `when`.)
- Las condiciones `when` de las rutas se evalúan contra
  `get_for_template()` = modo `accumulate` (todas las salidas) + `output`
  (`workflow.py:9043-9056`; `router.py:63-131`). **Las rutas pueden leer
  cualquier agente aunque no esté en `input:`**. El prompt de un `human_gate`
  también se renderiza con el contexto completo (`workflow.py:6303`).

## 3. `type: script` como router y guard

- Esquema (`schema.py:1489-1530`): `command`, `args` (con plantillas), `env`,
  `working_dir`, `stdin` (con plantilla, que se puede alimentar con `| tojson`),
  `timeout`, `output` (esquema opcional) y `routes`.
- Salida (`workflow.py:6461-6487`): siempre `{stdout, stderr, exit_code}`. Si
  stdout es un **objeto** JSON, sus claves se mezclan en el nivel superior, de
  modo que `output.next` se puede direccionar en `when`. Con `output:`
  declarado, la validación es estricta: stdout debe ser un objeto JSON válido con
  esos campos (`workflow.py:2561+`, issue #118 en el código).
- **Códigos de salida:** un código distinto de cero **no** lanza error
  (`executor/script.py:231-262`). Solo lanzan error command-not-found,
  start-failed y timeout. El patrón es `when: "{{ output.exit_code == 0 }}"`, ya
  presente en `layered-tdd.yaml:266`.
- Sintaxis de `when` (`router.py:146-160`): una plantilla Jinja `{{ ... }}` o una
  expresión simpleeval (`score > 7`, aplanada como `output_campo`/`campo`). Gana
  la primera ruta que coincide. Si ninguna coincide, se lanza `ValueError("No
  matching route found")` (`router.py:127-130`), así que conviene cerrar siempre
  con una ruta sin `when`. Un paso sin rutas va a `$end` (`workflow.py:9043-9045`).
- **Limitación clave:** `to:` es un nombre estático. `to: "{{ output.next }}"` se
  rechaza en la validación (validado: `dyn-target.yaml` →
  `routes to unknown agent ... '{{ output.next }}'`). El router o guard debe
  **enumerar una ruta por radio**:
  `- to: spoke_a / when: "{{ output.next == 'spoke_a' }}"`. Con N radios el YAML
  crece de forma lineal en el router y en el guard, pero el script decide.
- Un script no puede estar en un `parallel` ni ser el agente interno de un
  `for_each` (validator.py:955-959, 453-454; validado con `par-script.yaml`).

Encadenar router (script) → hub (agent) → guard (script) → gate/radio se
valida sin problemas (validado: `star-ok.yaml`).

## 4. `human_gate`: opciones estáticas, texto libre y alternativa dinámica

- `HumanGateStepDef.options: list[GateOption]` (`schema.py:1429-1449`). Cada
  `GateOption` tiene `label`, `value`, `route`, `prompt_for` y `multiline`
  (`schema.py:423-444`). **Ni `label` ni `route` se renderizan**
  (`gates/human.py:355-370` solo renderiza `prompt`). El validador exige que
  `option.route` sea un paso existente (`validator.py:388-393`). **Las opciones
  son estáticas.** El gate enruta por `option.route` e ignora `routes:`
  (`workflow.py:6364-6374`).
- Lo dinámico es el **prompt**: se renderiza contra el contexto completo y puede
  mostrar la propuesta del hub (`{{ hub.output.next }}`, su razón, etc.).
- **Texto libre:** con `prompt_for: campo` (y `multiline: true` opcional) se
  recoge texto tras elegir la opción (`human.py:398-412`). Se guarda como
  `gate.output = {selected, additional_input: {campo: texto}}`
  (`workflow.py:6351-6358`). `layered-tdd.yaml:792` ya lo consume
  (`layer_selection_gate.output.additional_input.selected_layer`).
- `--skip-gates` elige siempre la **primera** opción (`human.py:370`, `395`). Si
  "aceptar salto" va primero, un modo AFK aceptaría todos los saltos. Conviene
  ordenar las opciones pensando en esto.
- **Alternativa con elecciones dinámicas:** `type: questions`
  (`schema.py:1452-1486`) con `source: hub.output.choices`. Cada elemento puede
  ser un string o `{id, text|question, choices, allow_free_text, default, required}`
  (`executor/questions.py:149-204`). La salida es
  `{answers: {id: respuesta}, items, transcript, answered_count, skipped_count, answered_any, outcome}`
  (`questions.py:419-466`), y se enruta con `routes`/`when` normales
  (`workflow.py:6381-6413`). Cuesta 1 iteración aunque haga N preguntas. Las
  `choices` estáticas también se renderizan como plantilla (`questions.py:263`).
  Patrón posible para un salto no obvio: el hub emite `choices`, `questions`
  pregunta y el router/guard comprueba en `when` que la respuesta sea un destino
  conocido. (validado: `ask_dynamic` con `source: hub.output.choices` pasa.)

## 5. `parallel` con humano y AFK

- `ParallelGroup` (`schema.py:360-391`) tiene `agents` (>=2), `failure_mode`
  (`fail_fast` | `continue_on_error` | `all_or_nothing`) y `routes`.
- El validador **rechaza** en un paralelo: `human_gate`, `questions`, `script`,
  `wait`, `workflow`, `terminate`, rutas propias en los miembros, dependencias
  entre miembros y paralelos anidados (`validator.py:885-1036`). (validado:
  `par-gate.yaml` → "Human gates cannot be used in parallel groups";
  `par-script.yaml` → "Script steps cannot be used in parallel groups".) Los
  miembros permitidos son `agent`, `set` y `mcp` (`workflow.py:8000-8051`).
- Un agente con `dialog:` (el agente que "pregunta al humano") **pasa la
  validación** en un paralelo (validado: `par-dialog.yaml`), pero
  `_execute_parallel_group` (`workflow.py:7923-8400`) **no llama** a
  `_handle_dialog`. Solo el camino lineal lo hace (`workflow.py:7015-7016`). El
  diálogo se ignora sin aviso. Lo mismo ocurre en `for_each`, y `for_each`
  también prohíbe `human_gate` y `questions` como agente interno
  (`validator.py:467-480`).
- **Conclusión:** no se puede tener un humano en el bucle en paralelo con un
  agente AFK dentro de Conductor. La alternativa es secuencial: lanzar el AFK
  antes o después del gate, o sacar el trabajo AFK fuera de proceso (un script
  que lance un proceso en segundo plano y otro script que lo recoja más tarde).
- **Lectura tras el paralelo:** el grupo se guarda como
  `{type: 'parallel', outputs: {miembro: salida}, errors: {miembro: {agent_name, exception_type, message, suggestion}}}`
  (`workflow.py:6038-6055`). En las rutas del grupo:
  `when: "{{ output.outputs.afk.done and 'chatty' in output.outputs }}"`. En
  pasos posteriores: `input: [pg.outputs.afk.done, pg.outputs.chatty?, pg.errors?]`.
  Los miembros **no** quedan guardados bajo su propio nombre, solo bajo el
  grupo. (validado)

## 6. Otros puntos relevantes

- **`$end` frente a `terminate`:** `$end` construye la salida con
  `workflow.output` más la transformación `output:` de la ruta
  (`workflow.py:9111-9140`). `type: terminate` (`schema.py:1662-1694`) da
  `status: success|failed`, un `reason` con plantilla y `output_template`. Es
  un sumidero sin rutas (`workflow.py:6181-6216`) y no puede estar en un
  `parallel` ni en un `for_each`. Para un final explícito desde el router, usar
  `terminate`.
- **Transformación `output` en rutas** (`RouteDef.output`, `schema.py:349`): **solo
  se usa cuando el destino es `$end`**. Todos los usos de `output_transform` en
  `workflow.py` (6005, 6084, 6405, 6547, …) pasan por `_build_final_output`. No
  sirve para pasar datos entre el hub y un radio. Para eso, usar `input:` o un
  paso `set`.
- **`for_each`** (`schema.py:1751-1782`) tiene `source` (referencia con puntos a
  una lista), `as`, un `agent` interno, `max_concurrent` (<=100), `failure_mode`,
  `key_by` y `routes`. La salida es `{type:'for_each', outputs, errors, count}`
  (`workflow.py:5961-5974`). No admite anidamiento ni script/wait/terminate/
  gate/questions como agente interno. Es útil para lanzar varios radios AFK
  homogéneos, no como hub.
- **Checkpoint y reanudación bajo ciclos:**
  - Por defecto solo se guarda checkpoint **al fallar**, por ejemplo con
    `MaxIterationsError`, timeout o `BudgetExceededError`
    (`workflow.py:4191`). Opcionalmente se guarda en cada paso con
    `runtime.checkpoint.every_agent` / `every_seconds` / `keep_last`
    (`schema.py:2464-2510`, `2886`).
  - El checkpoint guarda el contexto completo: `agent_outputs` (solo la última
    salida de cada agente), `execution_history` y `current_iteration`
    (`context.py:556-590`), además de `current_agent`.
  - Al reanudar (`workflow.py:3800-3830`, `limits.py:117-157`) se **conservan**
    `current_iteration` y `max_iterations` (incluida cualquier ampliación
    manual), y se **reinician** el reloj de `timeout_seconds` y el presupuesto,
    que se toman de la configuración actual. Un hub en bucle que agotó las
    iteraciones vuelve a chocar enseguida si no se amplía el límite. El recuento
    de visitas basado en `context.history` sobrevive a la reanudación.
  - Un `questions` solo restaura respuestas parciales al reanudar. En una vuelta
    normal del ciclo vuelve a preguntar (`workflow.py:3826-3829`).

## Implicaciones para el diseño en estrella

1. Router = `type: script` con JSON en stdout (`next`, `reason`) y una ruta
   enumerada por radio, más una ruta por defecto → `hub`. El router recibe
   `context.history` y las salidas `spoke_*.output?` por `stdin | tojson`.
2. Hub = `agent` barato, con `input:` opcionales de todos los radios y del
   router. Emite `next` (y opcionalmente `choices`). Siempre va → `guard`.
3. Guard = `type: script`. Recibe `hub.output.next` y `context.history`, valida
   la transición contra una tabla y emite `needs_human`. Sus rutas, enumeradas,
   van a un radio, a `confirm_jump` o a `terminate: failed`.
4. `confirm_jump` = `human_gate` con opciones fijas (aceptar / rechazar con
   nota) que vuelven al router. El prompt muestra la propuesta dinámica. Si
   hacen falta destinos alternativos elegibles, usar `questions` con
   `source:`.
5. Radios = pasos actuales de ltdd con `routes: [{to: router}]`. Como
   `agent_outputs` se sobrescribe, cada radio debe dejar sus artefactos
   duraderos en disco (como ya hace ltdd).
6. Acotar con `max_iterations` (≈5 iteraciones por vuelta), límites de visitas
   en el guard basados en `context.history`, `timeout_seconds` (que incluye la
   espera humana) y `budget_usd` con `budget_mode: enforce`.
7. No intentar meter un gate y un agente AFK en paralelo. Conductor no lo
   soporta.

## Apéndice: YAML de prueba

Los ficheros de prueba estaban en un área temporal y no se guardan en el repo.
Resultados de `conductor validate` (v0.1.44):

- `star-ok.yaml`: router (script) → hub (agent) → guard (script) →
  `confirm_jump` (human_gate con `prompt_for`) / radios → router, más
  `questions` con `source: hub.output.choices`, `terminate` success/failed, un
  límite de visitas en `when` con `context.history` y `max_iterations: 500` +
  `timeout_seconds` + `budget_usd`/`enforce`. → **Validation Successful**, con
  `loop-back`. Solo da un aviso: un `terminate.reason` en modo `explicit` hace
  referencia a `hub.output` sin declararlo en `input:`.
- `par-gate.yaml` (`human_gate` en parallel) → **Falla**: "Human gates cannot be
  used in parallel groups".
- `par-script.yaml` (`script` en parallel) → **Falla**: "Script steps cannot be
  used in parallel groups".
- `dyn-target.yaml` (`to: "{{ output.next }}"`) → **Falla**: "routes to unknown
  agent ... '{{ output.next }}'".
- `par-dialog.yaml` (agent con `dialog` + agent AFK en parallel, con rutas sobre
  `output.outputs.*` y `input: pg.outputs.afk.done`) → **Validation Successful**,
  aunque en ejecución el diálogo no se dispara (ver sección 5).

Fragmento clave del router de `star-ok.yaml`:

```yaml
- name: router
  type: script
  command: python3
  args: ["router.py"]
  stdin: "{{ {'history': context.history, 'spoke_a': spoke_a.output | default(none)} | tojson }}"
  input: [spoke_a.output?, spoke_b.output?, confirm_jump.output?]
  output:
    next: {type: string}
    reason: {type: string}
  routes:
    - to: spoke_a
      when: "{{ output.next == 'spoke_a' and (context.history | select('equalto', 'spoke_a') | list | length) < 5 }}"
    - to: spoke_b
      when: "{{ output.next == 'spoke_b' }}"
    - to: done
      when: "{{ output.next == 'done' }}"
    - to: hub
```
