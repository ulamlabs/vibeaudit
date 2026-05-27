import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import api from '../lib/api'

export const Route = createFileRoute('/repo-picker')({ component: RepoPicker })

interface Repo {
  id: number
  full_name: string
  private: boolean
  description: string
}

interface AuditJob {
  id: number
  repo_full_name: string
  email: string
  status: string
  created_at: string
}

function RepoPicker() {
  const navigate = useNavigate()

  const [repos, setRepos] = useState<Repo[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedRepo, setSelectedRepo] = useState('')
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [job, setJob] = useState<AuditJob | null>(null)

  useEffect(() => {
    api
      .get('api/github/repos')
      .json<{ repos: Repo[] }>()
      .then((data) => {
        setRepos(data.repos)
        if (data.repos.length > 0) {
          setSelectedRepo(data.repos[0].full_name)
        }
      })
      .catch(async (err) => {
        if (err.response?.status === 401) {
          navigate({ to: '/connect' })
        } else {
          setError('Failed to load repositories.')
        }
      })
      .finally(() => setLoading(false))
  }, [navigate])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!selectedRepo || !email) return

    setSubmitting(true)
    setError(null)

    try {
      const data = await api
        .post('api/audit/start', { json: { repo_full_name: selectedRepo, email } })
        .json<AuditJob>()
      setJob(data)
    } catch (err: any) {
      if (err.response) {
        const body = await err.response.json().catch(() => ({}))
        setError(body.detail ?? JSON.stringify(body) ?? 'Failed to start audit.')
      } else {
        setError('Network error. Please try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return <div className="p-8">Loading repositories…</div>
  }

  if (job) {
    return (
      <div className="p-8 max-w-lg">
        <h1 className="text-3xl font-bold text-green-700">Audit queued!</h1>
        <p className="mt-4 text-gray-600">
          Job <span className="font-mono font-bold">#{job.id}</span> created for{' '}
          <span className="font-mono">{job.repo_full_name}</span>. Status:{' '}
          <span className="font-semibold">{job.status}</span>.
        </p>
        <p className="mt-2 text-gray-600">
          You'll receive the report at <span className="font-semibold">{job.email}</span> when it's
          ready.
        </p>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-lg">
      <h1 className="text-3xl font-bold">Pick a Repository</h1>
      <p className="mt-2 text-gray-600">Select a repository and provide your email to start an audit.</p>

      {error && (
        <div className="mt-4 rounded border border-red-300 bg-red-50 p-3 text-red-700">{error}</div>
      )}

      <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="repo">
            Repository
          </label>
          <select
            id="repo"
            value={selectedRepo}
            onChange={(e) => setSelectedRepo(e.target.value)}
            className="mt-1 block w-full rounded border border-gray-300 px-3 py-2"
            required
          >
            {repos.map((repo) => (
              <option key={repo.id} value={repo.full_name}>
                {repo.full_name}
                {repo.private ? ' 🔒' : ''}
              </option>
            ))}
          </select>
          {repos.find((r) => r.full_name === selectedRepo)?.description && (
            <p className="mt-1 text-xs text-gray-500">
              {repos.find((r) => r.full_name === selectedRepo)?.description}
            </p>
          )}
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="email">
            Email for report
          </label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="mt-1 block w-full rounded border border-gray-300 px-3 py-2"
            required
          />
        </div>

        <button
          type="submit"
          disabled={submitting}
          className="rounded bg-gray-900 px-5 py-2.5 text-white hover:bg-gray-700 disabled:opacity-50"
        >
          {submitting ? 'Starting…' : 'Start Audit'}
        </button>
      </form>
    </div>
  )
}
