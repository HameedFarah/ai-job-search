# /career-engine

Direct command reference for the centralized Career Engine.

```bash
./career-engine doctor
./career-engine bundle build
./career-engine bundle status
./career-engine prepare --jd-file <file> --company <company> --role <role> --application-url <official-url> --live-status live --live-verified-at <timestamp> --live-verification-source <source>
./career-engine status --job-id <job-id>
./career-engine score --job-id <job-id>
./career-engine route --job-id <job-id>
./career-engine generate export --job-id <job-id>
./career-engine generate import --job-id <job-id> --file <generated-application.json>
./career-engine validate --job-id <job-id>
./career-engine render --job-id <job-id>
./career-engine package --job-id <job-id>
./career-engine scanner ingest --file <jobs.json> --scanner-id <hermes_scanner|chatgpt_scanner>
```

Exit codes: `0` ready, `10` owner input required, `20` weak fit, `30` policy failure, `40` route unresolved, `50` template missing, `70` system failure.

All ChatGPT, Hermes, daily-scanner and future service flows must use the same runtime bundle and tracker. The command does not send email or submit applications.

The approved DOCX template is the single render source: `templates/cv/2026-08-03_Abdelhamid_Farah_CV_Template_Modern_Executive_Sidebar_v1.0.docx`. It is immutable; material layout changes create a new ISO-dated template version.
