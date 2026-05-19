import * as Sentry from '@sentry/react'

export function initSentry() {
  const dsn = import.meta.env.VITE_SENTRY_DSN?.trim()
  if (!dsn) {
    return
  }

  Sentry.init({
    dsn,
  })
}
