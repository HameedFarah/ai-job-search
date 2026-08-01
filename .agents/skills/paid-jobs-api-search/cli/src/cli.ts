/// <reference types="bun" />

// Paid Jobs API Abstraction Layer - Fixture Mode

interface Provider {
  search(opts: any): Promise<any[]>
  detail(id: string): Promise<any>
}

class FixtureProvider implements Provider {
  async search(opts: any): Promise<any[]> {
    return [
      {
        id: "paid-api-001",
        title: "Design Manager",
        company: "Test Company",
        location: "Riyadh, Saudi Arabia",
        posted_at: "2026-07-08",
        url: "https://example.com/job/paid-001"
      }
    ]
  }

  async detail(id: string): Promise<any> {
    return {
      source: "paid_api",
      source_url: "https://example.com/job/paid-001",
      canonical_apply_url: "https://company.com/careers",
      title: "Design Manager",
      company: "Test Company",
      location: "Riyadh, Saudi Arabia",
      country: "Saudi Arabia",
      posted_at: "2026-07-08",
      deadline: null,
      description_text: "Fixture job from paid API provider",
      requirements: [],
      salary: "Not disclosed",
      seniority: "senior",
      work_mode: "onsite",
      confidence: "medium",
      needs_review: true,
      needs_canonical_fetch: true
    }
  }
}

const provider = new FixtureProvider()

function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

const cmd = process.argv[2]
if (cmd === "search") {
  provider.search({}).then(r => console.log(JSON.stringify({ meta: { count: r.length }, results: r })))
} else if (cmd === "detail") {
  provider.detail(process.argv[3]).then(r => console.log(JSON.stringify(r, null, 2)))
} else {
  writeError("Unknown command", "INVALID_COMMAND")
  process.exit(1)
}