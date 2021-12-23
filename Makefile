SHELL=/bin/bash -eo pipefail

constants: aegea/constants.json

aegea/constants.json:
	python3 -c "import aegea; aegea.initialize(); from aegea.util.constants import write; write()"

test_deps:
	python3 -m pip install coverage flake8 mypy types-python-dateutil types-requests types-PyYAML

lint: test_deps
	flake8 $$(python3 setup.py --name)
	flake8 --filename='*' $$(grep -r -l '/usr/bin/env python3' aegea/missions aegea/rootfs.skel scripts)
	mypy --check-untyped-defs --no-strict-optional $$(python3 setup.py --name)

test: test_deps
	coverage run --source=$$(python3 setup.py --name) -m unittest discover --start-directory test --top-level-directory . --verbose

init_docs:
	cd docs; sphinx-quickstart

docs:
	$(MAKE) -C docs html

install: clean
	python3 -m pip install build
	python3 -m build
	python3 -m pip install --upgrade dist/*.whl

install_venv: clean
	virtualenv --prompt "(aegea-venv) " .venv
	source .venv/bin/activate; pip install --upgrade pip
	source .venv/bin/activate; pip install --upgrade setuptools
	source .venv/bin/activate; pip install --upgrade build
	source .venv/bin/activate; python -m build
	source .venv/bin/activate; pip install --upgrade dist/*.whl
	@echo "Run \". $$(pwd)/.venv/bin/activate\" to activate the aegea installation"

clean:
	-rm -rf build dist
	-rm -rf *.egg-info
	-rm -rf .venv

.PHONY: lint test test_deps docs install clean

include common.mk
