// Tier C — orchestration trajectories of workflows/run.js.
//
// Runs the workflow script with scripted agent() replies (no model calls) and
// asserts on the sequence of roles it spawns: gate order, loop limits, resume
// entry points, tag routing, and that every agent pins a model.
//
//   node --test evaluation/tier_c_trajectory/workflow_run.test.mjs

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const SRC = readFileSync(new URL('../../workflows/run.js', import.meta.url), 'utf8')
const BODY = SRC.replace(/^export const meta =/m, 'const meta =')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const script = new AsyncFunction('agent', 'phase', 'log', 'args', BODY)

const STATE = {
  intakeDocs: ['brainstorm.md'], planFilesExist: false, phases: [], planApproved: false,
  openPlanReview: false, phaseStatus: [], finalVerdict: 'NONE', evalCalibrated: false,
}
const TWO_PHASES = [{ n: 1, title: 'Phase 1', tag: 'NONE' }, { n: 2, title: 'Phase 2', tag: 'NONE' }]

// replies: role name -> array of signals (or reply objects), consumed in order.
async function run({ state = {}, replies = {}, args = '2026-01-01-demo' } = {}) {
  const calls = []
  const queues = Object.fromEntries(Object.entries(replies).map(([k, v]) => [k, [...v]]))
  const agent = async (prompt, opts = {}) => {
    const name = (opts.agentType || opts.label || '').replace(/^forge:/, '')
    calls.push({ name, model: opts.model, agentType: opts.agentType, label: opts.label, prompt })
    if (opts.label === 'recover-state') return { ...STATE, ...state }
    if (opts.label === 'calibrate-eval') return 'calibrated'
    const q = queues[name]
    if (!q || !q.length) throw new Error(`unscripted call to ${name} (${opts.label})`)
    const r = q.shift()
    const reply = typeof r === 'string' ? { signal: r } : r
    return { summary: `${name} report`, phases: TWO_PHASES, planLevelIssues: [], implementationLevelIssues: [], unverified: [], ...reply }
  }
  const result = await script(agent, () => {}, () => {}, args)
  return { result, calls, roles: calls.map(c => c.name).filter(n => n !== 'recover-state') }
}

test('feature flow: plan revision, phase rework, final GO', async () => {
  const { result, roles } = await run({
    replies: {
      planner: ['PLAN_COMPLETE', 'PLAN_COMPLETE'],
      'plan-reviewer': ['REVISION_REQUIRED', 'PLAN_APPROVED'],
      implementer: ['IMPLEMENTATION_COMPLETE', 'IMPLEMENTATION_COMPLETE', 'IMPLEMENTATION_COMPLETE'],
      reviewer: ['CHANGES_REQUESTED', 'PHASE_APPROVED', 'PHASE_APPROVED'],
      'final-reviewer': ['GO'],
    },
  })
  assert.equal(result.verdict, 'GO')
  assert.deepEqual(roles, [
    'planner', 'plan-reviewer', 'planner', 'plan-reviewer',
    'implementer', 'reviewer', 'implementer', 'reviewer',
    'implementer', 'reviewer',
    'final-reviewer',
  ])
})

test('every agent pins a non-Fable model and forge roles use the plugin prefix', async () => {
  const { calls } = await run({
    replies: {
      planner: ['PLAN_COMPLETE'], 'plan-reviewer': ['PLAN_APPROVED'],
      implementer: ['IMPLEMENTATION_COMPLETE', 'IMPLEMENTATION_COMPLETE'],
      reviewer: ['PHASE_APPROVED', 'PHASE_APPROVED'], 'final-reviewer': ['GO'],
    },
  })
  for (const c of calls) {
    assert.ok(['opus', 'sonnet', 'haiku'].includes(c.model), `${c.label} model=${c.model}`)
    if (c.agentType) assert.match(c.agentType, /^forge:/)
  }
  const model = Object.fromEntries(calls.filter(c => c.agentType).map(c => [c.name, c.model]))
  assert.deepEqual(model, {
    planner: 'opus', 'plan-reviewer': 'opus', implementer: 'sonnet', reviewer: 'opus', 'final-reviewer': 'opus',
  })
})

test('plan not approved after 3 rounds stops before implementation', async () => {
  const { result, roles } = await run({
    replies: {
      planner: ['PLAN_COMPLETE', 'PLAN_COMPLETE', 'PLAN_COMPLETE'],
      'plan-reviewer': ['REVISION_REQUIRED', 'REVISION_REQUIRED', 'REVISION_REQUIRED'],
    },
  })
  assert.equal(result.verdict, 'MAX_ITERATIONS')
  assert.equal(roles.filter(r => r === 'plan-reviewer').length, 3)
  assert.ok(!roles.includes('implementer'))
})

