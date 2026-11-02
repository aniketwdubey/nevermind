.PHONY: install test package synth diff bootstrap deploy destroy outputs drift

PY := ../.venv/bin/python
CDK := PATH="$(CURDIR)/node_modules/.bin:$(PATH)" node node_modules/aws-cdk/bin/cdk

install:  ## Install locked CDK libraries, CLI and pinned Node
	../.venv/bin/pip install --require-hashes -r requirements.lock
	npm ci

test:  ## Offline template assertions (no AWS credentials needed)
	$(PY) -m pytest -q tests

package:  ## Build the Lambda bundle and website that CDK uploads
	bash ../deploy/package.sh
	npm --prefix ../frontend run build

synth: package  ## Render templates with cdk-nag checks (no AWS changes)
	$(CDK) synth --strict

diff: package  ## Show what would change in AWS
	$(CDK) diff

bootstrap:  ## One-time per account/region
	$(CDK) bootstrap

deploy: package  ## Create or update both stacks (model image builds in CodeBuild)
	$(CDK) deploy --all --require-approval never

destroy:  ## Delete both stacks, including workshop data and users
	$(CDK) destroy --all --force

outputs:  ## Show website and login details
	aws cloudformation describe-stacks --stack-name $${NEVERMIND_PROJECT:-nevermind}-app \
	  --query 'Stacks[0].Outputs' --output table

drift:  ## Compare deployed resources with their templates
	$(CDK) drift --all --fail
