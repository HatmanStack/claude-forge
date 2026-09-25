export const meta = {
  name: 'run',
  description: 'Run the Forge adversarial pipeline (plan, implement, review) on a plan directory',
  whenToUse: 'After /forge:brainstorm or an audit skill has written an intake doc to docs/plans/<plan-id>/. Pass the plan id, e.g. /forge:run 2026-03-12-user-auth',
  phases: [
    { title: 'Recover', detail: 'read plan state from docs/plans/<plan-id>/' },
    { title: 'Plan', detail: 'planner <-> plan reviewer, max 3 iterations' },
    { title: 'Implement', detail: 'per phase: implementer <-> reviewer, max 3 iterations' },
    { title: 'Final gate', detail: 'final review (feature) or verification (audits)' },
  ],
}

// ---------------------------------------------------------------------------
// Input: `/forge:run <plan-id> [rework] [standalone]`, or an object
// { plan, rework, agentPrefix }.
//   rework:     re-enter after a recorded NO-GO / UNVERIFIED instead of stopping
//   standalone: roles are addressed without the `forge:` plugin prefix
// ---------------------------------------------------------------------------
function parseArgs(text) {
  const [plan, ...flags] = text.trim().split(/\s+/)
  const has = f => flags.includes(f) || flags.includes(`--${f}`)
  return { plan, rework: has('rework'), agentPrefix: has('standalone') ? '' : undefined }
}
const input = typeof args === 'string' ? parseArgs(args) : (args || {})
const PLAN = String(input.plan || '').trim()
if (!/^\d{4}-\d{2}-\d{2}-[a-z0-9-]+$/.test(PLAN)) {
  return { verdict: 'ERROR', message: `Expected a plan id like 2026-03-12-user-auth, got: ${JSON.stringify(input.plan)}` }
}
const PREFIX = input.agentPrefix === undefined ? 'forge:' : input.agentPrefix
const DIR = `docs/plans/${PLAN}`
const MAX_ITER = 3
const MAX_VERIFY_CYCLES = 2

// Every role pins its model in agents/*.md; the script repeats the pin so a
// session on another model never changes who does the work.
const MODEL = {
  planner: 'opus', 'plan-reviewer': 'opus', reviewer: 'opus', 'health-reviewer': 'opus',
  'doc-reviewer': 'opus', 'final-reviewer': 'opus',
  implementer: 'sonnet', 'health-hygienist': 'sonnet', 'health-fortifier': 'sonnet',
  'doc-engineer': 'sonnet',
}

// A phase's tag picks its implementer/reviewer pair; untagged phases use the
// flow's default pair.
const PAIRS = {
  IMPLEMENTER: ['implementer', 'reviewer'],
  HYGIENIST: ['health-hygienist', 'health-reviewer'],
  FORTIFIER: ['health-fortifier', 'health-reviewer'],
  'DOC-ENGINEER': ['doc-engineer', 'doc-reviewer'],
}
const DEFAULT_TAG = { feature: 'IMPLEMENTER', 'repo-eval': 'IMPLEMENTER', 'repo-health': 'HYGIENIST', 'doc-health': 'DOC-ENGINEER', audit: 'IMPLEMENTER' }

const PHASE_LIST = {
  type: 'array',
  items: {
    type: 'object',
    properties: {
      n: { type: 'integer', description: 'Phase number (N in Phase-N.md), excluding Phase-0' },
      title: { type: 'string' },
      tag: { type: 'string', enum: ['IMPLEMENTER', 'HYGIENIST', 'FORTIFIER', 'DOC-ENGINEER', 'NONE'] },
    },
    required: ['n', 'title', 'tag'],
  },
}

const report = (signals, extra = {}) => ({
  type: 'object',
  properties: {
    signal: { type: 'string', enum: signals },
    summary: { type: 'string', description: 'Your full report: what you did, what you ran and observed' },
    ...extra,
  },
  required: ['signal', 'summary', ...Object.keys(extra)],
})

const role = (name, prompt, schema, opts = {}) =>
  agent(prompt, {
    agentType: `${PREFIX}${name}`,
    model: MODEL[name],
    schema,
    label: opts.label || name,
    phase: opts.phase,
  })

