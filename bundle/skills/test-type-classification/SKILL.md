---
name: test-type-classification
description: "Trigger: classifying test type, scope, boundary, purpose, test level, or behavior-focused test naming."
---

# Test Type Classification

## Overview

Use this skill to identify what kind of test is needed or what kind of test already exists. Focus on scope, boundary, dependencies, and business perspective rather than development workflow.

For detailed classification rules and examples, read [references/test-types.md](references/test-types.md).

## Classification Questions

Ask these before naming the test type:

1. What behavior or risk is being verified?
2. What is the system boundary under test?
3. Does the test use plain objects only, or does it touch I/O, frameworks, databases, queues, files, time, network, or multiple deployed components?
4. Is the perspective internal implementation, component collaboration, external contract, or user/business behavior?
5. Are dependencies real, fake, mocked, simulated, or externally managed?

## Core Test Types

- Unit test: verifies a small behavior in isolation, usually with plain objects and no I/O.
- Integration test: verifies collaboration between real components, adapters, infrastructure, serialization, persistence, or transactions.
- Acceptance test: verifies business behavior from a user or external system perspective.
- Contract test: verifies compatibility between a provider and consumer boundary.
- Component test: verifies a deployable or logical component through its public boundary with dependencies controlled or simulated.
- End-to-end test: verifies a complete path across multiple real components or services.
- Smoke test: verifies a minimal critical path to detect obvious deployment or environment failure.
- Regression test: captures a previously broken behavior so it does not return.

## Naming Guidance

Use names that describe behavior, outcome, and context. Avoid names mechanically derived from implementation method names.

Good names answer: "what is verified, under what condition?"

Prefer:

- `should_reject_expired_coupon_when_checkout_is_submitted`
- `` `rejects expired coupon during checkout` ``

Avoid:

- `testDoSomething`
- `happyPath_callMethodName`
- `checkoutTest`

## Structure Guidance

Arrange-Act-Assert is a useful default for readable tests:

1. Arrange: prepare inputs, state, fixtures, or doubles.
2. Act: execute the behavior once.
3. Assert: verify the outcome.

Keep setup, execution, and verification visually separated when it improves readability. If multiple unrelated behaviors are asserted, split the test or use the testing framework's grouped assertion feature intentionally.

## Output Style

When reviewing tests, lead with misclassified tests, unclear boundaries, over-mocking, or tests placed at the wrong level. When proposing tests, identify the type first and explain the boundary and dependencies.
