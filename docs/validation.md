# Validation evidence

These results were observed during development on September 26, 2026. They distinguish execution tests from model performance.

## Local automated checks

`make setup` creates the virtual environment and compiles the source. `make test` exercises real temporary Git repositories, process groups, a local HTTP provider, and the dashboard API. Tests cover tool boundaries, exact edits, binary and untracked patches, credential redaction, provider retries, daily quotas, stale verification, cancellation, budgets, and resume.

The three scripted fixtures cover interval merging, CSV parsing, and TTL expiry. All three have failing baselines and pass after their prescribed correction. These fixtures validate the harness mechanics. They do not establish a model solve rate.

The dashboard was also exercised in the browser: starting a scripted run, observing verified completion, inspecting its patch and evidence, and viewing archived runs. `make run` was exercised in an interactive terminal through its demo option.

GitHub Actions runs clean-checkout setup, tests, the standard launch command, and the fixture suite on Python 3.9 and 3.12. The Python 3.12 job also builds the Docker image and runs its demo. Both Python jobs, the Docker build, and the container demo passed in [workflow 36251330798](https://github.com/Yash121l/forge-harness/actions/runs/36251330798) at commit `5a68d76`. The suite contains 50 automated tests.

## Live Gemini evidence

A real `gemini-3.8-flash` run autonomously changed the interval implementation. Its failing baseline became six passing tests. We applied its exported patch in a separate clean clone and reran all six tests successfully.

- [Actual live patch](../benchmarks/evidence/intervals-live.patch)
- [Independent replay output](../benchmarks/evidence/intervals-live-tests.txt)
- [Run metadata and limitation](../benchmarks/evidence/intervals-live.json)
- [Subsequent live suite results](../benchmarks/evidence/live-suite.json)

The model's next request hit the free-tier quota before it called `finish`. The original run is therefore `failed`, even though its patch passes the fixture tests. The subsequent three-case live suite completed zero verified runs because of provider overload and quota errors. That result is preserved rather than reported as a model solve rate.

Google returned a daily limit of 20 requests for this project's Gemini 3.8 Flash free tier. We stopped live requests after confirming the daily limit. The provider now recognizes that exact array-wrapped error format and stops retrying daily exhaustion. Unit tests cover the response, but a fresh live benchmark must wait for quota reset or an authorized endpoint with available quota.

No performance advantage over another harness, SWE-bench score, or hackathon rank has been measured.