// ---------------------------------------------------------------------------
// Recover: the workflow can't read files, so one read-only agent reports the
// plan's state. Everything after this is decided in code.
// ---------------------------------------------------------------------------
phase('Recover')
const STATE = {
  type: 'object',
  properties: {
    intakeDocs: { type: 'array', items: { type: 'string', enum: ['brainstorm.md', 'eval.md', 'health-audit.md', 'doc-audit.md'] } },
    planFilesExist: { type: 'boolean', description: 'Phase-0.md exists' },
    phases: PHASE_LIST,
    planApproved: { type: 'boolean' },
    openPlanReview: { type: 'boolean', description: 'Any OPEN item tagged PLAN_REVIEW' },
    phaseStatus: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          n: { type: 'integer' },
          status: { type: 'string', enum: ['approved', 'needs-fixes', 'needs-review', 'not-started'] },
        },
        required: ['n', 'status'],
      },
    },
    finalVerdict: { type: 'string', enum: ['GO', 'NO-GO', 'VERIFIED', 'UNVERIFIED', 'NONE'] },
    evalCalibrated: { type: 'boolean', description: 'eval.md already has a "## Calibration" section' },
  },
  required: ['intakeDocs', 'planFilesExist', 'phases', 'planApproved', 'openPlanReview', 'phaseStatus', 'finalVerdict', 'evalCalibrated'],
}
const state = await agent(
  `Report the state of the Forge plan in ${DIR}/. Read only; change nothing.

- intakeDocs: which of brainstorm.md, eval.md, health-audit.md, doc-audit.md exist in ${DIR}/.
- planFilesExist / phases: Phase-0.md exists; then every Phase-N.md with N >= 1, its title (first heading) and the tag in the title ([IMPLEMENTER], [HYGIENIST], [FORTIFIER], [DOC-ENGINEER], else NONE).
- From ${DIR}/feedback.md (absent means nothing recorded). Its "## Gate Log" section lists gate decisions one per line, oldest first:
  - planApproved: the log has a PLAN_APPROVED line after its last REWORK line (or it has no REWORK line), and no PLAN_REVIEW item is OPEN.
  - openPlanReview: an item tagged PLAN_REVIEW has "**Status:** OPEN".
  - phaseStatus, per phase: "approved" if the log has "PHASE_APPROVED — Phase N"; else "needs-fixes" if a CODE_REVIEW item for Phase N is OPEN; else "needs-review" if CODE_REVIEW items for Phase N are all resolved, or \`git log --oneline\` shows commits for phase N but it has no review entries; else "not-started".
  - finalVerdict: the last GO, NO-GO, VERIFIED or UNVERIFIED line in the log; NONE if there is none, or if a REWORK line comes after it.
- evalCalibrated: eval.md exists and has a "## Calibration" section.`,
  { label: 'recover-state', model: 'sonnet', effort: 'low', schema: STATE },
)
if (!state) return { verdict: 'ERROR', message: 'State recovery agent failed' }

const docs = new Set(state.intakeDocs)
const audits = ['eval.md', 'health-audit.md', 'doc-audit.md'].filter(d => docs.has(d))
let FLOW
if (docs.has('brainstorm.md')) {
  FLOW = 'feature'
  if (audits.length) log(`brainstorm.md present: ignoring audit docs (${audits.join(', ')}); use a separate plan directory for audit work`)
} else if (audits.length > 1) FLOW = 'audit'
else if (docs.has('eval.md')) FLOW = 'repo-eval'
else if (docs.has('health-audit.md')) FLOW = 'repo-health'
else if (docs.has('doc-audit.md')) FLOW = 'doc-health'
else return { verdict: 'ERROR', message: `No intake doc in ${DIR}/. Run /forge:brainstorm or an audit skill first.` }
const FINAL_OK = FLOW === 'feature' ? 'GO' : 'VERIFIED'
log(`Plan ${PLAN}: ${FLOW} flow`)

if (state.finalVerdict === FINAL_OK) {
  return { verdict: FINAL_OK, flow: FLOW, message: 'Already complete; nothing to do.' }
}
if (state.finalVerdict !== 'NONE' && !input.rework) {
  return {
    verdict: state.finalVerdict, flow: FLOW,
    message: `The last run ended ${state.finalVerdict}; see ${DIR}/feedback.md. To rework it, run /forge:run ${PLAN} rework.`,
  }
}

