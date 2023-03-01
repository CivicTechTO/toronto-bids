PYTHON_SCRAPERS_DIR=scrapers
SHELL := /bin/bash

deps:
	python3.9 -m venv ./venv
	source ./venv/bin/activate
	python3.9 -m pip install -r $(PYTHON_SCRAPERS_DIR)/requirements.txt

clean:
	rm -rf ./venv/
