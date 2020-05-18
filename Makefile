# Shortcuts for various dev tasks. Based on makefile from pydantic
.DEFAULT_GOAL := all
isort = isort -rc src tests examples *.py
black = black src tests examples *.py
flake8 = flake8 src tests examples *.py


.PHONY: install
install:
	pip install -U setuptools pip
	pip install -U -r requirements.dev.txt
	pip install -U -c requirements.txt . 

.PHONY: format
format:
	$(isort)
	$(black)
	$(flake8)

.PHONY: pep8
pep8:
	$(flake8)




