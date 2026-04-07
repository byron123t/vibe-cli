PYTHON := $(shell command -v python3 || command -v python)
PIP    := $(PYTHON) -m pip

.PHONY: install run test clean

install:
	$(PIP) install --upgrade pip setuptools
	$(PIP) install -e .

dev:
	$(PIP) install -e ".[dev]"

run:
	$(PYTHON) main.py $(ARGS)

test:
	$(PYTHON) -m pytest tests/ -v

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -name "*.egg-info" -exec rm -rf {} + 2>/dev/null; \
	find . -name "*.pyc" -delete 2>/dev/null; \
	true
