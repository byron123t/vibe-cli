PYTHON := $(shell command -v python3 || command -v python)
PIP    := $(PYTHON) -m pip

.PHONY: install run test clean man install-man

install: install-man
	$(PIP) install --upgrade pip setuptools
	$(PIP) install -e .

dev:
	$(PIP) install -e ".[dev]"

run:
	$(PYTHON) main.py $(ARGS)

test:
	$(PYTHON) -m pytest tests/ -v

man:
	man ./man/vibe.1

install-man:
	mkdir -p /opt/homebrew/share/man/man1
	cp man/vibe.1 /opt/homebrew/share/man/man1/vibe.1

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -name "*.egg-info" -exec rm -rf {} + 2>/dev/null; \
	find . -name "*.pyc" -delete 2>/dev/null; \
	true
