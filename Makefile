PYTHON     := python3
VENV       := .venv
PIP        := $(VENV)/bin/pip
PYTHON_BIN := $(VENV)/bin/python

APPS_FILE  := apps.yaml
INPUT_DIR  := envs
OUTPUT_DIR := build
TEMPLATE   := app-template.yaml
SCRIPT     := scripts/build.py
REQS       := scripts/requirements.txt

.PHONY: all build install clean

all: build

$(VENV): $(REQS)
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet -r $(REQS)
	@touch $(VENV)

install: $(VENV)

build: $(VENV)
	$(PYTHON_BIN) $(SCRIPT) \
		--apps-file $(APPS_FILE) \
		--input-dir $(INPUT_DIR) \
		--output-dir $(OUTPUT_DIR) \
		--template $(TEMPLATE)

clean:
	rm -rf $(OUTPUT_DIR)

clean-all: clean
	rm -rf $(VENV)
