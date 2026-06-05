SANDBOX_IMAGE ?= go-issue-agent-sandbox:go1.22

.PHONY: stage0 setup build-sandbox pull-models check-env test lint fmt lock clean

stage0: setup
	cp -n .env.example .env || true
	$(MAKE) pull-models
	$(MAKE) build-sandbox
	$(MAKE) check-env

setup:
	python -m pip install -e ".[dev]"

build-sandbox:
	docker build -t $(SANDBOX_IMAGE) -f Dockerfile .

pull-models:
	bash scripts/pull_models.sh

check-env:
	bash scripts/check_env.sh

test:
	pytest -q

lint:
	ruff check src scripts

fmt:
	ruff format src scripts

lock:
	python -m pip freeze > requirements.lock

clean:
	rm -rf .cache repos outputs/*/ ; find . -name __pycache__ -type d -prune -exec rm -rf {} +
