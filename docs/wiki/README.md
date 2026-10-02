# Wiki source

This folder is the source of truth for the [Sherpa GitHub wiki](https://github.com/pankajads/sherpa/wiki). Edit pages here and open a pull request. On merge to `main`, `.github/workflows/publish-wiki.yml` publishes them to the wiki. Edits made directly in the wiki UI are overwritten on the next publish.

- File name = page name (`How-It-Works.md` → "How It Works"). `Home.md` is the landing page; `_Sidebar.md` is the navigation.
- Link between pages with `[[Page Title|Page-Name]]`.
- Diagrams use Mermaid code blocks, which GitHub renders natively.
- This README is not published.

**One-time setup:** enable Wiki in repository Settings → General → Features, create any first page in the wiki UI (GitHub only creates the wiki git repository after that), then run the "Publish wiki" workflow manually or merge a change under `docs/wiki/`.
