PYTHON ?= python3

.PHONY: demo clean

demo:
	$(PYTHON) -m strut_unity examples/classify_score.c --function classify_score

clean:
	rm -rf build/*

