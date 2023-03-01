PYTHON_SCRAPERS_DIR = scrapers
PYTHON_VERSION = 3.9
VENV = venv
PYTHON  = $(VENV)/bin/python3
JUPYTER = $(VENV)/bin/jupyter

setup-py: $(VENV)

run-jupyter-notebook: $(VENV)
	$(JUPYTER) notebook --notebook-dir $(PYTHON_SCRAPERS_DIR)

clean:
	rm -rf $(VENV)

$(VENV): $(PYTHON_SCRAPERS_DIR)/requirements.txt
	python$(PYTHON_VERSION) -m venv venv
	. $(VENV)/bin/activate
	$(PYTHON) -m pip install -r $(PYTHON_SCRAPERS_DIR)/requirements.txt
	touch $(VENV)

.PHONY: run-jupyter-notebook setup-py clean

