SHELL=/bin/bash -eo pipefail

wheel: lint constants version clean
	./setup.py bdist_wheel

constants: aegea/constants.json
version: aegea/version.py

aegea/constants.json:
	python -c "import aegea; aegea.initialize(); from aegea.util.constants import write; write()"

aegea/version.py: setup.py
	echo "__version__ = '$$(python setup.py --version)'" > $@

test_deps:
	python -m pip install coverage flake8 mypy

lint: test_deps
	./setup.py flake8
	flake8 --filename='*' $$(grep -r -l '/usr/bin/env python' aegea/missions aegea/rootfs.skel scripts)
	mypy --check-untyped-defs --no-strict-optional $$(python setup.py --name)

test: test_deps
	coverage run --source=$$(python setup.py --name) -m unittest discover --start-directory test --top-level-directory . --verbose

init_docs:
	cd docs; sphinx-quickstart

docs:
	$(MAKE) -C docs html

install: clean version
	python -m pip install wheel
	./setup.py bdist_wheel
	python -m pip install --upgrade dist/*.whl

install_venv: clean
	virtualenv --prompt "(aegea-venv) " .venv
	source .venv/bin/activate; pip install --upgrade pip
	source .venv/bin/activate; pip install --upgrade setuptools
	source .venv/bin/activate; pip install --upgrade wheel
	source .venv/bin/activate; python ./setup.py bdist_wheel
	source .venv/bin/activate; pip install --upgrade dist/*.whl
	@echo "Run \". $$(pwd)/.venv/bin/activate\" to activate the aegea installation"

clean:
	-rm -rf build dist
	-rm -rf *.egg-info
	-rm -rf .venv

.PHONY: wheel lint test test_deps docs install clean version aegea/version.py setup.py

include common.mk