// ---------------------------------------------------------------------------
// Task text. Flow-specific planner and verifier instructions are carried over
// verbatim from skills/pipeline/flows/*.md.
// ---------------------------------------------------------------------------
const PLANNER_TASK = {
  feature: `Brainstorm document: ${DIR}/brainstorm.md

Read the brainstorm document, explore the codebase, and create the implementation plan files at ${DIR}/.

Remember to create feedback.md with the empty template structure.`,
  'repo-eval': `Input document: ${DIR}/eval.md (this replaces brainstorm.md)

This is a REPO EVALUATION remediation plan. Read the eval document — it contains scores from 3 evaluators (Hire, Stress, Day 2) across 12 pillars, and a Calibration section with the effective per-pillar thresholds. Create a remediation plan that brings every pillar listed under "Pillars Requiring Remediation" to its effective threshold.

Key constraints:
- The plan addresses code quality, not features — you're improving existing code
- Prioritize by: lowest scores first, then highest complexity
- Where evaluator pillars overlap (e.g., Architecture from Hire + Defensiveness from Stress both flag the same code), consolidate into a single task
- Hygiene work (cleanup, dead code) should come in early phases
- Structural work (architecture, patterns) should come in later phases
- Fortification work (linting, CI, hooks) should come last

Phase sizing: remediation phases are typically smaller than feature phases. Size to the work — a single-phase plan is fine if the scope fits. Do NOT pad phases.

Read the eval.md, explore the codebase, and create the plan files at ${DIR}/.`,
  'repo-health': `Input document: ${DIR}/health-audit.md (this replaces brainstorm.md)

This is a REPO HEALTH remediation plan. Read the audit document — it contains a prioritized tech debt ledger with specific file:line findings across 4 vectors (Architectural, Structural, Operational, Hygiene).

Key constraints:
- SUBTRACTIVE phases FIRST (cleanup, deletion, consolidation) — tag these phases with "[HYGIENIST]" in the phase title
- ADDITIVE phases LAST (linting, CI, hooks, type safety) — tag these phases with "[FORTIFIER]" in the phase title
- The hygienist must NOT add code or abstractions — only remove and simplify
- The fortifier must NOT fix existing code — only add guardrails that enforce the clean state
- Quick wins from the audit should be in Phase 1
- CRITICAL findings before HIGH before MEDIUM

Phase sizing: cleanup and hardening phases are typically smaller than feature phases. Size to the work — a single-phase plan is fine if the scope fits. Do NOT pad phases.

Read the health-audit.md, explore the codebase, and create the plan files at ${DIR}/.`,
  'doc-health': `Input document: ${DIR}/doc-audit.md (this replaces brainstorm.md)

This is a DOCUMENTATION HEALTH remediation plan. Read the audit document — it contains drift, gaps, stale docs, broken links, stale code examples, and config drift findings.

Key constraints:
- CONTENT FIX phases FIRST (delete stale docs, fix drift, create stubs, fix links/examples)
- PREVENTION phases LAST (doc linting, link checking, auto-gen API docs, CI integration)
- Deletions before updates before creations
- Every doc fix must be verified against actual source code — docs describe what code DOES, not what it should do
- Prevention tooling scope was defined during intake — only add what the user selected

Phase sizing: doc fix phases are typically smaller than feature phases. Size to the work — a single-phase plan is fine if the scope fits. Do NOT pad phases.

Read the doc-audit.md, explore the codebase, and create the plan files at ${DIR}/.`,
  audit: `This is a UNIFIED AUDIT remediation plan. Multiple intake documents exist — read ALL of them:
- ${DIR}/health-audit.md (if exists) — tech debt findings
- ${DIR}/eval.md (if exists) — 12-pillar evaluation scores
- ${DIR}/doc-audit.md (if exists) — documentation drift findings

Create ONE plan with phases sequenced in this order:
1. [HYGIENIST] phases FIRST — subtractive cleanup (dead code, unused deps, simplify)
2. [IMPLEMENTER] phases NEXT — code fixes (architecture, error handling, performance, testing)
3. [FORTIFIER] phases NEXT — additive guardrails (lint, CI, hooks, type safety)
4. [DOC-ENGINEER] phases LAST — documentation fixes and prevention tooling

Key constraints:
- Tag EVERY phase title with exactly one of: [HYGIENIST], [IMPLEMENTER], [FORTIFIER], [DOC-ENGINEER]
- The tag determines which implementer and reviewer handle that phase
- Cleanup before structural fixes before guardrails before docs
- Where findings overlap across audit types, consolidate into a single task
- Quick wins and CRITICAL findings should be in early phases
- Phase sizing: remediation phases are typically smaller than feature phases. Size to the work — a single-phase plan is fine if the scope fits. Do NOT pad phases.

Explore the codebase and create the plan files at ${DIR}/.`,
}