test('phase not approved after 3 reviews stops before the next phase', async () => {
  const { result, roles } = await run({
    state: { planFilesExist: true, planApproved: true, phases: TWO_PHASES },
    replies: {
      implementer: Array(3).fill('IMPLEMENTATION_COMPLETE'),
      reviewer: Array(3).fill('CHANGES_REQUESTED'),
    },
  })
  assert.equal(result.verdict, 'MAX_ITERATIONS')
  assert.match(result.message, /Phase 1/)
  assert.equal(roles.length, 6)
})

test('resume: approved phases are skipped, a reviewed-but-unapproved phase re-enters at review', async () => {
  const { result, roles } = await run({
    state: {
      planFilesExist: true, planApproved: true, phases: TWO_PHASES,
      phaseStatus: [{ n: 1, status: 'approved' }, { n: 2, status: 'needs-review' }],
    },
    replies: { reviewer: ['PHASE_APPROVED'], 'final-reviewer': ['GO'] },
  })
  assert.equal(result.verdict, 'GO')
  assert.deepEqual(roles, ['reviewer', 'final-reviewer'])
})

test('resume: open review feedback re-enters at the implementer with fix instructions', async () => {
  const { calls, roles } = await run({
    state: {
      planFilesExist: true, planApproved: true, phases: TWO_PHASES,
      phaseStatus: [{ n: 1, status: 'needs-fixes' }, { n: 2, status: 'approved' }],
    },
    replies: { implementer: ['IMPLEMENTATION_COMPLETE'], reviewer: ['PHASE_APPROVED'], 'final-reviewer': ['GO'] },
  })
  assert.deepEqual(roles, ['implementer', 'reviewer', 'final-reviewer'])
  assert.match(calls.find(c => c.name === 'implementer').prompt, /requested changes/)
})

test('existing plan files without open feedback start at plan review, not the planner', async () => {
  const { roles } = await run({
    state: { planFilesExist: true, phases: TWO_PHASES },
    replies: {
      'plan-reviewer': ['PLAN_APPROVED'], implementer: Array(2).fill('IMPLEMENTATION_COMPLETE'),
      reviewer: Array(2).fill('PHASE_APPROVED'), 'final-reviewer': ['GO'],
    },
  })
  assert.equal(roles[0], 'plan-reviewer')
})

test('a recorded verdict stops the run unless rework is requested', async () => {
  const done = await run({ state: { finalVerdict: 'GO' } })
  assert.equal(done.result.verdict, 'GO')
  assert.deepEqual(done.roles, [])
  const nogo = await run({ state: { finalVerdict: 'NO-GO' } })
  assert.equal(nogo.result.verdict, 'NO-GO')
  assert.deepEqual(nogo.roles, [])
})

test('rework after NO-GO re-plans, implements only new phases, and re-reviews', async () => {
  const { result, roles } = await run({
    args: { plan: '2026-01-01-demo', rework: true },
    state: {
      finalVerdict: 'NO-GO', planFilesExist: true, planApproved: true, phases: TWO_PHASES,
      phaseStatus: [{ n: 1, status: 'approved' }, { n: 2, status: 'approved' }],
    },
    replies: {
      planner: [{ signal: 'PLAN_COMPLETE', phases: [...TWO_PHASES, { n: 3, title: 'Phase 3', tag: 'NONE' }] }],
      'plan-reviewer': ['PLAN_APPROVED'],
      implementer: ['IMPLEMENTATION_COMPLETE'], reviewer: ['PHASE_APPROVED'], 'final-reviewer': ['GO'],
    },
  })
  assert.equal(result.verdict, 'GO')
  assert.deepEqual(roles, ['planner', 'plan-reviewer', 'implementer', 'reviewer', 'final-reviewer'])
})

test('phase tags route to their implementer/reviewer pair; repo-health defaults to the hygienist', async () => {
  const tagged = [
    { n: 1, title: 'Cleanup', tag: 'HYGIENIST' }, { n: 2, title: 'Fixes', tag: 'IMPLEMENTER' },
    { n: 3, title: 'Guardrails', tag: 'FORTIFIER' }, { n: 4, title: 'Docs', tag: 'DOC-ENGINEER' },
  ]
  const { roles } = await run({
    state: { intakeDocs: ['eval.md', 'health-audit.md', 'doc-audit.md'], planFilesExist: true, planApproved: true, phases: tagged },
    replies: {
      'health-hygienist': ['IMPLEMENTATION_COMPLETE'], 'health-fortifier': ['IMPLEMENTATION_COMPLETE'],
      implementer: ['IMPLEMENTATION_COMPLETE'], 'doc-engineer': ['IMPLEMENTATION_COMPLETE'],
      'health-reviewer': ['PHASE_APPROVED', 'PHASE_APPROVED'], reviewer: ['PHASE_APPROVED', 'VERIFIED'],
      'doc-reviewer': ['PHASE_APPROVED'],
    },
  })
  assert.deepEqual(roles, [
    'health-hygienist', 'health-reviewer', 'implementer', 'reviewer',
    'health-fortifier', 'health-reviewer', 'doc-engineer', 'doc-reviewer', 'reviewer',
  ])
  const health = await run({
    state: { intakeDocs: ['health-audit.md'], planFilesExist: true, planApproved: true, phases: [{ n: 1, title: 'x', tag: 'NONE' }] },
    replies: { 'health-hygienist': ['IMPLEMENTATION_COMPLETE'], 'health-reviewer': ['PHASE_APPROVED'], reviewer: ['VERIFIED'] },
  })
  assert.deepEqual(health.roles, ['health-hygienist', 'health-reviewer', 'reviewer'])
})

