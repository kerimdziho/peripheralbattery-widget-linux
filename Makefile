.PHONY: help test lint package install install-daemon uninstall clean

help:
	@echo "make test            run the unit tests"
	@echo "make lint            validate JSON/XML and shell scripts"
	@echo "make package         build dist/*.plasmoid"
	@echo "make install         install the widget for the current user"
	@echo "make install-daemon  widget + CLI + systemd user service"
	@echo "make uninstall"

test:
	python3 -m unittest discover -s tests -v

lint:
	python3 -m json.tool plasmoid/metadata.json >/dev/null
	xmllint --noout plasmoid/contents/config/main.xml
	python3 -m py_compile plasmoid/contents/code/pbattery.py scripts/build_plasmoid.py
	bash -n scripts/install.sh
	@command -v shellcheck >/dev/null && shellcheck scripts/install.sh || echo "(shellcheck not installed - skipped)"

package: test
	python3 scripts/build_plasmoid.py

install:
	./scripts/install.sh

install-daemon:
	./scripts/install.sh --daemon

uninstall:
	./scripts/install.sh --uninstall

clean:
	rm -rf dist plasmoid/contents/code/__pycache__ tests/__pycache__
