const requiredKeys = [
  'POSTGRES_HOST',
  'POSTGRES_PORT',
  'POSTGRES_USER',
  'POSTGRES_PASSWORD',
  'POSTGRES_DB',
]

export function requireIsolatedPostgres(environment) {
  const missing = requiredKeys.filter((key) => !environment[key])
  if (missing.length) {
    throw new Error(`PostgreSQL E2E environment is incomplete: ${missing.join(', ')}`)
  }
  if (environment.POSTGRES_DB !== 'kt_e2e') {
    throw new Error(`refusing destructive E2E setup for database ${environment.POSTGRES_DB}`)
  }
  return Object.fromEntries(requiredKeys.map((key) => [key, environment[key]]))
}
