import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import api from '../lib/api'

export const Route = createFileRoute('/installations')({ component: Installations })

interface Installation {
  installation_id: number
  account_login: string
  account_type: string
  created_at: string
  remote_deleted_at: string | null
  has_active_jobs: boolean
}

function Installations() {
  const navigate = useNavigate()
  const [installation, setInstallation] = useState<Installation | null>(null)
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get('api/github/installations')
      .json<Installation>()
      .then(setInstallation)
      .catch(async (err) => {
        if (err.response?.status === 401 || err.response?.status === 404) {
          navigate({ to: '/connect' })
        } else {
          setError('Failed to load installation.')
        }
      })
      .finally(() => setLoading(false))
  }, [navigate])

  async function handleDelete() {
    if (!installation) return
    setDeleting(true)
    setError(null)
    try {
      await api.delete(`api/github/installations/${installation.installation_id}`)
      navigate({ to: '/connect' })
    } catch (err: any) {
      const body = await err.response?.json().catch(() => ({}))
      setError(body?.error ?? 'Failed to delete installation.')
      setDeleting(false)
      setConfirming(false)
    }
  }

  if (loading) return <div className="p-8">Loading…</div>

  // Load failed (server error, GitHub outage) — show something actionable
  // instead of a blank page. The error state below the guard only covers
  // delete failures once the installation has rendered.
  if (!installation) {
    return (
      <div className="p-8 max-w-lg">
        <h1 className="text-3xl font-bold">GitHub Installation</h1>
        <div className="mt-4 rounded border border-red-300 bg-red-50 p-3 text-red-700">
          {error ?? 'Failed to load installation.'}
        </div>
        <a
          href="/connect"
          className="mt-4 inline-block rounded bg-gray-900 px-5 py-2.5 text-white hover:bg-gray-700"
        >
          Reconnect GitHub
        </a>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-lg">
      <h1 className="text-3xl font-bold">GitHub Installation</h1>

      <div className="mt-6 rounded border border-gray-200 p-4 space-y-2">
        <div>
          <span className="text-sm text-gray-500">Account</span>
          <p className="font-semibold">{installation.account_login}</p>
        </div>
        <div>
          <span className="text-sm text-gray-500">Type</span>
          <p>{installation.account_type}</p>
        </div>
        <div>
          <span className="text-sm text-gray-500">Connected</span>
          <p>{new Date(installation.created_at).toLocaleDateString()}</p>
        </div>
      </div>

      <div className="mt-4">
        <a
          href="/repo-picker"
          className="inline-block rounded bg-gray-900 px-5 py-2.5 text-white hover:bg-gray-700"
        >
          Run an audit
        </a>
      </div>

      <div className="mt-8 border-t pt-6">
        <h2 className="text-lg font-semibold text-red-700">Danger zone</h2>

        {installation.has_active_jobs && (
          <p className="mt-2 text-sm text-amber-700 bg-amber-50 rounded px-3 py-2">
            Active audit jobs are running. You cannot disconnect while jobs are in progress.
          </p>
        )}

        {error && (
          <p className="mt-2 text-sm text-red-600">{error}</p>
        )}

        {!confirming ? (
          <button
            onClick={() => setConfirming(true)}
            disabled={installation.has_active_jobs}
            className="mt-3 rounded border border-red-600 px-4 py-2 text-red-600 hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Disconnect GitHub
          </button>
        ) : (
          <div className="mt-3 space-y-2">
            <p className="text-sm text-gray-700">
              This will uninstall VibeAudit from GitHub and clear your session. Are you sure?
            </p>
            <div className="flex gap-3">
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="rounded bg-red-600 px-4 py-2 text-white hover:bg-red-700 disabled:opacity-50"
              >
                {deleting ? 'Disconnecting…' : 'Yes, disconnect'}
              </button>
              <button
                onClick={() => setConfirming(false)}
                disabled={deleting}
                className="rounded border border-gray-300 px-4 py-2 text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
