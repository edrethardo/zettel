---
name: Translation
about: A language for the interface, or a wrong/missing string
labels: i18n
---

**Language**

**What you are contributing** — a whole file, or single strings?

A partial translation is welcome. Anything you leave out falls back to German,
so an incomplete file is a working interface, not a broken one. There is
deliberately no test that fails on missing keys.

```bash
.venv/bin/python -m zettel.sprache <code>   # what is still missing
```

Screen text lives in `zettel/web/texte/*.json`. Note that the sentences the
chat agent composes at runtime (`zettel/assistant/`) are not translatable yet —
that is a separate, welcome piece of work.
