import ky from 'ky'

function getCsrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)
  return match ? decodeURIComponent(match[1]) : ''
}

const api = ky.create({
  hooks: {
    beforeRequest: [
      (requestOrState: unknown) => {
        const maybeState = requestOrState as { request?: Request }
        const request = maybeState.request ?? (requestOrState as Request)
        if (!request || !request.headers) {
          return
        }

        if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
          request.headers.set('X-CSRFToken', getCsrfToken())
        }
      },
    ],
  },
})

export default api
