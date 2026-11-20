.PHONY: docs docs-check

docs:
	uv run --group docs scriptorium render docs/scriptorium.yaml --only site

# Every block ran (scriptorium prints a failing block's traceback and exits 0),
# docs/examples/ matches the chapters, and the book names every class and command.
docs-check:
	for c in docs/chapters/*.md; do uv run --group docs scriptorium tangle --test $$c -d docs || exit 1; done
	$(MAKE) docs
	test -f docs/site/index.html
	! grep -l 'Traceback (most recent call last)' docs/site/*.html
	uvx --from git+https://github.com/apiad/rift@v0.9.0 rift check .
