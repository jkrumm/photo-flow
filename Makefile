.PHONY: help setup install build dev reload reload-if-stale serve service-install service-uninstall service-status service-restart test

PY := venv/bin/python
PHOTOFLOW := venv/bin/photoflow

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: install build service-install ## One-shot: install deps, build SPA, install + start the LaunchAgent

install: ## Install the Python package into venv (editable)
	$(PY) -m pip install -e .

build: ## Build the control panel SPA (npm install + build)
	cd control_panel/web && npm install && npm run build

dev: ## Run the Vite dev server (proxies to a running `make serve`)
	cd control_panel/web && npm run dev

reload: build service-restart ## Rebuild the SPA and restart the daemon — run after ANY panel or API change
	@# Poll rather than sleep: launchd takes a variable moment to bring uvicorn back up,
	@# and a fixed sleep races it and reports a false failure.
	@for i in $$(seq 1 40); do \
	  curl -sf -o /dev/null http://127.0.0.1:7717/health && break; \
	  sleep 0.5; \
	done
	@printf 'API  '; curl -s -o /dev/null -w '%{http_code} %{content_type}\n' 'http://127.0.0.1:7717/api/photos?root=final&limit=1'
	@printf 'SPA  '; curl -s -o /dev/null -w '%{http_code} %{content_type}\n' 'http://127.0.0.1:7717/photos'
	@echo 'API must be application/json — text/html means the route is missing and the SPA catch-all answered.'

reload-if-stale: ## Redeploy only if sources changed since the last one (wired to the Claude Code Stop hook)
	@scripts/reload-if-stale.sh

serve: ## Run the control panel in the foreground (no LaunchAgent)
	$(PHOTOFLOW) serve

service-install: ## Build the SPA, install the LaunchAgent, and start it (always-on)
	$(PHOTOFLOW) service install

service-uninstall: ## Stop and remove the LaunchAgent
	$(PHOTOFLOW) service uninstall

service-status: ## Show whether the service is installed and running
	$(PHOTOFLOW) service status

service-restart: ## Reload the service after a rebuild
	$(PHOTOFLOW) service restart

test: ## Run the Python test suite
	$(PY) -m pytest -q