test('unified audit re-plans significant unverified findings once, then reports', async () => {
  const phase1 = [{ n: 1, title: 'Fixes', tag: 'IMPLEMENTER' }]
  const { result, roles } = await run({
    state: { intakeDocs: ['eval.md', 'health-audit.md'], planFilesExist: true, planApproved: true, phases: phase1 },
    replies: {
      implementer: ['IMPLEMENTATION_COMPLETE', 'IMPLEMENTATION_COMPLETE'],
      reviewer: [
        'PHASE_APPROVED',
        { signal: 'UNVERIFIED', unverified: ['a', 'b', 'c'] },
        'PHASE_APPROVED',
        { signal: 'UNVERIFIED', unverified: ['a', 'b', 'c'] },
      ],
      planner: [{ signal: 'PLAN_COMPLETE', phases: [...phase1, { n: 2, title: 'Leftovers', tag: 'IMPLEMENTER' }] }],
      'plan-reviewer': ['PLAN_APPROVED'],
    },
  })
  assert.equal(result.verdict, 'UNVERIFIED')
  assert.deepEqual(roles, [
    'implementer', 'reviewer', 'reviewer',          // phase 1, verification 1
    'planner', 'plan-reviewer',                     // re-plan the leftovers
    'implementer', 'reviewer', 'reviewer',          // phase 2 only, verification 2
  ])
})

test('single-audit flows report unverified findings without re-planning', async () => {
  const { result, roles } = await run({
    state: { intakeDocs: ['doc-audit.md'], planFilesExist: true, planApproved: true, phases: [{ n: 1, title: 'x', tag: 'NONE' }] },
    replies: {
      'doc-engineer': ['IMPLEMENTATION_COMPLETE'], 'doc-reviewer': ['PHASE_APPROVED'],
      reviewer: [{ signal: 'UNVERIFIED', unverified: ['a', 'b', 'c', 'd'] }],
    },
  })
  assert.equal(result.verdict, 'UNVERIFIED')
  assert.deepEqual(roles, ['doc-engineer', 'doc-reviewer', 'reviewer'])
})

test('repo-eval calibrates once before planning', async () => {
  const { calls } = await run({
    state: { intakeDocs: ['eval.md'] },
    replies: {
      planner: ['PLAN_COMPLETE'], 'plan-reviewer': ['PLAN_APPROVED'],
      implementer: Array(2).fill('IMPLEMENTATION_COMPLETE'), reviewer: ['PHASE_APPROVED', 'PHASE_APPROVED', 'VERIFIED'],
    },
  })
  const labels = calls.map(c => c.label)
  assert.equal(labels[1], 'calibrate-eval')
  assert.equal(labels.filter(l => l === 'calibrate-eval').length, 1)
})

test('standalone installs address roles without the plugin prefix', async () => {
  const { calls } = await run({
    args: { plan: '2026-01-01-demo', agentPrefix: '' },
    state: { finalVerdict: 'NONE', planFilesExist: true, planApproved: true, phases: [{ n: 1, title: 'x', tag: 'NONE' }] },
    replies: { implementer: ['IMPLEMENTATION_COMPLETE'], reviewer: ['PHASE_APPROVED'], 'final-reviewer': ['GO'] },
  })
  assert.deepEqual(calls.filter(c => c.agentType).map(c => c.agentType), ['implementer', 'reviewer', 'final-reviewer'])
})

test('rejects a malformed plan id without spawning anything', async () => {
  const { result, calls } = await run({ args: '../etc' })
  assert.equal(result.verdict, 'ERROR')
  assert.equal(calls.length, 0)
})

test('no intake doc is an error, not a plan', async () => {
  const { result, roles } = await run({ state: { intakeDocs: [] } })
  assert.equal(result.verdict, 'ERROR')
  assert.deepEqual(roles, [])
})