const VERIFY_TASK = {
  'repo-eval': `This is a VERIFICATION pass after remediation. You are NOT doing a full evaluation — you are verifying that specific remediation targets were addressed.

Read ${DIR}/eval.md — focus on the REMEDIATION TARGETS section.

For each target:
1. Read the specific file:line referenced
2. Verify the issue was addressed (Glob/Grep/Read)
3. Run tests if the target was about test coverage or behavior

Also run the full test suite to catch regressions.

Signal VERIFIED if all targets are verified and tests pass; otherwise UNVERIFIED, listing each unverified target.`,
  'repo-health': `This is a VERIFICATION pass after remediation. You are NOT doing a full audit — you are verifying that specific CRITICAL and HIGH findings were addressed.

Read ${DIR}/health-audit.md — focus on CRITICAL and HIGH items in the Tech Debt Ledger.

For each CRITICAL/HIGH finding:
1. Read the specific file:line referenced
2. Verify the issue was addressed (Glob/Grep/Read)
3. Run tests if the finding was about test coverage or behavior

Also run the full test suite to catch regressions. MEDIUM/LOW findings do not need verification — they are acceptable to carry.

Signal VERIFIED if all CRITICAL/HIGH findings are verified and tests pass; otherwise UNVERIFIED, listing each unverified finding.`,
  'doc-health': `This is a VERIFICATION pass after remediation. You are NOT doing a full doc audit — you are verifying that specific findings were addressed.

Read ${DIR}/doc-audit.md — focus on DRIFT, STALE, and BROKEN LINK findings.

For each finding:
1. Check the specific doc path and code path referenced
2. Verify drift was fixed (doc now matches code)
3. Verify stale docs were deleted or updated
4. Verify broken links now resolve (Glob for targets)

GAP findings (missing docs) do not need verification unless the plan included creating them.

Signal VERIFIED if all DRIFT/STALE/BROKEN findings are verified; otherwise UNVERIFIED, listing each unverified finding.`,
  audit: `This is a VERIFICATION pass after remediation. You are NOT doing a full code review — you are verifying that specific findings from the original audit were addressed.

Read the original intake docs to get the list of findings:
- ${DIR}/eval.md (if exists) — check REMEDIATION TARGETS
- ${DIR}/health-audit.md (if exists) — check CRITICAL and HIGH findings
- ${DIR}/doc-audit.md (if exists) — check DRIFT, STALE, and BROKEN LINK findings

For each finding:
1. Read the specific file:line referenced in the finding
2. Verify the issue was addressed (Glob/Grep/Read)
3. Run tests if the finding was about test coverage or behavior

Also run the full test suite to catch regressions.

Signal VERIFIED if all findings are verified and tests pass; otherwise UNVERIFIED, listing each unverified finding.`,
}

const PLAN_REPORT = report(['PLAN_COMPLETE'], { phases: PHASE_LIST })
const PLAN_REVIEW = report(['PLAN_APPROVED', 'REVISION_REQUIRED'])
const IMPL_REPORT = report(['IMPLEMENTATION_COMPLETE'])
const CODE_REVIEW = report(['PHASE_APPROVED', 'CHANGES_REQUESTED'])
const FINAL_REPORT = report(['GO', 'NO-GO'], {
  planLevelIssues: { type: 'array', items: { type: 'string' } },
  implementationLevelIssues: { type: 'array', items: { type: 'string' } },
})
const VERIFY_REPORT = report(['VERIFIED', 'UNVERIFIED'], { unverified: { type: 'array', items: { type: 'string' } } })

