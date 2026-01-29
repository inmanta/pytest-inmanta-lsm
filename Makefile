# Shortcuts for various dev tasks. Based on makefile from pydantic
.DEFAULT_GOAL := all
isort = isort src tests examples *.py
black = black src tests examples *.py
flake8 = flake8 src tests examples *.py


.PHONY: install
install:
	pip install -U setuptools pip
	pip install -U --upgrade-strategy=eager -r requirements.dev.txt -c requirements.txt -e .

.PHONY: format
format:
	$(isort)
	$(black)
	$(flake8)

.PHONY: pep8
pep8:
	$(flake8)

.PHONY: mypy mypy-diff mypy-save
RUN_MYPY=MYPYPATH=src python -m mypy --html-report mypy -p pytest_inmanta_lsm
mypy_baseline = python -m mypy_baseline

.PHONY: mypy ci-mypy
mypy:
	$(RUN_MYPY) | $(mypy_baseline) filter --sort-baseline
ci-mypy:
	$(RUN_MYPY) --junit-xml junit-mypy.xml --cobertura-xml-report coverage | $(mypy_baseline) filter --no-colors --sort-baseline

.PHONY: mypy-sync
mypy-sync:
	$(RUN_MYPY) | $(mypy_baseline) sync --sort-baseline
stub:
	stubgen --include-docstrings src/pytest_inmanta_lsm/remote_service_instance_async.py
	sed -i -e 's/async def/def/g' out/pytest_inmanta_lsm/remote_service_instance_async.pyi
	mv out/pytest_inmanta_lsm/remote_service_instance_async.pyi src/pytest_inmanta_lsm/remote_service_instance.pyi
	$(MAKE) format
