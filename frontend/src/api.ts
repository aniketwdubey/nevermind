import { z } from 'zod'

const goal = z.object({
  topic: z.string(), delivery: z.enum(['in_person', 'online', 'hybrid']),
  date: z.string(), time: z.string(), timezone: z.string(), venue: z.string(),
  room_required: z.boolean(), catering_required: z.boolean(), attendees: z.number(), meeting_url: z.string(),
})
const resource = z.object({
  id: z.string(), name: z.string(), kind: z.string(), content: z.string(),
  status: z.string(), version: z.number(), depends_on: z.array(z.string()), assumption: z.string(),
})
const workspace = z.object({
  revision: z.number(), goal, resources: z.array(resource),
  messages: z.array(z.object({ id: z.string(), subject: z.string(), content: z.string(), created_at: z.string() })),
})
const decision = z.object({
  resource_id: z.string(), choice: z.string(), confidence: z.number(),
  probabilities: z.record(z.string(), z.number()), seconds: z.number(), model: z.string(),
})
const change = z.object({
  resource_id: z.string(),
  operation: z.enum(['keep', 'update', 'release', 'reserve', 'request_cancellation', 'ask']),
  before: z.string(), after: z.string(), reason: z.string(), expected_version: z.number(),
})
const preview = z.object({
  base_revision: z.number(), goal, changed_fields: z.array(z.string()), intent: z.string(),
  changes: z.array(change), decisions: z.array(decision), questions: z.array(z.string()),
  summary: z.string(), can_apply: z.boolean(), cancellation_message: z.string(), hash: z.string(),
})
const run = z.object({
  id: z.string(), request: z.string(), base_revision: z.number(),
  status: z.enum(['queued', 'running', 'ready', 'applied', 'cancellation_requested', 'failed']),
  created_at: z.string(), updated_at: z.string(), preview: preview.nullable(), error: z.string().nullable(),
  applied_revision: z.number().nullable(),
})
export const workspaceResponse = z.object({
  workspace: workspace.nullable(),
  history: z.array(z.object({ id: z.string(), created_at: z.string(), title: z.string(), details: z.array(z.string()), revision: z.number() })),
  run: run.nullable(),
})
export const runResponse = z.object({ run })
export const mutationResponse = z.object({ workspace })
export type Workspace = z.infer<typeof workspace>
export type Run = z.infer<typeof run>
export type Change = z.infer<typeof change>
export type WorkspaceResponse = z.infer<typeof workspaceResponse>

const configSchema = z.object({ clientId: z.string(), loginUrl: z.url(), redirectUrl: z.url() })
type Config = z.infer<typeof configSchema>
let config: Config
let accessToken: string | null = null
let expiry = 0
let initialization: Promise<boolean> | undefined

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '')
}

export function initializeAuth(): Promise<boolean> {
  initialization ??= (async () => {
    const response = await fetch('/config.json', { cache: 'no-store' })
    if (!response.ok) throw new Error('The website configuration could not be loaded.')
    config = configSchema.parse(await response.json())
    const params = new URLSearchParams(window.location.search)
    if (params.has('error')) {
      window.history.replaceState({}, '', '/')
      throw new Error('Sign-in was not completed. Please try again.')
    }
    const code = params.get('code')
    if (!code) return false
    const state = sessionStorage.getItem('signin_state')
    const verifier = sessionStorage.getItem('signin_verifier')
    sessionStorage.removeItem('signin_state')
    sessionStorage.removeItem('signin_verifier')
    window.history.replaceState({}, '', '/')
    if (!state || !verifier || state !== params.get('state')) throw new Error('Sign-in verification failed. Please try again.')
    const result = await fetch(`${config.loginUrl}/oauth2/token`, {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code', client_id: config.clientId, code,
        redirect_uri: config.redirectUrl, code_verifier: verifier,
      }),
    })
    if (!result.ok) throw new Error('The sign-in session could not be established.')
    const token = z.object({ access_token: z.string(), expires_in: z.number() }).parse(await result.json())
    accessToken = token.access_token
    expiry = Date.now() + token.expires_in * 1000
    return true
  })()
  return initialization
}

export async function signIn(): Promise<void> {
  const state = base64url(crypto.getRandomValues(new Uint8Array(32)))
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(48)))
  const challenge = base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))))
  sessionStorage.setItem('signin_state', state)
  sessionStorage.setItem('signin_verifier', verifier)
  window.location.assign(`${config.loginUrl}/oauth2/authorize?${new URLSearchParams({
    client_id: config.clientId, response_type: 'code', scope: 'openid profile',
    redirect_uri: config.redirectUrl, state, code_challenge_method: 'S256', code_challenge: challenge,
  })}`)
}

export function signOut(): void {
  accessToken = null
  window.location.assign(`${config.loginUrl}/logout?${new URLSearchParams({
    client_id: config.clientId, logout_uri: config.redirectUrl,
  })}`)
}

export async function api<T>(path: string, schema: z.ZodType<T>, body?: unknown): Promise<T> {
  if (!accessToken || Date.now() >= expiry) throw new Error('Your session expired. Sign out and sign in again.')
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const value: unknown = await response.json()
  if (!response.ok) {
    const message = z.object({ error: z.string() }).safeParse(value)
    throw new Error(message.success ? message.data.error : `The request failed (${response.status}). Please refresh.`)
  }
  return schema.parse(value)
}
