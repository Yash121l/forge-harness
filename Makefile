PYTHON ?= python3
RUFF ?= ruff
ARGS ?=

.PHONY: setup run test lint demo dashboard clean
setup:
	$(PYTHON) scripts/check_runtime.py
	$(PYTHON) -m venv .venv
	.venv/bin/python -m compileall -q forge

run:
	.venv/bin/python -m forge $(ARGS)

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	$(RUFF) check .

demo:
	$(PYTHON) -m forge demo

dashboard:
	$(PYTHON) -m forge serve

clean:
	$(PYTHON) scripts/clean.py
