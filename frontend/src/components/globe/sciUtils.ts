import * as THREE from 'three'

export const REGIONS = [
  { id: 'eu-north', name: 'EU North',      sub: 'Sweden · eu-north-1',        lat: 59.3,  lon: 18.1,   sci: 22  },
  { id: 'eu-west',  name: 'EU West',       sub: 'Ireland · eu-west-1',        lat: 53.3,  lon: -6.3,   sci: 38  },
  { id: 'us-west',  name: 'US West',       sub: 'Oregon · us-west-2',         lat: 45.5,  lon: -122.7, sci: 55  },
  { id: 'sa-east',  name: 'SA East',       sub: 'São Paulo · sa-east-1',      lat: -23.5, lon: -46.6,  sci: 61  },
  { id: 'ap-au',    name: 'AP Australia',  sub: 'Sydney · ap-southeast-2',    lat: -33.9, lon: 151.2,  sci: 74  },
  { id: 'us-east',  name: 'US East',       sub: 'Virginia · us-east-1',       lat: 38.9,  lon: -77.0,  sci: 89  },
  { id: 'ap-ne',    name: 'AP Northeast',  sub: 'Tokyo · ap-northeast-1',     lat: 35.7,  lon: 139.7,  sci: 112 },
  { id: 'ap-se',    name: 'AP Southeast',  sub: 'Singapore · ap-southeast-1', lat: 1.35,  lon: 103.8,  sci: 148 },
] as const

export type Region = typeof REGIONS[number]

export function sciToColor(sci: number): string {
  if (sci < 50)  return '#5DB800'  // cyber green — low carbon
  if (sci < 80)  return '#D4860A'  // cyber amber — medium
  if (sci < 110) return '#C8470A'  // cyber orange — high
  return '#E03030'                  // cyber red — critical
}

export function latLonToVector3(lat: number, lon: number, radius = 1.05): THREE.Vector3 {
  const phi   = (90 - lat) * (Math.PI / 180)
  const theta = (lon + 180) * (Math.PI / 180)
  return new THREE.Vector3(
    -(radius * Math.sin(phi) * Math.cos(theta)),
      (radius * Math.cos(phi)),
      (radius * Math.sin(phi) * Math.sin(theta))
  )
}

export type TaskType = 'code' | 'reason' | 'summ'

export const MODELS: Record<TaskType, { name: string; sci: number; note: string; best: boolean }[]> = {
  code: [
    { name: 'Qwen-SCI-7B', sci: 38, note: 'Baseline · lowest SCI', best: true  },
    { name: 'Qwen-72B',    sci: 52, note: 'High quality',           best: false },
    { name: 'Claude 3.5',  sci: 78, note: 'Balanced',               best: false },
    { name: 'GPT-4o',      sci: 91, note: 'Quality-first',          best: false },
  ],
  reason: [
    { name: 'Qwen-72B',    sci: 52, note: 'Best quality/SCI',       best: true  },
    { name: 'Qwen-SCI-7B', sci: 38, note: 'Baseline',               best: false },
    { name: 'Claude 3.5',  sci: 78, note: 'Strong reasoning',       best: false },
    { name: 'GPT-4o',      sci: 91, note: 'Quality-first',          best: false },
  ],
  summ: [
    { name: 'Qwen-SCI-7B', sci: 38, note: 'Best efficiency',        best: true  },
    { name: 'Qwen-7B',     sci: 41, note: 'Fast',                   best: false },
    { name: 'Claude 3.5',  sci: 78, note: 'Good quality',           best: false },
    { name: 'GPT-4o',      sci: 91, note: 'Overkill',               best: false },
  ],
}

// ── Task classification ───────────────────────────────────────────────────────

const TASK_KEYWORDS: Record<TaskType, string[]> = {
  code: [
    'code', 'program', 'function', 'script', 'implement', 'build', 'debug',
    'develop', 'algorithm', 'api', 'class', 'method', 'refactor', 'generate',
    'write', 'compile', 'test', 'unit test', 'typescript', 'python', 'javascript',
  ],
  reason: [
    'analyze', 'reason', 'explain', 'why', 'how', 'solve', 'logic', 'problem',
    'math', 'compare', 'evaluate', 'decision', 'think', 'infer', 'deduce',
    'question', 'calculate', 'plan', 'strategy', 'research', 'assess',
  ],
  summ: [
    'summarize', 'summary', 'tldr', 'brief', 'condense', 'extract', 'key points',
    'overview', 'digest', 'shorten', 'shorter', 'recap', 'highlight', 'outline',
    'simplify', 'compress', 'paraphrase',
  ],
}

export function classifyTask(input: string): {
  task: TaskType
  confidence: 'high' | 'medium' | 'low'
  matched: string[]
} {
  const text = input.toLowerCase()
  const scores: Record<TaskType, number> = { code: 0, reason: 0, summ: 0 }
  const matchedByTask: Record<TaskType, string[]> = { code: [], reason: [], summ: [] }

  for (const [taskId, keywords] of Object.entries(TASK_KEYWORDS) as [TaskType, string[]][]) {
    for (const kw of keywords) {
      const hit = kw.includes(' ')
        ? text.includes(kw)
        : new RegExp(`\\b${kw}\\b`).test(text)
      if (hit) {
        scores[taskId]++
        matchedByTask[taskId].push(kw)
      }
    }
  }

  const best = (Object.keys(scores) as TaskType[]).reduce((a, b) =>
    scores[a] >= scores[b] ? a : b
  )
  const topScore = scores[best]

  const confidence: 'high' | 'medium' | 'low' =
    topScore >= 4 ? 'high' :
    topScore >= 2 ? 'medium' :
    'low'

  // Low confidence → fallback to 'reason'
  const task: TaskType = confidence === 'low' ? 'reason' : best

  return { task, confidence, matched: matchedByTask[task] }
}
