.PHONY: help setup install build dev serve service-install service-uninstall service-status service-restart test

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