const history = []  // one line per gate decision, returned with the verdict
const REVISE_PLAN = `The Plan Reviewer has requested revisions. Read ${DIR}/feedback.md for OPEN items tagged PLAN_REVIEW.

Address each item by revising the plan files. Move resolved feedback to the "Resolved Feedback" section with a resolution note.`
const task = body => `<task>\nVersion: ${PLAN}\n\n${body}\n</task>`

// ---------------------------------------------------------------------------
// Plan: planner <-> plan reviewer until PLAN_APPROVED or MAX_ITER.
// `firstTask` is the planner's opening task; later rounds revise from feedback.
// ---------------------------------------------------------------------------
async function planLoop(firstTask, startWithReview) {
  phase('Plan')
  let phases = null
  let plannerTask = startWithReview ? null : firstTask
  for (let i = 1; i <= MAX_ITER; i++) {
    if (plannerTask) {
      const p = await role('planner', task(plannerTask), PLAN_REPORT, { label: `planner #${i}`, phase: 'Plan' })
      if (!p) return { ok: false, why: 'planner failed' }
      phases = p.phases
    }
    const r = await role('plan-reviewer', task(`Plan location: ${DIR}/

Review the implementation plan. Verify file existence with Glob. Check dependencies, actionability, and testing strategy. If a previous review left OPEN PLAN_REVIEW items in feedback.md, check they were resolved.

If issues found: write feedback to ${DIR}/feedback.md tagged PLAN_REVIEW and signal REVISION_REQUIRED.
If the plan is good: record the approval in feedback.md and signal PLAN_APPROVED.`), PLAN_REVIEW, { label: `plan-reviewer #${i}`, phase: 'Plan' })
    if (!r) return { ok: false, why: 'plan reviewer failed' }
    history.push(`plan review ${i}: ${r.signal}`)
    if (r.signal === 'PLAN_APPROVED') return { ok: true, phases, iterations: i }
    plannerTask = REVISE_PLAN
  }
  return { ok: false, why: `plan not approved after ${MAX_ITER} iterations` }
}

// ---------------------------------------------------------------------------
// Implement: phases run in order (each builds on the last); within a phase,
// implementer <-> reviewer until PHASE_APPROVED or MAX_ITER.
// ---------------------------------------------------------------------------
async function phaseLoop(ph, status) {
  const tag = ph.tag === 'NONE' ? DEFAULT_TAG[FLOW] : ph.tag
  const [impl, rev] = PAIRS[tag]
  const label = `Phase ${ph.n}${ph.tag === 'NONE' ? '' : ` [${tag}]`}`
  let implTask = status === 'needs-review' ? null
    : status === 'needs-fixes' ? fixTask(ph.n)
    : `Phase: ${ph.n}

Read these files in order:
1. ${DIR}/README.md
2. ${DIR}/Phase-0.md
3. ${DIR}/Phase-${ph.n}.md
4. ${DIR}/feedback.md (check for OPEN CODE_REVIEW items)

Implement all tasks in Phase-${ph.n} following TDD. Make atomic commits.`
  for (let i = 1; i <= MAX_ITER; i++) {
    if (implTask) {
      const r = await role(impl, task(implTask), IMPL_REPORT, { label: `${impl} p${ph.n} #${i}`, phase: 'Implement' })
      if (!r) return { ok: false, why: `${label}: ${impl} failed` }
    }
    const v = await role(rev, task(`Phase: ${ph.n}

Review the Phase ${ph.n} implementation:
1. Read ${DIR}/Phase-0.md first (architecture source of truth)
2. Read ${DIR}/Phase-${ph.n}.md (the spec)
3. If feedback.md has CODE_REVIEW items for Phase ${ph.n}, check each OPEN one was resolved
4. Verify implementation matches spec using Read, Glob, Grep
5. Run tests and build with Bash
6. Check git commits

If issues found: write feedback to ${DIR}/feedback.md tagged CODE_REVIEW and signal CHANGES_REQUESTED.
If implementation is good: record the approval in feedback.md and signal PHASE_APPROVED.`), CODE_REVIEW, { label: `${rev} p${ph.n} #${i}`, phase: 'Implement' })
    if (!v) return { ok: false, why: `${label}: ${rev} failed` }
    history.push(`${label} review ${i}: ${v.signal}`)
    if (v.signal === 'PHASE_APPROVED') { log(`${label} approved after ${i} iteration(s)`); return { ok: true } }
    implTask = fixTask(ph.n)
  }
  return { ok: false, why: `${label} not approved after ${MAX_ITER} iterations` }
}

