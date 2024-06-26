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

mypy:
	$(RUN_MYPY)

# baseline file mypy-diff will compare to
MYPY_BASELINE_FILE=.mypy-baseline
# temporary file used to store most recent mypy run
MYPY_TMP_FILE=.mypy-tmp
# temporary file used to store baseline with line numbers filtered out
MYPY_BASELINE_FILE_NO_LN_NB=$(MYPY_BASELINE_FILE).nolnnb
# prepare file for diff: remove last 2 lines and filter out line numbers
MYPY_DIFF_PREPARE=head -n -2 | sed 's/^\(.\+:\)[0-9]\+\(:\)/\1\2/'
# read old/new line number (format +n for new or -n for old) from stdin and transform to old/new line
MYPY_DIFF_FETCH_LINES=xargs -I{} sh -c 'sed -n -e "s/^/$(MYPY_SET_COLOUR)$$(echo {} | cut -c 1 -) /" -e "$$(echo {} | cut -c 2- -)p" $(MYPY_SELECT_FILE)'
MYPY_SELECT_FILE=$$(if [[ "{}" == +* ]]; then echo $(MYPY_TMP_FILE); else echo $(MYPY_BASELINE_FILE); fi)
MYPY_SET_COLOUR=$$(if [[ "{}" == +* ]]; then tput setaf 1; else tput setaf 2; fi)
# diff line format options
LFMT_LINE_NB=%dn
LFMT_NEWLINE=%c'\\012'

# compare mypy output with baseline file, show newly introduced and resolved type errors
mypy-diff:
	@ # run mypy and temporarily save result
	@ $(RUN_MYPY) > $(MYPY_TMP_FILE) || true
	@ # prepare baseline for diff and temporarily save result
	@ cat $(MYPY_BASELINE_FILE) | $(MYPY_DIFF_PREPARE) > $(MYPY_BASELINE_FILE_NO_LN_NB) || true
	@ # prepare most recent mypy output and run diff, returing +n for new lines and -n for old lines, where n is the line number
	@ cat $(MYPY_TMP_FILE) | $(MYPY_DIFF_PREPARE) | diff $(MYPY_BASELINE_FILE_NO_LN_NB) - \
		--new-line-format="+$(LFMT_LINE_NB)$(LFMT_NEWLINE)" \
		--old-line-format="-$(LFMT_LINE_NB)$(LFMT_NEWLINE)" \
		--unchanged-line-format='' \
		--unidirectional-new-file \
		| $(MYPY_DIFF_FETCH_LINES) \
		|| true
	@ # cleanup
	@ rm -f $(MYPY_TMP_FILE) $(MYPY_BASELINE_FILE_NO_LN_NB)

# save mypy output to baseline file
mypy-save:
	$(RUN_MYPY) > $(MYPY_BASELINE_FILE) || true


