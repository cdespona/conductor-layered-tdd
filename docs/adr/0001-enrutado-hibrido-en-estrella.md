---
status: accepted
---

# Enrutado híbrido en el flujo en estrella: router determinista, hub LLM solo en casos frontera y guard como único escritor

El flujo en estrella necesita redirigirse ante incoherencias sin que eso encarezca el camino feliz. Por eso el siguiente radio lo elige primero un router determinista que aplica una tabla canónica al ledger, y esa tabla reproduce el orden de ltdd. El hub LLM ligero solo se invoca ante un caso frontera. Toda transición, la proponga el router, el hub o el humano, la confirma el guard, que es el único que escribe en el ledger. Así un run sin escalados no hace ninguna llamada al hub, y ninguna garantía TDD depende de que un LLM obedezca su prompt.

## Considered Options

- **Hub LLM puro en cada salto**: máxima flexibilidad, pero añade una llamada de modelo por transición y deja las invariantes en manos del prompt.
- **Grafo lineal (ltdd actual)**: barato en el camino feliz, pero caro y torpe de redirigir cuando aparece una incoherencia bloqueante.

## Consequences

- La garantía "nunca implementar sin test" es un veto del guard. Solo se exceptúa mediante hechos deterministas: un red gate permitido, un triage `fast` acotado, o una capa `cleanup` o `infrastructure` aprobada por humano. Nunca mediante la salida del hub.
- El nivel de modelo de cada radio sale de los hechos (tamaño de capa, reintento tras `blocked`) y no lo elige el hub.
