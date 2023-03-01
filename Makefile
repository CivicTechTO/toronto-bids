PYTHON_SCRAPERS_DIR=scrapers
VENV = venv
PYTHON = $(VENV)/bin/python3
PYTHON_VERSION = 3.9
PIP = $(VENV)/bin/pip

setup-py: $(PYTHON_SCRAPERS_DIR)/requirements.txt
	python$(PYTHON_VERSION) -m venv venv
	. $(VENV)/bin/activate
	$(PYTHON) -m pip install -r $(PYTHON_SCRAPERS_DIR)/requirements.txt

run-jupyter-notebook: setup-py
	$(VENV)/bin/jupyter notebook --notebook-dir $(PYTHON_SCRAPERS_DIR)

clean:
	rm -rf venv
