// Bayt CLI (fixture mode - live scraping blocked by Cloudflare)

/// <reference types="bun" />

function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

const FIXTURE_JOBS = [
  {
    id: "bayt-001",
    title: "Senior Architect",
    company: "Al Rashed Group",
    companyUrl: "https://www.bayt.com/en/company/al-rashed-group",
    location: "Riyadh, Saudi Arabia",
    date: "2026-07-08",
    url: "https://www.bayt.com/en/jobs/view/senior-architect-12345/"
  },
  {
    id: "bayt-002",
    title: "Design Manager",
    company: "Zawaya Albina Engineering Consultancy",
    companyUrl: "https://www.bayt.com/en/company/zawaya-albina",
    location: "Amman, Jordan",
    date: "2026-07-07",
    url: "https://www.bayt.com/en/jobs/view/design-manager-67890/"
  }
]

function search() {
  const query = process.argv.includes("--query") 
    ? process.argv[process.argv.indexOf("--query") + 1] 
    : ""
  const limit = process.argv.includes("--limit")
    ? parseInt(process.argv[process.argv.indexOf("--limit") + 1])
    : 10
  
  const results = FIXTURE_JOBS.filter(j => 
    query === "" || j.title.toLowerCase().includes(query.toLowerCase())
  ).slice(0, limit)
  
  const output = {
    meta: { count: results.length, page: 1 },
    results
  }
  
  console.log(JSON.stringify(output))
}

function detail() {
  const id = process.argv[3] || ""
  const job = FIXTURE_JOBS.find(j => j.id === id || j.url.includes(id))
  
  if (!job) {
    writeError("Job not found", "NOT_FOUND")
    process.exit(1)
  }
  
  // Normalize to job.json schema
  const normalized = {
    source: "bayt",
    source_url: job.url,
    canonical_apply_url: job.url,
    title: job.title,
    company: job.company,
    location: job.location,
    country: job.location.includes("Jordan") ? "Jordan" : "Saudi Arabia",
    posted_at: job.date,
    deadline: null,
    description_text: `Fixture job for ${job.title} at ${job.company}`,
    requirements: [],
    salary: null,
    seniority: job.title.toLowerCase().includes("senior") ? "senior" : "mid",
    work_mode: "onsite",
    confidence: "medium",
    needs_review: true
  }
  
  console.log(JSON.stringify(normalized, null, 2))
}

if (process.argv[2] === "search") search()
else if (process.argv[2] === "detail") detail()
else {
  console.log("Usage: bun run cli.ts search|detail [args]")
  console.log("Note: Live scraping disabled - using fixtures")
  process.exit(1)
}