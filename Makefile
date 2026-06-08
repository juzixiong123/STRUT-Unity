PYTHON ?= python3

.PHONY: demo rules-demo clean
.PHONY: llm-demo hybrid-demo

demo:
	$(PYTHON) -m strut_unity examples/classify_score.c --function classify_score

rules-demo:
	$(PYTHON) -m strut_unity examples/classify_score.c --function classify_score --case-source rules --no-optimize

llm-demo:
	$(PYTHON) -m strut_unity examples/classify_score.c --function classify_score --case-source llm

hybrid-demo:
	$(PYTHON) -m strut_unity examples/classify_score.c --function classify_score --case-source hybrid

clean:
	rm -rf build/*
