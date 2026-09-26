# Forge

Forge resolves a text issue in a Git repository and returns a patch with execution evidence. It explores code, reproduces the failure, edits files, and runs tests through one configured model.

A model saying "done" does not complete a run. Forge reruns the verification command against the final patch and rejects completion if the command fails or changes the patch. This prevents an earlier passing test from being presented as evidence for later edits. A passing command still proves only what that command tests.

## Run the demo

Use Python 3.9 or later, Git, and Make on macOS or Linux. Forge has no third-party Python runtime dependencies.

```sh
make setup
make test
make demo
make dashboard
```

Open `http://127.0.0.1:8765` for the dashboard. Start the scripted demo to inspect a failing baseline, file edits, final verification, a patch, and event history. The scripted demo uses known fixture corrections and requires no credentials. It measures the execution machinery, not model ability.

## Resolve an issue

Use a clean Git repository with at least one commit. Commit or stash its changes before starting. Forge creates a separate local clone for each run. Ignored files, virtual environments, and installed dependencies from the source directory are not copied into that clone. Include any required project setup commands in the issue so the agent can run them in its workspace.

Set `AI_API_KEY` in your shell or secret manager. Do not put it in the repository. With the default provider, this is the only required environment variable.

```sh
make run ARGS='solve --repo /absolute/path/to/project --issue-file /absolute/path/to/issue.txt --test-command "python3 -B -m unittest discover -v"'
```

Use `make run` without arguments for interactive issue entry. For direct CLI use after setup:

```sh
.venv/bin/python -m forge solve \
	--repo /absolute/path/to/project \
	--issue 'Fix the reported boundary error and add regression coverage.' \
	--test-command 'python3 -B -m unittest discover -v' \
	--max-steps 40 --max-tokens 120000 --max-seconds 900 \
	--json
```

Use `--issue-file -` to read issue text from stdin. If you omit `--test-command`, the model chooses a command after inspecting the project. Supply the command yourself when evaluation requires a specific test suite.

The standard `make run` entry point also accepts one JSON task on stdin:

```sh
printf '%s\n' '{"repo":"/absolute/path/to/project","issue":"Fix the reported bug.","test_command":"python3 -B -m unittest discover -v","max_steps":40}' | make run
```

The stdin entry point prints a startup banner followed by the result JSON. Use `solve --json` when you need output containing only the result JSON. The stdin task supports `repo`, `issue`, `test_command`, and `max_steps`. Use `solve` flags for token and time budgets.

## Configure the evaluation model

The default is `gemini-3.8-flash` through Google's OpenAI-compatible endpoint. Google's [OpenAI compatibility documentation](https://ai.google.dev/gemini-api/docs/openai) describes that endpoint and model. Forge sends text messages and function tool calls using the [Chat Completions interface](https://developers.openai.com/api/reference/resources/chat).

If the organizers specify another model or endpoint, override these values before evaluation:

```sh
export AI_MODEL='organizer-provided-model-id'
export AI_BASE_URL='https://organizer-provided-host/v1'
export AI_TOKEN_PARAMETER='max_tokens'
```

Set `AI_TOKEN_PARAMETER=max_completion_tokens` when the selected endpoint requires that field. Forge uses exactly one configured model during a run and has no automatic fallback model. For free-tier testing, `AI_REQUEST_INTERVAL=13` spaces requests by at least 13 seconds. Daily quota exhaustion still requires waiting for reset. The complete configuration reference is in [architecture](docs/architecture.md#configuration).

## Inspect and apply the patch

Each run writes `.forge/runs/<run-id>/` by default. Set `FORGE_RUNS_DIR` to choose another output directory. Inspect `report.md`, `report.json`, `events.jsonl`, `patch.diff`, and the retained `workspace/`. Command output is stored under `artifacts/`.

Review the patch, then apply it to the original repository at the recorded `base_commit`:

```sh
git -C /absolute/path/to/project apply --check /absolute/path/to/patch.diff
git -C /absolute/path/to/project apply /absolute/path/to/patch.diff
```

Rerun your project tests after applying it. Forge does not commit or push the generated patch. Failed, cancelled, and budget-limited runs preserve their partial work for inspection.

## Resume after an interruption

```sh
.venv/bin/python -m forge resume .forge/runs/<run-id>
```

Resume creates a new attempt with fresh budgets, copies the retained workspace and conversation, and links it to the original report. It requires the same model and endpoint. It rejects changes to the saved Git patch state or HEAD, but does not detect changes to ignored dependencies or generated files. The final test command runs again before completion.

## Run the fixture suite

```sh
.venv/bin/python -m forge bench
.venv/bin/python -m forge bench --live
```

The three fixtures cover interval merging, CSV quoting, and TTL cache expiry. The first command uses prescribed edits and labels results `scripted-infrastructure`. The second invokes the configured model and labels results `live-model`. They write `.forge/benchmark-scripted.json` and `.forge/benchmark-live.json`, respectively, and retain individual run artifacts. These small local fixtures are not SWE-bench or a hidden evaluation, and their results do not establish a general solve rate.

## Run in a disposable environment

Shell commands execute with the runner's permissions. A cloned worktree protects the original Git state; it is not a security sandbox. File tools block repository escapes and common secret paths, but shell commands can bypass those restrictions. Use trusted repositories or a disposable environment with only the required mounts and credentials.

The included Dockerfile packages Python 3.12, Git, and Make. Its build and scripted demo passed in [clean Linux CI](https://github.com/Yash121l/forge-harness/actions/runs/36251330798). Container behavior still depends on the host's mounts and the target project's dependencies.

```sh
docker build -t forge .
docker run --rm forge demo
```

For a live task, mount the source repository read-only, mount a separate writable output directory, and pass the credential through the environment. Avoid mounting your home directory or Docker socket.

```sh
docker run --rm \
	-e AI_API_KEY \
	-v /absolute/path/to/project:/input/repo:ro \
	-v /absolute/path/to/output:/output \
	forge solve --repo /input/repo --issue 'Describe the issue here.'
```

Read the [observed validation results](docs/validation.md), [architecture and limits](docs/architecture.md), [submission checklist](docs/submission.md), and [demo script](docs/demo-script.md) before presenting.
