// NaukriGulf CLI (fixture mode - live scraping blocked)

/// <reference types="bun" />

function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

const FIXTURE_JOBS = [
  {
    id: "ng-001",
    title: "Architect - High Rise Buildings",
    company: "Saudi Binladin Group",
    companyUrl: "https://www.naukrigulf.com/company/saudi-binladin",
    location: "Riyadh, Saudi Arabia",
    date: "2026-07-06",
    url: "https://www.naukrigulf.com/jobs/architect-high-rise-12345/"
  },
  {
    id: "ng-002",
    title: "Project Manager - Construction",
    company: "Khatib & Alami",
    companyUrl: "https://www.naukrigulf.com/company/khatib-alami",
    location: "Dubai, UAE",
    date: "2026-07-05",
    url: "https://www.naukrigulf.com/jobs/project-manager-67890/"
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
  
  const normalized = {
    source: "naukrigulf",
    source_url: job.url,
    canonical_apply_url: job.url,
    title: job.title,
    company: job.company,
    location: job.location,
    country: job.location.includes("UAE") ? "UAE" : "Saudi Arabia",
    posted_at: job.date,
    deadline: null,
    description_text: `Fixture job for ${job.title} at ${job.company}`,
    requirements: ["Bachelor's in Architecture", "10+ years experience"],
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