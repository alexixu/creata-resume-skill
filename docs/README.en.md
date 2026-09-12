# creata-resume-skill

A conversational resume skill: discover a target role, collect real evidence, write achievements, review with an anchored rubric, and deliver editable text and polished PDFs. [中文](../README.md)

Install the **whole repository** at `~/.codex/skills/creata-resume-skill`, or symlink a local checkout there. Keep fonts and scripts with `SKILL.md`; check an existing installation before replacing anything.

```bash
git clone git@github.com:alexixu/creata-resume-skill.git ~/.codex/skills/creata-resume-skill
```

Start a new Codex conversation:

> $creata-resume-skill Help me write a one-page resume for a software engineering role. Interview me briefly, turn my actual experience into evidence-backed bullets, and produce a reviewed PDF.

Ten templates cover software, data/ML, product, design, marketing, sales/customer success, finance, operations, graduates and career transitions. The six-dimension rubric explains its evidence and unassessed areas; it is not a hiring prediction or ATS score. [Sources and adaptation limits](../references/sources.md).

Python 3.9+ is sufficient for editable output. PDF generation additionally requires Tectonic and Poppler.

```bash
python3 scripts/resume.py init --role software --language en --output /tmp/my-resume/profile.json
# Fill and confirm real facts first, then:
python3 scripts/resume.py render /tmp/my-resume/profile.json --out /tmp/my-resume/v1 --theme plain --pdf
```

Outputs include JSON, Markdown, plain text, LaTeX and optionally PDF. `classic` retains section icons; `plain` uses sequential text without decorative icons. Both need visual and text-order review. Unconfirmed drafts require `--draft`. [Schema and delivery steps](../references/delivery.md).

The original bilingual templates remain available through `make -B`, producing `dist/resume-en.pdf` and `dist/resume-zh.pdf`. Test helpers with `python3 -m unittest discover -s tests -v`. Store personal outputs outside this public repository. Bundled fonts and icons retain their upstream notices.