function fixTask(n) {
  return `Phase: ${n}

The Code Reviewer has requested changes. Read ${DIR}/feedback.md for OPEN items tagged CODE_REVIEW for Phase ${n}.

Address each item. Move resolved feedback to "Resolved Feedback" with a resolution note. Continue following TDD.`
}

async function runPhases(phases, statusOf) {
  phase('Implement')
  for (const ph of [...phases].sort((a, b) => a.n - b.n)) {
    const status = statusOf(ph.n)
    if (status === 'approved') continue
    const r = await phaseLoop(ph, status)
    if (!r.ok) return r
  }
  return { ok: true }
}

const stopped = (why) => ({ verdict: 'MAX_ITERATIONS', flow: FLOW, message: `Pipeline paused: ${why}. Unresolved items are in ${DIR}/feedback.md.`, history })

// ---------------------------------------------------------------------------
// repo-eval: calibrate the three evaluators' scores before planning.
// ---------------------------------------------------------------------------
if (FLOW === 'repo-eval' && !state.evalCalibrated) {
  phase('Plan')
  await agent(`Calibrate ${DIR}/eval.md before remediation planning. Edit only eval.md.

1. Read all 3 evaluator scorecards from eval.md.
2. For pillars that overlap conceptually (Architecture <-> Defensiveness, Code Quality <-> Performance), compare scores. A divergence of 3 or more points is signal, not noise: note it. The planner should prioritize the LOWER score for overlapping areas.
3. Read \`pillar_overrides\` from eval.md frontmatter. Default threshold is 9/10 (the \`target\` field; assume 9 if missing). Overridden pillars use their custom threshold; pillars marked \`accept\` are excluded from the gate.
4. Append this section to eval.md:

## Calibration

### Cross-Evaluator Divergences
- [Pillar A] (Hire) vs [Pillar B] (Stress): X/10 vs Y/10 — [what this signals]

### Effective Thresholds
| Pillar | Target | Source |
|--------|--------|--------|

### Pillars Requiring Remediation
[only pillars below their effective threshold]`, { label: 'calibrate-eval', model: 'sonnet', phase: 'Plan' })
}

// ---------------------------------------------------------------------------
// Rework after a recorded NO-GO / UNVERIFIED (only with the rework flag).
// ---------------------------------------------------------------------------
let phases = state.phases
let planApproved = state.planApproved
const statusMap = new Map(state.phaseStatus.map(s => [s.n, s.status]))
if (input.rework && (state.finalVerdict === 'NO-GO' || state.finalVerdict === 'UNVERIFIED')) {
  const source = state.finalVerdict === 'NO-GO' ? 'OPEN FINAL_REVIEW' : 'unverified (under "## Verification")'
  const plan = await planLoop(`Rework after ${state.finalVerdict}. First append the line REWORK under "## Gate Log" in ${DIR}/feedback.md; an interrupted run then knows this rework is under way and waits for its own plan approval. Then read the ${source} items.

Revise the plan to address them: fix plan-level issues in the existing phase files, and add new Phase-N.md files (numbered after the last existing phase) for implementation work. Tag new phases the same way as existing ones.${state.finalVerdict === 'NO-GO'
    ? ' Move each FINAL_REVIEW item you plan for to "Resolved Feedback" with a resolution naming the phase that addresses it.'
    : ''}`, false)
  if (!plan.ok) return stopped(plan.why)
  planApproved = true
  // New phases start unreviewed; previously approved phases stay approved.
  phases = plan.phases || phases
}

