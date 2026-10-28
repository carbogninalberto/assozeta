# Bakney Sport - Manual (Documentation)

## General rules

- Never use Claude Code as co-author in git commits. Do not add the `Co-Authored-By` line in commit messages.
- Documentation content must be written exclusively in Italian.
- Target users are non-technical people (sports association managers). Use simple, clear language.
- Framework: Mintlify with Venus theme.
- Documentation files are in `.mdx` format in the `docs/`, `faq/`, `tutorials/` folders.
- Navigation configuration is in `mint.json`.

## Project structure

- `docs/` - Main feature documentation
- `faq/` - Frequently asked questions
- `tutorials/` - Step-by-step guides and tutorials
- `images/` - Screenshots and supporting images
- `video/` - Demo videos and GIFs
- `logo/` - Brand assets
- `_snippets/` - Reusable components

## Writing conventions

- File names in kebab-case (e.g. `come-si-crea-un-socio.mdx`)
- Frontmatter with `title` and `description` in Italian
- Use Mintlify components: `<Steps>`, `<Step>`, `<Info>`, `<Tip>`, `<Note>`, `<Warning>`, `<Card>`, `<CardGroup>`, `<Frame>`
- Images organized by section in subfolders of `images/`
- Images numbered sequentially (1.png, 2.png, etc.)
