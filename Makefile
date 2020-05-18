# Shortcuts for various dev tasks. Based on makefile from pydantic
.DEFAULT_GOAL := all
isort = isort -rc plugins tests
black = black plugins tests
flake8 = flake8 plugins tests


.PHONY: install
install:
	pip install -U setuptools pip
	pip install -U requirements.dev.txt
	pip install -U -c requirements.txt . 

.PHONY: format
format:
	$(isort)
	$(black)
	$(flake8)

.PHONY: pep8
pep8:
	$(flake8)