// ---------------------------------------------------------------------------
// Main line.
// ---------------------------------------------------------------------------
if (!planApproved) {
  // Existing plan files: open review items resume at revision, otherwise at
  // review. Never re-send the creation task over an existing plan.
  const opening = state.planFilesExist ? (state.openPlanReview ? REVISE_PLAN : null) : PLANNER_TASK[FLOW]
  const plan = await planLoop(opening, opening === null)
  if (!plan.ok) return stopped(plan.why)
  phases = plan.phases || phases
  log(`Plan approved after ${plan.iterations} iteration(s); ${phases.length} phase(s)`)
}
if (!phases || !phases.length) return { verdict: 'ERROR', flow: FLOW, message: 'Plan approved but no phases were reported.', history }

let impl = await runPhases(phases, n => statusMap.get(n) || 'not-started')
if (!impl.ok) return stopped(impl.why)

phase('Final gate')
if (FLOW === 'feature') {
  const f = await role('final-reviewer', task(`Plan location: ${DIR}/

Conduct the final comprehensive review:
1. Run the full test suite
2. Verify spec compliance across all phases — read each Phase-N.md and verify every task has corresponding code
3. Check integration points between phases
4. Scan for security issues, dead code, and tech debt
5. Produce the Production Readiness Dashboard

If ready: log GO in feedback.md and signal GO.
If not ready: write feedback to ${DIR}/feedback.md tagged FINAL_REVIEW, categorize issues as plan-level or implementation-level, log NO-GO, and signal NO-GO.`), FINAL_REPORT, { label: 'final-reviewer', phase: 'Final gate' })
  if (!f) return { verdict: 'ERROR', flow: FLOW, message: 'Final reviewer failed', history }
  history.push(`final review: ${f.signal}`)
  return ({
    verdict: f.signal, flow: FLOW, summary: f.summary, history,
    planLevelIssues: f.planLevelIssues, implementationLevelIssues: f.implementationLevelIssues,
    next: f.signal === 'GO' ? 'Production ready.' : `Address the issues or run /forge:run ${PLAN} rework.`,
  })
}

// Audit flows: verify the original findings; the unified audit flow re-plans
// significant leftovers itself, up to MAX_VERIFY_CYCLES.
for (let cycle = 1; ; cycle++) {
  const v = await role('reviewer', task(`${VERIFY_TASK[FLOW]}

Record the result in ${DIR}/feedback.md: log VERIFIED or UNVERIFIED as a line under "## Gate Log", and for UNVERIFIED list the unverified findings under a "## Verification" heading (add either heading if missing).`), VERIFY_REPORT, { label: `verification #${cycle}`, phase: 'Final gate' })
  if (!v) return { verdict: 'ERROR', flow: FLOW, message: 'Verification reviewer failed', history }
  history.push(`verification ${cycle}: ${v.signal}`)
  const loopBack = FLOW === 'audit' && v.signal === 'UNVERIFIED' && v.unverified.length >= 3 && cycle < MAX_VERIFY_CYCLES
  if (!loopBack) {
    return ({
      verdict: v.signal, flow: FLOW, summary: v.summary, unverified: v.unverified, history,
      next: v.signal === 'VERIFIED' ? 'All remediation is committed and verified.'
        : `Review the unverified items, then run /forge:run ${PLAN} rework, or accept as-is.`,
    })
  }
  log(`${v.unverified.length} findings unverified: re-planning (cycle ${cycle + 1} of ${MAX_VERIFY_CYCLES})`)
  const before = new Set(phases.map(p => p.n))
  const plan = await planLoop(`Verification found unverified items. First append the line REWORK under "## Gate Log" in ${DIR}/feedback.md. Then read the unverified findings under "## Verification".

Create a NEW remediation plan addressing ONLY the unverified items. Previous plan files exist — create new Phase-N.md files starting after the last existing phase number.

Tag every phase with [HYGIENIST], [IMPLEMENTER], [FORTIFIER], or [DOC-ENGINEER].`, false)
  if (!plan.ok) return stopped(plan.why)
  phases = plan.phases || phases
  impl = await runPhases(phases, n => (before.has(n) ? 'approved' : 'not-started'))
  if (!impl.ok) return stopped(impl.why)
}
