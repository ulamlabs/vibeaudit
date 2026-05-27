import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import api from '../lib/api'

export const Route = createFileRoute('/connect')({ component: Connect })

function Connect() {
  const navigate = useNavigate()
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    api
      .get('api/github/installations')
      .json()
      .then(() => {
        navigate({ to: '/installations' })
      })
      .catch(() => {
        // 401 (no session) or 404 (deleted) — stay on this page.
        setChecking(false)
      })
  }, [navigate])

  if (checking) {
    return <div className="p-8">Checking connection…</div>
  }

  return (
    <div className="p-8 max-w-lg">
      <h1 className="text-3xl font-bold">Connect GitHub Repositories</h1>
      <p className="mt-4 text-gray-600">
        VibeAudit needs access to your repositories to run code audits.
        You'll be redirected to GitHub to select which repositories to grant access to.
      </p>
      <a
        href="/api/github/connect"
        className="mt-6 inline-block rounded bg-gray-900 px-5 py-2.5 text-white hover:bg-gray-700"
      >
        Connect GitHub
      </a>
    </div>
  )
}
