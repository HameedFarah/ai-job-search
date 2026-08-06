# Career Engine Repository Authority

## Decision

`HameedFarah/ai-job-search` is the **canonical Career Engine source repository**.

It began as a fork of `MadsLorentzen/ai-job-search`, but the HameedFarah fork is now the authority for the owner-specific Career Engine implementation, tests, schemas, commands, scanners and runtime bundle.

Do not create a second Career Engine source repository and do not move application source code into the Obsidian Vault.

## Authorities

| Concern | Authority |
|---|---|
| Career Engine code, tests, schemas and source documentation | `HameedFarah/ai-job-search`, branch `master` |
| Local working/runtime checkout | `/home/hameedo/projects/ai-job-search` tracking `origin/master` |
| Upstream reference | `MadsLorentzen/ai-job-search`, branch `master` |
| Career facts, governance, decisions and operating contract | `HameedFarah/obsidian/projects/job-automation` |
| Per-job records, comments, revisions and outcomes | Career Engine tracker/event log and Kanban comments |
| Generated applications and private runtime data | Runtime storage outside Git |

The upstream repository is **reference-only**. Upstream changes do not override the HameedFarah fork or the Career Engine operating contract.

## Change tracking

### Source and configuration changes

Track every durable code, test, schema or source-documentation change in this repository:

1. use a bounded branch when work is material or risky;
2. commit with a clear Career Engine scope;
3. run the applicable tests, security checks and diff checks;
4. merge or fast-forward only after verification;
5. push the accepted commit to `HameedFarah/ai-job-search`;
6. record important release commits and runtime bundle hashes in the Career Engine Vault status.

Small, independently verified documentation or low-risk fixes may be committed directly to `master` when the tree is clean and no concurrent implementation branch is being bypassed.

### Upstream synchronization

Never merge upstream directly into the production checkout without review.

Use a dedicated branch such as:

```text
sync/upstream-YYYYMMDD
```

On that branch:

1. fetch `MadsLorentzen/ai-job-search` as `upstream`;
2. compare upstream with the current HameedFarah `master`;
3. select only changes that remain compatible with the GCC/KSA Career Engine, central Python engine, runtime bundle and owner approval gates;
4. resolve conflicts without restoring obsolete Denmark-specific defaults or bypassing current policy;
5. run the full regression and security suite;
6. merge the verified sync into the HameedFarah `master` and record the upstream commit range.

A large upstream divergence is not a reason for a blanket merge or rebase.

### Per-job edits and owner feedback

Do **not** commit personal job records, CV drafts, recruiter details, mailbox data or owner comments to Git.

Track them through:

- the shared Career Engine tracker;
- append-only job event/edit history;
- Kanban comments and unresolved AI requests;
- generated-version provenance for each CV, cover letter or application packet.

Every regeneration creates a new version and preserves the prior state. Git tracks the engine; the event log tracks the owner's job-specific work.

## Public/private boundary

The canonical fork is currently public and retains its upstream fork relationship. Keep it public only while source-control exclusions continue to prevent personal data, generated applications, credentials, mailbox data and runtime tracker contents from entering Git.

A new private source repository is justified only if proprietary source code or sensitive configuration must be versioned and cannot safely remain excluded. Do not create one merely to duplicate the current fork. Runtime personal data should remain outside Git even if a future private source repository is created.

## Required exclusions

Never commit:

- secrets or credentials;
- `.env` files containing values;
- personal job tracker databases or CSV exports;
- generated CVs, cover letters and application packages;
- recruiter or mailbox data;
- runtime caches, prompts or scanner results containing personal data;
- unredacted logs.

## Vault relationship

The Vault project is `projects/job-automation`, titled **Career Engine / Job Automation**. It owns strategy, career truth, operating rules, current status and release pointers. This repository owns executable implementation. Neither authority duplicates the other.
