# Present Forge in three minutes

Start the dashboard with `make dashboard`. Have the repository open at `forge/core.py`, specifically `_finish`, and keep a terminal ready for `make test`.

## 0:00 to 0:25: state the problem

"A coding agent can change a file, run a test, change the file again, and still tell you it is done. Forge makes completion a tool the runner can reject. It reruns the test command against the final patch and keeps the result beside the diff."

## 0:25 to 1:20: run the fixture

Click **Try demo**. Say explicitly: "This is a scripted fixture that demonstrates the execution system without requiring an API."

Open the failing baseline, then the patch. The interval implementation originally sorts the caller's list in place and fails to merge touching intervals. The patch uses `sorted(intervals)` and changes the boundary check to `<=`.

Show the final command output. Point to the run status, exact diff, token counts, and report download. The demo reports zero model tokens because it makes no inference calls.

## 1:20 to 2:10: show what is enforced

Open the regression test for stale verification in `tests/test_core.py`. Explain that it first gets a passing result, then changes the implementation to an incorrect one. The final completion request is rejected.

Show a failing provider run and its preserved workspace. Explain `forge resume`: a new attempt copies the retained workspace and conversation, pins the same model and endpoint, and rejects changes to the saved Git patch state. A fresh test is still required at completion.

## 2:10 to 2:40: distinguish live evidence

Open `benchmarks/evidence/intervals-live.patch` and `intervals-live-tests.txt`. Say: "Gemini 3.8 Flash generated this correction autonomously. Six tests passed, including a separate replay in a clean clone. The free API quota ran out before the model called finish, so the original run remains failed."

Do not present that run as a completed live benchmark. If a later live suite completes, show its actual report instead and retain this earlier evidence as history.

## 2:40 to 3:00: close with the evaluator path

Show `make setup`, `make run`, and the documented `AI_API_KEY` configuration. Explain that the organizers can override the model without source edits.

"The contribution is an inspectable execution loop: isolated code changes, bounded tools, recoverable conversations, and completion based on fresh command evidence."

## Before presenting

Use an authorized live endpoint with available quota if you plan to run a live task. Keep the scripted demo available for a network outage. Do not expose credentials on screen. Run untrusted repositories in a disposable environment because shell execution is not sandboxed by the clone.
