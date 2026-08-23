# Warehouse Automation Design Review Assistant

A local, evidence-grounded workspace for reviewing warehouse-automation supplier FS/DS documents against controlled URS/ES requirements.

The application keeps its review state in PostgreSQL:

- a frozen **Review Package** captures the URS/ES baseline and selected source versions;
- each **Analysis Run** stores durable, per-requirement progress, findings, and citations;
- reopening a Review Package resumes its most recent run, including partial and failed work.

The legacy prototype, open-web search, Streamlit interface, and in-memory chat state have been removed from this branch.

## Run locally

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Set `OPENAI_API_KEY` and a unique `AUTH_SECRET` in `.env`. Open the Next.js workspace at http://localhost:3000.

The available departments are fixed to `DDIT` and `QA`. Set
`DDIT_ADMIN_EMAIL` / `DDIT_ADMIN_PASSWORD` and/or `QA_ADMIN_EMAIL` /
`QA_ADMIN_PASSWORD` to bootstrap a department administrator; users choose their
department during registration.

For the complete workflow, architecture, and API notes, see [UPDATE_README.md](UPDATE_README.md).
