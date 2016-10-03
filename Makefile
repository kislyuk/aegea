SHELL=/bin/bash -eo pipefail

wheel: lint constants clean
	./setup.py bdist_wheel

constants: aegea/constants.json

aegea/constants.json:
	python -c "import aegea; aegea.initialize(); from aegea.util.constants import write; write()"

test_deps:
	pip install coverage flake8

lint: test_deps
	./setup.py flake8
	flake8 --filename='*' $$(grep -r -l '/usr/bin/env python' aegea scripts)

test: test_deps lint
	coverage run setup.py test

init_docs:
	cd docs; sphinx-quickstart

docs:
	$(MAKE) -C docs html

install: clean
	python ./setup.py bdist_wheel
	pip install --upgrade dist/*.whl

clean:
	-rm -rf build dist
	-rm -rf *.egg-info

.PHONY: wheel lint test test_deps docs install clean

include common.mk
