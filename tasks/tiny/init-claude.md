<!-- bench init CLAUDE.md: repo=tiny base=0eaa01b995b8ea2cb20052be69bbf39cd2c00953 via=/init -->
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A minimal Python project: a single module `calc.py` exposing arithmetic functions (currently just `add`), with pytest tests under `tests/`. There are no dependencies, no packaging config, and no build or lint setup.

## Commands

- Run all tests: `python3 -m pytest`
- Run a single test: `python3 -m pytest tests/test_calc.py::test_add`

## Structure notes

- `calc.py` sits at the repo root and is not an installable package. `tests/test_calc.py` adds the repo root to `sys.path` itself before `from calc import add`, so run tests from any directory without installing anything. New test files need the same `sys.path` shim, or a `conftest.py` at the root.
- Known state: `add` currently returns `a - b` (see commit "base: add is broken"), so `test_add` fails until that is fixed.
