.PHONY: cdk-install cdk-synth cdk-diff cdk-deploy cdk-destroy cdk-outputs cdk-bootstrap cdk-drift cdk-test

cdk-install cdk-synth cdk-diff cdk-deploy cdk-destroy cdk-outputs cdk-bootstrap cdk-drift cdk-test:
	$(MAKE) -C infra $(patsubst cdk-%,%,$@)
