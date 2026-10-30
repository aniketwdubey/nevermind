.PHONY: install test synth diff bootstrap deploy runtime destroy outputs drift

PY := ../.venv/bin/python
CDK := PATH="$(CURDIR)/node_modules/.bin:$(PATH)" node node_modules/aws-cdk/bin/cdk
export PATH := $(CURDIR)/node_modules/.bin:$(PATH)

install:
	../.venv/bin/pip install --require-hashes -r requirements.lock
	npm ci

test:
	$(PY) -m pytest -q tests

synth:
	$(CDK) synth --strict

diff:
	$(CDK) diff

bootstrap:
	$(CDK) bootstrap

deploy:
	$(PY) lifecycle.py deploy

runtime:
	$(PY) lifecycle.py runtime

destroy:
	$(PY) lifecycle.py destroy

outputs:
	$(PY) lifecycle.py outputs

drift:
	$(CDK) drift --fail
