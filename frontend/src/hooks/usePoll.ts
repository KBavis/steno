import { useCallback, useEffect, useState } from 'react'

/** Load data now and every `intervalMs` after. `reload` fetches immediately. */
export function usePoll<T>(load: () => Promise<T>, intervalMs = 2000) {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<string>()

  const reload = useCallback(() => {
    load()
      .then((d) => {
        setData(d)
        setError(undefined)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [load])

  useEffect(() => {
    reload()
    const timer = setInterval(reload, intervalMs)
    return () => clearInterval(timer)
  }, [reload, intervalMs])

  return { data, error, reload }
}
