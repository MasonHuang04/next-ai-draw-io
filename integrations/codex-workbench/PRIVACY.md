# Publication review

This review applies to the new `integrations/codex-workbench` source addition. It does not rewrite the public upstream repository or its existing author history.

## Included in the branch

- Reusable Python, Electron, HTML, JavaScript and CSS sources.
- Dependency metadata and generic synthetic/mocked tests.
- Setup, transport, data-flow and publication-scope documentation.

## Excluded

- Codex login/authentication files and the contents of the user's Codex configuration.
- Generated localhost connection keys and state directories.
- Conversation identifiers, transcripts, titles from local history and local verification reports.
- Screenshots, clipboard attachments, personal drawings and browser profiles.
- Real home-directory paths, personal contact details, environment files and logs.
- Machine-specific desktop shortcuts and installed runtime binaries.

The source was copied with a file allowlist, not by exporting the working directory or its Git history. Local tests that depended on a real saved conversation were omitted; the public tests use synthetic data or mocked Codex output. A tracked-file check rejects forbidden runtime files, common secret formats, literal home paths, private session identifiers and personal email addresses.

## Runtime data flow

The services bind to loopback and require a generated local connection key for model/conversation APIs. The workbench reads the local Codex history, draft text, project paths and selected drawing. When the user sends a request, its prompt and optional drawing context are passed to the normal installed Codex runtime. That runtime owns its sign-in and model networking. The integration reads local configuration only to resolve the executable/model or disable unused local integrations; it does not publish the configuration.

No user's login, API key, conversation or drawing is embedded in the source. This is not a promise that prompts are processed offline: the normal Codex model/network behavior applies.

Commit attribution for this addition uses a GitHub no-reply address rather than a personal contact email. Generated runtime state remains excluded even after the service is started.
