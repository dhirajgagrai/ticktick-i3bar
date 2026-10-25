PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin
NAME   := ticktick-i3bar

.PHONY: install link uninstall test

install:
	install -Dm755 ticktick_i3bar.py $(DESTDIR)$(BINDIR)/$(NAME)

# Symlink instead of copy, so edits in this checkout take effect immediately.
link:
	mkdir -p $(BINDIR)
	ln -sf $(CURDIR)/ticktick_i3bar.py $(BINDIR)/$(NAME)

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/$(NAME)

test:
	python3 -m unittest discover -s tests -v
