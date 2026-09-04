# Test Types Reference

## Classification Heuristic

Classify tests by observable scope, not by filename or framework annotation alone.

Key dimensions:

- scope: function, class, use case, adapter, component, system
- boundary: internal code, public API, transport endpoint, deployed service, external contract
- dependencies: none, fakes, mocks, real infrastructure, external services
- speed and determinism: fast/local vs slower/environment-dependent
- perspective: developer internals, component behavior, consumer/provider compatibility, user/business behavior

## Unit Tests

Goal: validate a small piece of behavior quickly and in isolation.

Use when:

- testing pure domain logic
- checking value objects, aggregates, policies, or algorithms
- verifying edge cases and invariants
- testing application orchestration with ports mocked or faked

Avoid calling something a unit test when it touches real databases, files, network, containers, brokers, or framework bootstrapping.

Rule of thumb: if it runs with plain objects and no external I/O, it is probably a unit test.

## Integration Tests

Goal: validate collaboration between real components or between application code and infrastructure.

Use when:

- testing repositories or persistence mappings
- validating serialization and schema compatibility
- verifying transaction boundaries
- checking adapters against realistic infrastructure
- testing controllers or message handlers with framework wiring inside one bounded context

Rule of thumb: if the test touches I/O or multiple real layers working together, it is probably integration.

## Acceptance Tests

Goal: validate that the system satisfies business behavior from a user or external system perspective.

Use when:

- testing a complete use case from input to observable output
- validating business rules in domain language
- checking externally visible behavior rather than internal design
- confirming that the system meets acceptance criteria

Acceptance tests may run through APIs, UI, messages, or other public boundaries. They should avoid asserting internal implementation details.

## Contract Tests

Goal: verify that a provider and consumer agree on request/response shape, semantics, and compatibility.

Use when:

- independent services evolve separately
- consumers rely on specific provider behavior
- external APIs or events must remain compatible
- schema changes need compatibility protection

Contract tests are not general integration tests. Their focus is the boundary agreement.

## Component Tests

Goal: verify one deployable or logical component through its public boundary while controlling external dependencies.

Use when:

- testing a service through HTTP, messaging, CLI, or public API
- replacing external services with fakes, stubs, or simulators
- validating behavior across internal layers without involving the whole system

Component tests sit between integration and end-to-end tests.

## End-To-End Tests

Goal: verify a complete path across multiple real components.

Use when:

- the risk is in cross-service wiring or complete user journeys
- several deployed components must collaborate
- deployment, routing, authentication, or environment configuration matters

Keep end-to-end tests few and focused because they are slower and more fragile than lower-level tests.

## Smoke Tests

Goal: quickly detect whether a deployed app or environment is obviously broken.

Use when:

- checking a deployment
- verifying critical health endpoints or minimal happy paths
- confirming infrastructure wiring after release

Smoke tests should be small, fast, and not exhaustive.

## Regression Tests

Goal: prevent a known bug from returning.

Use when:

- a defect was fixed and needs a durable guard
- the previous failure mode is specific and reproducible
- future refactors are likely to disturb the behavior

Regression describes why the test exists, not necessarily its level. A regression test can be unit, integration, contract, or acceptance.

## Test Naming

Names should communicate behavior, outcome, and context.

Patterns:

- `should_<expected_behavior>_when_<condition>`
- `given_<context>_when_<event>_then_<outcome>`
- descriptive natural language names where the language supports them

Avoid:

- method-name mirrors
- vague happy-path names
- framework-generated names that hide intent

## Test Structure

Arrange-Act-Assert is a useful default:

- Arrange: inputs, fixtures, state, doubles
- Act: execute behavior
- Assert: observable result

Do not interleave setup, execution, and assertions unless the framework pattern clearly calls for it. Prefer one logical behavior per test.

## External Resources

When integration, component, acceptance, or end-to-end tests use external resources:

- use dynamic ports or isolated names
- avoid shared mutable global state
- prefer lifecycle managers for shared resources
- isolate container, database, queue, topic, and network names when running in parallel
- avoid stopping shared resources from one test when other tests may still depend on them
