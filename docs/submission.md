# Submit Forge

The supplied opening slides say to submit before **Sunday, 6:00 AM** through the team leader's submission link. They also describe an event ending at 10:00 AM. Use the earlier submission deadline unless organizers explicitly revise it.

The shared attachments do not include a numeric scoring rubric, an official evaluation model, or the submission form URL. Confirm those details with the organizers. The current Gemini default is the participant's chosen development model, not a claim about the official model.

## Check the submitted revision

1. Clone the private repository into a fresh directory.
2. Run `make setup`.
3. Run `make test`.
4. Run `make run`, select `2`, and confirm the scripted demo finishes as `VERIFIED`.
5. Set the evaluator's `AI_API_KEY` outside Git. Override `AI_MODEL`, `AI_BASE_URL`, and `AI_TOKEN_PARAMETER` if the organizers specify another endpoint.
6. Run one representative live issue with a supplied test command when API quota is available.
7. Confirm the judges can clone the private repository. Grant access to the organizer-provided GitHub identities without making the repository public.
8. Submit the repository URL and exact commit SHA through the official form. Include other materials only if the form requests them.

## Requirement mapping

| Supplied requirement | Implementation |
| --- | --- |
| Root `Makefile` with setup, run, and test | `Makefile`; no third-party Python runtime dependencies |
| Credential from `AI_API_KEY` | Environment-only provider configuration; example file contains no key |
| Text evaluation input | CLI issue string, issue file, stdin JSON, or dashboard text field |
| Text-only execution | Text Chat Completions requests and JSON function tools; no image, audio, or video dependency |
| Explicit model configuration | `forge/provider.py` defaults plus exact environment overrides; no model fallback |
| Autonomous issue resolution | `forge/core.py` exploration, edit, verification, budgets, and failure recovery |
| Repository exploration and tools | Six repository tools in `forge/tools.py` |
| Context management | Bounded observations, checkpoint notes, compaction, persisted conversation |
| Telemetry and reporting | JSONL events, command artifacts, Markdown and JSON reports, patch export |
| Architecture README | `README.md` and `docs/architecture.md` |
| Reproducible evaluator entry point | `make setup` then `make run`; CLI and dashboard share the same core |

`source-code/`, `configuration-files/`, and `dependency-files/` appear as illustrative directory names in the provided submission document. The actual source is in `forge/`; configuration is documented in `docs/architecture.md`; runtime dependencies are declared in `pyproject.toml`.

## Report evidence accurately

The scripted fixture suite exercises the harness with known edits. It does not measure model reasoning. Live evidence and its quota limitation are documented in [validation](validation.md). Do not describe either set as a SWE-bench score or claim a general solve rate.

The exported patch is the submission artifact of each agent task. `verified` means the final configured command passed against a nonempty, stable patch. It does not certify that every possible behavior is correct. Prefer the evaluator's own tests for independent assessment.

Use the [demo script](demo-script.md) for a short presentation. Keep the runtime model and endpoint visible to the judges.
