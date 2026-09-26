PYTHON ?= python3
ARGS ?=

.PHONY: setup run test demo dashboard clean
setup:
	$(PYTHON) scripts/check_runtime.py
	$(PYTHON) -m venv .venv
	.venv/bin/python -m compileall -q forge

run:
	.venv/bin/python -m forge $(ARGS)

test:
	$(PYTHON) -m unittest discover -s tests -v

demo:
	$(PYTHON) -m forge demo

dashboard:
	$(PYTHON) -m forge serve

clean:
	$(PYTHON) scripts/clean.py
