import { useState } from 'react'

/** Run an async action (submit, delete, …), tracking whether it's running and its error. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()

  async function run(action: () => Promise<unknown>): Promise<boolean> {
    setBusy(true)
    setError(undefined)
    try {
      await action()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    } finally {
      setBusy(false)
    }
  }

  return { run, busy, error }
}
