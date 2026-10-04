import { ArrowUpRight, BookOpen, PenLine, type LucideIcon } from 'lucide-react'

/** One kind of link between two tiles at the organization and space levels, with its count */
export interface LabelPart {
  type: string
  n: number
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** How each kind of link reads on a line: an icon and a short phrase */
export const PARTS: Record<string, { icon: LucideIcon; text: (n: number) => string }> = {
  CALLS: { icon: ArrowUpRight, text: (n) => `${plural(n, 'flow calls', 'flows call')}` },
  WRITES_TO: { icon: PenLine, text: (n) => `writes ${plural(n, 'table', 'tables')}` },
  READS_FROM: { icon: BookOpen, text: (n) => `reads ${plural(n, 'table', 'tables')}` },
}
export const partText = (p: LabelPart) => PARTS[p.type]?.text(p.n) ?? p.type.toLowerCase()

/** The full sentence, shown when the pointer is over a label */
export function sentence(p: LabelPart, src: string, dst: string, dstKind: string): string {
  if (p.type === 'CALLS') {
    const what = dstKind === 'external' ? dst : `endpoints in ${dst}`
    return `${plural(p.n, 'flow', 'flows')} in ${src} ${p.n === 1 ? 'calls' : 'call'} ${what}.`
  }
  if (p.type === 'WRITES_TO') return `${src} writes to ${plural(p.n, 'table', 'tables')} in ${dst}.`
  if (p.type === 'READS_FROM') return `${src} reads from ${plural(p.n, 'table', 'tables')} in ${dst}.`
  return `${src} → ${dst}`
}

