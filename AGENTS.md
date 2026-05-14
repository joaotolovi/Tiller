# AGENTS.md

## Principles

No hard workflows, no hard orchestration, no rigidity. Use the minimum necessary to reach the best result.

---

## Simplicity

Prefer the simplest smart solution.

Avoid unnecessary complexity. Simple does not mean naive. Simple means direct, clear, and easy to change.

---

## Decoupling

Keep things independent.

A decision in one part should not force unnecessary decisions elsewhere. Avoid coupling concepts that can evolve separately. If two things can change for different reasons, they should not depend on each other more than necessary.

---

## Agnostic

Do not bind the system to one tool, provider, workflow, vendor, or implementation style.

Prefer contracts over assumptions. Prefer replaceable parts over fixed choices. The system should work with the current tools without becoming defined by them.

---

## Modular

Build with small, clear, replaceable pieces.

Each part should have a purpose. Each boundary should make the system easier to understand, replace, or remove. Modularity exists to keep change cheap.

---

## Assume it works

Do not design around hypothetical failures.

Do not add fallback chains, defensive layers, retries, detection logic, or compatibility paths just because something might fail. Follow the straight path. If a real failure appears, handle that real failure simply and locally.

Assume the intended path works. Keep it clear.

---

## Decision rule

When choosing between two paths, prefer the one that is:

- simpler
- more decoupled
- more smart
- more agnostic
- more modular
- less speculative

If a change does not strengthen these principles, rethink it.
