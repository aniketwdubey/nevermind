import { useCallback, useEffect, useState } from 'react'
import { api, initializeAuth, mutationResponse, runResponse, signIn, signOut, workspaceResponse } from './api'
import type { Change, Run, WorkspaceResponse } from './api'
import './App.css'

const operationLabels: Record<Change['operation'], string> = {
  keep: 'Keep', update: 'Update', release: 'Release hold', reserve: 'Reserve room',
  request_cancellation: 'Review cancellation', ask: 'Clarification needed',
}
const examples = [
  'Make it online. Same date and time.',
  'What if we made it online?',
  'Make it online, but keep the room for local attendees.',
]
const friendly: Record<string, string> = {
  in_person: 'In person', online: 'Online', hybrid: 'Hybrid',
  supported: 'Still needed', contradicted: 'Needs a change', unresolved: 'Not enough information',
  change_request: 'Change requested', hypothetical: 'Exploring an option', unrelated: 'No change requested',
  unclear: 'Clarification needed',
}
function message(error: unknown): string { return error instanceof Error ? error.message : 'An unexpected error occurred.' }

function App() {
  const [signedIn, setSignedIn] = useState(false)
  const [initializing, setInitializing] = useState(true)
  const [data, setData] = useState<WorkspaceResponse>({ workspace: null, run: null, history: [] })
  const [run, setRun] = useState<Run | null>(null)
  const [request, setRequest] = useState(examples[0])
  const [selected, setSelected] = useState('invitation')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [notice, setNotice] = useState('')
  const state = data.workspace
  const pending = run?.status === 'queued' || run?.status === 'running'
  const runId = run?.id
  const runCreatedAt = run?.created_at
  const preview = run?.preview
  const selectedResource = state?.resources.find(r => r.id === selected)
  const selectedChange = preview?.changes.find(c => c.resource_id === selected)
  const selectedDecision = preview?.decisions.find(d => d.resource_id === selected)
  const stale = run?.status === 'ready' && state?.revision !== preview?.base_revision
  const load = useCallback(async () => {
    const next = await api('/workspace', workspaceResponse)
    setData(next)
    setRun(next.run)
  }, [])

  useEffect(() => {
    let active = true
    initializeAuth().then(async ok => {
      if (!active) return
      setSignedIn(ok)
      if (ok) await load()
    }).catch(e => { if (active) setError(message(e)) })
      .finally(() => { if (active) setInitializing(false) })
    return () => { active = false }
  }, [load])

  useEffect(() => {
    if (!pending || !runId || !runCreatedAt) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    let failures = 0
    const poll = async () => {
      try {
        const next = await api(`/runs/${runId}`, runResponse)
        if (!active) return
        setRun(next.run)
        failures = 0
        if (next.run.status !== 'queued' && next.run.status !== 'running') {
          await load()
          return
        }
      } catch (e) {
        if (!active) return
        setError(message(e))
        failures += 1
      }
      if (active && failures < 5) timer = setTimeout(poll, 4000)
    }
    timer = setTimeout(poll, 3000)
    const clock = setInterval(() => setElapsed(Math.max(0, Math.round((Date.now() - Date.parse(runCreatedAt)) / 1000))), 1000)
    return () => { active = false; clearTimeout(timer); clearInterval(clock) }
  }, [pending, runId, runCreatedAt, load])

  async function action(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await work() } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  async function reset() {
    if (state && !window.confirm('Replace this sandbox with the original prepared workshop? Existing previews will become invalid.')) return
    await action(async () => {
      await api('/workspace/reset', mutationResponse, { confirm: true, revision: state?.revision ?? 0 })
      await load()
      setNotice('Prepared workshop loaded. These are synthetic resources, not real bookings.')
    })
  }
  async function analyze() {
    if (!state) return
    await action(async () => {
      const next = await api('/runs', runResponse, { id: crypto.randomUUID(), request, revision: state.revision })
      setRun(next.run); setElapsed(0)
    })
  }
  async function approve(cancellation = false) {
    if (!run || !preview) return
    await action(async () => {
      await api(`/runs/${run.id}/${cancellation ? 'request-cancellation' : 'apply'}`, mutationResponse, { hash: preview.hash })
      await load()
      setNotice(cancellation
        ? 'Cancellation requested in the sandbox. The original message remains; vendor acceptance is unknown.'
        : 'Approved changes applied. Any cancellation still needs its own approval.')
    })
  }

  return <div className="app">
    <header className="site-header">
      <a className="brand" href="/">nevermind<span className="brand-dot">.</span></a>
      <div className="header-right"><span className="environment">WORKSHOP SANDBOX</span>
        {signedIn && <button className="text-button" onClick={signOut}>Sign out</button>}
      </div>
    </header>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert success" role="status">{notice}</div>}
    {!signedIn ? <main className="landing">
      <p className="eyebrow">A CHANGE OF PLANS, NOT A FRESH START</p>
      <h1>Change your mind.<br /><span>Keep the good work.</span></h1>
      <p className="lead">Your assistant prepared the workshop. Now it needs to be online.
        What should stay, what needs updating, and what cannot simply be undone?</p>
      <button className="primary" disabled={initializing} onClick={() => void action(signIn)}>
        {initializing ? 'Loading...' : 'Sign in to try Nevermind'}
      </button>
      <p className="small muted">Invite-only live demo. Real Amazon Nova and Strands Decider calls.<br />
        Bookings and messages are simulated. No real vendors are contacted.</p>
      <div className="story-cards">
        <article><span className="step">01</span><h3>Keep what still works</h3><p>Your slide outline does not need to disappear because the venue changed.</p></article>
        <article><span className="step">02</span><h3>Review the exact changes</h3><p>See what will change before approving anything. Uncertainty stays visible.</p></article>
        <article><span className="step">03</span><h3>Handle what cannot be undone</h3><p>A sent catering request stays sent. A cancellation needs separate approval.</p></article>
      </div>
    </main> : <main>
      <section className="workspace-heading">
        <div><p className="eyebrow">YOUR WORKSHOP</p><h1>A change of plans.</h1>
          <p className="muted">Preserve what is valid. Review the rest.</p></div>
        <div className="toolbar">
          <button disabled={busy} onClick={() => void action(load)}>Refresh</button>
          <button disabled={busy || pending} onClick={() => void reset()}>{state ? 'Reset sandbox' : 'Load prepared workshop'}</button>
        </div>
      </section>
      {state ? <>
        <section className="goal-strip" aria-label="Current confirmed plan">
          <div><span className="field-label">CURRENT PLAN</span><strong>{state.goal.topic}</strong></div>
          <div><span className="field-label">FORMAT</span><strong>{friendly[state.goal.delivery]}</strong></div>
          <div><span className="field-label">WHEN</span><strong>{state.goal.date} / {state.goal.time} IST</strong></div>
          <div><span className="field-label">ATTENDEES</span><strong>{state.goal.attendees}</strong></div>
          <span className="revision">Version {state.revision}</span>
        </section>
        <section className="composer">
          <div><p className="eyebrow">WHAT CHANGED?</p><h2>Tell us the new plan.</h2></div>
          <form onSubmit={e => { e.preventDefault(); void analyze() }}>
            <label className="sr-only" htmlFor="change-request">Describe your change</label>
            <textarea id="change-request" maxLength={1500} value={request} disabled={pending || busy}
              onChange={e => setRequest(e.target.value)} rows={2} />
            <button className="primary" disabled={pending || busy || !request.trim()} type="submit">
              {pending ? `Analyzing / ${elapsed}s` : 'Review changes'}
            </button>
          </form>
          <div className="examples">{examples.map(example => <button disabled={pending} className="example" key={example} onClick={() => setRequest(example)}>{example}</button>)}</div>
          <p className="small muted">{pending
            ? 'Real model checks are running. Starting Decider can take about a minute; the full review may take longer. Nothing is changing.'
            : 'Analysis never applies changes. You approve the exact preview. Up to 20 analyses per account each day.'}</p>
        </section>
        {run?.error && <div className="alert error" role="alert">{run.error}</div>}
        {preview && <section className="review-summary">
          <div><span className="eyebrow">{friendly[preview.intent] ?? preview.intent}</span><h2>Review before anything changes.</h2>
            <p>{preview.summary}</p>
            {preview.changed_fields.length > 0 && <p className="small muted">Proposed format: {friendly[preview.goal.delivery]} / {preview.goal.date} / {preview.goal.time} IST</p>}
          </div>
          {stale ? <span className="alert error">This plan changed; review it again.</span>
            : run.status === 'ready' && <button className="primary" disabled={!preview.can_apply || busy} onClick={() => void approve()}>Apply reviewed changes</button>}
          {run.status !== 'ready' && <span className="applied-badge">Approved changes applied</span>}
          {preview.questions.length > 0 && <div className="questions"><h3>Clarification needed</h3><ul>{preview.questions.map(q => <li key={q}>{q}</li>)}</ul><p className="small">Add the missing details to your request and review again.</p></div>}
          {preview.intent === 'hypothetical' && <p className="full-width small">Exploration only. Submit an explicit change request to make an executable plan.</p>}
        </section>}
        <div className="workbench">
          <section className="resources" aria-label="Workshop resources">
            <div className="section-heading"><h2>Prepared work</h2><span className="muted small">{state.resources.length} items</span></div>
            {state.resources.map(resource => {
              const change = preview?.changes.find(c => c.resource_id === resource.id)
              return <button key={resource.id} className={`resource-card ${selected === resource.id ? 'selected' : ''}`} onClick={() => setSelected(resource.id)}>
                <div className="resource-title"><strong>{resource.name}</strong><span className={`tag ${change?.operation ?? 'keep'}`}>{change ? operationLabels[change.operation] : resource.status.replaceAll('_', ' ')}</span></div>
                <p>{resource.content}</p><span className="small muted">Version {resource.version}{resource.kind === 'sent_request' ? ' / Already sent' : ''}</span>
              </button>
            })}
          </section>
          <section className="inspector" aria-label="Review selected item">
            <div className="section-heading"><h2>{selectedResource?.name}</h2>{selectedChange && <span className={`tag ${selectedChange.operation}`}>{operationLabels[selectedChange.operation]}</span>}</div>
            <p className="muted small">{selectedChange?.reason ?? 'Select an item to inspect it. Request a change to see its proposed update.'}</p>
            <div className="diff">
              <article><h3>{run?.status === 'ready' ? 'Before' : 'Current content'}</h3><pre>{run?.status === 'ready' && selectedChange ? selectedChange.before : selectedResource?.content}</pre></article>
              <article className="after"><h3>Proposed change</h3><pre>{selectedChange?.operation === 'keep'
                ? 'Keep this item exactly as it is.'
                : selectedChange?.after || (selectedChange?.operation === 'request_cancellation'
                  ? 'Keep the original request. Review a separate cancellation below.' : 'No draft update is available yet.')}</pre></article>
            </div>
            {selectedDecision && <details className="decision" open>
              <summary>Decider check / {selectedDecision.seconds.toFixed(1)}s</summary>
              <p><strong>{friendly[selectedDecision.choice] ?? selectedDecision.choice}</strong></p>
              <p className="small">Confidence signal: {(selectedDecision.confidence * 100).toFixed(1)}%. This is not a guarantee of correctness.</p>
              <dl>{Object.entries(selectedDecision.probabilities).map(([choice, probability]) => <div key={choice}><dt>{friendly[choice] ?? choice}</dt><dd>{(probability * 100).toFixed(1)}%</dd></div>)}</dl>
              <p className="small muted">{selectedDecision.model}</p>
            </details>}
            {!selectedDecision && preview && <div className="preserved"><strong>Preserved without another model call</strong><p className="small">This item's recorded inputs did not change.</p></div>}
          </section>
        </div>
        {preview?.cancellation_message && <section className="cancellation">
          <div><p className="eyebrow">SEPARATE APPROVAL</p><h2>A sent request cannot be unsent.</h2>
            <p>The original catering message stays in history. This sends a new cancellation request in the sandbox.</p></div>
          <pre>{preview.cancellation_message}</pre>
          <button disabled={busy || run?.status !== 'applied'} onClick={() => void approve(true)}>
            {run?.status === 'cancellation_requested' ? 'Cancellation requested' : 'Approve cancellation request'}
          </button>
          <p className="small muted">Vendor acceptance is not known. No real message will be sent.</p>
        </section>}
        <section className="history"><h2>Action history</h2>
          {data.history.map(event => <article key={event.id}><div><strong>{event.title}</strong><time>{new Date(event.created_at).toLocaleString()}</time></div><ul>{event.details.map((detail, index) => <li key={index}>{detail}</li>)}</ul></article>)}
          <details><summary>Sent sandbox messages ({state.messages.length})</summary>{state.messages.map(item => <article key={item.id}><strong>{item.subject}</strong><pre>{item.content}</pre></article>)}</details>
        </section>
      </> : <section className="empty-state"><h2>Start with a prepared workshop.</h2><p>Six synthetic resources make it possible to see what changes and what should stay.</p><button className="primary" onClick={() => void reset()} disabled={busy}>Load prepared workshop</button></section>}
    </main>}
    <footer>Nevermind / Experimental project. Real model inference; simulated workshop effects.</footer>
  </div>
}
export default App
